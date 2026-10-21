-- Copyright 1986-2019 Xilinx, Inc. All Rights Reserved.
-- --------------------------------------------------------------------------------
-- Tool Version: Vivado v.2019.1 (win64) Build 2552052 Fri May 24 14:49:42 MDT 2019
-- Date        : Sat Sep 26 16:01:58 2026
-- Host        : DESKTOP-PMV5OJJ running 64-bit major release  (build 9200)
-- Command     : write_vhdl -force -mode synth_stub d:/fpgafile/e203_cnn/ip/PLL/PLL_stub.vhdl
-- Design      : PLL
-- Purpose     : Stub declaration of top-level module interface
-- Device      : xc7z020clg400-2
-- --------------------------------------------------------------------------------
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;

entity PLL is
  Port ( 
    CLK_O_16M : out STD_LOGIC;
    CLK_O_8M388 : out STD_LOGIC;
    resetn : in STD_LOGIC;
    locked : out STD_LOGIC;
    CLK_I_50M : in STD_LOGIC
  );

end PLL;

architecture stub of PLL is
attribute syn_black_box : boolean;
attribute black_box_pad_pin : string;
attribute syn_black_box of stub : architecture is true;
attribute black_box_pad_pin of stub : architecture is "CLK_O_16M,CLK_O_8M388,resetn,locked,CLK_I_50M";
begin
end;
