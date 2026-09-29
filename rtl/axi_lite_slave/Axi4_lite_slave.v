//**************************************************************************
// *** file name   : Axi4_lite_slave.v
// *** version     : 1.0
// *** Description : AXI4-Lite slave interface
// *** Blogs       : https://www.cnblogs.com/WenGalois123/
// *** Author      : Galois_V
// *** Date        : 2022.3.29
// *** Changes     :
//**************************************************************************

module Axi4_lite_slave#(
    parameter AW = 32,
    parameter DW = 32 
)
(
    input        wire                 i_s_axi_aclk            ,
    input        wire                 i_s_axi_aresetn         ,
    input        wire    [AW-1:0]     i_s_axi_awaddr          ,
    input        wire    [2:0]        i_s_axi_awprot          ,
    input        wire                 i_s_axi_awvalid         ,
    output       reg                  o_s_axi_awready         ,
    input        wire    [DW-1:0]     i_s_axi_wdata           ,
    input        wire    [(DW/8)-1:0] i_s_axi_wstrb           ,
    input        wire                 i_s_axi_wvalid          ,
    output       reg                  o_s_axi_wready          ,

    output       wire    [1:0]        o_s_axi_bresp           ,
    output       reg                  o_s_axi_bvalid          ,
    input        wire                 i_s_axi_bready          ,

    input        wire    [AW-1:0]     i_s_axi_araddr          ,
    input        wire    [2:0]        i_s_axi_arprot          ,
    input        wire                 i_s_axi_arvalid         ,
    output       reg                  o_s_axi_arready         ,
    output       reg     [DW-1:0]     o_s_axi_rdata           ,
    output       wire    [1:0]        o_s_axi_rresp           ,
    output       reg                  o_s_axi_rvalid          ,
    input        wire                 i_s_axi_rready          ,

    output       reg     [AW-1:0]     o_ctrl_wr_addr          ,
    output       wire                 o_ctrl_wr_en            ,
    output       wire    [DW-1:0]     o_ctrl_wr_data          ,
    output       wire    [(DW/8)-1:0] o_ctrl_wr_mask          ,
    output       reg     [AW-1:0]     o_ctrl_rd_addr          ,
    input        wire    [DW-1:0]     i_ctrl_rd_data
);

    reg                                r_wr_en;
    reg                                r_rd_en;
    wire                               w_raddr_en;
/******************************************************************************\
Write Address operation
\******************************************************************************/
    always@(posedge i_s_axi_aclk)
    begin
        if(~i_s_axi_aresetn)
        begin
            r_wr_en <= 1'b1;
        end
        else if(o_ctrl_wr_en)
        begin
            r_wr_en <= 1'b0;
        end
        else if(o_s_axi_bvalid & i_s_axi_bready)
        begin
            r_wr_en <= 1'b1;
        end
    end

    always@(posedge i_s_axi_aclk)
    begin
        if(~i_s_axi_aresetn)
        begin
            o_s_axi_awready <= 'd0;
        end
        else if(~o_s_axi_awready & i_s_axi_wvalid & i_s_axi_awvalid & r_wr_en)
        begin
            o_s_axi_awready <= 1'b1;
        end
        else
        begin
            o_s_axi_awready <= 'd0;
        end
    end

    always@(posedge i_s_axi_aclk)
    begin
        if(~i_s_axi_aresetn)
        begin
            o_ctrl_wr_addr <= 'd0;
        end
        else if(~o_s_axi_awready & i_s_axi_awvalid & i_s_axi_wvalid)
        begin
            o_ctrl_wr_addr <= i_s_axi_awaddr;
        end
    end
/******************************************************************************\
Write data operation
\******************************************************************************/
    always@(posedge i_s_axi_aclk)
    begin
        if(~i_s_axi_aresetn)
        begin
            o_s_axi_wready <= 'd0;
        end
        else if(~o_s_axi_wready & i_s_axi_wvalid & i_s_axi_awvalid & r_wr_en)
        begin
            o_s_axi_wready <= 1'b1;
        end
        else
        begin
            o_s_axi_wready <= 'd0;
        end
    end

    assign o_ctrl_wr_data = i_s_axi_wdata;
    assign o_ctrl_wr_mask = i_s_axi_wstrb;
    assign o_ctrl_wr_en = o_s_axi_awready & i_s_axi_awvalid & i_s_axi_wvalid & o_s_axi_wready;

/******************************************************************************\
write response and response
\******************************************************************************/
    always@(posedge i_s_axi_aclk)
    begin
        if(~i_s_axi_aresetn)
        begin
            o_s_axi_bvalid <= 'd0;
        end
        else if(~o_s_axi_bvalid & o_ctrl_wr_en)
        begin
            o_s_axi_bvalid <= 1'b1;
        end
        else if(o_s_axi_bvalid & i_s_axi_bready)
        begin
            o_s_axi_bvalid <= 'd0;
        end
    end

/******************************************************************************\
Read Address operation
\******************************************************************************/
    always@(posedge i_s_axi_aclk)
    begin
        if(~i_s_axi_aresetn)
        begin
            r_rd_en <= 1'b1;
        end
        else if(w_raddr_en)
        begin
            r_rd_en <= 1'b0;
        end
        else if(o_s_axi_rvalid & i_s_axi_rready)
        begin
            r_rd_en <= 1'b1;
        end
    end

    always@(posedge i_s_axi_aclk)
    begin
        if(~i_s_axi_aresetn)
        begin
            o_s_axi_arready <= 'd0;
        end
        else if(~o_s_axi_arready & i_s_axi_arvalid & r_rd_en)
        begin
            o_s_axi_arready <= 1'b1;
        end
        else
        begin
            o_s_axi_arready <= 'd0;
        end
    end

    always@(posedge i_s_axi_aclk)
    begin
        if(~i_s_axi_aresetn)
        begin
            o_ctrl_rd_addr <= 'd0;
        end
        else if(~o_s_axi_arready & i_s_axi_arvalid)
        begin
            o_ctrl_rd_addr <= i_s_axi_araddr;
        end
    end

    assign w_raddr_en = o_s_axi_arready & i_s_axi_arvalid & (~o_s_axi_rvalid);
/******************************************************************************\
Read data operation
\******************************************************************************/
    always@(posedge i_s_axi_aclk)
    begin
        if(~i_s_axi_aresetn)
        begin
            o_s_axi_rvalid <= 'd0;
        end
        else if(w_raddr_en)
        begin
            o_s_axi_rvalid <= 1'b1;
        end
        else if(o_s_axi_rvalid & i_s_axi_rready)
        begin
            o_s_axi_rvalid <= 'd0;
        end
    end

    always@(posedge i_s_axi_aclk)
    begin
        if(~i_s_axi_aresetn)
        begin
            o_s_axi_rdata <= 'd0;
        end
        else if(w_raddr_en)
        begin
            o_s_axi_rdata <= i_ctrl_rd_data;
        end
    end


    assign o_s_axi_rresp = 2'b00;
    assign o_s_axi_bresp = 2'b00;

endmodule