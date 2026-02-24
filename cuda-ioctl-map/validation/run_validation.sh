#!/usr/bin/env bash
# run_validation.sh — end-to-end replay correctness check.
#
# Steps:
#   1. Run real cu_init, snapshot driver state.
#   2. Run replay (kept alive until snapshotted), snapshot driver state.
#   3. Compare snapshots structurally.
#
# Run from the cuda-ioctl-map/ directory:
#   bash validation/run_validation.sh [capture.jsonl] [handle_offsets.json]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

CAPTURE="${1:-$ROOT_DIR/sniffed/cu_init.jsonl}"
OFFSETS="${2:-$ROOT_DIR/intercept/handle_offsets.json}"

SNAPSHOT_REAL="$SCRIPT_DIR/snapshot_real.txt"
SNAPSHOT_REPLAY="$SCRIPT_DIR/snapshot_replay.txt"

echo "[1/4] Running real cu_init..."
"$ROOT_DIR/programs/cu_init"
"$ROOT_DIR/tools/snapshot_driver_state.sh" "$SNAPSHOT_REAL"

echo ""
echo "[2/4] Running replay (kept alive for snapshot)..."
rm -f "$ROOT_DIR/replay.ready"

"$ROOT_DIR/replay/replay" "$CAPTURE" "$OFFSETS" &
REPLAY_PID=$!

# Poll for the sentinel file (replay.c writes it just before exit)
for i in $(seq 1 100); do
    if [ -f "$ROOT_DIR/replay.ready" ]; then
        break
    fi
    sleep 0.1
done

if [ ! -f "$ROOT_DIR/replay.ready" ]; then
    echo "ERROR: replay did not write replay.ready within 10 s" >&2
    kill "$REPLAY_PID" 2>/dev/null || true
    exit 1
fi

"$ROOT_DIR/tools/snapshot_driver_state.sh" "$SNAPSHOT_REPLAY" "$REPLAY_PID"
wait "$REPLAY_PID" && REPLAY_EXIT=0 || REPLAY_EXIT=$?
rm -f "$ROOT_DIR/replay.ready"

if [ "$REPLAY_EXIT" -ne 0 ]; then
    echo "WARNING: replay exited with code $REPLAY_EXIT (some ioctls failed)" >&2
fi

echo ""
echo "[3/4] Comparing snapshots..."
python3 "$ROOT_DIR/tools/compare_snapshots.py" \
    "$SNAPSHOT_REAL" \
    "$SNAPSHOT_REPLAY"

echo ""
echo "[4/4] Done."
