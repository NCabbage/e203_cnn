#============================================================================
#
#
#	Bing
#
#
#----------------------------------------------------------------------------



#=========================== Sys Clock & rstn ================================
## Clock Signal     50M
set_property -dict { PACKAGE_PIN H16    IOSTANDARD LVCMOS33 } [get_ports { clk }]; #K17
create_clock -add -name sys_clk_pin -period 20.00 -waveform {0 10} [get_ports clk]

## mcu Rst_n
set_property -dict { PACKAGE_PIN H17  IOSTANDARD LVCMOS33 } [get_ports { mcu_rst }]; #PL_KEY1   M20
#----------------------------------------------------------------------------




#=========================== Debug JTAG ======================================

set_property CLOCK_DEDICATED_ROUTE FALSE [get_nets IOBUF_jtag_TCK/O]

# mcu_TCK
# mcu_TDO
# mcu_TMS
# rst
# mcu_TDI
# JTAG Debug Port
set_property -dict { PACKAGE_PIN M17  IOSTANDARD LVCMOS33 } [get_ports { mcu_TCK }]; #V20
set_property -dict { PACKAGE_PIN J19  IOSTANDARD LVCMOS33 } [get_ports { mcu_TDO }]; #U20
set_property -dict { PACKAGE_PIN F19  IOSTANDARD LVCMOS33 } [get_ports { mcu_TMS }]; #T20
set_property -dict { PACKAGE_PIN D19  IOSTANDARD LVCMOS33 } [get_ports { mcu_TDI }]; #P20


#------------------------ End of Debug JTAG ----------------------------------





#=============================== UART 0 ======================================

set_property -dict { PACKAGE_PIN B20   IOSTANDARD LVCMOS33 } [get_ports { uart0_tx }]; #N20
set_property -dict { PACKAGE_PIN B19   IOSTANDARD LVCMOS33 } [get_ports { uart0_rx }]; #N18

#---------------------------- End of UART 0 --------------------------------


#LED1       ---> T12
#LED2       ---> U12
#LED3       ---> V12
#LED4       ---> W13
#PL_KEY1    ---> M20
#PL_KEY2    ---> M19



## GPIOA
#LEDs
set_property -dict { PACKAGE_PIN L17   IOSTANDARD LVCMOS33 } [get_ports { gpioA_0 }]; # T12
set_property -dict { PACKAGE_PIN H18   IOSTANDARD LVCMOS33 } [get_ports { gpioA_1 }]; # U12
set_property -dict { PACKAGE_PIN N17   IOSTANDARD LVCMOS33 } [get_ports { gpioA_2 }]; # V12 tuozhan
set_property -dict { PACKAGE_PIN P15   IOSTANDARD LVCMOS33 } [get_ports { gpioA_3 }]; # W13 tuozhan

#KEY
set_property -dict { PACKAGE_PIN L16   IOSTANDARD LVCMOS33 } [get_ports { gpioA_4 }]; # M19

# 40P
set_property -dict { PACKAGE_PIN R16   IOSTANDARD LVCMOS33 } [get_ports { gpioA_5 }]; # P18
set_property -dict { PACKAGE_PIN T16   IOSTANDARD LVCMOS33 } [get_ports { gpioA_6 }]; # P19 tuozhan
set_property -dict { PACKAGE_PIN U14   IOSTANDARD LVCMOS33 } [get_ports { gpioA_7 }]; # R18 tuozhan


## OLED Display
set_property -dict { PACKAGE_PIN V15   IOSTANDARD LVCMOS33 } [get_ports { gpioA_20 }]; #OLED_DC          Y14
set_property -dict { PACKAGE_PIN W14   IOSTANDARD LVCMOS33 } [get_ports { gpioA_21 }]; #OLED_RES         V16
set_property -dict { PACKAGE_PIN V16   IOSTANDARD LVCMOS33 } [get_ports { gpioA_22 }]; #OLED_CLK   D0    U18
set_property -dict { PACKAGE_PIN V17   IOSTANDARD LVCMOS33 } [get_ports { gpioA_23 }]; #OLED_SDIN  D1    T17
set_property -dict { PACKAGE_PIN Y18   IOSTANDARD LVCMOS33 } [get_ports { gpioA_24 }]; #VBAT             R17
set_property -dict { PACKAGE_PIN W18   IOSTANDARD LVCMOS33 } [get_ports { gpioA_25 }]; #VDD              W20

#############timing

set_clock_groups -asynchronous \
    -group [get_clocks CLK_O_8M388_PLL] \
    -group [get_clocks CLK_O_16M_PLL]

set_clock_groups -asynchronous \
    -group [get_clocks CLK_O_8M388_PLL] \
    -group [get_clocks CLK_O_16M_PLL_1]

set_clock_groups -asynchronous \
    -group [get_clocks CLK_O_8M388_PLL_1] \
    -group [get_clocks CLK_O_16M_PLL]

set_clock_groups -asynchronous \
    -group [get_clocks CLK_O_8M388_PLL_1] \
    -group [get_clocks CLK_O_16M_PLL_1]
