// ===========================================================================
// cnn_top.v  --  INT8 卷积内核（4 路并行 MAC 阵列版）
//
// 与单 MAC 版数值约定完全一致，只改并行度：
//   单 MAC 版：每个窗口串行跑 K*K*C 次 MAC   -> ceil(K*K*C) 拍
//   本版    ：每拍并行 4 个 tap             -> ceil(K*K*C/4) 拍
//
// 【怎么并行的】把 (kh,kw,c) 拍平成一个线性 tap 序号，一拍吃 4 个连续 tap。
//   为了不用除法从 tap 序号反推 (kh,kw,c)，这里维护 4 个连续的 tap 状态
//   t0..t3，每拍整体前移一位、末尾用 tap_next() 补一个新的（见 tap_next 函数）。
//   K*K*C 不能被 4 整除时，用 (tcnt+i < tap_total) 掩掉多余 lane —— 被掩掉的
//   lane 乘 0，不影响累加结果。
//
// 数据布局（与 python/golden/int8_conv.py 一致）
//   x: [H][W][C]          地址 = (h*W + w)*C + c
//   w: [K][K][C][OC]      地址 = ((kh*K + kw)*C + c)*OC + oc
//   y: [OH][OW][OC]       顺序写，用 out_cnt 递增
//
// 板无关：只有 clk / rst_n 和端口，无引脚 / 板名 / 频率常数。
// ===========================================================================

module cnn_top #(
    parameter X_DEPTH = 4096,
    parameter W_DEPTH = 8192,
    parameter Y_DEPTH = 4096,
    parameter B_DEPTH = 256,
    parameter PAR     = 4          // 并行 lane 数（本版固定按 4 写死，仅作标识）
)(
    input  wire               clk,
    input  wire               rst_n,

    // ---- 配置（start 之前必须稳定）----
    input  wire [7:0]         cfg_h,
    input  wire [7:0]         cfg_w,
    input  wire [7:0]         cfg_c,
    input  wire [7:0]         cfg_k,
    input  wire [7:0]         cfg_oc,
    input  wire [1:0]         cfg_stride,
    input  wire [3:0]         cfg_pad,
    input  wire [4:0]         cfg_shift,
    input  wire               cfg_relu,

    // ---- 装载端口 ----
    input  wire               x_wen,
    input  wire [11:0]        x_waddr,
    input  wire signed [7:0]  x_wdata,
    input  wire               w_wen,
    input  wire [15:0]        w_waddr,
    input  wire signed [7:0]  w_wdata,
    input  wire               b_wen,
    input  wire [7:0]         b_waddr,
    input  wire signed [31:0] b_wdata,

    // ---- 控制 ----
    input  wire               start,
    output reg                busy,
    output reg                done,

    // ---- PMU（性能监测单元）----
    input  wire               pmu_en,
    input  wire               pmu_clr,
    output reg  [31:0]        pmu_mac_cycles,
    output reg  [31:0]        pmu_wload_cycles,
    output reg  [31:0]        pmu_xload_cycles,
    output reg  [31:0]        pmu_bubble_cycles,
    output reg  [31:0]        pmu_total_cycles,

    // ---- 结果读出 ----
    input  wire [11:0]        y_raddr,
    output wire signed [7:0]  y_rdata,
    output wire [11:0]        y_count   // 已完成输出个数（供寄存器组读状态）
);

    // ------------------------------------------------------------- 存储器
    reg signed [7:0]  x_mem [0:X_DEPTH-1];
    reg signed [7:0]  w_mem [0:W_DEPTH-1];
    reg signed [31:0] b_mem [0:B_DEPTH-1];
    reg signed [7:0]  y_mem [0:Y_DEPTH-1];

    always @(posedge clk) if (x_wen) x_mem[x_waddr] <= x_wdata;
    always @(posedge clk) if (w_wen) w_mem[w_waddr] <= w_wdata;

    // y_mem 的写口单独放在【纯同步、无复位】的 always 块里 —— BRAM 的数据口不能带异步复位，
    // 放在主状态机那个带 negege rst_n 的块里会让 Vivado 放弃 BRAM 推断（拆成 3 万多个寄存器）。
    reg                 y_we;
    reg  [11:0]         y_wa;
    reg  signed [7:0]   y_wd;
    always @(posedge clk) if (y_we) y_mem[y_wa] <= y_wd;
    always @(posedge clk) if (b_wen) b_mem[b_waddr] <= b_wdata;

    // y_mem 改成【同步读】—— 这样才能被综合成 BRAM，而不是被拆成 3 万多个寄存器。
    // 读口有 1 拍延迟：y_raddr 给出后，下一个时钟沿 y_rdata 才有效。
    reg signed [7:0] y_rdata_r;
    always @(posedge clk) y_rdata_r <= y_mem[y_raddr];
    assign y_rdata = y_rdata_r;

    // ------------------------------------------------------------- tap 前进函数
    // 拍平的 tap 序号 -> {kh, kw, c}；输入输出都打包成 16bit：{kh[3:0], kw[3:0], c[7:0]}
    // 注意：K 最大按 15 处理（本设计 K=3），够用。
    function [15:0] tap_next;
        input [15:0] t;
        input [7:0]  KK;
        input [7:0]  CC;
        reg [3:0] kh, kw;
        reg [7:0] c;
        begin
            kh = t[15:12];
            kw = t[11:8];
            c  = t[7:0];
            if      ((c + 8'd1) < CC)           tap_next = {kh, kw, c + 8'd1};
            else if ((kw + 4'd1) < KK[3:0])     tap_next = {kh, kw + 4'd1, 8'd0};
            else if ((kh + 4'd1) < KK[3:0])     tap_next = {kh + 4'd1, 4'd0, 8'd0};
            else                                tap_next = t;   // 窗口末尾，不再前进
        end
    endfunction

    reg  [15:0] tcur;   // 本组 4 个 lane 的第 0 个 tap（必须先声明）

    // 本组 4 个 lane = 从 tcur 开始的连续 4 个 tap；下一组从 lt4 开始
    wire [15:0] lt0 = tcur;
    wire [15:0] lt1 = tap_next(lt0, cfg_k, cfg_c);
    wire [15:0] lt2 = tap_next(lt1, cfg_k, cfg_c);
    wire [15:0] lt3 = tap_next(lt2, cfg_k, cfg_c);
    wire [15:0] lt4 = tap_next(lt3, cfg_k, cfg_c);

    // ------------------------------------------------------------- 循环状态
    reg  [7:0]         oh, ow, oc;
    reg  [31:0]        tcnt;               // 本窗口已发出的 tap 数（0,4,8,...）
    reg  signed [31:0] acc;
    reg  [11:0]        out_cnt;

    wire [31:0] tap_total = ({24'd0, cfg_k} * {24'd0, cfg_k}) * {24'd0, cfg_c};

    assign y_count = out_cnt;   // 已完成输出个数（out_cnt 在上面已声明）

    // 32 位坐标运算
    wire signed [31:0] h32 = cfg_h, w32 = cfg_w, cch32 = cfg_c, k32 = cfg_k;
    wire signed [31:0] oc32 = cfg_oc, st32 = cfg_stride, pd32 = cfg_pad;
    wire signed [31:0] oh32 = oh, ow32 = ow;
    wire [31:0]        ocv  = {24'd0, oc};

    wire [15:0] lane [0:3];
    assign lane[0] = lt0;
    assign lane[1] = lt1;
    assign lane[2] = lt2;
    assign lane[3] = lt3;

    // ------------------------------------------------------------- 4 个 lane 的地址 + MAC
    wire signed [7:0]  x_val  [0:3];
    wire signed [7:0]  w_val  [0:3];
    wire signed [31:0] prod32 [0:3];
    wire [3:0]         lane_valid;

    genvar gi;
    generate
    for (gi = 0; gi < 4; gi = gi + 1) begin : lane_gen
        wire [3:0] l_kh = lane[gi][15:12];
        wire [3:0] l_kw = lane[gi][11:8];
        wire [7:0] l_c  = lane[gi][7:0];

        // 该 lane 是否还有有效 tap（处理 K*K*C 不是 4 的倍数）
        wire lane_en = ((tcnt + gi) < tap_total);

        // 输入坐标（含 padding）
        wire signed [31:0] ih = oh32 * st32 + $signed({28'd0, l_kh}) - pd32;
        wire signed [31:0] iw = ow32 * st32 + $signed({28'd0, l_kw}) - pd32;
        wire in_range = (ih >= 0) && (ih < h32) && (iw >= 0) && (iw < w32);
        assign lane_valid[gi] = lane_en && in_range;

        wire [31:0] xa = (ih * w32 + iw) * cch32 + {24'd0, l_c};
        assign x_val[gi] = (lane_en && in_range) ? x_mem[xa[11:0]] : 8'sd0;

        // 权重地址：w 的线性序号 = tap_idx*OC + oc，所以直接由 (kh,kw,c) 算
        wire [31:0] wa = (({28'd0, l_kh} * k32 + {28'd0, l_kw}) * cch32 + {24'd0, l_c}) * oc32 + ocv;
        assign w_val[gi] = lane_en ? w_mem[wa[13:0]] : 8'sd0;

        wire signed [15:0] prod = x_val[gi] * w_val[gi];
        assign prod32[gi] = {{16{prod[15]}}, prod};
    end
    endgenerate

    // 4 路乘积求和（都是 int32，不会溢出）
    wire signed [31:0] acc_sum = prod32[0] + prod32[1] + prod32[2] + prod32[3];

    wire signed [31:0] b_val = b_mem[oc];

    wire signed [7:0] quant_y;
    cnn_quant u_quant (
        .acc   (acc),
        .bias  (b_val),
        .shift (cfg_shift),
        .relu  (cfg_relu),
        .y     (quant_y)
    );

    // ------------------------------------------------------------- 输出位置推进
    wire [15:0] ow16 = {8'd0, ow}, oh16 = {8'd0, oh};
    wire [15:0] st16 = {14'd0, cfg_stride}, k16 = {8'd0, cfg_k};
    wire [15:0] w16  = {8'd0, cfg_w}, h16 = {8'd0, cfg_h};
    wire [15:0] pd16 = {12'd0, cfg_pad};

    wire ow_has_next = ((ow16 + 16'd1) * st16 + k16) <= (w16 + pd16 + pd16);
    wire oh_has_next = ((oh16 + 16'd1) * st16 + k16) <= (h16 + pd16 + pd16);
    wire last_oc     = (oc + 8'd1 >= cfg_oc);
    wire all_done    = last_oc && !ow_has_next && !oh_has_next;
    wire last_round  = ((tcnt + 32'd4) >= tap_total);   // 这一拍就发完所有 tap

    // ------------------------------------------------------------- 主状态机
    localparam S_IDLE = 2'd0, S_MAC = 2'd1, S_QUANT = 2'd2, S_DONE = 2'd3;
    reg [1:0] state;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state   <= S_IDLE;
            busy    <= 1'b0;
            done    <= 1'b0;
            oh <= 8'd0; ow <= 8'd0; oc <= 8'd0;
            tcur <= 16'd0;
            tcnt <= 32'd0; acc <= 32'sd0; out_cnt <= 12'd0;
        end else begin
            y_we <= 1'b0;                  // 默认不写结果
            case (state)
            // -------------------------------------------------
            S_IDLE: begin
                done <= 1'b0;
                if (start) begin
                    oh <= 8'd0; ow <= 8'd0; oc <= 8'd0;
                    tcur <= 16'd0;
                    tcnt <= 32'd0; acc <= 32'sd0; out_cnt <= 12'd0;
                    busy <= 1'b1;
                    state <= S_MAC;
                end
            end
            // -------------------------------------------------
            S_MAC: begin
                acc  <= acc + acc_sum;              // 一拍 4 个 MAC
                tcur <= lt4;                        // 直接跳到下一组 4 个 tap
                tcnt <= tcnt + 32'd4;
                if (last_round) state <= S_QUANT;   // 下一拍 acc 已是最终值
            end
            // -------------------------------------------------
            S_QUANT: begin
                y_we <= 1'b1; y_wa <= out_cnt; y_wd <= quant_y;   // 写结果（真正写在纯同步块里）
                out_cnt <= out_cnt + 12'd1;
                acc  <= 32'sd0;
                tcnt <= 32'd0;
                tcur <= 16'd0;                                      // 下一个窗口从 tap0 开始

                if (all_done) begin
                    busy  <= 1'b0;
                    state <= S_DONE;
                end else begin
                    if (!last_oc) begin
                        oc <= oc + 8'd1;
                    end else begin
                        oc <= 8'd0;
                        if (ow_has_next) begin
                            ow <= ow + 8'd1;
                        end else begin
                            ow <= 8'd0;
                            if (oh_has_next) oh <= oh + 8'd1;
                        end
                    end
                    state <= S_MAC;
                end
            end
            // -------------------------------------------------
            S_DONE: begin
                done <= 1'b1;
                busy <= 1'b0;
                if (!start) begin
                    done  <= 1'b0;
                    state <= S_IDLE;
                end
            end
            default: state <= S_IDLE;
            endcase
        end
    end

    // ------------------------------------------------------------- PMU 性能监测单元
    // 计数口径：
    //   pmu_mac_cycles    : 处于 S_MAC 的周期数
    //   pmu_wload_cycles  : w_wen 有效周期数（权重装载）
    //   pmu_xload_cycles  : x_wen 有效周期数（输入激活装载）
    //   pmu_bubble_cycles : S_MAC 中至少 1 个 lane 无效（padding / 尾部 / 越界）的周期数
    //   pmu_total_cycles  : start 拍 + busy 周期，表示一次推理的端到端有效周期
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            pmu_mac_cycles    <= 32'd0;
            pmu_wload_cycles  <= 32'd0;
            pmu_xload_cycles  <= 32'd0;
            pmu_bubble_cycles <= 32'd0;
            pmu_total_cycles  <= 32'd0;
        end else if (pmu_clr) begin
            pmu_mac_cycles    <= 32'd0;
            pmu_wload_cycles  <= 32'd0;
            pmu_xload_cycles  <= 32'd0;
            pmu_bubble_cycles <= 32'd0;
            pmu_total_cycles  <= 32'd0;
        end else if (pmu_en) begin
            if (state == S_MAC)
                pmu_mac_cycles <= pmu_mac_cycles + 32'd1;
            if (w_wen)
                pmu_wload_cycles <= pmu_wload_cycles + 32'd1;
            if (x_wen)
                pmu_xload_cycles <= pmu_xload_cycles + 32'd1;
            if ((state == S_MAC) && (lane_valid != 4'b1111))
                pmu_bubble_cycles <= pmu_bubble_cycles + 32'd1;
            if (busy || (state == S_IDLE && start))
                pmu_total_cycles <= pmu_total_cycles + 32'd1;
        end
    end

endmodule