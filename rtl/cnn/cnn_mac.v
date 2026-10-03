// ===========================================================================
// cnn_mac.v  --  INT8 有符号乘加单元
//
// 约定（与 python/golden/int8_conv.py 完全一致）：
//   * 两个操作数都是 int8 有符号 (-128..127)
//   * 乘积是 16 位有符号
//   * 累加器 int32（乘累加 32 位，绝不中途截断）
//   * 不做饱和/舍入，饱和在 cnn_quant.v 里统一做
// ===========================================================================

module cnn_mac (
    input  wire signed [7:0]  a,      // 激活
    input  wire signed [7:0]  b,      // 权重
    input  wire signed [31:0] acc_i,  // 当前累加值
    output wire signed [31:0] acc_o   // 累加后的值
);
    // 8bit x 8bit -> 16bit 有符号
    wire signed [15:0] prod = a * b;
    // 符号扩展后相加（32 位累加，不截断）
    assign acc_o = acc_i + {{16{prod[15]}}, prod};

endmodule