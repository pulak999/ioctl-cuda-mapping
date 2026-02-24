#!/usr/bin/env bash
# collect.sh — build the interposer and run all four CUDA programs under it.
# Output: sniffed/{cu_init,cu_device_get,cu_ctx_create,cu_ctx_destroy}.jsonl
#
# Run from the cuda-ioctl-map/ directory:
#   bash intercept/collect.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "[collect] building libnv_sniff.so..."
make -C "$SCRIPT_DIR" --no-print-directory

mkdir -p "$ROOT_DIR/sniffed"

for step in cu_init cu_device_get cu_ctx_create cu_ctx_destroy; do
    echo "[collect] collecting: $step"
    NV_SNIFF_LOG="$ROOT_DIR/sniffed/${step}.jsonl" \
    LD_PRELOAD="$SCRIPT_DIR/libnv_sniff.so" \
    "$ROOT_DIR/programs/${step}"
    echo "[collect] done: sniffed/${step}.jsonl ($(wc -l < "$ROOT_DIR/sniffed/${step}.jsonl") lines)"
done

echo "[collect] all done."
