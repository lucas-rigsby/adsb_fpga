# cocotb block tests.  Run inside the OSS CAD Suite environment:
#   source ~/eda/oss-cad-suite/environment && make -C tb all
# Each block target re-invokes `sim` with its own toplevel, test module and build dir.

SIM            ?= icarus
TOPLEVEL_LANG  ?= verilog
RTL            := $(abspath ../rtl)

# cocotb from the OSS CAD Suite has a stale built-in libpython path
export LIBPYTHON_LOC ?= $(shell cocotb-config --libpython 2>/dev/null)
# tests import the reference model (tb/) and the packet formats (host/protocol.py)
export PYTHONPATH := $(abspath .):$(abspath ..):$(abspath ../host):$(PYTHONPATH)

.PHONY: all sim clean iq_mag chip_energy tick_line crc24_112 cand_eval adsb_emit dsp_core

all: iq_mag chip_energy tick_line crc24_112 cand_eval adsb_emit dsp_core

iq_mag:
	$(MAKE) sim TOPLEVEL=iq_mag COCOTB_TEST_MODULES=test_iq_mag \
	    SRCS="$(RTL)/iq_mag.v" SIM_BUILD=sim_build/iq_mag

chip_energy:
	$(MAKE) sim TOPLEVEL=chip_energy COCOTB_TEST_MODULES=test_chip_energy \
	    SRCS="$(RTL)/chip_energy.v" SIM_BUILD=sim_build/chip_energy

tick_line:
	$(MAKE) sim TOPLEVEL=tick_line COCOTB_TEST_MODULES=test_tick_line \
	    SRCS="$(RTL)/tick_line.v" SIM_BUILD=sim_build/tick_line

crc24_112:
	$(MAKE) sim TOPLEVEL=crc24_112 COCOTB_TEST_MODULES=test_crc24_112 \
	    SRCS="$(RTL)/crc24_112.v" SIM_BUILD=sim_build/crc24_112

cand_eval:
	$(MAKE) sim TOPLEVEL=cand_eval COCOTB_TEST_MODULES=test_cand_eval \
	    SRCS="$(RTL)/cand_eval.v" SIM_BUILD=sim_build/cand_eval

adsb_emit:
	$(MAKE) sim TOPLEVEL=adsb_emit COCOTB_TEST_MODULES=test_adsb_emit \
	    SRCS="$(RTL)/adsb_emit.v" SIM_BUILD=sim_build/adsb_emit

DSP_SRCS := $(addprefix $(RTL)/,dsp_core.v iq_mag.v chip_energy.v tick_line.v cand_eval.v crc24_112.v adsb_emit.v)

# The whole chain runs under Verilator (about 100x faster than Icarus here: the
# 17,220-bit delay line shifts every cycle). The simulator binary loads the
# Suite's libpython, whose dependencies are found relative to the executable; on
# macOS DYLD_* variables are stripped when /usr/bin/make starts a child, so the
# path is passed through env on the simulator's own command line.
dsp_core:
	$(MAKE) sim TOPLEVEL=dsp_core COCOTB_TEST_MODULES=test_dsp_core \
	    SRCS="$(DSP_SRCS)" SIM_BUILD=sim_build/dsp_core_vl SIM=verilator \
	    SIM_CMD_PREFIX="env DYLD_LIBRARY_PATH=$(dir $(shell which yosys))../lib"

ifdef TOPLEVEL
COCOTB_TOPLEVEL := $(TOPLEVEL)
VERILOG_SOURCES := $(SRCS)
COCOTB_RESULTS_FILE := $(SIM_BUILD)/results.xml
include $(shell cocotb-config --makefiles)/Makefile.sim
endif

clean::
	rm -rf sim_build __pycache__ results.xml
