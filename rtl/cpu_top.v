module cpu_top
(

  //----------------input clock----------------
  //  clk--->K17
  input wire clk,
  

  //----------------reset signal----------------
  //  rst_n--->M20
  input wire mcu_rst, //MCU_RESET


  // Dedicated QSPI interface
  // output wire qspi0_cs,
  // output wire qspi0_sck,
  // inout wire [3:0] qspi0_dq,
  /*
  GPIOA++FUNCTION:            GPIOB++FUNCTION:
      0--->PWM0_0                  PWM2_0
      1--->PWM0_1                  PWM2_1
      2--->PWM0_2                  PWM2_2
      3--->PWM0_3                  PWM2_3
      4--->PWM1_0                  PWM3_0
      5--->PWM1_1                  PWM3_1
      6--->PWM1_2                  PWM3_2
      7--->PWM1_3                  PWM3_3

      8--->QSPI1:SCK               QSPI2:SCK
      9--->QSPI1:CS                QSPI2:CS
      10--->QSPI1:DQ0              QSPI2:DQ0
      11--->QSPI1:DQ1              QSPI2:DQ1
      12--->QSPI1:DQ2              QSPI2:DQ2
      13--->QSPI1:DQ3              QSPI2:DQ3

      14--->I2C0:SCL               I2C1:SCL
      15--->I2C0:SDA               I2C1:SDA

      16--->UART0:RX               UART1:RX
      17--->UART0:TX               UART1:TX
      18--->UART2:RX
      19--->UART2:TX
  */                        

  //  RX--->N18
  //  TX--->N20
  inout wire uart0_rx,
  inout wire uart0_tx,

  // inout wire uart1_rx,
  // inout wire uart1_tx,

  // inout wire uart2_rx,
  // inout wire uart2_tx,

  //some leds
  //  led1---> T12
  //  led2---> U12
  //  led3---> V12
  //  led4---> W13
  inout wire gpioA_0,
  inout wire gpioA_1,
  inout wire gpioA_2,
  inout wire gpioA_3,

  // Key
  // PL_KEY2--->M19
  inout wire gpioA_4,

  // 40P GPIO
  inout wire gpioA_5, //P18
  inout wire gpioA_6, //P19
  inout wire gpioA_7, //R19
  
  //oled display
  inout wire gpioA_20, //OLED_DC        Y14
  inout wire gpioA_21, //OLED_RES       V16
  inout wire gpioA_22, //OLED_CLK       U18  
  inout wire gpioA_23, //OLED_SDIN      T17
  inout wire gpioA_24, //VBAT           R17
  inout wire gpioA_25, //VDD            W20



  //some GPIO B
  // inout wire gpioB_0, // W16
  // inout wire gpioB_1, // T16
  // inout wire gpioB_2, // R16
  // inout wire gpioB_3, // P16
  // inout wire gpioB_4, // W14
  // inout wire gpioB_5, // N17
  // inout wire gpioB_6, // W19
  // inout wire gpioB_7, // Y19


  // rv_jtag
  //  MCU_TDO--->
  //  MCU_TCK--->
  //  MCU_TDI--->
  //  MCU_TMS--->
  //  mcu_TCK
  //  mcu_TDO
  //  mcu_TMS
  //  mcu_RST
  //  mcu_TDI
  inout wire mcu_TCK,
  inout wire mcu_TDO,
  inout wire mcu_TMS,
  inout wire mcu_TDI

  //pmu_wakeup

  // inout wire pmu_paden,  //PMU_VDDPADEN
  // inout wire pmu_padrst, //PMU_VADDPARST
  // inout wire mcu_wakeup  //MCU_WAKE
);

aic_rv32 u_aic_rv32
(

  //----------------input clock----------------
  //  clk--->K17
  .clk		(clk		),
  

  //----------------reset signal----------------
  //  rst_n--->M20
  .mcu_rst	(mcu_rst	), //MCU_RESET


  // Dedicated QSPI interface
  // output wire qspi0_cs,
  // output wire qspi0_sck,
  // inout wire [3:0] qspi0_dq,
  /*
  GPIOA++FUNCTION:            GPIOB++FUNCTION:
      0--->PWM0_0                  PWM2_0
      1--->PWM0_1                  PWM2_1
      2--->PWM0_2                  PWM2_2
      3--->PWM0_3                  PWM2_3
      4--->PWM1_0                  PWM3_0
      5--->PWM1_1                  PWM3_1
      6--->PWM1_2                  PWM3_2
      7--->PWM1_3                  PWM3_3

      8--->QSPI1:SCK               QSPI2:SCK
      9--->QSPI1:CS                QSPI2:CS
      10--->QSPI1:DQ0              QSPI2:DQ0
      11--->QSPI1:DQ1              QSPI2:DQ1
      12--->QSPI1:DQ2              QSPI2:DQ2
      13--->QSPI1:DQ3              QSPI2:DQ3

      14--->I2C0:SCL               I2C1:SCL
      15--->I2C0:SDA               I2C1:SDA

      16--->UART0:RX               UART1:RX
      17--->UART0:TX               UART1:TX
      18--->UART2:RX
      19--->UART2:TX
  */                        

  //  RX--->N18
  //  TX--->N20
  .uart0_rx		(uart0_rx	),
  .uart0_tx		(uart0_tx	),

  // inout wire uart1_rx,
  // inout wire uart1_tx,

  // inout wire uart2_rx,
  // inout wire uart2_tx,

  //some leds
  //  led1---> T12
  //  led2---> U12
  //  led3---> V12
  //  led4---> W13
  .gpioA_0			(gpioA_0	),
  .gpioA_1			(gpioA_1	),
  .gpioA_2			(gpioA_2	),
  .gpioA_3			(gpioA_3	),

  // Key
  // PL_KEY2--->M19
  .gpioA_4			(gpioA_4	),

  // 40P GPIO
  .gpioA_5			(gpioA_5	), //P18
  .gpioA_6			(gpioA_6	), //P19
  .gpioA_7			(gpioA_7	), //R19
  
  //oled display
  .gpioA_20			(gpioA_20	), //OLED_DC        Y14
  .gpioA_21			(gpioA_21	), //OLED_RES       V16
  .gpioA_22			(gpioA_22	), //OLED_CLK       U18  
  .gpioA_23			(gpioA_23	), //OLED_SDIN      T17
  .gpioA_24			(gpioA_24	), //VBAT           R17
  .gpioA_25			(gpioA_25	), //VDD            W20



  //some GPIO B
  // inout wire gpioB_0, // W16
  // inout wire gpioB_1, // T16
  // inout wire gpioB_2, // R16
  // inout wire gpioB_3, // P16
  // inout wire gpioB_4, // W14
  // inout wire gpioB_5, // N17
  // inout wire gpioB_6, // W19
  // inout wire gpioB_7, // Y19


  // rv_jtag
  //  MCU_TDO--->
  //  MCU_TCK--->
  //  MCU_TDI--->
  //  MCU_TMS--->
  //  mcu_TCK
  //  mcu_TDO
  //  mcu_TMS
  //  mcu_RST
  //  mcu_TDI
  .mcu_TCK			(mcu_TCK	),
  .mcu_TDO			(mcu_TDO	),
  .mcu_TMS			(mcu_TMS	),
  .mcu_TDI			(mcu_TDI	)

  //pmu_wakeup

  // inout wire pmu_paden,  //PMU_VDDPADEN
  // inout wire pmu_padrst, //PMU_VADDPARST
  // inout wire mcu_wakeup  //MCU_WAKE
);

  cpu cpu_i(
		.DDR_addr(DDR_addr),
        .DDR_ba	(DDR_ba),
        .DDR_cas_n(DDR_cas_n),
        .DDR_ck_n(DDR_ck_n),
        .DDR_ck_p(DDR_ck_p),
        .DDR_cke(DDR_cke),
        .DDR_cs_n(DDR_cs_n),
        .DDR_dm(DDR_dm),
        .DDR_dq(DDR_dq),
        .DDR_dqs_n(DDR_dqs_n),
        .DDR_dqs_p(DDR_dqs_p),
        .DDR_odt(DDR_odt),
        .DDR_ras_n(DDR_ras_n),
        .DDR_reset_n(DDR_reset_n),
        .DDR_we_n(DDR_we_n),
        .FIXED_IO_ddr_vrn(FIXED_IO_ddr_vrn),
        .FIXED_IO_ddr_vrp(FIXED_IO_ddr_vrp),
        .FIXED_IO_mio(FIXED_IO_mio),
        .FIXED_IO_ps_clk(FIXED_IO_ps_clk),
        .FIXED_IO_ps_porb(FIXED_IO_ps_porb),
        .FIXED_IO_ps_srstb(FIXED_IO_ps_srstb)
		
);

endmodule