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
#define CNN_WDATA    (CNN_BASE + 0x08)   /* 保留但不再使用 */
#define CNN_IDATA    (CNN_BASE + 0x0C)
#define CNN_BIAS     (CNN_BASE + 0x10)
#define CNN_CFG0     (CNN_BASE + 0x14)
#define CNN_CFG1     (CNN_BASE + 0x18)
#define CNN_OINDEX   (CNN_BASE + 0x1C)
#define CNN_ODATA    (CNN_BASE + 0x20)
#define CNN_WPTR     (CNN_BASE + 0x24)   /* 保留，读恒 0 */
#define CNN_IPTR     (CNN_BASE + 0x28)
#define CNN_YCOUNT   (CNN_BASE + 0x2C)
#define CNN_VERSION  (CNN_BASE + 0x30)
#define CNN_WBASE    (CNN_BASE + 0x34)   /* 新增：每层权重起始字地址 */

/* ---- CTRL 位定义 ---- */
#define CNN_CTRL_START     (1u<<0)
#define CNN_CTRL_CLR_WPTR  (1u<<1)       /* 保留，无实际作用 */
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

/* 设置当前层的权重基地址（单位：32bit 字）
 * 每层启动前调用一次，值从 wbase_tap.h 的 W_BASE_xxx 抄 */
static inline void cnn_set_wbase(uint32_t base)
{
    CNN_WR(CNN_WBASE, base & 0xFFFFu);
}

static inline void cnn_clear_ptrs(void)
{
    /* wptr 已经不用了，只清 iptr/bptr */
    CNN_WR(CNN_CTRL, CNN_CTRL_CLR_IPTR | CNN_CTRL_CLR_BPTR);
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

/* cnn_load_weight 已删除：权重预加载到 BRAM，不再由 CPU 写 */

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

/* ---- 一层完整流程 ----
 * 与原来相比：
 *   - 去掉 w 参数（权重已在 BRAM）
 *   - 调用前需要用 cnn_set_wbase(W_BASE_xxx) 设定层基地址
 */
static inline void cnn_layer(int in_h, int in_w, int in_c, int k,
                             int oc, int stride, int pad, int shift, int relu,
                             const int8_t *x, const int32_t *b,
                             int8_t *y, int out_n)
{
    cnn_clear_ptrs();
    cnn_config(in_h, in_w, in_c, k, oc, stride, pad, shift, relu);
    cnn_load_input(x, in_h * in_w * in_c);
    /* cnn_load_weight 删除 */
    cnn_load_bias(b, oc);
    cnn_run();
    for (int i = 0; i < out_n; i++)
        y[i] = cnn_read_output(i);
}

#endif /* __CNN_H__ */

#endif /* CNN_H_ */
