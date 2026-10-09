// ===========================================================================
// cnn_top.v  --  INT8 卷积内核（4 路并行 MAC 阵列 + 权重预加载 BRAM 版）
//
// 与原来 tap 切版本数值约定完全一致，只改权重来源：
//   原来：CPU 逐字节写 WDATA -> w_mem[wptr]
//   现在：上电 $readmemh 从 rom_all_tap.hex 读进 w_rom，4 路 lane 同步读
//          一个 32bit 字，拆成 4 个 tap 的权重。
//
// 数据布局（与 python 导出的 tap 顺序 hex 一致）
//   x: [H][W][C]              地址 = (h*W + w)*C + c
//   y: [OH][OW][OC]           顺序写，用 out_cnt 递增
//   w_rom 字布局: [OC][TAP_GROUP][LANE]
//     字地址 = w_base + oc * num_tap_groups + tap_group
//     32bit 字里:
//       [7:0]   = tap_group*4 + 0 的权重
//       [15:8]  = tap_group*4 + 1 的权重
//       [23:16] = tap_group*4 + 2 的权重
//       [31:24] = tap_group*4 + 3 的权重
//
// 同步读流水线：w_rom 是同步读，晚一拍。S_MAC 里用 mac_first 标志
//   跳过第一拍，让 w_word 对齐 tcur。
// ===========================================================================

module cnn_top #(
    parameter X_DEPTH       = 4096,
    parameter Y_DEPTH       = 4096,
    parameter B_DEPTH       = 256,
    parameter PAR           = 4,
    parameter W_TOTAL_WORDS = 11060   // 从 wbase_tap.h 的 W_TOTAL_WORDS_TAP 抄
)(
    input  wire               clk,
    input  wire               rst_n,

    // ---- 配置 ----
    input  wire [7:0]         cfg_h,
    input  wire [7:0]         cfg_w,
    input  wire [7:0]         cfg_c,
    input  wire [7:0]         cfg_k,
    input  wire [7:0]         cfg_oc,
    input  wire [1:0]         cfg_stride,
    input  wire [3:0]         cfg_pad,
    input  wire [4:0]         cfg_shift,
    input  wire               cfg_relu,

    // ---- 装载端口（权重不再从这里进）----
    input  wire               x_wen,
    input  wire [11:0]        x_waddr,
    input  wire signed [7:0]  x_wdata,
    input  wire               b_wen,
    input  wire [7:0]         b_waddr,
    input  wire signed [31:0] b_wdata,

    // ---- 权重基地址（从 cnn_regs 传进来，单位：32bit 字）----
    input  wire [15:0]        w_base,

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
    output wire [11:0]        y_count
);

    // ------------------------------------------------------------- 存储器
    reg signed [7:0]  x_mem [0:X_DEPTH-1];
    reg signed [31:0] b_mem [0:B_DEPTH-1];
    reg signed [7:0]  y_mem [0:Y_DEPTH-1];

    // 权重 ROM：32bit 字，$readmemh 初始化
    reg [31:0]        w_rom [0:W_TOTAL_WORDS-1];
    initial $readmemh("rom_all_tap.hex", w_rom);

    always @(posedge clk) if (x_wen) x_mem[x_waddr] <= x_wdata;
    always @(posedge clk) if (b_wen) b_mem[b_waddr] <= b_wdata;

    // y_mem 写口（纯同步，无复位，保证 BRAM 推断）
    reg                 y_we;
    reg  [11:0]         y_wa;
    reg  signed [7:0]   y_wd;
    always @(posedge clk) if (y_we) y_mem[y_wa] <= y_wd;

    // y_mem 同步读（1 拍延迟）
    reg signed [7:0] y_rdata_r;
    always @(posedge clk) y_rdata_r <= y_mem[y_raddr];
    assign y_rdata = y_rdata_r;

    // w_rom 同步读（1 拍延迟）
    reg [31:0] w_word;
    reg [31:0] w_word_addr_r;
    always @(posedge clk) w_word <= w_rom[w_word_addr_r];

    // ------------------------------------------------------------- tap 前进函数
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
            else                                tap_next = t;
        end
    endfunction

    reg  [15:0] tcur;
    wire [15:0] lt0 = tcur;
    wire [15:0] lt1 = tap_next(lt0, cfg_k, cfg_c);
    wire [15:0] lt2 = tap_next(lt1, cfg_k, cfg_c);
    wire [15:0] lt3 = tap_next(lt2, cfg_k, cfg_c);
    wire [15:0] lt4 = tap_next(lt3, cfg_k, cfg_c);

    // ------------------------------------------------------------- 循环状态
    reg  [7:0]         oh, ow, oc;
    reg  [31:0]        tcnt;
    reg  signed [31:0] acc;
    reg  [11:0]        out_cnt;
    reg                mac_first;

    wire [31:0] tap_total      = ({24'd0, cfg_k} * {24'd0, cfg_k}) * {24'd0, cfg_c};
    wire [31:0] num_tap_groups = (tap_total + 32'd3) >> 2;

    assign y_count = out_cnt;

    // 32 位坐标运算
    wire signed [31:0] h32 = cfg_h, w32 = cfg_w, cch32 = cfg_c, k32 = cfg_k;
    wire signed [31:0] oc32 = cfg_oc, st32 = cfg_stride, pd32 = cfg_pad;
    wire signed [31:0] oh32 = oh, ow32 = ow;

    wire [15:0] lane [0:3];
    assign lane[0] = lt0;
    assign lane[1] = lt1;
    assign lane[2] = lt2;
    assign lane[3] = lt3;

    // 32bit 字拆成 4 个 tap 的权重
    wire signed [7:0] w0 = w_word[7:0];
    wire signed [7:0] w1 = w_word[15:8];
    wire signed [7:0] w2 = w_word[23:16];
    wire signed [7:0] w3 = w_word[31:24];

    // ------------------------------------------------------------- 4 个 lane
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

        wire lane_en = ((tcnt + gi) < tap_total);

        // 输入坐标（含 padding）
        wire signed [31:0] ih = oh32 * st32 + $signed({28'd0, l_kh}) - pd32;
        wire signed [31:0] iw = ow32 * st32 + $signed({28'd0, l_kw}) - pd32;
        wire in_range = (ih >= 0) && (ih < h32) && (iw >= 0) && (iw < w32);
        assign lane_valid[gi] = lane_en && in_range;

        wire [31:0] xa = (ih * w32 + iw) * cch32 + {24'd0, l_c};
        assign x_val[gi] = (lane_en && in_range) ? x_mem[xa[11:0]] : 8'sd0;

        wire signed [15:0] prod = x_val[gi] * w_val[gi];
        assign prod32[gi] = {{16{prod[15]}}, prod};
    end
    endgenerate

    // 权重拆包后按 lane 分配
    assign w_val[0] = (tcnt + 32'd0 < tap_total) ? w0 : 8'sd0;
    assign w_val[1] = (tcnt + 32'd1 < tap_total) ? w1 : 8'sd0;
    assign w_val[2] = (tcnt + 32'd2 < tap_total) ? w2 : 8'sd0;
    assign w_val[3] = (tcnt + 32'd3 < tap_total) ? w3 : 8'sd0;

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
    wire last_round  = ((tcnt + 32'd4) >= tap_total);

    // 下一个输出点的 oc
    wire [7:0] oc_next = (!last_oc) ? (oc + 8'd1) : 8'd0;

    // ------------------------------------------------------------- 主状态机
    localparam S_IDLE = 2'd0, S_MAC = 2'd1, S_QUANT = 2'd2, S_DONE = 2'd3;
    reg [1:0] state;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state         <= S_IDLE;
            busy          <= 1'b0;
            done          <= 1'b0;
            oh <= 8'd0; ow <= 8'd0; oc <= 8'd0;
            tcur <= 16'd0; tcnt <= 32'd0;
            acc <= 32'sd0; out_cnt <= 12'd0;
            mac_first     <= 1'b0;
            w_word_addr_r <= 32'd0;
        end else begin
            y_we <= 1'b0;
            case (state)
            // -------------------------------------------------
            S_IDLE: begin
                done <= 1'b0;
                // 持续预取第一个 oc/tap_group 的地址
                w_word_addr_r <= w_base;   // oc=0, tap_group=0
                if (start) begin
                    oh <= 8'd0; ow <= 8'd0; oc <= 8'd0;
                    tcur <= 16'd0; tcnt <= 32'd0;
                    acc <= 32'sd0; out_cnt <= 12'd0;
                    mac_first <= 1'b1;
                    busy <= 1'b1;
                    state <= S_MAC;
                end
            end
            // -------------------------------------------------
            S_MAC: begin
                if (mac_first) begin
                    // 预热拍：跳过 MAC，让 w_word 与 tcur 对齐
                    mac_first     <= 1'b0;
                    w_word_addr_r <= w_base + oc * num_tap_groups + 32'd1;
                end else begin
                    acc  <= acc + acc_sum;
                    tcur <= lt4;
                    tcnt <= tcnt + 32'd4;
                    // 预取下一拍的地址
                    w_word_addr_r <= w_base + oc * num_tap_groups
                                     + ((tcnt + 32'd4) >> 2);
                    if (last_round) state <= S_QUANT;
                end
            end
            // -------------------------------------------------
            S_QUANT: begin
                y_we <= 1'b1; y_wa <= out_cnt; y_wd <= quant_y;
                out_cnt <= out_cnt + 12'd1;
                acc  <= 32'sd0;
                tcnt <= 32'd0;
                tcur <= 16'd0;

                if (all_done) begin
                    busy  <= 1'b0;
                    state <= S_DONE;
                end else begin
                    // 推进 oc/ow/oh
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
                    // 预取下一个输出点的第一个地址
                    w_word_addr_r <= w_base + oc_next * num_tap_groups;
                    mac_first     <= 1'b1;
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