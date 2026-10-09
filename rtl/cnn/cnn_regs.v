// ===========================================================================
// cnn_regs.v  --  CNN 寄存器组（权重预加载 BRAM 版）
//
// 改动：
//   - 删掉 WDATA/wptr 写权重路径
//   - 新增 0x34 WBASE：每层权重起始字地址（单位：32bit 字）
//   - 其余寄存器与原来一致
// ===========================================================================

module cnn_regs #(
    parameter AW = 32,
    parameter DW = 32
)(
    input  wire                clk,
    input  wire                rst_n,

    input  wire                wr_en,
    input  wire [AW-1:0]       wr_addr,
    input  wire [DW-1:0]       wr_data,
    input  wire [(DW/8)-1:0]   wr_mask,

    input  wire [AW-1:0]       rd_addr,
    output reg  [DW-1:0]       rd_data
);

    // ------------------------------------------------ 地址
    localparam [7:0] A_CTRL   = 8'h00,
                     A_STATUS = 8'h04,
                     // A_WDATA 保留但不再使用
                     A_IDATA  = 8'h0C,
                     A_BIAS   = 8'h10,
                     A_CFG0   = 8'h14,
                     A_CFG1   = 8'h18,
                     A_OINDEX = 8'h1C,
                     A_ODATA  = 8'h20,
                     A_WPTR   = 8'h24,   // 保留，读恒 0
                     A_IPTR   = 8'h28,
                     A_YCOUNT = 8'h2C,
                     A_VERSION= 8'h30,
                     A_WBASE  = 8'h34;

    wire [7:0] wa = wr_addr[7:0];
    wire [7:0] ra = rd_addr[7:0];
    wire       wr = wr_en;

    // ------------------------------------------------ 配置寄存器
    reg [7:0] cfg_h, cfg_w, cfg_c, cfg_k, cfg_oc;
    reg [1:0] cfg_stride;
    reg [3:0] cfg_pad;
    reg [4:0] cfg_shift;
    reg       cfg_relu;

    // ------------------------------------------------ 指针与控制
    reg [11:0] iptr;
    reg [7:0]  bptr;
    reg [11:0] oidx;
    reg        cnn_start;
    reg [15:0] w_base;

    // ------------------------------------------------ 写端口脉冲（1 拍）
    wire x_e = wr && (wa == A_IDATA);
    wire b_e = wr && (wa == A_BIAS);

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) iptr <= 12'd0;
        else if (wr && (wa == A_CTRL) && wr_data[2]) iptr <= 12'd0;
        else if (x_e) iptr <= iptr + 12'd1;
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) bptr <= 8'd0;
        else if (wr && (wa == A_CTRL) && wr_data[3]) bptr <= 8'd0;
        else if (b_e) bptr <= bptr + 8'd1;
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)                        cnn_start <= 1'b0;
        else if (wr && (wa == A_CTRL))     cnn_start <= wr_data[0];
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) oidx <= 12'd0;
        else if (wr && (wa == A_OINDEX)) oidx <= wr_data[11:0];
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) w_base <= 16'd0;
        else if (wr && (wa == A_WBASE)) w_base <= wr_data[15:0];
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            cfg_h <= 8'd0; cfg_w <= 8'd0; cfg_c <= 8'd0; cfg_k <= 8'd0;
            cfg_oc <= 8'd0; cfg_stride <= 2'd0; cfg_pad <= 4'd0;
            cfg_shift <= 5'd0; cfg_relu <= 1'b0;
        end else if (wr && (wa == A_CFG0)) begin
            cfg_h <= wr_data[7:0];
            cfg_w <= wr_data[15:8];
            cfg_c <= wr_data[23:16];
            cfg_k <= wr_data[31:24];
        end else if (wr && (wa == A_CFG1)) begin
            cfg_oc    <= wr_data[7:0];
            cfg_stride<= wr_data[9:8];
            cfg_pad   <= wr_data[13:10];
            cfg_shift <= wr_data[18:14];
            cfg_relu  <= wr_data[19];
        end
    end

    // ------------------------------------------------ CNN 内核
    wire        cnn_busy, cnn_done;
    wire signed [7:0] cnn_y;
    wire [11:0] cnn_ycount;

    cnn_top #(
        .W_TOTAL_WORDS (11060)   // 与 wbase_tap.h 保持一致
    ) u_cnn (
        .clk        (clk        ),
        .rst_n      (rst_n      ),
        .cfg_h      (cfg_h      ),
        .cfg_w      (cfg_w      ),
        .cfg_c      (cfg_c      ),
        .cfg_k      (cfg_k      ),
        .cfg_oc     (cfg_oc     ),
        .cfg_stride (cfg_stride ),
        .cfg_pad    (cfg_pad    ),
        .cfg_shift  (cfg_shift  ),
        .cfg_relu   (cfg_relu   ),

        .x_wen      (x_e        ),
        .x_waddr    (iptr       ),
        .x_wdata    (wr_data[7:0]),
        .b_wen      (b_e        ),
        .b_waddr    (bptr       ),
        .b_wdata    (wr_data    ),

        .w_base     (w_base     ),

        .start      (cnn_start  ),
        .busy       (cnn_busy   ),
        .done       (cnn_done   ),

        .y_raddr    (oidx       ),
        .y_rdata    (cnn_y      ),
        .y_count    (cnn_ycount )
    );

    // ------------------------------------------------ 读 mux（组合读）
    always @(*) begin
        case (ra)
            A_STATUS : rd_data = {30'd0, cnn_done, cnn_busy};
            A_ODATA  : rd_data = {{24{cnn_y[7]}}, cnn_y};
            A_WPTR   : rd_data = 32'd0;                      // 已弃用
            A_IPTR   : rd_data = {20'd0, iptr};
            A_YCOUNT : rd_data = {20'd0, cnn_ycount};
            A_VERSION: rd_data = 32'h0001_0000;
            A_WBASE  : rd_data = {16'd0, w_base};
            default  : rd_data = 32'hDEAD_BEEF;
        endcase
    end

endmodule