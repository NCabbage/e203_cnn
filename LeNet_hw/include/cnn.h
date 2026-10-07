/*
 * cnn.h
 *
 *  Created on: 2026年10月3日
 *      Author: lenovo
 */

#ifndef CNN_H_
#define CNN_H_

#ifndef __CNN_H__
#define __CNN_H__
#include <stdint.h>

/* ---- 寄存器地址 ---- */
#define CNN_BASE     0x40000000UL
#define CNN_CTRL     (CNN_BASE + 0x00)
#define CNN_STATUS   (CNN_BASE + 0x04)
#define CNN_WDATA    (CNN_BASE + 0x08)
#define CNN_IDATA    (CNN_BASE + 0x0C)
#define CNN_BIAS     (CNN_BASE + 0x10)
#define CNN_CFG0     (CNN_BASE + 0x14)
#define CNN_CFG1     (CNN_BASE + 0x18)
#define CNN_OINDEX   (CNN_BASE + 0x1C)
#define CNN_ODATA    (CNN_BASE + 0x20)
#define CNN_WPTR     (CNN_BASE + 0x24)
#define CNN_IPTR     (CNN_BASE + 0x28)
#define CNN_YCOUNT   (CNN_BASE + 0x2C)
#define CNN_VERSION  (CNN_BASE + 0x30)

/* ---- CTRL 位定义 ---- */
#define CNN_CTRL_START     (1u<<0)
#define CNN_CTRL_CLR_WPTR  (1u<<1)
#define CNN_CTRL_CLR_IPTR  (1u<<2)
#define CNN_CTRL_CLR_BPTR  (1u<<3)

/* ---- STATUS 位定义 ---- */
#define CNN_STATUS_BUSY    (1u<<0)
#define CNN_STATUS_DONE    (1u<<1)

/* ---- 读写宏 ---- */
#define CNN_RD(a)   (*(volatile uint32_t *)(a))
#define CNN_WR(a,v) do { (*(volatile uint32_t *)(a)) = (uint32_t)(v); } while (0)

/* ---- 配置组装 ---- */
static inline uint32_t cnn_cfg0(uint32_t h, uint32_t w, uint32_t c, uint32_t k)
{
    return (h & 0xFF) | ((w & 0xFF) << 8) | ((c & 0xFF) << 16) | ((k & 0xFF) << 24);
}
static inline uint32_t cnn_cfg1(uint32_t oc, uint32_t stride, uint32_t pad,
                                uint32_t shift, uint32_t relu)
{
    return (oc & 0xFF) | ((stride & 3u) << 8) | ((pad & 0xFu) << 10)
         | ((shift & 0x1Fu) << 14) | ((relu & 1u) << 19);
}

/* ---- 基本操作 ---- */
static inline int cnn_init(void)
{
    return (CNN_RD(CNN_VERSION) == 0x00010000u) ? 0 : -1;
}

static inline void cnn_clear_ptrs(void)
{
    CNN_WR(CNN_CTRL, CNN_CTRL_CLR_WPTR | CNN_CTRL_CLR_IPTR | CNN_CTRL_CLR_BPTR);
}

static inline void cnn_config(uint32_t h, uint32_t w, uint32_t c, uint32_t k,
                              uint32_t oc, uint32_t stride, uint32_t pad,
                              uint32_t shift, uint32_t relu)
{
    CNN_WR(CNN_CFG0, cnn_cfg0(h, w, c, k));
    CNN_WR(CNN_CFG1, cnn_cfg1(oc, stride, pad, shift, relu));
}

static inline void cnn_load_input(const int8_t *x, int n)
{
    for (int i = 0; i < n; i++)
        CNN_WR(CNN_IDATA, (uint32_t)(int32_t)x[i]);
}

static inline void cnn_load_weight(const int8_t *w, int n)
{
    for (int i = 0; i < n; i++)
        CNN_WR(CNN_WDATA, (uint32_t)(int32_t)w[i]);
}

static inline void cnn_load_bias(const int32_t *b, int n)
{
    for (int i = 0; i < n; i++)
        CNN_WR(CNN_BIAS, (uint32_t)b[i]);
}

static inline void cnn_run(void)
{
    CNN_WR(CNN_CTRL, CNN_CTRL_START);
    while ((CNN_RD(CNN_STATUS) & CNN_STATUS_DONE) == 0);
    CNN_WR(CNN_CTRL, 0);   /* 必须拉低 start，否则下一轮启动不了 */
}

static inline int8_t cnn_read_output(int idx)
{
    CNN_WR(CNN_OINDEX, (uint32_t)idx);
    return (int8_t)CNN_RD(CNN_ODATA);
}

/* ---- 一层完整流程 ---- */
static inline void cnn_layer(int in_h, int in_w, int in_c, int k,
                             int oc, int stride, int pad, int shift, int relu,
                             const int8_t *x, const int8_t *w, const int32_t *b,
                             int8_t *y, int out_n)
{
    cnn_clear_ptrs();
    cnn_config(in_h, in_w, in_c, k, oc, stride, pad, shift, relu);
    cnn_load_input(x, in_h * in_w * in_c);
    cnn_load_weight(w, k * k * in_c * oc);
    cnn_load_bias(b, oc);
    cnn_run();
    for (int i = 0; i < out_n; i++)
        y[i] = cnn_read_output(i);
}


/* ---- 输出通道分块执行：某层权重数 k*k*C*OC 超过硬件 W_DEPTH(8192) 时用 ----
 * 权重数组布局固定为 [k][k][C][OC_total]，本函数只取 oc_start..oc_start+oc_n-1 这一段，
 * 按 [k][k][C][oc_n] 的紧凑布局写进硬件权重内存。
 * 用法：把一个大层切成几块依次调用，每块的结果分别写回输出数组。
 */
static inline void cnn_layer_occhunk(int in_h, int in_w, int in_c, int k,
                                     int oc_total, int oc_start, int oc_n,
                                     int stride, int pad, int shift, int relu,
                                     const int8_t *x, const int8_t *w,
                                     const int32_t *b,
                                     int8_t *y, int out_n)
{
    cnn_clear_ptrs();
    cnn_config(in_h, in_w, in_c, k, oc_n, stride, pad, shift, relu);
    cnn_load_input(x, in_h * in_w * in_c);
    for (int t = 0; t < k * k * in_c; t++)
        for (int o = 0; o < oc_n; o++)
            CNN_WR(CNN_WDATA, (uint32_t)(int32_t)w[t * oc_total + oc_start + o]);
    cnn_load_bias(&b[oc_start], oc_n);
    cnn_run();
    for (int i = 0; i < out_n; i++)
        y[i] = cnn_read_output(i);
}

#endif /* __CNN_H__ */

#endif /* CNN_H_ */
