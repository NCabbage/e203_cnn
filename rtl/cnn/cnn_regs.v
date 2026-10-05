// ===========================================================================
// cnn_regs.v  --  CNN 寄存器组（把 CNN 内核接到 AXI-Lite 从机后面）
//
// 端口与原来的占位 regfile.v **完全一致**，所以 e203_subsys_mems.v 里
// 只需要把模块名 regfile 换成 cnn_regs，其余接线不用动。
//
// 数据通路：
//    CPU -> ICB -> icb2axi -> Axi4_lite_slave -> cnn_regs -> cnn_top
//                                                ^ 本文件
//
// 【寄存器映射】（偏移相对 CNN 基址 0x4000_0000）
//   0x00 CTRL    W  [0]start [1]clr_wptr [2]clr_iptr [3]clr_bptr
//   0x04 STATUS  R  [0]busy  [1]done
//   0x08 WDATA   W  写一个 int8 权重 -> w_mem[wptr++]
//   0x0C IDATA   W  写一个 int8 激活 -> x_mem[iptr++]
//   0x10 BIAS    W  写一个 int32 偏置 -> b_mem[bptr++]
//   0x14 CFG0    W  H[7:0] W[15:8] C[23:16] K[31:24]
//   0x18 CFG1    W  OC[7:0] STRIDE[9:8] PAD[13:10] SHIFT[18:14] RELU[19]
//   0x1C OINDEX  W  设置要读的输出序号
//   0x20 ODATA   R  读 y_mem[OINDEX]（int8 符号扩展到 32 位）
//   0x24 WPTR    R  当前权重写指针（调试用）
//   0x28 IPTR    R  当前输入写指针（调试用）
//   0x2C YCOUNT  R  已完成输出个数
//   0x30 VERSION R  固定 0x0001_0000（联调时判断桥通没通）
//
// 【典型时序（驱动/C 代码照这个写）】
//   1. 写 CTRL   = 0x0000_000E   // clr_wptr|clr_iptr|clr_bptr，指针归零
//   2. 写 CFG0/CFG1               // 形状与量化参数
//   3. 循环写 WDATA / IDATA / BIAS // 自动递增指针
//   4. 写 CTRL   = 0x0000_0001   // start=1 启动
//   5. 轮询 STATUS 直到 done=1
//   6. 循环 { 写 OINDEX=k; 读 ODATA } 取结果
//   7. 写 CTRL   = 0            // 拉低 start，让内核回 IDLE
//
// 说明：读是组合读（与占位 regfile 一致），因此输出用"先写索引再读数据"两步，
//       不依赖从机给读使能脉冲。
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
                     A_WDATA  = 8'h08,
                     A_IDATA  = 8'h0C,
                     A_BIAS   = 8'h10,
                     A_CFG0   = 8'h14,
                     A_CFG1   = 8'h18,
                     A_OINDEX = 8'h1C,
                     A_ODATA  = 8'h20,
                     A_WPTR   = 8'h24,
                     A_IPTR   = 8'h28,
                     A_YCOUNT = 8'h2C,
                     A_VERSION= 8'h30;

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
    reg [15:0] wptr;
    reg [11:0] iptr;
    reg [7:0]  bptr;
    reg [11:0] oidx;
    reg        cnn_start;

    // ------------------------------------------------ 写端口脉冲（1 拍）
    wire w_e = wr && (wa == A_WDATA);
    wire x_e = wr && (wa == A_IDATA);
    wire b_e = wr && (wa == A_BIAS);

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            wptr <= 15'd0;
        end else if (wr && (wa == A_CTRL) && wr_data[1]) begin
            wptr <= 15'd0;
        end else if (w_e) begin
            wptr <= wptr + 15'd1;
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            iptr <= 12'd0;
        end else if (wr && (wa == A_CTRL) && wr_data[2]) begin
            iptr <= 12'd0;
        end else if (x_e) begin
            iptr <= iptr + 12'd1;
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            bptr <= 8'd0;
        end else if (wr && (wa == A_CTRL) && wr_data[3]) begin
            bptr <= 8'd0;
        end else if (b_e) begin
            bptr <= bptr + 8'd1;
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)                                  cnn_start <= 1'b0;
        else if (wr && (wa == A_CTRL))               cnn_start <= wr_data[0];
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) oidx <= 12'd0;
        else if (wr && (wa == A_OINDEX)) oidx <= wr_data[11:0];
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

    cnn_top u_cnn (
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
        .w_wen      (w_e        ),
        .w_waddr    (wptr       ),
        .w_wdata    (wr_data[7:0]),
        .b_wen      (b_e        ),
        .b_waddr    (bptr       ),
        .b_wdata    (wr_data    ),

        .start      (cnn_start  ),
        .busy       (cnn_busy   ),
        .done       (cnn_done   ),

        .y_raddr    (oidx       ),
        .y_rdata    (cnn_y      ),
        .y_count    (cnn_ycount )
    );

    // ------------------------------------------------ 读mux（组合读）
    always @(*) begin
        case (ra)
            A_STATUS : rd_data = {30'd0, cnn_done, cnn_busy};
            A_ODATA  : rd_data = {{24{cnn_y[7]}}, cnn_y};   // int8 符号扩展
            A_WPTR   : rd_data = {16'd0, wptr};
            A_IPTR   : rd_data = {20'd0, iptr};
            A_YCOUNT : rd_data = {20'd0, cnn_ycount};
            A_VERSION: rd_data = 32'h0001_0000;
            default  : rd_data = 32'hDEAD_BEEF;
        endcase
    end

endmodule