#!/usr/bin/env python3
"""
compare_snapshots.py — structurally diff two driver state snapshots.

Strips:
  - Lines containing hex handles / addresses (0x[0-9a-fA-F]{6,})
  - Lines with bare PID-like numbers
  - Timestamp / date lines

Compares GPU count, memory used, device names, and other structural fields.

Exit 0 = PASS (snapshots are structurally identical).
Exit 1 = FAIL (structural differences found).

Usage:
    python3 tools/compare_snapshots.py snapshot_real.txt snapshot_replay.txt
"""

import re
import sys
import difflib
from pathlib import Path


# ── Patterns to strip / normalise before comparing ──────────────────────────

# Any token that looks like a hex address or handle
HEX_PAT = re.compile(r'\b0x[0-9a-fA-F]{6,}\b')
# Lines that are pure timestamps (e.g. "Mon Jan 24 12:00:00 2026")
TIMESTAMP_PAT = re.compile(
    r'(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+'
    r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d+\s+\d+:\d+:\d+\s+\d{4}'
)
# Lines whose value is a process ID ("PID" or just a bare integer > 3 digits)
PID_LINE_PAT = re.compile(r'(PID\s*[:=]\s*|\bPid\s*:\s*)\d+', re.IGNORECASE)
# UUIDs (GPU UUID)
UUID_PAT = re.compile(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')
# Memory addresses in decimal (large numbers > 8 digits)
LARGE_NUM_PAT = re.compile(r'\b\d{9,}\b')

# ── Hardware telemetry patterns — strip entire line ──────────────────────────
# These fluctuate between measurements and do not represent structural state.
SKIP_LINE_PATS = [
    re.compile(r'Fan Speed\s*:', re.IGNORECASE),
    re.compile(r'Power Draw\s*:', re.IGNORECASE),
    re.compile(r'Current Power Limit\s*:', re.IGNORECASE),   # can vary by 0.01 W
    re.compile(r'Throughput\s*:', re.IGNORECASE),
    re.compile(r'open nvidia fds in pid', re.IGNORECASE),    # fd-count section (PID-specific)
    re.compile(r'^\s*\d+\s*$'),   # bare integer lines (from grep -c fd count)
    re.compile(r'Timestamp\s*:', re.IGNORECASE),
]


def normalise(line: str) -> str:
    """Strip variable fields from a line before comparison."""
    line = HEX_PAT.sub('<HEX>', line)
    line = UUID_PAT.sub('<UUID>', line)
    line = PID_LINE_PAT.sub('<PID>', line)
    line = LARGE_NUM_PAT.sub('<NUM>', line)
    line = line.rstrip()
    return line


def load_normalised(path: str) -> list[str]:
    lines = []
    with open(path) as f:
        for raw in f:
            # Skip pure-timestamp lines
            if TIMESTAMP_PAT.search(raw):
                continue
            # Skip hardware-telemetry lines that fluctuate between samples
            if any(pat.search(raw) for pat in SKIP_LINE_PATS):
                continue
            norm = normalise(raw)
            # Skip blank-after-normalise lines (reduces noise)
            if not norm.strip():
                continue
            lines.append(norm)
    return lines


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: compare_snapshots.py <real.txt> <replay.txt>", file=sys.stderr)
        return 2

    real_path   = sys.argv[1]
    replay_path = sys.argv[2]

    if not Path(real_path).exists():
        print(f"ERROR: {real_path} not found", file=sys.stderr)
        return 2
    if not Path(replay_path).exists():
        print(f"ERROR: {replay_path} not found", file=sys.stderr)
        return 2

    real_lines   = load_normalised(real_path)
    replay_lines = load_normalised(replay_path)

    # Structural diff
    diff = list(difflib.unified_diff(
        real_lines, replay_lines,
        fromfile=real_path,
        tofile=replay_path,
        lineterm=""
    ))

    if not diff:
        print("PASS — snapshots are structurally identical.")
        return 0
    else:
        print("FAIL — structural differences found:")
        for ln in diff:
            print(ln)
        return 1


if __name__ == "__main__":
    sys.exit(main())
