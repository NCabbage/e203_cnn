#include <stdio.h>
#include <stdint.h>
#include "hbird_sdk_soc.h"

#include "../include/cnn.h"
#include "../include/lenet_weights_hw.h"
#include "../include/lenet_c5_split.h"
#include "../include/mnist_100.h"

// ============ 网络配置 ============
#define INPUT_H       28
#define INPUT_W       28

#define C1_OUT_H      24
#define C1_OUT_W      24

#define S2_OUT_H      12
#define S2_OUT_W      12

#define C3_OUT_H      8
#define C3_OUT_W      8

#define S4_OUT_H      4
#define S4_OUT_W      4

#define C5_OUT        120
#define F6_OUT        84
#define OUT_NUM       10

#define QUANT_SHIFT   8

// ============ 中间张量 ============
static int8_t  input_img[INPUT_H][INPUT_W];
static int8_t  c1_out[6][C1_OUT_H][C1_OUT_W];
static int8_t  s2_out[6][S2_OUT_H][S2_OUT_W];
static int8_t  c3_out[16][C3_OUT_H][C3_OUT_W];
static int8_t  s4_out[16][S4_OUT_H][S4_OUT_W];
static int8_t  c5_out[C5_OUT];
static int8_t  f6_out[F6_OUT];
static int32_t final_out[OUT_NUM];

// ============ 硬件缓冲 ============
static int8_t  hw_x[4096];
static int8_t  hw_y[4096];

// ============ 计时（加 always_inline 避免 undefined reference） ============
static __attribute__((always_inline)) inline uint32_t get_cycle(void) {
    uint32_t val;
    asm volatile ("csrr %0, mcycle" : "=r"(val));
    return val;
}

// ============ S2 池化 ============
static void pool_s2(void) {
    for (int ch = 0; ch < 6; ch++)
        for (int oh = 0; oh < S2_OUT_H; oh++)
            for (int ow = 0; ow < S2_OUT_W; ow++) {
                int32_t s = 0;
                for (int m = 0; m < 2; m++)
                    for (int n = 0; n < 2; n++)
                        s += c1_out[ch][oh*2+m][ow*2+n];
                s2_out[ch][oh][ow] = (int8_t)(s >> 2);
            }
}

// ============ S4 池化 ============
static void pool_s4(void) {
    for (int ch = 0; ch < 16; ch++)
        for (int oh = 0; oh < S4_OUT_H; oh++)
            for (int ow = 0; ow < S4_OUT_W; ow++) {
                int32_t s = 0;
                for (int m = 0; m < 2; m++)
                    for (int n = 0; n < 2; n++)
                        s += c3_out[ch][oh*2+m][ow*2+n];
                s4_out[ch][oh][ow] = (int8_t)(s >> 2);
            }
}

// ============ Argmax ============
static int argmax(const int32_t *a, int n) {
    int best = 0;
    for (int i = 1; i < n; i++)
        if (a[i] > a[best]) best = i;
    return best;
}

// ============ 单张推理 ============
static int infer_one(const unsigned char img[28][28])
{
    // ---- 输入：0-255 → 0-100 ----
    for (int i = 0; i < INPUT_H; i++)
        for (int j = 0; j < INPUT_W; j++)
            input_img[i][j] = (int8_t)(img[i][j] * 100 / 255);

    // ==================== C1 ====================
    for (int h = 0; h < 28; h++)
        for (int w = 0; w < 28; w++)
            hw_x[h*28 + w] = input_img[h][w];

    cnn_layer(28, 28, 1, 5, 6, 1, 0, QUANT_SHIFT, 1,
              hw_x, c1_weight_hw, c1_bias, hw_y, 24*24*6);

    for (int i = 0; i < 24*24*6; i++) {
        int oc = i % 6;
        int ow = (i / 6) % 24;
        int oh = (i / 6) / 24;
        c1_out[oc][oh][ow] = hw_y[i];
    }

    // ==================== S2 ====================
    pool_s2();

    // ==================== C3 ====================
    for (int h = 0; h < 12; h++)
        for (int w = 0; w < 12; w++)
            for (int c = 0; c < 6; c++)
                hw_x[(h*12 + w)*6 + c] = s2_out[c][h][w];

    cnn_layer(12, 12, 6, 5, 16, 1, 0, QUANT_SHIFT, 1,
              hw_x, c3_weight_hw, c3_bias, hw_y, 8*8*16);

    for (int i = 0; i < 8*8*16; i++) {
        int oc = i % 16;
        int ow = (i / 16) % 8;
        int oh = (i / 16) / 8;
        c3_out[oc][oh][ow] = hw_y[i];
    }

    // ==================== S4 ====================
    pool_s4();

    // ==================== C5 (4 段) ====================
    for (int h = 0; h < 4; h++)
        for (int w = 0; w < 4; w++)
            for (int c = 0; c < 16; c++)
                hw_x[(h*4 + w)*16 + c] = s4_out[c][h][w];

    // C5a: oc=0..31
    cnn_layer(4, 4, 16, 4, 32, 1, 0, QUANT_SHIFT, 1,
              hw_x, c5a_weight_hw, c5a_bias, hw_y, 32);
    for (int i = 0; i < 32; i++) c5_out[i] = hw_y[i];

    // C5b: oc=32..63
    cnn_layer(4, 4, 16, 4, 32, 1, 0, QUANT_SHIFT, 1,
              hw_x, c5b_weight_hw, c5b_bias, hw_y, 32);
    for (int i = 0; i < 32; i++) c5_out[32 + i] = hw_y[i];

    // C5c: oc=64..95
    cnn_layer(4, 4, 16, 4, 32, 1, 0, QUANT_SHIFT, 1,
              hw_x, c5c_weight_hw, c5c_bias, hw_y, 32);
    for (int i = 0; i < 32; i++) c5_out[64 + i] = hw_y[i];

    // C5d: oc=96..119
    cnn_layer(4, 4, 16, 4, 24, 1, 0, QUANT_SHIFT, 1,
              hw_x, c5d_weight_hw, c5d_bias, hw_y, 24);
    for (int i = 0; i < 24; i++) c5_out[96 + i] = hw_y[i];

    // ==================== F6 ====================
    for (int i = 0; i < 120; i++) hw_x[i] = c5_out[i];

    cnn_layer(1, 1, 120, 1, 84, 1, 0, QUANT_SHIFT, 1,
              hw_x, f6_weight_hw, f6_bias, hw_y, 84);
    for (int i = 0; i < 84; i++) f6_out[i] = hw_y[i];

    // ==================== OUT (relu=0) ====================
    for (int i = 0; i < 84; i++) hw_x[i] = f6_out[i];

    cnn_layer(1, 1, 84, 1, 10, 1, 0, QUANT_SHIFT, 0,
              hw_x, out_weight_hw, out_bias, hw_y, 10);
    for (int i = 0; i < 10; i++) final_out[i] = (int32_t)hw_y[i];

    return argmax(final_out, 10);
}

// ============ 主函数 ============
int main(void)
{
    printf("\n===== LeNet-5 Accuracy Test (100 images) =====\n");

    if (cnn_init() != 0) {
        printf("ERROR: CNN VERSION mismatch!\n");
        while (1);
    }
    printf("CNN VERSION OK\n\n");

    uint32_t t_start = get_cycle();

    int correct = 0;
    int per_class_correct[10] = {0};
    int per_class_total[10]   = {0};

    for (int i = 0; i < 10; i++) {
        int label = mnist_100_labels[i];
        int pred  = infer_one(mnist_100[i]);

        per_class_total[label]++;
        if (pred == label) {
            correct++;
            per_class_correct[label]++;
        } else {
            printf("  [%3d] label=%d, pred=%d  MISMATCH\n",
                   i, label, pred);
        }
    }

    uint32_t t_end = get_cycle();
    uint32_t cycles = t_end - t_start;

    // ---------- 准确率 ----------
    printf("\n===== Accuracy =====\n");
    printf("Total: %d/100 = %d%%\n", correct, correct);

    printf("\nPer-class:\n");
    for (int c = 0; c < 10; c++) {
        int pct = per_class_total[c] > 0
                ? per_class_correct[c] * 100 / per_class_total[c]
                : 0;
        printf("  class %d: %2d/%2d = %3d%%\n",
               c, per_class_correct[c], per_class_total[c], pct);
    }

    // ---------- 计时 ----------
    printf("\n===== Timing =====\n");
    printf("Total cycles: %u\n", cycles);
    printf("Per-image cycles: %u\n", cycles / 100);
    printf("CPU Frequency: %u Hz\n", (uint32_t)SystemCoreClock);
    if (SystemCoreClock > 0) {
        uint32_t ms = (uint32_t)((uint64_t)cycles * 1000ULL / SystemCoreClock);
        printf("Total time: %u ms\n", ms);
        printf("Per-image time: %u ms\n", ms / 100);
    }

    printf("===== Done =====\n\n");

    while (1);
    return 0;
}
