# Rebuild the PYNQ-Z2 E203/CNN project from an existing Vivado project.
# Required: set E203_XPR to the project .xpr before launching Vivado.
set script_dir [file normalize [file dirname [info script]]]
set repo_root  [file normalize [file join $script_dir ".." ".."]]
if {![info exists ::env(E203_XPR)]} { error "Set E203_XPR to the Vivado .xpr path" }
set xpr [file normalize $::env(E203_XPR)]
set rom_xci [file normalize [file join $repo_root "ip" "rom_all_tap" "rom_all_tap.xci"]]
set rom_coe [file normalize [file join $repo_root "py" "rom_all_tap.coe"]]
set out_dir [file normalize [file join $repo_root "docs" "reports" "2026-10-30_第八章复现验证" "build"]]
file mkdir $out_dir
open_project $xpr
set project_part [get_property PART [current_project]]
puts "PROJECT_PART: $project_part"
if {[llength [get_files -quiet rom_all_tap.xci]] == 0} {
    add_files -norecurse [list $rom_xci]
    update_compile_order -fileset sources_1
}
set ip [get_ips -quiet rom_all_tap]
if {[llength $ip] == 0} { error "rom_all_tap IP not found" }
if {[get_property IS_VERSION_LOCKED $ip]} { upgrade_ip $ip; set ip [get_ips -quiet rom_all_tap] }
if {[get_property PART $ip] ne $project_part} { set_property PART $project_part $ip }
set_property CONFIG.Coe_File $rom_coe $ip
puts "ROM_IP_PART: [get_property PART $ip]"
reset_target all $ip
generate_target all $ip -force
set ip_run_name "rom_all_tap_synth_1"
if {[llength [get_runs -quiet $ip_run_name]] == 0} { error "IP synthesis run not found" }
reset_run $ip_run_name
launch_runs $ip_run_name -jobs 4
wait_on_run $ip_run_name
if {[get_property PROGRESS [get_runs $ip_run_name]] ne "100%"} { error "ROM IP synthesis failed" }
reset_run synth_1
launch_runs synth_1 -jobs 4
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} { error "Top-level synthesis failed" }
set impl [get_runs impl_1]
catch { set_property STEPS.OPT_DESIGN.ARGS.DIRECTIVE Explore $impl }
catch { set_property STEPS.PLACE_DESIGN.ARGS.DIRECTIVE Explore $impl }
catch { set_property STEPS.PHYS_OPT_DESIGN.ARGS.DIRECTIVE Explore $impl }
catch { set_property STEPS.ROUTE_DESIGN.ARGS.DIRECTIVE Explore $impl }
reset_run impl_1
launch_runs impl_1 -to_step write_bitstream -jobs 4
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} { error "Implementation/bitstream failed" }
open_run impl_1
report_utilization -file [file join $out_dir "utilization.rpt"]
report_timing_summary -file [file join $out_dir "timing.rpt"]
puts "TIMING WNS=[get_property STATS.WNS [get_runs impl_1]] WHS=[get_property STATS.WHS [get_runs impl_1]]"
puts "REPRO_BUILD_OK"
close_project
