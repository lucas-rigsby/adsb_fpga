# cocotb block tests.  Run inside the OSS CAD Suite environment:
#   source ~/eda/oss-cad-suite/environment && make -C tb all
# Each block target re-invokes `sim` with its own toplevel, test module and build dir.

SIM            ?= icarus
TOPLEVEL_LANG  ?= verilog
RTL            := $(abspath ../rtl)

# cocotb from the OSS CAD Suite has a stale built-in libpython path
export LIBPYTHON_LOC ?= $(shell cocotb-config --libpython 2>/dev/null)
# tests import the reference model (tb/) and host/protocol.py (repo root)
export PYTHONPATH := $(abspath .):$(abspath ..):$(PYTHONPATH)

.PHONY: all sim clean iq_mag chip_energy tick_line

all: iq_mag chip_energy tick_line

iq_mag:
	$(MAKE) sim TOPLEVEL=iq_mag COCOTB_TEST_MODULES=test_iq_mag \
	    SRCS="$(RTL)/iq_mag.v" SIM_BUILD=sim_build/iq_mag

chip_energy:
	$(MAKE) sim TOPLEVEL=chip_energy COCOTB_TEST_MODULES=test_chip_energy \
	    SRCS="$(RTL)/chip_energy.v" SIM_BUILD=sim_build/chip_energy

tick_line:
	$(MAKE) sim TOPLEVEL=tick_line COCOTB_TEST_MODULES=test_tick_line \
	    SRCS="$(RTL)/tick_line.v" SIM_BUILD=sim_build/tick_line

ifdef TOPLEVEL
COCOTB_TOPLEVEL := $(TOPLEVEL)
VERILOG_SOURCES := $(SRCS)
COCOTB_RESULTS_FILE := $(SIM_BUILD)/results.xml
include $(shell cocotb-config --makefiles)/Makefile.sim
endif

clean::
	rm -rf sim_build __pycache__ results.xml
