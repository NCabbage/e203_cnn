// ===========================================================================
// rom_all_tap_sim.v -- 仿真专用 ROM 行为模型
//
// 综合时使用 ip/rom_all_tap 的 Block Memory Generator；xsim 单元测试里
// 用这个简化模型替代，方便 TB 直接写入测试用例的自定义权重。
// 位宽/深度与 IP 配置一致：16384 x 32bit，同步读。
// ===========================================================================
module rom_all_tap (
    input  wire        clka,
    input  wire [13:0] addra,
    output reg  [31:0] douta
);
    reg [31:0] mem [0:16383];

    always @(posedge clka)
        douta <= mem[addra];
endmodule
