#include <stdio.h>
#include <stdint.h>

#define CNN_BASE_ADDR   0x40000000UL
#define CNN_REG_CTRL    (CNN_BASE_ADDR + 0x00)
#define CNN_REG_STATUS  (CNN_BASE_ADDR + 0x04)
#define CNN_REG_WDATA   (CNN_BASE_ADDR + 0x08)
#define CNN_REG_RDATA   (CNN_BASE_ADDR + 0x0C)

#define CNN_WRITE(addr, val)  (*(volatile uint32_t *)(addr) = (uint32_t)(val))
#define CNN_READ(addr)        (*(volatile uint32_t *)(addr))

int main(void)
{
    uint32_t val;

    printf("write 0x%08X = 0x12345678\n", (unsigned)CNN_REG_CTRL);
    CNN_WRITE(CNN_REG_CTRL, 0x12345678);

    val = CNN_READ(CNN_REG_CTRL);
    printf("read back = 0x%08X\n", val);

    printf("write 0x%08X = 0xDEADBEEF\n", (unsigned)CNN_REG_WDATA);
    CNN_WRITE(CNN_REG_WDATA, 0xDEADBEEF);

    val = CNN_READ(CNN_REG_WDATA);
    printf("read back = 0x%08X\n", val);

    printf("done\n");
    return 0;
}
