//===========================================================================
// cnn_top.v  --  INT8 卷积内核（4 路并行 MAC + ROM IP 权重 + 2x2 max pool）
//
// 权重来源：Block Memory Generator IP（Single Port ROM），
//           IP 初始化文件 rom_all_tap.coe，内部同步读，1 拍延迟。
//
// 数据布局（与 python 导出的 tap 顺序 coe 一致）
//   x: [H][W][C]              地址 = (h*W + w)*C + c
//   y: [OH][OW][OC]           顺序写，用 out_cnt 递增
//   ROM 字布局: [OC][TAP_GROUP][LANE]
//     字地址 = w_base + oc * num_tap_groups + tap_group
//     32bit 字里:
//       [7:0]   = tap_group*4 + 0 的权重
//       [15:8]  = tap_group*4 + 1 的权重
//       [23:16] = tap_group*4 + 2 的权重
//       [31:24] = tap_group*4 + 3 的权重
//
// 同步读流水线：ROM IP 输出晚一拍。S_MAC 里用 mac_first 标志
//   跳过第一拍，让 w_word 对齐 tcur。
//
// cfg_pool = 1 时进入池化模式：
//   - tap_total = 4（只处理 2x2 空间窗口）
//   - 4 个 lane 固定为 (kh,kw) = (0,0)/(0,1)/(1,0)/(1,1)，c = oc
//   - 权重不参与计算，bias 忽略
//   - acc 初始化为 -128，取 4 个 lane 输入的最大值
//   - 每个输出点只需 1 拍
//===========================================================================

module cnn_top (
    input  wire               clk,
    input  wire               rst_n,

    input  wire [7:0]         cfg_h,
    input  wire [7:0]         cfg_w,
    input  wire [7:0]         cfg_c,
    input  wire [7:0]         cfg_k,
    input  wire [7:0]         cfg_oc,
    input  wire [1:0]         cfg_stride,
    input  wire [3:0]         cfg_pad,
    input  wire [4:0]         cfg_shift,
    input  wire               cfg_relu,
    input  wire               cfg_pool,

    input  wire               x_wen,
    input  wire [11:0]        x_waddr,
    input  wire signed [7:0]  x_wdata,
    input  wire               b_wen,
    input  wire [7:0]         b_waddr,
    input  wire signed [31:0] b_wdata,

    input  wire [15:0]        w_base,

    input  wire               start,
    output reg                busy,
    output reg                done,

    input  wire               pmu_en,
    input  wire               pmu_clr,
    output reg  [31:0]        pmu_mac_cycles,
    output reg  [31:0]        pmu_wload_cycles,
    output reg  [31:0]        pmu_xload_cycles,
    output reg  [31:0]        pmu_bubble_cycles,
    output reg  [31:0]        pmu_total_cycles,

    input  wire [11:0]        y_raddr,
    output wire signed [7:0]  y_rdata,
    output wire [11:0]        y_count
);

    // ------------------------------------------------------------- 存储器
    reg signed [7:0]  x_mem [0:4096-1];
    reg signed [31:0] b_mem [0:256-1];
    reg signed [7:0]  y_mem [0:4096-1];

    always @(posedge clk) if (x_wen) x_mem[x_waddr] <= x_wdata;
    always @(posedge clk) if (b_wen) b_mem[b_waddr] <= b_wdata;

    reg                 y_we;
    reg  [11:0]         y_wa;
    reg  signed [7:0]   y_wd;
    always @(posedge clk) if (y_we) y_mem[y_wa] <= y_wd;

    reg signed [7:0] y_rdata_r;
    always @(posedge clk) y_rdata_r <= y_mem[y_raddr];
    assign y_rdata = y_rdata_r;

    // ------------------------------------------------------------- 权重 ROM IP
    reg  [31:0] w_word_addr_r;
    wire [13:0] w_rom_addr = w_word_addr_r[13:0];
    wire [31:0] w_word;

    rom_all_tap u_rom (
        .clka  (clk        ),
        .addra (w_rom_addr ),
        .douta (w_word     )
    );

    // ------------------------------------------------------------- tap_next
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

    // 池化模式 tap_total = 4（只处理 2x2 空间窗口）
    wire [31:0] tap_total = cfg_pool
                          ? 32'd4
                          : (({24'd0, cfg_k} * {24'd0, cfg_k}) * {24'd0, cfg_c});
    wire [31:0] num_tap_groups = (tap_total + 32'd3) >> 2;

    assign y_count = out_cnt;

    wire signed [31:0] h32 = cfg_h, w32 = cfg_w, cch32 = cfg_c, k32 = cfg_k;
    wire signed [31:0] oc32 = cfg_oc, st32 = cfg_stride, pd32 = cfg_pad;
    wire signed [31:0] oh32 = oh, ow32 = ow;

    // 池化模式 4 个 lane 固定为 2x2 空间窗口，c = oc
    wire [15:0] lane [0:3];
    wire [15:0] pool_lane0 = {4'd0, 4'd0, oc};   // (kh=0, kw=0)
    wire [15:0] pool_lane1 = {4'd0, 4'd1, oc};   // (kh=0, kw=1)
    wire [15:0] pool_lane2 = {4'd1, 4'd0, oc};   // (kh=1, kw=0)
    wire [15:0] pool_lane3 = {4'd1, 4'd1, oc};   // (kh=1, kw=1)

    assign lane[0] = cfg_pool ? pool_lane0 : lt0;
    assign lane[1] = cfg_pool ? pool_lane1 : lt1;
    assign lane[2] = cfg_pool ? pool_lane2 : lt2;
    assign lane[3] = cfg_pool ? pool_lane3 : lt3;

    wire signed [7:0] w0 = w_word[7:0];
    wire signed [7:0] w1 = w_word[15:8];
    wire signed [7:0] w2 = w_word[23:16];
    wire signed [7:0] w3 = w_word[31:24];

    // ------------------------------------------------------------- 4 lane
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

    assign w_val[0] = (tcnt + 32'd0 < tap_total) ? w0 : 8'sd0;
    assign w_val[1] = (tcnt + 32'd1 < tap_total) ? w1 : 8'sd0;
    assign w_val[2] = (tcnt + 32'd2 < tap_total) ? w2 : 8'sd0;
    assign w_val[3] = (tcnt + 32'd3 < tap_total) ? w3 : 8'sd0;

    wire signed [31:0] acc_sum = prod32[0] + prod32[1] + prod32[2] + prod32[3];

    // 池化：本拍 4 个 lane 的输入最大值（无效 lane 视为 -128）
    wire signed [7:0] pool_x0 = lane_valid[0] ? x_val[0] : 8'sh80;
    wire signed [7:0] pool_x1 = lane_valid[1] ? x_val[1] : 8'sh80;
    wire signed [7:0] pool_x2 = lane_valid[2] ? x_val[2] : 8'sh80;
    wire signed [7:0] pool_x3 = lane_valid[3] ? x_val[3] : 8'sh80;
    wire signed [7:0] pool_m01 = (pool_x0 > pool_x1) ? pool_x0 : pool_x1;
    wire signed [7:0] pool_m23 = (pool_x2 > pool_x3) ? pool_x2 : pool_x3;
    wire signed [7:0] pool_round = (pool_m01 > pool_m23) ? pool_m01 : pool_m23;
    wire signed [31:0] pool_max32 = {{24{pool_round[7]}}, pool_round};

    wire signed [31:0] b_val = cfg_pool ? 32'sd0 : b_mem[oc];

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

    wire [7:0] oc_next = (!last_oc) ? (oc + 8'd1) : 8'd0;

    // ------------------------------------------------------------- 状态机
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
            S_IDLE: begin
                done <= 1'b0;
                w_word_addr_r <= w_base;
                if (start) begin
                    oh <= 8'd0; ow <= 8'd0; oc <= 8'd0;
                    tcur <= 16'd0; tcnt <= 32'd0;
                    acc <= cfg_pool ? -32'sd128 : 32'sd0;
                    out_cnt <= 12'd0;
                    mac_first <= 1'b1;
                    busy <= 1'b1;
                    state <= S_MAC;
                end
            end
            S_MAC: begin
                if (mac_first) begin
                    mac_first     <= 1'b0;
                    w_word_addr_r <= w_base + oc * num_tap_groups + 32'd1;
                end else begin
                    acc  <= cfg_pool ? ((acc > pool_max32) ? acc : pool_max32)
                                     : (acc + acc_sum);
                    tcur <= lt4;
                    tcnt <= tcnt + 32'd4;
                    w_word_addr_r <= w_base + oc * num_tap_groups
                                     + (tcnt >> 2) + 32'd2;
                    if (last_round) state <= S_QUANT;
                end
            end
            S_QUANT: begin
                y_we <= 1'b1; y_wa <= out_cnt; y_wd <= quant_y;
                out_cnt <= out_cnt + 12'd1;
                acc  <= cfg_pool ? -32'sd128 : 32'sd0;
                tcnt <= 32'd0;
                tcur <= 16'd0;

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
                    w_word_addr_r <= w_base + oc_next * num_tap_groups;
                    mac_first     <= 1'b1;
                    state <= S_MAC;
                end
            end
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

    // ------------------------------------------------------------- PMU
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
            if (state == S_MAC && !mac_first)
                pmu_mac_cycles <= pmu_mac_cycles + 32'd1;
            if (state == S_MAC && !mac_first)
                pmu_wload_cycles <= pmu_wload_cycles + 32'd1;
            if (x_wen)
                pmu_xload_cycles <= pmu_xload_cycles + 32'd1;
            if ((state == S_MAC) && !mac_first && (lane_valid != 4'b1111))
                pmu_bubble_cycles <= pmu_bubble_cycles + 32'd1;
            if (busy || (state == S_IDLE && start))
                pmu_total_cycles <= pmu_total_cycles + 32'd1;
        end
    end

endmodule