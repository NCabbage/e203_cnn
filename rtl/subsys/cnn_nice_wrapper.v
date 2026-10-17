//===========================================================================
// cnn_nice_wrapper.v  --  CNN 加速器 NICE 紧耦合控制接口
// 
// 指令集（Custom0, opcode = 7'b0001011）：
//   1. cnn.start : funct3 = 3'h1  (启动 CNN，立刻返回)
//   2. cnn.wait  : funct3 = 3'h2  (阻塞等待 CNN 完成，返回 PMU 周期数)
//===========================================================================

module cnn_nice_wrapper (
    input  wire        clk,
    input  wire        rst_n,

    ////////////////////////////////////////////////////////////
    // NICE 请求通道 (来自 e203_core)
    ////////////////////////////////////////////////////////////
    input  wire        nice_req_valid,
    output wire        nice_req_ready,
    input  wire [31:0] nice_req_inst,
    input  wire [31:0] nice_req_rs1,
    input  wire [31:0] nice_req_rs2,

    ////////////////////////////////////////////////////////////
    // NICE 响应通道 (回到 e203_core)
    ////////////////////////////////////////////////////////////
    output reg         nice_rsp_valid,
    input  wire        nice_rsp_ready,
    output reg  [31:0] nice_rsp_rdat,
    output wire        nice_rsp_err,

    ////////////////////////////////////////////////////////////
    // NICE 存储通道 (本版本不用，全接固定值)
    ////////////////////////////////////////////////////////////
    output wire        nice_icb_cmd_valid,
    input  wire        nice_icb_cmd_ready,
    output wire [31:0] nice_icb_cmd_addr,
    output wire        nice_icb_cmd_read,
    output wire [31:0] nice_icb_cmd_wdata,
    output wire [1:0]  nice_icb_cmd_size,
    input  wire        nice_icb_rsp_valid,
    output wire        nice_icb_rsp_ready,
    input  wire [31:0] nice_icb_rsp_rdata,
    input  wire        nice_icb_rsp_err,

    ////////////////////////////////////////////////////////////
    // 状态与保护信号
    ////////////////////////////////////////////////////////////
    output wire        nice_active,
    output wire        nice_mem_holdup,

    ////////////////////////////////////////////////////////////
    // 对接 cnn_regs 的物理控制线
    ////////////////////////////////////////////////////////////
    output reg         nice_start_r,   // 拉高启动 CNN，拉低让 cnn_top 状态机回 IDLE
    input  wire        cnn_done,       // 来自 cnn_regs
    input  wire [31:0] cnn_cycles      // 来自 cnn_regs (PMU)
);

    // ---------------------------------------------------------
    // 1. 存储通道全部关闭 (我们走 AXI-Lite 旁路)
    // ---------------------------------------------------------
    assign nice_icb_cmd_valid = 1'b0;
    assign nice_icb_cmd_addr  = 32'd0;
    assign nice_icb_cmd_read  = 1'b0;
    assign nice_icb_cmd_wdata = 32'd0;
    assign nice_icb_cmd_size  = 2'b00;
    assign nice_icb_rsp_ready = 1'b0;  // 永远不接收响应
    assign nice_mem_holdup    = 1'b0;  // 不占用 CPU 总线

    // ---------------------------------------------------------
    // 2. 指令解码
    // ---------------------------------------------------------
    wire [6:0] opcode = nice_req_inst[6:0];
    wire [2:0] funct3 = nice_req_inst[14:12];
    
    wire is_custom    = (opcode == 7'b1111011); // Custom3 (0x7B)
    wire is_cnn_start = nice_req_valid && is_custom && (funct3 == 3'h1);
    wire is_cnn_wait  = nice_req_valid && is_custom && (funct3 == 3'h2);

    // ---------------------------------------------------------
    // 3. 状态机定义
    // ---------------------------------------------------------
    localparam S_IDLE    = 2'd0,  // 空闲
               S_START   = 2'd1,  // 收到 start，过渡一拍
               S_RUNNING = 2'd2,  // CNN 计算中
               S_WAIT    = 2'd3;  // 收到 wait，死等 done

    reg [1:0] state;

    // 只有在空闲或计算中才接收新指令
    assign nice_req_ready = (state == S_IDLE) || (state == S_RUNNING);
    assign nice_active    = (state != S_IDLE);
    assign nice_rsp_err   = 1'b0; // 无异常

    // ---------------------------------------------------------
    // 4. 状态机与握手逻辑
    // ---------------------------------------------------------
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state          <= S_IDLE;
            nice_start_r   <= 1'b0;
            nice_rsp_valid <= 1'b0;
            nice_rsp_rdat  <= 32'd0;
        end else begin
            // 响应握手完成，拉低 valid
            if (nice_rsp_valid && nice_rsp_ready)
                nice_rsp_valid <= 1'b0;

            case (state)
            S_IDLE: begin
                nice_start_r <= 1'b0; // 守卫：确保此时 start 为低
                
                if (is_cnn_start) begin
                    nice_rsp_valid <= 1'b1; // start 立刻响应
                    nice_rsp_rdat  <= 32'd0;
                    state          <= S_START;
                end 
                else if (is_cnn_wait) begin
                    // 没有任何计算就 wait，直接返回 0，防止 CPU 挂死
                    nice_rsp_valid <= 1'b1;
                    nice_rsp_rdat  <= 32'd0;
                end
            end

            S_START: begin
                nice_start_r <= 1'b1; // 拉高 start 信号，启动 cnn_top
                state        <= S_RUNNING;
            end

            S_RUNNING: begin
                nice_start_r <= 1'b1; // 保持高电平
                
                if (is_cnn_wait) begin
                    if (cnn_done) begin
                        // 已经算完了，立刻返回结果，并拉低 start
                        nice_rsp_valid <= 1'b1;
                        nice_rsp_rdat  <= cnn_cycles;
                        nice_start_r   <= 1'b0; // 关键：让 cnn_top 回 IDLE
                        state          <= S_IDLE;
                    end else begin
                        // 还没算完，进入阻塞等待状态
                        state          <= S_WAIT;
                    end
                end
                // 如果计算中又来一个 start，直接返回 0 忽略
                else if (is_cnn_start) begin
                    nice_rsp_valid <= 1'b1;
                    nice_rsp_rdat  <= 32'd0;
                end
            end

            S_WAIT: begin
                nice_start_r <= 1'b1;
                
                if (cnn_done) begin
                    // 算完了，返回结果，并拉低 start
                    nice_rsp_valid <= 1'b1;
                    nice_rsp_rdat  <= cnn_cycles;
                    nice_start_r   <= 1'b0; // 关键：让 cnn_top 回 IDLE
                    state          <= S_IDLE;
                end
            end

            default: state <= S_IDLE;
            endcase
        end
    end

endmodule