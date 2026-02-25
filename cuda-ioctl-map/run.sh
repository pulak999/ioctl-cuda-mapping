#!/usr/bin/env bash
# run.sh — end-to-end: compile → capture → replay
#
# Usage:
#   bash run.sh programs/matmul.cu        # compile .cu, capture, replay
#   bash run.sh programs/matmul           # already compiled: capture, replay
#   bash run.sh sniffed/matmul.jsonl      # already captured: replay only
#
# Options:
#   -v          verbose replay (DEBUG logging)
#   -c          capture only (skip replay)
#   -r          replay only (skip capture; requires existing .jsonl)
#
# Run from the cuda-ioctl-map/ directory.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

NVCC="${NVCC:-/usr/local/cuda-12.5/bin/nvcc}"
NVCCFLAGS="${NVCCFLAGS:--arch=native -O0 -lcuda}"
INTERCEPT="$SCRIPT_DIR/intercept"
SNIFF_LIB="$INTERCEPT/libnv_sniff.so"
OFFSETS="$INTERCEPT/handle_offsets.json"

VERBOSE=""
CAPTURE_ONLY=false
REPLAY_ONLY=false

# ── Parse flags ──
while getopts "vcr" opt; do
    case "$opt" in
        v) VERBOSE="-v" ;;
        c) CAPTURE_ONLY=true ;;
        r) REPLAY_ONLY=true ;;
        *) echo "Usage: $0 [-v] [-c|-r] <file.cu | binary | capture.jsonl>" >&2; exit 1 ;;
    esac
done
shift $((OPTIND - 1))

if [ $# -lt 1 ]; then
    echo "Usage: $0 [-v] [-c|-r] <file.cu | binary | capture.jsonl>" >&2
    echo "" >&2
    echo "Examples:" >&2
    echo "  bash run.sh programs/matmul.cu          # full pipeline" >&2
    echo "  bash run.sh programs/matmul             # capture + replay" >&2
    echo "  bash run.sh sniffed/matmul.jsonl        # replay only" >&2
    echo "  bash run.sh -v programs/vector_add.cu   # verbose replay" >&2
    echo "  bash run.sh -c programs/matmul.cu       # capture only" >&2
    exit 1
fi

INPUT="$1"

# ── Determine what we're working with ──
BINARY=""
CAPTURE=""
NAME=""

if [[ "$INPUT" == *.jsonl ]]; then
    # Already a capture file — replay only
    CAPTURE="$INPUT"
    NAME="$(basename "$CAPTURE" .jsonl)"
    REPLAY_ONLY=true
elif [[ "$INPUT" == *.cu ]]; then
    # Source file — compile first
    NAME="$(basename "$INPUT" .cu)"
    BINARY="programs/$NAME"

    echo "━━━ Compile ━━━"
    echo "  $NVCC $NVCCFLAGS -o $BINARY $INPUT"
    $NVCC $NVCCFLAGS -o "$BINARY" "$INPUT"
    echo "  → $BINARY"
    echo ""
else
    # Assume it's a compiled binary
    BINARY="$INPUT"
    NAME="$(basename "$BINARY")"
fi

# ── Build sniffer if needed ──
if [ "$REPLAY_ONLY" = false ]; then
    if [ ! -f "$SNIFF_LIB" ]; then
        echo "━━━ Build sniffer ━━━"
        make -C "$INTERCEPT" --no-print-directory
        echo ""
    fi
fi

# ── Capture ──
if [ "$REPLAY_ONLY" = false ]; then
    CAPTURE="sniffed/${NAME}.jsonl"
    mkdir -p sniffed

    echo "━━━ Capture ━━━"
    echo "  LD_PRELOAD=$SNIFF_LIB $BINARY"
    NV_SNIFF_LOG="$CAPTURE" LD_PRELOAD="$SNIFF_LIB" "$BINARY" 2>/dev/null || true
    LINES=$(wc -l < "$CAPTURE")
    IOCTLS=$(python3 -c "
import json
events = [json.loads(l) for l in open('$CAPTURE')]
print(sum(1 for e in events if e['type']=='ioctl'))
")
    echo "  → $CAPTURE ($LINES lines, $IOCTLS ioctls)"
    echo ""
fi

# ── Replay ──
if [ "$CAPTURE_ONLY" = false ]; then
    echo "━━━ Replay ━━━"
    echo "  python3 replay/replay.py $VERBOSE $CAPTURE"
    echo ""
    python3 replay/replay.py $VERBOSE "$CAPTURE"
fi
