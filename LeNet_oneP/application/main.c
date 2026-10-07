#include <stdio.h>
#include <stdint.h>
#include "hbird_sdk_soc.h"

#include "../include/cnn.h"
#include "../include/lenet_weights_hw.h"
#include "../include/lenet_c5_split.h"       // ← 新增：C5a~C5d
#include "../include/mnist_samples.h"

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

// ============ 计时 ============
static __attribute__((always_inline)) inline uint32_t get_cycle(void) {
    uint32_t val;
    asm volatile ("csrr %0, mcycle" : "=r"(val));
    return val;
}

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
    printf("\n===== LeNet-5 INT8 Hardware Inference =====\n");
    printf("Core: RV32I (E203) + CNN Accelerator\n");
    printf("Quant shift: %d\n", QUANT_SHIFT);
    printf("Test image: MNIST real digit %d\n", TEST_DIGIT);
    printf("C5: 4 segments (32+32+32+24)\n");

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

    // 计时开始
    uint32_t t_start = get_cycle();

    // ==================== C1 ====================
    for (int h = 0; h < 28; h++)
        for (int w = 0; w < 28; w++)
            hw_x[h * 28 + w] = input_img[h][w];

    cnn_layer(28, 28, 1, 5, 6, 1, 0, QUANT_SHIFT, 1,
              hw_x, c1_weight_hw, c1_bias, hw_y, 24 * 24 * 6);

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

    cnn_layer(12, 12, 6, 5, 16, 1, 0, QUANT_SHIFT, 1,
              hw_x, c3_weight_hw, c3_bias, hw_y, 8 * 8 * 16);

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

    // ==================== C5 (拆成 4 段) ====================
    // 输入 4×4×16，K=4，4 段共享同一输入
    for (int h = 0; h < 4; h++)
        for (int w = 0; w < 4; w++)
            for (int c = 0; c < 16; c++)
                hw_x[(h * 4 + w) * 16 + c] = s4_out[c][h][w];

    // C5a: oc=0..31
    cnn_layer(4, 4, 16, 4, 32, 1, 0, QUANT_SHIFT, 1,
              hw_x, c5a_weight_hw, c5a_bias, hw_y, 32);
    for (int i = 0; i < 32; i++)
        c5_out[i] = hw_y[i];
    printf("C5a done (oc 0..31)\n");

    // C5b: oc=32..63
    cnn_layer(4, 4, 16, 4, 32, 1, 0, QUANT_SHIFT, 1,
              hw_x, c5b_weight_hw, c5b_bias, hw_y, 32);
    for (int i = 0; i < 32; i++)
        c5_out[32 + i] = hw_y[i];
    printf("C5b done (oc 32..63)\n");

    // C5c: oc=64..95
    cnn_layer(4, 4, 16, 4, 32, 1, 0, QUANT_SHIFT, 1,
              hw_x, c5c_weight_hw, c5c_bias, hw_y, 32);
    for (int i = 0; i < 32; i++)
        c5_out[64 + i] = hw_y[i];
    printf("C5c done (oc 64..95)\n");

    // C5d: oc=96..119
    cnn_layer(4, 4, 16, 4, 24, 1, 0, QUANT_SHIFT, 1,
              hw_x, c5d_weight_hw, c5d_bias, hw_y, 24);
    for (int i = 0; i < 24; i++)
        c5_out[96 + i] = hw_y[i];
    printf("C5d done (oc 96..119)\n");

    // ==================== F6 ====================
    for (int i = 0; i < 120; i++)
        hw_x[i] = c5_out[i];

    cnn_layer(1, 1, 120, 1, 84, 1, 0, QUANT_SHIFT, 1,
              hw_x, f6_weight_hw, f6_bias, hw_y, 84);

    for (int i = 0; i < 84; i++)
        f6_out[i] = hw_y[i];
    printf("F6 done\n");

    // ==================== OUT ====================
    for (int i = 0; i < 84; i++)
        hw_x[i] = f6_out[i];

    cnn_layer(1, 1, 84, 1, 10, 1, 0, QUANT_SHIFT, 0,
              hw_x, out_weight_hw, out_bias, hw_y, 10);

    for (int i = 0; i < 10; i++)
        final_out[i] = (int32_t)hw_y[i];

    // 计时结束
    uint32_t t_end = get_cycle();
    uint32_t cycles = t_end - t_start;

    // ==================== 结果 ====================
    int pred = argmax(final_out, OUT_NUM);

    printf("\nPredicted class: %d\n", pred);
    printf("Final output scores:\n");
    for (int i = 0; i < OUT_NUM; i++)
        printf("  class[%d] = %d\n", i, (int)final_out[i]);

    printf("\n===== Benchmark Result =====\n");
    printf("Total cycles: %u\n", cycles);
    printf("CPU Frequency: %u Hz\n", (uint32_t)SystemCoreClock);

    if (SystemCoreClock > 0) {
        uint32_t ms = (uint32_t)((uint64_t)cycles * 1000ULL / SystemCoreClock);
        printf("Total time: %u ms\n", ms);
    }

    printf("===== Benchmark Done =====\n\n");

    while (1);
    return 0;
}
