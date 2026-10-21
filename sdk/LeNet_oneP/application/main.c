#include <stdio.h>
#include <stdint.h>
#include "hbird_sdk_soc.h"

#include "../include/csr.h"
#include "../include/cnn.h"
#include "../include/wbase_tap.h"
#include "../include/lenet_weights_hw.h"
#include "../include/mnist_samples.h"   // ★ 单张测试需要这个头文件

// ============ 网络配置 ============
#define INPUT_H       28
#define INPUT_W       28

#define QUANT_SHIFT   9
#define OUT_NUM       10
#define TEST_DIGIT    9           	   // ★ 测试哪一张图 (0~9)

// ============ 张量尺寸 ============
#define C1_OUT_N      (24 * 24 * 6)    // 3456
#define S2_OUT_N      (12 * 12 * 6)    // 864
#define C3_OUT_N      (8 * 8 * 16)     // 1024
#define S4_OUT_N      (4 * 4 * 16)     // 256
#define C5_OUT_N      120
#define F6_OUT_N      84

// ============ 输入 ============
static int8_t  input_img[INPUT_H][INPUT_W];

// ============ 硬件缓冲 ============
static int8_t  hw_x[4096];
static int8_t  hw_y[4096];

// ============ 输出 ============
static int32_t final_out[OUT_NUM];

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

// ============ Argmax ============
static int argmax(const int32_t *arr, int n) {
    int best = 0;
    for (int i = 1; i < n; i++)
        if (arr[i] > arr[best]) best = i;
    return best;
}

// ============ 打印辅助 ============
static void print_ints(const char *tag, const int8_t *data, int n)
{
    printf("%s", tag);
    for (int i = 0; i < n; i++)
        printf(" %d", (int)data[i]);
    printf("\n");
}

// ============ 跑一次完整推理，返回预测类别 ============
static int run_one_inference(void)
{
    // ==================== C1 卷积 ====================
    for (int i = 0; i < 28 * 28; i++)
        hw_x[i] = input_img[i / 28][i % 28];

    cnn_set_wbase(W_BASE_C1);
    cnn_layer(28, 28, 1, 5, 6, 1, 0, QUANT_SHIFT, 1,
              hw_x, c1_bias, hw_y, C1_OUT_N);

    // ==================== S2 硬件池化 ====================
    for (int i = 0; i < C1_OUT_N; i++) hw_x[i] = hw_y[i];
    cnn_pool(24, 24, 6, hw_x, hw_y, S2_OUT_N);

    for (int i = 0; i < S2_OUT_N; i++) hw_x[i] = hw_y[i];

    // ==================== C3 卷积 ====================
    cnn_set_wbase(W_BASE_C3);
    cnn_layer(12, 12, 6, 5, 16, 1, 0, QUANT_SHIFT, 1,
              hw_x, c3_bias, hw_y, C3_OUT_N);

    // ==================== S4 硬件池化 ====================
    for (int i = 0; i < C3_OUT_N; i++) hw_x[i] = hw_y[i];
    cnn_pool(8, 8, 16, hw_x, hw_y, S4_OUT_N);

    for (int i = 0; i < S4_OUT_N; i++) hw_x[i] = hw_y[i];

    // ==================== C5 卷积 ====================
    cnn_set_wbase(W_BASE_C5);
    cnn_layer(4, 4, 16, 4, 120, 1, 0, QUANT_SHIFT, 1,
              hw_x, c5_bias, hw_y, C5_OUT_N);

    for (int i = 0; i < C5_OUT_N; i++) hw_x[i] = hw_y[i];

    // ==================== F6 全连接 ====================
    cnn_set_wbase(W_BASE_F6);
    cnn_layer(1, 1, 120, 1, 84, 1, 0, QUANT_SHIFT, 1,
              hw_x, f6_bias, hw_y, F6_OUT_N);

    for (int i = 0; i < F6_OUT_N; i++) hw_x[i] = hw_y[i];

    // ==================== OUT 全连接 ====================
    cnn_set_wbase(W_BASE_OUT);
    cnn_layer(1, 1, 84, 1, 10, 1, 0, QUANT_SHIFT, 0,
              hw_x, out_bias, hw_y, 10);

    for (int i = 0; i < 10; i++)
        final_out[i] = (int32_t)hw_y[i];

    return argmax(final_out, OUT_NUM);
}

// ============ 主函数：单张测试 ============
int main(void)
{
    printf("\n===== LeNet-5 INT8 Single Inference (HW Pooling) =====\n");
    printf("Core: RV32I (E203) + CNN Accelerator\n");
    printf("Quant shift: %d\n", QUANT_SHIFT);
    printf("Test image: MNIST real digit %d\n", TEST_DIGIT);
    printf("Pooling: 2x2 MAX hardware, multi-channel\n");

    if (cnn_init() != 0) {
        printf("ERROR: CNN VERSION != 0x00010000, bridge not working!\n");
        while (1);
    }
    printf("CNN VERSION OK\n");

    // 1. 初始化输入图（从 mnist_samples.h 读取）
    printf("Initializing test data...\n");
    init_input();
    printf("Init done.\n");

    // 2. 清 PMU 计数并保持使能
    CNN_WR(CNN_PMU_CTRL, CNN_PMU_CTRL_CLEAR | CNN_PMU_CTRL_EN);

    // 3. CPU-CSR 差值口径
    uint32_t mul_0   = csr_read_mulcnt();
    uint32_t load_0  = csr_read_loadcnt();
    uint32_t store_0 = csr_read_storecnt();
    uint32_t stall_0 = csr_read_stallcnt();

    // 4. 开始计时
    uint32_t t0 = get_cycle();

    // 5. 跑推理
    int pred = run_one_inference();

    // 6. 结束计时
    uint32_t t1 = get_cycle();

    uint32_t mulcnt   = csr_read_mulcnt()   - mul_0;
    uint32_t loadcnt  = csr_read_loadcnt()  - load_0;
    uint32_t storecnt = csr_read_storecnt() - store_0;
    uint32_t stallcnt = csr_read_stallcnt() - stall_0;
    uint32_t cycles = t1 - t0;

    // 7. 打印结果
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

    while (1);
    return 0;
}
