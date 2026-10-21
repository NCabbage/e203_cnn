#include <stdio.h>
#include "hbird_sdk_soc.h"

#define LED_PIN     (0)  // gpioA_0

int main(void)
{
    int i = 0;
    // PADDIR：把gpioA_0设置成输出
    GPIOA->PADDIR |= (1 << LED_PIN);

    while(1)
    {
        printf("%d: Hello Zynq7010, LED0 toggle\r\n",i);
        i++;
        // 翻转PADOUT寄存器的bit0，控制gpioA_0电平
        GPIOA->PADOUT = i % 2;
        for(int j=0;j<=10000000;j++);
    }
    return 0;
}
