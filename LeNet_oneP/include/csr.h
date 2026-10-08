/*
 * csr.h
 *
 *  Created on: 2026年10月6日
 *      Author: lenovo
 */

#ifndef INCLUDE_CSR_H_
#define INCLUDE_CSR_H_

#include <stdint.h>

// 读自定义 CSR
static inline uint32_t csr_read_mulcnt(void) {
    uint32_t v;
    asm volatile ("csrr %0, 0xCC0" : "=r"(v));
    return v;
}

static inline uint32_t csr_read_loadcnt(void) {
    uint32_t v;
    asm volatile ("csrr %0, 0xCC1" : "=r"(v));
    return v;
}

static inline uint32_t csr_read_storecnt(void) {
    uint32_t v;
    asm volatile ("csrr %0, 0xCC2" : "=r"(v));
    return v;
}

static inline uint32_t csr_read_stallcnt(void) {
    uint32_t v;
    asm volatile ("csrr %0, 0xCC3" : "=r"(v));
    return v;
}

// 写清零（如果 RTL 支持写通路）
static inline void csr_clear_mulcnt(void) {
    asm volatile ("csrw 0xCC0, zero");
}
static inline void csr_clear_loadcnt(void) {
    asm volatile ("csrw 0xCC1, zero");
}
static inline void csr_clear_storecnt(void) {
    asm volatile ("csrw 0xCC2, zero");
}
static inline void csr_clear_stallcnt(void) {
    asm volatile ("csrw 0xCC3, zero");
}

// mcycle 你已经有
static inline uint32_t get_cycle(void) {
    uint32_t v;
    asm volatile ("csrr %0, 0xB00" : "=r"(v));
    return v;
}

#endif /* INCLUDE_CSR_H_ */
