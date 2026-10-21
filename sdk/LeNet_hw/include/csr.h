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

static inline uint32_t get_cycle(void) {
    uint32_t v;
    asm volatile ("csrr %0, 0xB00" : "=r"(v));
    return v;
}

#endif /* INCLUDE_CSR_H_ */
