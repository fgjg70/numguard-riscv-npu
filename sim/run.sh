#!/bin/sh
# ============================================================================
# run.sh - build every regression image, then run the FULL regression.
#
#   Authoritative entry point (11 tests, all of them):
#       python3 tools/fpga_workflow.py verify
#
#   What changed (W-05/W-06):
#     - the old script ran only tb_units + tb_core (2 of 11) and then printed an
#       unconditional green banner -> 18% coverage, all-pass look.
#     - a failed assembly step used to leave the OLD .hex in place (PITFALLS R1);
#       now every image is deleted first and a failed build aborts non-zero.
#
#   Usage: ./sim/run.sh [verify args, e.g. --force --only tb_units]
# ============================================================================
set -e
cd "$(dirname "$0")/.."

PY=python3
command -v python3 >/dev/null 2>&1 || PY=python

echo
echo "=== [1/2] rebuilding every regression image (delete-then-build) ==="
"$PY" tools/build_sw.py --all

echo
echo "=== [2/2] full regression (11 tests, via fpga_workflow.py verify) ==="
exec "$PY" tools/fpga_workflow.py verify "$@"
