#include <stdio.h>
#include <stdint.h>
#include "hbird_sdk_soc.h"

#include "../include/csr.h"
#include "../include/cnn.h"
#include "../include/wbase_tap.h"          // ← 新增：W_BASE_xxx 定义
#include "../include/lenet_weights_hw.h"    // 偏置仍从这里来
#include "../include/mnist_samples.h"

// ============ WBASE 寄存器（新增）============
// cnn.h 里如果没有定义 CNN_BASE，就把这里换成你实际的宏

#define CNN_WBASE_OFFSET  0x34u

// ============ 网络配置 ============
#define INPUT_H       28
#define INPUT_W       28

#define C1_OUT_CH     6
#define C1_KERNEL     5
#define C1_OUT_H      24
#define C1_OUT_W      24

#define S2_OUT_H      12
#define S2_OUT_W      12

#define C3_OUT_CH     16
#define C3_KERNEL     5
#define C3_OUT_H      8
#define C3_OUT_W      8

#define S4_OUT_CH     16
#define S4_OUT_H      4
#define S4_OUT_W      4

#define C5_OUT        120
#define C5_IN         256

#define F6_OUT        84
#define OUT_NUM       10

#define QUANT_SHIFT   8
#define TEST_DIGIT    5

// ============ 中间张量 ============
static int8_t  input_img[INPUT_H][INPUT_W];
static int8_t  c1_out[C1_OUT_CH][C1_OUT_H][C1_OUT_W];
static int8_t  s2_out[C1_OUT_CH][S2_OUT_H][S2_OUT_W];
static int8_t  c3_out[C3_OUT_CH][C3_OUT_H][C3_OUT_W];
static int8_t  s4_out[S4_OUT_CH][S4_OUT_H][S4_OUT_W];
static int8_t  c5_out[C5_OUT];
static int8_t  f6_out[F6_OUT];
static int32_t final_out[OUT_NUM];

// ============ 硬件缓冲 ============
static int8_t  hw_x[4096];
static int8_t  hw_y[4096];

// ============ MNIST 样本查找表 ============
static const unsigned char (* const mnist_table[10])[28] = {
    digit0_mnist, digit1_mnist, digit2_mnist, digit3_mnist, digit4_mnist,
    digit5_mnist, digit6_mnist, digit7_mnist, digit8_mnist, digit9_mnist,
};

// ============ 输入初始化 ============
static void init_input(void) {
    const unsigned char (*src)[28] = mnist_table[TEST_DIGIT];
    for (int i = 0; i < INPUT_H; i++)
        for (int j = 0; j < INPUT_W; j++)
            input_img[i][j] = (int8_t)(src[i][j] * 100 / 255);
}

// ============ S2 平均池化 ============
static void pool_s2(void) {
    for (int ch = 0; ch < C1_OUT_CH; ch++)
        for (int oh = 0; oh < S2_OUT_H; oh++)
            for (int ow = 0; ow < S2_OUT_W; ow++) {
                int32_t sum = 0;
                for (int m = 0; m < 2; m++)
                    for (int n = 0; n < 2; n++)
                        sum += c1_out[ch][oh * 2 + m][ow * 2 + n];
                s2_out[ch][oh][ow] = (int8_t)(sum >> 2);
            }
}

// ============ S4 平均池化 ============
static void pool_s4(void) {
    for (int ch = 0; ch < S4_OUT_CH; ch++)
        for (int oh = 0; oh < S4_OUT_H; oh++)
            for (int ow = 0; ow < S4_OUT_W; ow++) {
                int32_t sum = 0;
                for (int m = 0; m < 2; m++)
                    for (int n = 0; n < 2; n++)
                        sum += c3_out[ch][oh * 2 + m][ow * 2 + n];
                s4_out[ch][oh][ow] = (int8_t)(sum >> 2);
            }
}

// ============ Argmax ============
static int argmax(const int32_t *arr, int n) {
    int best = 0;
    for (int i = 1; i < n; i++)
        if (arr[i] > arr[best]) best = i;
    return best;
}

// ============ 主函数 ============
int main(void)
{
    printf("\n===== LeNet-5 INT8 Hardware Inference (BRAM Weights) =====\n");
    printf("Core: RV32I (E203) + CNN Accelerator\n");
    printf("Quant shift: %d\n", QUANT_SHIFT);
    printf("Test image: MNIST real digit %d\n", TEST_DIGIT);
    printf("Weights: preloaded to BRAM via $readmemh\n");

    // 硬件初始化
    if (cnn_init() != 0) {
        printf("ERROR: CNN VERSION != 0x00010000, bridge not working!\n");
        while (1);
    }
    printf("CNN VERSION OK\n");

    // 输入
    printf("Initializing test data...\n");
    init_input();
    printf("Init done.\n");

    // 清 PMU 计数并保持使能：保证后面的 PMU 读数就是本轮推理口径
    CNN_WR(CNN_PMU_CTRL, CNN_PMU_CTRL_CLEAR | CNN_PMU_CTRL_EN);

    // CPU-CSR 只有读通路、无写清零（A 侧 RTL），所以本轮用差值口径
    uint32_t mul_0   = csr_read_mulcnt();
    uint32_t load_0  = csr_read_loadcnt();
    uint32_t store_0 = csr_read_storecnt();
    uint32_t stall_0 = csr_read_stallcnt();

    // 计时开始
    uint32_t t_start = get_cycle();

    // ==================== C1 ====================
    for (int h = 0; h < 28; h++)
        for (int w = 0; w < 28; w++)
            hw_x[h * 28 + w] = input_img[h][w];

    cnn_set_wbase(W_BASE_C1);
    cnn_layer(28, 28, 1, 5, 6, 1, 0, QUANT_SHIFT, 1,
              hw_x, c1_bias, hw_y, 24 * 24 * 6);

    for (int i = 0; i < 24 * 24 * 6; i++) {
        int oc = i % 6;
        int ow = (i / 6) % 24;
        int oh = (i / 6) / 24;
        c1_out[oc][oh][ow] = hw_y[i];
    }
    printf("C1 done\n");

    // ==================== S2 ====================
    pool_s2();
    printf("S2 done\n");

    // ==================== C3 ====================
    for (int h = 0; h < 12; h++)
        for (int w = 0; w < 12; w++)
            for (int c = 0; c < 6; c++)
                hw_x[(h * 12 + w) * 6 + c] = s2_out[c][h][w];

    cnn_set_wbase(W_BASE_C3);
    cnn_layer(12, 12, 6, 5, 16, 1, 0, QUANT_SHIFT, 1,
              hw_x, c3_bias, hw_y, 8 * 8 * 16);

    for (int i = 0; i < 8 * 8 * 16; i++) {
        int oc = i % 16;
        int ow = (i / 16) % 8;
        int oh = (i / 16) / 8;
        c3_out[oc][oh][ow] = hw_y[i];
    }
    printf("C3 done\n");

    // ==================== S4 ====================
    pool_s4();
    printf("S4 done\n");

    // ==================== C5 (一次跑完，不再拆 4 段) ====================
    for (int h = 0; h < 4; h++)
        for (int w = 0; w < 4; w++)
            for (int c = 0; c < 16; c++)
                hw_x[(h * 4 + w) * 16 + c] = s4_out[c][h][w];

    cnn_set_wbase(W_BASE_C5);
    cnn_layer(4, 4, 16, 4, 120, 1, 0, QUANT_SHIFT, 1,
              hw_x, c5_bias, hw_y, 120);

    for (int i = 0; i < 120; i++)
        c5_out[i] = hw_y[i];
    printf("C5 done (120 outputs)\n");

    // ==================== F6 ====================
    for (int i = 0; i < 120; i++)
        hw_x[i] = c5_out[i];

    cnn_set_wbase(W_BASE_F6);
    cnn_layer(1, 1, 120, 1, 84, 1, 0, QUANT_SHIFT, 1,
              hw_x, f6_bias, hw_y, 84);

    for (int i = 0; i < 84; i++)
        f6_out[i] = hw_y[i];
    printf("F6 done\n");

    // ==================== OUT ====================
    for (int i = 0; i < 84; i++)
        hw_x[i] = f6_out[i];

    cnn_set_wbase(W_BASE_OUT);
    cnn_layer(1, 1, 84, 1, 10, 1, 0, QUANT_SHIFT, 0,
              hw_x, out_bias, hw_y, 10);

    for (int i = 0; i < 10; i++)
        final_out[i] = (int32_t)hw_y[i];

    // 计时结束
    uint32_t t_end = get_cycle();
    uint32_t cycles = t_end - t_start;

    uint32_t mulcnt   = csr_read_mulcnt()   - mul_0;
    uint32_t loadcnt  = csr_read_loadcnt()  - load_0;
    uint32_t storecnt = csr_read_storecnt() - store_0;
    uint32_t stallcnt = csr_read_stallcnt() - stall_0;

    // ==================== 结果 ====================
    int pred = argmax(final_out, OUT_NUM);

    printf("\nPredicted class: %d\n", pred);
    printf("Final output scores:\n");
    for (int i = 0; i < OUT_NUM; i++)
        printf("  class[%d] = %d\n", i, (int)final_out[i]);

    printf("\n===== Benchmark Result =====\n");
    printf("Total cycles: %u\n", cycles);
    printf("PMU MAC=%u WLOAD=%u XLOAD=%u BUBBLE=%u TOTAL=%u AXIWR=%u\n",
           (unsigned)CNN_RD(CNN_PMU_MAC),
           (unsigned)CNN_RD(CNN_PMU_WLOAD),
           (unsigned)CNN_RD(CNN_PMU_XLOAD),
           (unsigned)CNN_RD(CNN_PMU_BUBBLE),
           (unsigned)CNN_RD(CNN_PMU_TOTAL),
           (unsigned)CNN_RD(CNN_PMU_AXIWR));
    printf("Total mul num: %u\n", mulcnt);
    printf("Total load num: %u\n", loadcnt);
    printf("Total store num: %u\n", storecnt);
    printf("Total stall num: %u\n", stallcnt);
    printf("CPU Frequency: %u Hz\n", (uint32_t)SystemCoreClock);

    if (SystemCoreClock > 0) {
        uint32_t ms = (uint32_t)((uint64_t)cycles * 1000ULL / SystemCoreClock);
        printf("Total time: %u ms\n", ms);
    }

    printf("===== Benchmark Done =====\n\n");

    // ---- 2x2 max pooling 自检（CFG1[20]=1，权重不参与）----
    printf("===== Pooling Selftest (2x2 max) =====\n");
    {
        static const int8_t pool_in[36] = {
            -5, -2,  3,  4, -5,  6,
            -7, -8, -9, 10, 11,-12,
           -13, 14, 15,-16, 17, 18,
            19,-20, 21, 22,-23, 24,
            25, 26,-27, 28, 29,-30,
            31,-32, 33, 34,-35, 36
        };
        static const int8_t pool_exp[9] = { -2, 10, 11, 19, 22, 24, 31, 34, 36 };
        int pool_ok = 1;
        cnn_clear_ptrs();
        CNN_WR(CNN_CFG0, cnn_cfg0(6, 6, 1, 2));
        CNN_WR(CNN_CFG1, cnn_cfg1(1, 2, 0, 0, 0) | (1u << 20));   /* pool=1 */
        cnn_load_input(pool_in, 36);
        cnn_run();
        for (int i = 0; i < 9; i++) {
            int v = (int)cnn_read_output(i);
            printf("  pool[%d] = %d (expect %d)%s\n", i, v, (int)pool_exp[i],
                   (v == (int)pool_exp[i]) ? "" : "  <-- MISMATCH");
            if (v != (int)pool_exp[i]) pool_ok = 0;
        }
        printf("POOL SELFTEST: %s\n", pool_ok ? "PASS" : "FAIL");
    }
    printf("===== Pooling Done =====\n\n");

    // ---- Sobel 边缘检测自检（K=3, C=1, OC=2；权重在 ROM 的 W_BASE_SOBEL 段）----
    printf("===== Sobel Edge Selftest (K=3, OC=2) =====\n");
    {
        static const int8_t sob_in[64] = {
             0,  0,  0,  0,  0,  0,-30,-30,
             0,  0,  0,  0,  0,  0,-30,-30,
             0,  0, 30, 30, 30, 30,  0,  0,
             0,  0, 30, 30, 30, 30,  0,  0,
             0,  0, 30, 30, 30, 30,  0,  0,
             0,  0, 30, 30, 30, 30,  0,  0,
             0,  0,  0,  0,  0,  0,  0,  0,
             0,  0,  0,  0,  0,  0,  0,  0
        };
        static const int32_t sob_b[2] = { 0, 0 };
        int8_t gx[36], gy[36];
        int nz = 0, gx_mid0 = 1, gy_mid0 = 1, r, c;

        cnn_clear_ptrs();
        cnn_set_wbase(W_BASE_SOBEL);
        cnn_config(8, 8, 1, 3, 2, 1, 0, 0, 0);   /* H=8 W=8 C=1 K=3 OC=2 S=1 SHIFT=0 RELU=0 */
        cnn_load_input(sob_in, 64);
        cnn_load_bias(sob_b, 2);
        cnn_run();

        for (r = 0; r < 36; r++) {
            gx[r] = cnn_read_output((uint32_t)(r * 2 + 0));   /* oc0 = Gx */
            gy[r] = cnn_read_output((uint32_t)(r * 2 + 1));   /* oc1 = Gy */
        }

        printf("  Gx (vertical edges, 6x6):\n");
        for (r = 0; r < 6; r++) {
            printf("   ");
            for (c = 0; c < 6; c++) printf("%5d", (int)gx[r * 6 + c]);
            printf("\n");
        }
        printf("  Gy (horizontal edges, 6x6):\n");
        for (r = 0; r < 6; r++) {
            printf("   ");
            for (c = 0; c < 6; c++) printf("%5d", (int)gy[r * 6 + c]);
            printf("\n");
        }

        for (r = 0; r < 36; r++) {
            if (gx[r] != 0) nz++;
            if (gy[r] != 0) nz++;
        }
        for (r = 0; r < 6; r++)                 /* 方块内部：Gx 中两列应为 0 */
            if (gx[r * 6 + 2] != 0 || gx[r * 6 + 3] != 0) gx_mid0 = 0;
        for (c = 0; c < 6; c++)                 /* 方块内部：Gy 中两行应为 0 */
            if (gy[2 * 6 + c] != 0 || gy[3 * 6 + c] != 0) gy_mid0 = 0;

        printf("  nonzero = %d (expect 48)  Gx-mid-cols-zero = %d  Gy-mid-rows-zero = %d\n",
               nz, gx_mid0, gy_mid0);
        printf("SOBEL SELFTEST: %s\n",
               (nz == 48 && gx_mid0 && gy_mid0) ? "PASS" : "FAIL");
    }
    printf("===== Sobel Done =====\n\n");

    while (1);
    return 0;
}
