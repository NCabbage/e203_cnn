#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <stdlib.h>
#include "hbird_sdk_soc.h"

#include "../include/csr.h"
#include "../include/cnn.h"
#include "../include/wbase_tap.h"
#include "../include/lenet_weights_hw.h"

// ============ 网络配置 ============
#define INPUT_H       28
#define INPUT_W       28

#define QUANT_SHIFT   9
#define OUT_NUM       10

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

// ============ UART 接收辅助 ============
static inline int uart_rx_byte(void)
{
    return getchar();
}

static void uart_rx_bytes(uint8_t *buf, int n)
{
    for (int i = 0; i < n; i++)
        buf[i] = (uint8_t)uart_rx_byte();
}

static void uart_rx_line(char *buf, int max_len)
{
    int i = 0;
    while (i < max_len - 1) {
        int c = uart_rx_byte();
        if (c == '\r') continue;
        if (c == '\n') break;
        buf[i++] = (char)c;
    }
    buf[i] = '\0';
}

// ============ Argmax ============
static int argmax(const int32_t *arr, int n) {
    int best = 0;
    for (int i = 1; i < n; i++)
        if (arr[i] > arr[best]) best = i;
    return best;
}

// ============ 软件 2x2 max pooling ============
// 输入:  hw_y 布局 [in_h][in_w][C]，地址 = (h*in_w + w)*C + c
// 输出:  hw_x 布局 [out_h][out_w][C]，地址 = (oh*out_w + ow)*C + c
//        out_h = in_h/2, out_w = in_w/2
static void sw_pool_max(int in_h, int in_w, int C)
{
    int out_h = in_h / 2;
    int out_w = in_w / 2;

    for (int oh = 0; oh < out_h; oh++) {
        for (int ow = 0; ow < out_w; ow++) {
            for (int c = 0; c < C; c++) {
                int h0 = oh * 2;
                int w0 = ow * 2;

                int8_t v00 = hw_y[(h0     * in_w + w0    ) * C + c];
                int8_t v01 = hw_y[(h0     * in_w + w0 + 1) * C + c];
                int8_t v10 = hw_y[((h0+1) * in_w + w0    ) * C + c];
                int8_t v11 = hw_y[((h0+1) * in_w + w0 + 1) * C + c];

                int8_t m = v00;
                if (v01 > m) m = v01;
                if (v10 > m) m = v10;
                if (v11 > m) m = v11;

                hw_x[(oh * out_w + ow) * C + c] = m;
            }
        }
    }
}

// ============ 跑一次完整推理，返回预测类别 ============
static int run_one_inference(void)
{
    // ==================== C1 卷积 ====================
    // 输入 28x28x1 -> 输出 24x24x6（hw_y 布局 [oh][ow][oc]）
    for (int i = 0; i < 28 * 28; i++)
        hw_x[i] = input_img[i / 28][i % 28];

    cnn_set_wbase(W_BASE_C1);
    cnn_layer(28, 28, 1, 5, 6, 1, 0, QUANT_SHIFT, 1,
              hw_x, c1_bias, hw_y, C1_OUT_N);

    // ==================== S2 软件 max pooling ====================
    // 输入 hw_y: 24x24x6 -> 输出 hw_x: 12x12x6
    sw_pool_max(24, 24, 6);

    // ==================== C3 卷积 ====================
    // 输入 12x12x6 -> 输出 8x8x16
    cnn_set_wbase(W_BASE_C3);
    cnn_layer(12, 12, 6, 5, 16, 1, 0, QUANT_SHIFT, 1,
              hw_x, c3_bias, hw_y, C3_OUT_N);

    // ==================== S4 软件 max pooling ====================
    // 输入 hw_y: 8x8x16 -> 输出 hw_x: 4x4x16
    sw_pool_max(8, 8, 16);

    // ==================== C5 卷积 ====================
    // 输入 4x4x16 -> 输出 120
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

// ============ 主函数：批处理服务器 ============
int main(void)
{
    printf("\n===== LeNet-5 Batch Test Server (SW Pooling) =====\n");
    printf("Core: RV32I (E203) + CNN Accelerator\n");
    printf("Quant shift: %d\n", QUANT_SHIFT);
    printf("Pooling: 2x2 MAX software\n");

    if (cnn_init() != 0) {
        printf("ERROR: CNN VERSION != 0x00010000, bridge not working!\n");
        while (1);
    }
    printf("CNN VERSION OK\n");
    printf("Waiting for PC commands...\n");

    static uint8_t img_buf[INPUT_H * INPUT_W];
    char cmd[32];

    while (1) {
        uart_rx_line(cmd, sizeof(cmd));

        if (strncmp(cmd, "RUN", 3) == 0) {
            int label = 0;
            if (cmd[3] == ' ')
                label = atoi(cmd + 4);

            printf("READY\n");

            // 接收 784 字节原始图像
            uart_rx_bytes(img_buf, INPUT_H * INPUT_W);

            // 转成 int8（和训练脚本缩放一致）
            for (int i = 0; i < INPUT_H * INPUT_W; i++)
                input_img[i / INPUT_W][i % INPUT_W] =
                    (int8_t)(img_buf[i] * 100 / 255);

            // 计时跑推理
            uint32_t t0 = get_cycle();
            int pred = run_one_inference();
            uint32_t t1 = get_cycle();

            // 回传结果
            printf("PRED %d %d %u\n", pred, label, (unsigned)(t1 - t0));
        }
        else if (strncmp(cmd, "EXIT", 4) == 0) {
            printf("BYE\n");
            break;
        }
        else if (cmd[0] != '\0') {
            printf("UNKNOWN: %s\n", cmd);
        }
    }

    while (1);
    return 0;
}
