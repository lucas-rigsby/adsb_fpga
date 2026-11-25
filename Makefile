# adsb_fpga: convenience targets. Run inside the OSS CAD Suite environment:
#   source ~/eda/oss-cad-suite/environment      (the `eda` alias)
#
#   make test     all cocotb block tests
#   make lint     Verilator -Wall on every RTL file
#   make formal   SymbiYosys prove + cover for every block
#   make model    reference-model doctests + self-test
#   make trace    spec traceability report
#   make all      model lint test formal

BLOCKS := iq_mag chip_energy tick_line
RTL    := $(addprefix rtl/,$(addsuffix .v,$(BLOCKS)))

.PHONY: all test lint formal model trace clean

all: model lint test formal

model:
	python3 -m doctest tb/adsb_ref.py host/protocol.py
	python3 tb/adsb_ref.py

test:
	$(MAKE) -C tb all

lint:
	@for b in $(BLOCKS); do echo "lint $$b"; \
	    verilator --lint-only -Wall rtl/$$b.v --top-module $$b || exit 1; done

formal:
	@for b in $(BLOCKS); do (cd formal && sby -f $$b.sby) || exit 1; done

trace:
	python3 ~/.claude/skills/spec/spec_trace.py

clean:
	$(MAKE) -C tb clean
	rm -rf $(foreach b,$(BLOCKS),formal/$(b)_prove formal/$(b)_cover)
