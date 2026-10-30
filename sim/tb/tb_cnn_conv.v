// ===========================================================================
// tb_cnn_conv.v  --  CNN 内核与 Python 黄金模型逐点比对（参数化回归 TB）
//
// 用例参数不在代码里写死，而是用 xsim 的 -testplusarg 传进来：
//     xsim tb_sim -testplusarg H=4 -testplusarg W=4 ... -runall
// 每个用例目录里的 xsim_args.txt 已经生成好了，run_sim.bat 会自动读取。
//
// 判据：按 out_cnt 顺序的每个输出都必须 === 期望值
//       （用 !== 而不是 !=，这样 X/Z 也会被判为错）
// ===========================================================================

module tb_cnn_conv;

    // ---------------- 用例参数（由 plusargs 设定，缺省 = t1_tiny）----------------
    integer H, W, C, K, OC, STRIDE, PAD, SHIFT, RELU, POOL;
    integer X_COUNT, W_COUNT, B_COUNT, Y_COUNT;

    // ---------------- 时钟 / 复位 ----------------
    reg clk = 1'b0;
    reg rst_n = 1'b0;
    always #5 clk = ~clk;          // 100MHz 仿真时钟（纯仿真，与板无关）

    // ---------------- DUT 接口 ----------------
    reg  [7:0]         cfg_h, cfg_w, cfg_c, cfg_k, cfg_oc;
    reg  [1:0]         cfg_stride;
    reg  [3:0]         cfg_pad;
    reg  [4:0]         cfg_shift;
    reg                cfg_relu;
    reg                cfg_pool;

    reg                x_wen = 0;
    reg  [11:0]        x_waddr = 0;
    reg  signed [7:0]  x_wdata = 0;
    reg                b_wen = 0;
    reg  [7:0]         b_waddr = 0;
    reg  signed [31:0] b_wdata = 0;

    reg                start = 0;
    wire               busy, done;

    // PMU 观测
    reg                pmu_en = 1'b1;
    reg                pmu_clr = 1'b0;
    wire [31:0]        pmu_mac_cycles;
    wire [31:0]        pmu_wload_cycles;
    wire [31:0]        pmu_xload_cycles;
    wire [31:0]        pmu_bubble_cycles;
    wire [31:0]        pmu_total_cycles;

    // 权重预加载

    reg  [11:0]        y_raddr = 0;
    wire signed [7:0]  y_rdata;

    cnn_top dut (
        .clk       (clk),          .rst_n     (rst_n),
        .cfg_h     (cfg_h),        .cfg_w     (cfg_w),      .cfg_c   (cfg_c),
        .cfg_k     (cfg_k),        .cfg_oc    (cfg_oc),
        .cfg_stride(cfg_stride),   .cfg_pad   (cfg_pad),
        .cfg_shift (cfg_shift),    .cfg_relu  (cfg_relu),   .cfg_pool(cfg_pool),
        .x_wen     (x_wen),        .x_waddr   (x_waddr),    .x_wdata (x_wdata),
        .b_wen     (b_wen),        .b_waddr   (b_waddr),    .b_wdata (b_wdata),
        .start     (start),        .busy      (busy),       .done    (done),
        .pmu_en    (pmu_en),       .pmu_clr   (pmu_clr),
        .pmu_mac_cycles(pmu_mac_cycles),     .pmu_wload_cycles(pmu_wload_cycles),
        .pmu_xload_cycles(pmu_xload_cycles), .pmu_bubble_cycles(pmu_bubble_cycles),
        .pmu_total_cycles(pmu_total_cycles),
        .w_base    (16'd0),
        .y_raddr   (y_raddr),      .y_rdata   (y_rdata),      .y_count()
    );

    // ---------------- 测试向量（用 32bit 读，避免 $readmemh 截断告警）----------------
    reg [31:0] xraw [0:4095];
    reg [31:0] wraw [0:4095];
    reg [31:0] braw [0:255];
    reg [31:0] eraw [0:4095];
    reg [31:0] pm   [0:15];    // params.hex: 用例参数

    integer i, wi, by, errors, cycles, timeout_lim;
    integer first_bad, bad_cnt;
    integer pmu_errors;

    initial begin
        // ---------- 读用例参数 params.hex ----------
        // 顺序：H W C K OC STRIDE PAD SHIFT RELU X_COUNT W_COUNT B_COUNT Y_COUNT
        for (i = 0; i < 16; i = i + 1) pm[i] = 32'd0;   // 旧用例无 POOL 项时保持 0
        $readmemh("params.hex", pm);
        H       = pm[0];  W       = pm[1];  C       = pm[2];  K  = pm[3];
        OC      = pm[4];  STRIDE  = pm[5];  PAD     = pm[6];  SHIFT = pm[7];
        RELU    = pm[8];  X_COUNT = pm[9];  W_COUNT = pm[10];
        B_COUNT = pm[11]; Y_COUNT = pm[12]; POOL = pm[13];
        if (POOL === 32'hx) POOL = 0;
        cfg_h = H[7:0]; cfg_w = W[7:0]; cfg_c = C[7:0]; cfg_k = K[7:0];
        cfg_oc = OC[7:0]; cfg_stride = STRIDE[1:0]; cfg_pad = PAD[3:0];
        cfg_shift = SHIFT[4:0]; cfg_relu = (RELU != 0); cfg_pool = (POOL != 0);
        timeout_lim = 200 + Y_COUNT * (K*K*C + 4) * 4;

        $dumpfile("tb_cnn_conv.vcd");
        $dumpvars(0, tb_cnn_conv);

        $display("================================================================");
        $display(" CNN 内核  vs  Python 黄金模型   逐点比对");
        $display(" H=%0d W=%0d C=%0d K=%0d OC=%0d stride=%0d pad=%0d shift=%0d relu=%0d pool=%0d",
                 H,W,C,K,OC,STRIDE,PAD,SHIFT,RELU,POOL);
        $display(" X=%0d W=%0d B=%0d Y=%0d  (超时上限 %0d 拍)", X_COUNT,W_COUNT,B_COUNT,Y_COUNT,timeout_lim);
        $display("================================================================");

        $readmemh("x_w.hex",      xraw);
        $readmemh("w_w.hex",      wraw);
        $readmemh("b.hex",        braw);
        $readmemh("expect_w.hex", eraw);

        rst_n = 1'b0;
        repeat (5) @(posedge clk);
        rst_n = 1'b1;
        repeat (2) @(posedge clk);

        // ---- 清 PMU 计数 ----
        @(posedge clk); pmu_clr = 1'b1;
        @(posedge clk); pmu_clr = 1'b0;

        // ---- 装载激活 x ----
        for (i = 0; i < X_COUNT; i = i + 1) begin
            @(posedge clk);
            x_wen = 1'b1; x_waddr = i[11:0]; x_wdata = xraw[i][7:0];
        end
        @(posedge clk); x_wen = 1'b0;

        // ---- Preload weights directly into w_rom (BRAM version, simulation only) ----
        // w_w.hex order is [KH][KW][C][OC]; RTL layout is [OC][tap_group][lane],
        // and each OC's tap groups are padded to a 32-bit word boundary.
        for (i = 0; i < W_COUNT; i = i + 1) begin
            @(posedge clk);
            // Python shape is (K,K,C,OC): OC changes fastest in w_w.hex.
            // RTL layout is [OC][tap_group][lane], so de-interleave OC first.
            wi = (i % OC) * (((K*K*C) + 3) / 4) + ((i / OC) / 4);
            by = (i / OC) % 4;
            dut.u_rom.mem[wi][by * 8 +: 8] = wraw[i][7:0];
        end
        @(posedge clk);

        // ---- 装载 bias ----
        for (i = 0; i < B_COUNT; i = i + 1) begin
            @(posedge clk);
            b_wen = 1'b1; b_waddr = i[7:0]; b_wdata = braw[i];
        end
        @(posedge clk); b_wen = 1'b0;

        // ---- 启动 ----
        @(posedge clk);
        start = 1'b1;
        $display("[%0t] 启动计算 ...", $time);

        cycles = 0;
        while (!done && cycles < timeout_lim) begin
            @(posedge clk);
            cycles = cycles + 1;
        end

        errors = 0;
        if (!done) begin
            $display("*** FAIL: 超时！%0d 拍内没有 done（可能死锁）***", cycles);
            errors = -1;
        end else begin
            $display("[%0t] done，共 %0d 拍", $time, cycles);
            $display("CYCLES = %0d", cycles);
            @(posedge clk);
            start = 1'b0;

            bad_cnt = 0; first_bad = -1;
            for (i = 0; i < Y_COUNT; i = i + 1) begin
                y_raddr = i[11:0];
                @(posedge clk);   // y_mem 是同步读，等一拍
                #1;
                if (y_rdata !== eraw[i][7:0]) begin
                    if (first_bad < 0) first_bad = i;
                    if (bad_cnt < 8)
                        $display("  [FAIL] y[%0d] = %0d ,  期望 %0d", i, y_rdata, eraw[i][7:0]);
                    bad_cnt = bad_cnt + 1;
                end
            end
            if (bad_cnt > 0) begin
                $display("  （共 %0d 个点不一致，首个在 y[%0d]）", bad_cnt, first_bad);
                errors = bad_cnt;
            end else if (Y_COUNT <= 16) begin
                for (i = 0; i < Y_COUNT; i = i + 1) begin
                    y_raddr = i[11:0]; @(posedge clk); #1;
                    $display("  [ ok ] y[%0d] = %0d ,  期望 %0d", i, y_rdata, eraw[i][7:0]);
                end
            end

            // ---- PMU 计数检查 ----
            $display("PMU: MAC=%0d WLOAD=%0d XLOAD=%0d BUBBLE=%0d TOTAL=%0d",
                     pmu_mac_cycles, pmu_wload_cycles, pmu_xload_cycles,
                     pmu_bubble_cycles, pmu_total_cycles);
            pmu_errors = 0;
            // BRAM mode: weights are preloaded in w_rom, WLOAD stays 0
            if (pmu_wload_cycles !== 32'd0) begin
                $display("  [FAIL] PMU WLOAD=%0d, expected 0 (BRAM mode)", pmu_wload_cycles);
                pmu_errors = pmu_errors + 1;
            end
            if (pmu_xload_cycles !== X_COUNT) begin
                $display("  [FAIL] PMU XLOAD=%0d，期望 %0d", pmu_xload_cycles, X_COUNT);
                pmu_errors = pmu_errors + 1;
            end
            if (pmu_mac_cycles == 0) begin
                $display("  [FAIL] PMU MAC=0");
                pmu_errors = pmu_errors + 1;
            end
            if (pmu_total_cycles == 0) begin
                $display("  [FAIL] PMU TOTAL=0");
                pmu_errors = pmu_errors + 1;
            end
            errors = errors + pmu_errors;
        end

        $display("================================================================");
        if (errors == 0)
            $display(" RESULT: *** PASS ***   (%0d/%0d 逐点一致)", Y_COUNT, Y_COUNT);
        else
            $display(" RESULT: *** FAIL ***   (%0d 个点不一致)", errors);
        $display("================================================================");
        $finish;
    end

endmodule