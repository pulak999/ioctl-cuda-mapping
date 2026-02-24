#!/usr/bin/env bash
# snapshot_driver_state.sh — capture GPU driver state to a file.
#
# Usage:
#   tools/snapshot_driver_state.sh <output_file> [pid]
#
# If pid is given, also counts open /dev/nvidia* fds in that process.
set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 <output_file> [pid]" >&2
    exit 1
fi

OUT="$1"
TARGET_PID="${2:-}"

{
    echo "=== nvidia-smi ==="
    nvidia-smi -q 2>/dev/null || echo "nvidia-smi unavailable"

    echo ""
    echo "=== /proc/driver/nvidia/gpus ==="
    for f in /proc/driver/nvidia/gpus/*/information; do
        if [ -f "$f" ]; then
            echo "--- $f ---"
            cat "$f" 2>/dev/null || echo "(unreadable)"
        fi
    done

    echo ""
    echo "=== /proc/driver/nvidia/params ==="
    if [ -f /proc/driver/nvidia/params ]; then
        cat /proc/driver/nvidia/params 2>/dev/null || echo "(unreadable)"
    else
        echo "(not present)"
    fi

    if [ -n "$TARGET_PID" ]; then
        echo ""
        echo "=== open nvidia fds in pid $TARGET_PID ==="
        ls -la /proc/"$TARGET_PID"/fd 2>/dev/null \
            | grep -c nvidia || echo "0"
    fi
} > "$OUT"

echo "[snapshot] wrote $OUT"
