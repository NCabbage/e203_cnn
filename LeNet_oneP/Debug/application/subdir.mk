################################################################################
# Automatically-generated file. Do not edit!
################################################################################

# Add inputs and outputs from these tool invocations to the build variables 
C_SRCS += \
../application/main.c 

OBJS += \
./application/main.o 

C_DEPS += \
./application/main.d 


# Each subdirectory must supply rules for building sources it contributes
application/%.o: ../application/%.c
	@echo 'Building file: $<'
	@echo 'Invoking: GNU RISC-V Cross C Compiler'
	riscv-nuclei-elf-gcc -march=rv32imac -mabi=ilp32 -mcmodel=medany -mno-save-restore -O2 -ffunction-sections -fdata-sections -fno-common  -g -D__IDE_RV_CORE=null -DSOC_HBIRDV2 -DDOWNLOAD_MODE=DOWNLOAD_MODE_ILM -DDOWNLOAD_MODE_STRING=\"ILM\" -DBOARD_DDR200T -I"D:\fpgafile\e203_cnn\sdk\LeNet_oneP\hbird_sdk\NMSIS\Core\Include" -I"D:\fpgafile\e203_cnn\sdk\LeNet_oneP\hbird_sdk\SoC\hbirdv2\Common\Include" -I"D:\fpgafile\e203_cnn\sdk\LeNet_oneP\hbird_sdk\SoC\hbirdv2\Board\ddr200t\Include" -I"D:\fpgafile\e203_cnn\sdk\LeNet_oneP\application" -std=gnu11 -MMD -MP -MF"$(@:%.o=%.d)" -MT"$(@)" -c -o "$@" "$<"
	@echo 'Finished building: $<'
	@echo ' '


