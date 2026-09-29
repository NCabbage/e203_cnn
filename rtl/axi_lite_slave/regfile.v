module regfile # (
	parameter AW = 32,
	parameter DW = 32
)
(
	input					clk			,
	input					rst_n		,

	input					wr_en		,
	input	[AW-1:0]		wr_addr		,
	input	[DW-1:0]		wr_data 	,
	input	[(DW/8)-1:0]	wr_mask 	,
	input	[AW-1:0]		rd_addr		,
	
	output	[DW-1:0]		rd_data		

);

reg		[DW-1:0]	test_reg	[0:15]	;
wire	[4:0]		wr_idx				;
wire	[4:0]		rd_idx				;
reg		[DW-1:0]	mask_data			;

assign wr_idx = wr_addr[5:2];
assign rd_idx = rd_addr[5:2];

always@(*) begin
	if(wr_mask[0]) mask_data[7:0] = wr_data[7:0];
	if(wr_mask[1]) mask_data[15:8] = wr_data[15:8];
	if(wr_mask[2]) mask_data[23:16] = wr_data[23:16];
	if(wr_mask[3]) mask_data[31:24] = wr_data[31:24];
end

integer i;

always@(posedge clk or negedge rst_n)
	if(!rst_n)
		for(i=0;i<16;i=i+1)
			test_reg[i] <= 0;
	else if(wr_en)
		test_reg[wr_idx] <= mask_data;

assign rd_data = test_reg[rd_idx];

endmodule