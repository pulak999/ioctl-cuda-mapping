#!/usr/bin/env python3
"""
find_handle_offsets.py — diff two cu_init captures to discover handle offsets.

For each ioctl request code on /dev/nvidiactl, compares the 'before' buffer
byte-for-byte across two independent runs.  Any aligned 4-byte window that
differs between runs (and is non-zero in both) is a candidate handle field.

An offset is confirmed as a handle field if it varies in at least
  max(MIN_VARY_COUNT, total_records * MIN_VARY_FRACTION)
records for that request code.

Also computes output_handle_offset: the aligned 4-byte window within the
'after' buffer that was 0 in 'before' but non-zero in 'after' (kernel-written).

UVM ioctls (on /dev/nvidia-uvm) are excluded: the 4096-byte fallback capture
fills those buffers with stack noise, making offset discovery unreliable.

Pointer filter: a 4-byte window that forms the lower half of a 64-bit
userspace pointer (where the upper half at off+4 is in 0x00007f00–0x00007fff)
is excluded as a false positive.

Usage:
    python3 tools/find_handle_offsets.py \\
        sniffed/cu_init_a.jsonl \\
        sniffed/cu_init_b.jsonl \\
        intercept/handle_offsets.json

Output: intercept/handle_offsets.json (consumed by replay tool)
"""

import json
import sys
import struct
from collections import defaultdict
from pathlib import Path

# ── Thresholds ──────────────────────────────────────────────────────────────
MIN_VARY_COUNT    = 2      # must vary in at least this many records
MIN_VARY_FRACTION = 0.05   # ... OR at least this fraction of total records
# (whichever is larger wins)

# ── Hard-coded fd_offsets ────────────────────────────────────────────────────
# Some ioctls pass kernel fd numbers in their argument buffer.  These cannot
# be discovered by XOR-diffing (fds vary, but so does their format), so we
# enumerate them manually based on known NVIDIA RM interface knowledge.
#
# Format: req_hex (uppercase 0xXXXXXXXX) → list of byte offsets that hold fd values.
KNOWN_FD_OFFSETS: dict[str, list[int]] = {
    "0xC00446C9": [0],  # NV_ESC_REGISTER_FD: 4-byte arg is the nvidiactl/control fd
}

# Device filter: only run handle discovery on /dev/nvidiactl ioctls.
# UVM ioctls (/dev/nvidia-uvm, etc.) use a 4096-byte fallback capture that
# is filled with stack data → too noisy for reliable offset discovery.
NVIDIACTL_ONLY = True
NVIDIACTL_DEVS = {"/dev/nvidiactl"}   # extend if needed


def load_jsonl(path: str) -> list[dict]:
    """Load a JSONL file, return all records (open + ioctl) in seq order."""
    records = []
    with open(path) as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"WARNING: {path}:{lineno}: JSON parse error: {e}",
                      file=sys.stderr)
                continue
            records.append(rec)
    return records


def u32le(buf: bytes, offset: int) -> int:
    """Read a little-endian uint32 at the given byte offset."""
    return struct.unpack_from("<I", buf, offset)[0]


def is_ptr_lower_half(buf_a: bytes, buf_b: bytes, off: int) -> bool:
    """
    Return True if off+4 in both buffers looks like the upper 32 bits of a
    userspace 64-bit pointer (0x00007f00–0x00007fff).  If so, `off` is the
    lower 32-bit half of a pointer and should not be treated as a handle.
    """
    upper_off = off + 4
    if upper_off + 4 > len(buf_a) or upper_off + 4 > len(buf_b):
        return False
    ua = u32le(buf_a, upper_off)
    ub = u32le(buf_b, upper_off)
    # Canonical x86-64 userspace pointer high word: 0x00007f00–0x00007fff
    def looks_like_ptr_high(v: int) -> bool:
        return 0x00007e00 <= v <= 0x00007fff

    return looks_like_ptr_high(ua) or looks_like_ptr_high(ub)


def find_offsets(path_a: str, path_b: str, out_path: str) -> None:
    print(f"[find_handle_offsets] loading {path_a} ...")
    all_a = load_jsonl(path_a)
    print(f"[find_handle_offsets] loading {path_b} ...")
    all_b = load_jsonl(path_b)

    # Filter to ioctl records only, applying device filter
    def filter_ioctls(records: list[dict]) -> list[dict]:
        out = []
        for r in records:
            if r.get("type") != "ioctl":
                continue
            if NVIDIACTL_ONLY and r.get("dev", "") not in NVIDIACTL_DEVS:
                continue
            out.append(r)
        return out

    recs_a = filter_ioctls(all_a)
    recs_b = filter_ioctls(all_b)

    print(f"[find_handle_offsets] run A: {len(recs_a)} nvidiactl ioctl records")
    print(f"[find_handle_offsets] run B: {len(recs_b)} nvidiactl ioctl records")

    if len(recs_a) != len(recs_b):
        print("WARNING: nvidiactl ioctl counts differ between runs "
              f"({len(recs_a)} vs {len(recs_b)}).  "
              "Pairing by position up to the shorter length.",
              file=sys.stderr)

    n = min(len(recs_a), len(recs_b))

    # Counters indexed by req code
    candidate_counts: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    total_counts:     dict[int, int]             = defaultdict(int)
    zero_to_nonzero:  dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    zero_before_cnt:  dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))

    aborts = 0
    for i in range(n):
        ra = recs_a[i]
        rb = recs_b[i]

        req_str_a = ra.get("req", "")
        req_str_b = rb.get("req", "")

        if req_str_a != req_str_b:
            print(f"WARNING: position {i}: req mismatch "
                  f"({req_str_a} vs {req_str_b}) — stopping alignment.",
                  file=sys.stderr)
            aborts += 1
            if aborts >= 5:
                print("ERROR: too many req mismatches, aborting.", file=sys.stderr)
                sys.exit(1)
            break

        req = int(req_str_a, 16)

        before_a = bytes.fromhex(ra.get("before", ""))
        before_b = bytes.fromhex(rb.get("before", ""))
        after_a  = bytes.fromhex(ra.get("after",  ""))

        scan_len = min(len(before_a), len(before_b))
        if scan_len < 4:
            continue

        total_counts[req] += 1

        for off in range(0, scan_len - 3, 4):
            val_a = u32le(before_a, off)
            val_b = u32le(before_b, off)

            # Handle candidate: non-zero in both runs but differs between them
            if val_a != val_b and val_a != 0 and val_b != 0:
                # Reject if this looks like the lower half of a 64-bit pointer
                if not is_ptr_lower_half(before_a, before_b, off):
                    candidate_counts[req][off] += 1

            # Output handle candidate: was 0 before call, non-zero after call
            if off + 4 <= len(after_a):
                after_val = u32le(after_a, off)
                if val_a == 0:
                    zero_before_cnt[req][off] += 1
                    if after_val != 0:
                        zero_to_nonzero[req][off] += 1

    # ── Build result ────────────────────────────────────────────────────────
    ioctl_table_path = (Path(out_path).parent.parent / "lookup" / "ioctl_table.json")
    name_map: dict[str, str] = {}
    if ioctl_table_path.exists():
        with open(ioctl_table_path) as f:
            table = json.load(f)
        for code, info in table.items():
            name_map[code.upper()] = info.get("name", code)

    result: dict = {}
    all_reqs = set(candidate_counts.keys()) | set(total_counts.keys())

    for req in sorted(all_reqs):
        req_hex = f"0x{req:08X}"
        total   = total_counts.get(req, 0)
        if total == 0:
            continue

        # Threshold: vary in at least MIN_VARY_COUNT records OR MIN_VARY_FRACTION
        threshold = max(MIN_VARY_COUNT, total * MIN_VARY_FRACTION)

        counts    = candidate_counts.get(req, {})
        confirmed = sorted(
            off for off, cnt in counts.items()
            if cnt >= threshold
        )

        # Output handle: 0→non-zero in more than half the records where it
        # started at 0 (catches kernel-assigned handles reliably)
        ztn  = zero_to_nonzero.get(req, {})
        zbc  = zero_before_cnt.get(req, {})
        output_off  = None
        best_score  = 0
        for off, cnt in ztn.items():
            z_count = zbc.get(off, 0)
            if z_count > 0 and cnt >= max(1, z_count * 0.5):
                score = cnt
                if score > best_score:
                    best_score = score
                    output_off = off

        entry: dict = {
            "name":           name_map.get(req_hex, req_hex),
            "handle_offsets": confirmed,
            "sample_count":   total,
        }
        if output_off is not None:
            entry["output_handle_offset"] = output_off
        if req_hex in KNOWN_FD_OFFSETS:
            entry["fd_offsets"] = KNOWN_FD_OFFSETS[req_hex]

        result[req_hex] = entry

    # Ensure every entry in KNOWN_FD_OFFSETS appears in result (even if the
    # corresponding ioctl wasn't seen on nvidiactl).
    for req_hex_raw, fd_offs in KNOWN_FD_OFFSETS.items():
        # Normalise to "0xXXXXXXXX" uppercase hex digits, lowercase prefix
        req_val = int(req_hex_raw, 16)
        req_hex = f"0x{req_val:08X}"
        if req_hex not in result:
            result[req_hex] = {
                "name":           name_map.get(req_hex, req_hex),
                "handle_offsets": [],
                "sample_count":   0,
                "fd_offsets":     fd_offs,
            }
        else:
            result[req_hex]["fd_offsets"] = fd_offs

    # Write output
    out_file = Path(out_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w") as f:
        json.dump(result, f, indent=2)
    print(f"[find_handle_offsets] wrote {len(result)} req entries to {out_path}")

    # ── Summary table ────────────────────────────────────────────────────────
    print()
    hdr = (f"{'req_code':<14}  {'name':<38}  "
           f"{'n_handle_fields':>15}  {'sample_count':>12}  {'output_offset':>13}")
    print(hdr)
    print("-" * len(hdr))
    for req_hex, info in sorted(result.items()):
        out_off = info.get("output_handle_offset", "-")
        print(f"{req_hex:<14}  {info['name'][:38]:<38}  "
              f"{len(info['handle_offsets']):>15}  "
              f"{info['sample_count']:>12}  "
              f"{str(out_off):>13}")


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        print("Usage: find_handle_offsets.py <run_a.jsonl> <run_b.jsonl> "
              "[handle_offsets.json]",
              file=sys.stderr)
        sys.exit(1)

    path_a = sys.argv[1]
    path_b = sys.argv[2]
    out    = sys.argv[3] if len(sys.argv) == 4 else "intercept/handle_offsets.json"

    find_offsets(path_a, path_b, out)
