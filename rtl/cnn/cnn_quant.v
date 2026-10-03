// ===========================================================================
// cnn_quant.v  --  量化 + ReLU + 饱和
//
// 顺序（必须与 golden 一致，顺序错了结果就错）：
//      acc_b = acc + bias            (int32)
//      sh    = acc_b >>> SHIFT       (算术右移! 必须用 >>>)
//      r     = RELU ? max(sh,0) : sh
//      y     = saturate(r, -128, 127)   (int8)
//
// 注意：右移一定要用 >>>（算术右移）。
//       如果写成 >>（逻辑右移），负数会变成大正数，直接算错。
// ===========================================================================

module cnn_quant (
    input  wire signed [31:0] acc,      // 累加器
    input  wire signed [31:0] bias,     // 偏置
    input  wire        [4:0]  shift,    // 量化右移位数
    input  wire               relu,     // 1 = 做 ReLU
    output reg  signed [7:0]  y         // int8 输出
);
    wire signed [31:0] acc_b  = acc + bias;
    wire signed [31:0] sh     = (shift == 5'd0) ? acc_b : (acc_b >>> shift);
    wire signed [31:0] r      = (relu && sh[31]) ? 32'sd0 : sh;   // 负数取 0

    always @(*) begin
        if      (r >  32'sd127) y =  8'sd127;
        else if (r < -32'sd128) y = -8'sd128;
        else                    y = r[7:0];
    end

endmodule