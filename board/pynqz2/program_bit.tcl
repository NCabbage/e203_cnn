if {![info exists ::env(E203_BIT)]} { error "Set E203_BIT to the .bit path" }
set bit [file normalize $::env(E203_BIT)]
open_hw_manager
connect_hw_server -url localhost:3121 -allow_non_jtag
set found_dev ""
foreach t [get_hw_targets -quiet] {
    current_hw_target $t
    catch {open_hw_target}
    foreach d [get_hw_devices -quiet] {
        if {[string match "*xc7z020*" $d]} { set found_dev $d; break }
    }
    if {$found_dev ne ""} break
    catch {close_hw_target}
}
if {$found_dev eq ""} { error "No xc7z020 device found" }
current_hw_device $found_dev
set_property PROGRAM.FILE $bit $found_dev
program_hw_devices $found_dev
puts "PROGRAM_OK: $bit"
catch {close_hw_target}
catch {disconnect_hw_server}
