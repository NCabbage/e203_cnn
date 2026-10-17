// ===========================================================================
// cnn_regs.v  --  CNN 寄存器组（权重预加载 BRAM + PMU + Pooling）
//
// 寄存器映射（基址 0x4000_0000）：
//   0x00 CTRL     W  [0]start [2]clr_iptr [3]clr_bptr
//   0x04 STATUS   R  [0]busy [1]done
//   0x08 WDATA    W  保留（BRAM 版本不再使用）
//   0x0C IDATA    W  输入激活
//   0x10 BIAS     W  偏置
//   0x14 CFG0     W  H/W/C/K
//   0x18 CFG1     W  OC/STRIDE/PAD/SHIFT/RELU/POOL[20]
//   0x1C OINDEX   W
//   0x20 ODATA    R
//   0x24 WPTR     R  保留，恒 0
//   0x28 IPTR     R
//   0x2C YCOUNT   R
//   0x30 VERSION  R  0x0001_0000
//   0x34 PMU_CTRL W/R
//   0x38 PMU_MAC  R
//   0x3C PMU_WLOAD R  （BRAM 版本恒 0）
//   0x40 PMU_XLOAD R
//   0x44 PMU_BUBBLE R
//   0x48 PMU_TOTAL R
//   0x4C PMU_AXIWR R
//   0x50 PMU_VERSION R 0x0001_0000
//   0x54 WBASE    W/R  当前层权重起始 32bit 字地址
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
                     A_WDATA  = 8'h08,   // 保留
                     A_IDATA  = 8'h0C,
                     A_BIAS   = 8'h10,
                     A_CFG0   = 8'h14,
                     A_CFG1   = 8'h18,
                     A_OINDEX = 8'h1C,
                     A_ODATA  = 8'h20,
                     A_WPTR   = 8'h24,   // 保留
                     A_IPTR   = 8'h28,
                     A_YCOUNT = 8'h2C,
                     A_VERSION= 8'h30,
                     A_PMU_CTRL   = 8'h34,
                     A_PMU_MAC    = 8'h38,
                     A_PMU_WLOAD  = 8'h3C,
                     A_PMU_XLOAD  = 8'h40,
                     A_PMU_BUBBLE = 8'h44,
                     A_PMU_TOTAL  = 8'h48,
                     A_PMU_AXIWR  = 8'h4C,
                     A_PMU_VERSION= 8'h50,
                     A_WBASE  = 8'h54;

    wire [7:0] wa = wr_addr[7:0];
    wire [7:0] ra = rd_addr[7:0];
    wire       wr = wr_en;

    // ------------------------------------------------ 配置寄存器
    reg [7:0] cfg_h, cfg_w, cfg_c, cfg_k, cfg_oc;
    reg [1:0] cfg_stride;
    reg [3:0] cfg_pad;
    reg [4:0] cfg_shift;
    reg       cfg_relu;
    reg       cfg_pool;          // CFG1[20]

    // ------------------------------------------------ 指针与控制
    reg [11:0] iptr;
    reg [7:0]  bptr;
    reg [11:0] oidx;
    reg        cnn_start;
    reg [15:0] w_base;

    // ------------------------------------------------ PMU
    reg  [31:0] pmu_axi_wr;
    reg         pmu_en;
    wire        pmu_clr = wr && (wa == A_PMU_CTRL) && wr_data[1];
    wire [31:0] pmu_mac_cycles;
    wire [31:0] pmu_wload_cycles;
    wire [31:0] pmu_xload_cycles;
    wire [31:0] pmu_bubble_cycles;
    wire [31:0] pmu_total_cycles;

    // ------------------------------------------------ 写端口脉冲
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
        if (!rst_n)                    cnn_start <= 1'b0;
        else if (wr && (wa == A_CTRL)) cnn_start <= wr_data[0];
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) pmu_en <= 1'b1;
        else if (wr && (wa == A_PMU_CTRL)) begin
            if (wr_data[0])      pmu_en <= 1'b1;
            else if (!wr_data[1]) pmu_en <= 1'b0;
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) pmu_axi_wr <= 32'd0;
        else if (pmu_clr) pmu_axi_wr <= 32'd0;
        else if (pmu_en && wr) pmu_axi_wr <= pmu_axi_wr + 32'd1;
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
            cfg_shift <= 5'd0; cfg_relu <= 1'b0; cfg_pool <= 1'b0;
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
            cfg_pool  <= wr_data[20];
        end
    end

    // ------------------------------------------------ CNN 内核
    wire        cnn_busy, cnn_done;
    wire signed [7:0] cnn_y;
    wire [11:0] cnn_ycount;

    cnn_top #(
        .W_TOTAL_WORDS (11066)
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
        .cfg_pool   (cfg_pool   ),

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

        .pmu_en          (pmu_en          ),
        .pmu_clr         (pmu_clr         ),
        .pmu_mac_cycles  (pmu_mac_cycles  ),
        .pmu_wload_cycles(pmu_wload_cycles),
        .pmu_xload_cycles(pmu_xload_cycles),
        .pmu_bubble_cycles(pmu_bubble_cycles),
        .pmu_total_cycles(pmu_total_cycles),

        .y_raddr    (oidx       ),
        .y_rdata    (cnn_y      ),
        .y_count    (cnn_ycount )
    );

    // ------------------------------------------------ 读 mux
    always @(*) begin
        case (ra)
            A_STATUS : rd_data = {30'd0, cnn_done, cnn_busy};
            A_ODATA  : rd_data = {{24{cnn_y[7]}}, cnn_y};
            A_WPTR   : rd_data = 32'd0;
            A_IPTR   : rd_data = {20'd0, iptr};
            A_YCOUNT : rd_data = {20'd0, cnn_ycount};
            A_VERSION: rd_data = 32'h0001_0000;
            A_PMU_CTRL   : rd_data = {31'd0, pmu_en};
            A_PMU_MAC    : rd_data = pmu_mac_cycles;
            A_PMU_WLOAD  : rd_data = pmu_wload_cycles;
            A_PMU_XLOAD  : rd_data = pmu_xload_cycles;
            A_PMU_BUBBLE : rd_data = pmu_bubble_cycles;
            A_PMU_TOTAL  : rd_data = pmu_total_cycles;
            A_PMU_AXIWR  : rd_data = pmu_axi_wr;
            A_PMU_VERSION: rd_data = 32'h0001_0000;
            A_WBASE      : rd_data = {16'd0, w_base};
            default      : rd_data = 32'hDEAD_BEEF;
        endcase
    end

endmodule