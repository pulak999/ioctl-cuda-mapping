#!/usr/bin/env bash
# collect_two_runs.sh — capture two independent cu_init traces for handle offset discovery.
# Outputs: sniffed/cu_init_a.jsonl and sniffed/cu_init_b.jsonl
#
# Run from the cuda-ioctl-map/ directory:
#   bash tools/collect_two_runs.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
INTERCEPT_DIR="$ROOT_DIR/intercept"

echo "[two-runs] building libnv_sniff.so..."
make -C "$INTERCEPT_DIR" --no-print-directory

mkdir -p "$ROOT_DIR/sniffed"

echo "[two-runs] collecting run A..."
NV_SNIFF_LOG="$ROOT_DIR/sniffed/cu_init_a.jsonl" \
LD_PRELOAD="$INTERCEPT_DIR/libnv_sniff.so" \
"$ROOT_DIR/programs/cu_init"

echo "[two-runs] collecting run B..."
NV_SNIFF_LOG="$ROOT_DIR/sniffed/cu_init_b.jsonl" \
LD_PRELOAD="$INTERCEPT_DIR/libnv_sniff.so" \
"$ROOT_DIR/programs/cu_init"

A_LINES=$(wc -l < "$ROOT_DIR/sniffed/cu_init_a.jsonl")
B_LINES=$(wc -l < "$ROOT_DIR/sniffed/cu_init_b.jsonl")
echo "[two-runs] run A: $A_LINES lines"
echo "[two-runs] run B: $B_LINES lines"

if [ "$A_LINES" -ne "$B_LINES" ]; then
    echo "[two-runs] WARNING: line counts differ ($A_LINES vs $B_LINES) — handle offset discovery may be unreliable." >&2
fi

echo "[two-runs] done."
