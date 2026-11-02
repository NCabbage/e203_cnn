#------------------------------------------------------------------
# run_e203_bin.tcl
#   Load an E203 raw binary image over JTAG and run it.
#
#   IMAGE      : path to .bin (must be produced from ELF with objcopy)
#   ENTRY      : entry address, default 0x80000000
#
#   This avoids OpenOCD's ELF/CRC verify path, which is not compatible
#   with this E203 debug module and can hang after a successful load.
#------------------------------------------------------------------
if {![info exists IMAGE]} {
    error "IMAGE is not set"
}
if {![info exists ENTRY]} { set ENTRY 0x80000000 }

if {![file exists $IMAGE]} {
    puts "E203: IMAGE NOT FOUND: $IMAGE"
    shutdown
    return
}

halt
puts "E203: loading $IMAGE ([expr {[file size $IMAGE] / 1024}] KiB) at [format 0x%08x $ENTRY]"
if {[catch {load_image $IMAGE $ENTRY bin} err]} {
    puts "E203: LOAD FAILED: $err"
    shutdown
    return
}
puts "E203: load OK (verify skipped: RISC-V CRC verify is not supported by this target)"
reg pc $ENTRY
puts "E203: PC = [reg pc] -> resuming"
resume $ENTRY
puts "E203: RUNNING (JTAG released, core continues on its own)"
shutdown