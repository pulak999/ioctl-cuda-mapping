#!/usr/bin/env python3
"""
parse_trace.py — temporal, single-pass trace parser

W2: FD→device map is maintained in temporal order.  Device is resolved at
    ioctl-time (not from a pre-built final scan), so FD reuse after a close()
    cannot mislabel an ioctl with the wrong device.

W6: Lines produced by strace -f carry a leading PID (e.g. "12345 ioctl(...)").
    strip_pid() removes that prefix before pattern matching, so -f output and
    single-process output are both handled transparently.
"""
import re, json, sys, os

DIR_MAP = {
    "_IOC_NONE": 0, "_IOC_WRITE": 1, "_IOC_READ": 2,
    "_IOC_READ|_IOC_WRITE": 3, "_IOC_WRITE|_IOC_READ": 3,
}
def _ioc(d, t, n, s):
    return ((d & 3) << 30) | ((s & 0x3FFF) << 16) | ((t & 0xFF) << 8) | (n & 0xFF)

# Patterns are applied after stripping an optional leading PID (W6).
# P3-fix: return value group accepts integer, hex pointer, or "?" (signal/unknown)
_RET = r'(-?\d+|0x[0-9a-fA-F]+|\?)'
IOCTL_IOC = re.compile(
    r'^ioctl\((\d+),\s*_IOC\(([^,]+),\s*(0x[0-9a-fA-F]+|\d+),\s*'
    r'(0x[0-9a-fA-F]+|\d+),\s*(0x[0-9a-fA-F]+|\d+)\),\s*(.*)\)\s*=\s*' + _RET)
IOCTL_HEX = re.compile(r'^ioctl\((\d+),\s*(0x[0-9a-fA-F]+),?\s*(.*)\)\s*=\s*' + _RET)
OPENAT    = re.compile(r'^openat\([^,]*,\s*"(/dev/nvidia[^"]*)"[^)]*\)\s*=\s*(\d+)')
CLOSE     = re.compile(r'^close\((\d+)\)')
PID_STRIP = re.compile(r'^\d+\s+')   # matches "12345 " prefix from strace -f


def strip_pid(line):
    """Remove optional leading PID added by strace -f (W6)."""
    return PID_STRIP.sub('', line, count=1)


def parse_lines(lines):
    """
    Core single-pass parser (W9: exposed so check_reproducibility.py can reuse it).

    Accepts a list of raw strace output lines (with or without PID prefixes).
    Returns (ioctls, fd_snap) where:
      ioctls  — list of ioctl event dicts (is_new is always False here)
      fd_snap — dict of all FD→device assignments seen
    """
    fd_map  = {}   # live FD→device (W2)
    fd_snap = {}   # accumulated snapshot
    ioctls  = []

    for raw in lines:
        s = strip_pid(raw).rstrip('\n')   # W6

        mo = OPENAT.match(s)
        if mo:
            device, fd = mo.group(1), mo.group(2)
            fd_map[fd]  = device
            fd_snap[fd] = device
            continue

        mc = CLOSE.match(s)
        if mc:
            fd_map.pop(mc.group(1), None)
            continue

        m = IOCTL_IOC.match(s)
        if m:
            fd      = m.group(1)
            dir_str = m.group(2).strip()
            # P2-fix: warn instead of silently defaulting on unknown direction tokens
            if dir_str not in DIR_MAP:
                print(f"  WARNING [P2]: unknown _IOC direction {dir_str!r}, "
                      f"defaulting to _IOC_NONE (0). Line: {s[:80]}", file=sys.stderr)
            code = _ioc(
                DIR_MAP.get(dir_str, 0),
                int(m.group(3), 0), int(m.group(4), 0), int(m.group(5), 0))
            ioctls.append({
                "sequence_index": len(ioctls),
                "fd":             fd,
                "device":         fd_map.get(fd, "unknown"),
                "request_code":   f"0x{code:08X}",
                "decoded":        s,
                "args":           m.group(6).strip(),
                "return_value":   m.group(7),
                "is_new":         False,
            })
            continue

        m = IOCTL_HEX.match(s)
        if m:
            fd, req, args, ret = m.groups()
            ioctls.append({
                "sequence_index": len(ioctls),
                "fd":             fd,
                "device":         fd_map.get(fd, "unknown"),
                "request_code":   req.upper(),
                "decoded":        s,
                "args":           args.strip(),
                "return_value":   ret,
                "is_new":         False,
            })

    return ioctls, fd_snap


def parse(log_path, prev_parsed=None):
    step = os.path.basename(log_path).replace(".log", "")
    with open(log_path) as f:
        lines = f.readlines()

    ioctls, fd_snap = parse_lines(lines)   # W9: delegate to shared helper

    # ── mark first-seen codes vs previous step ───────────────────────────────
    prev = _load_prev_codes(prev_parsed)
    seen = set()
    for i in ioctls:
        c = i["request_code"]
        if c not in prev and c not in seen:
            i["is_new"] = True
        seen.add(c)

    out      = {"cuda_call": step, "fd_map": fd_snap, "ioctl_sequence": ioctls}
    out_dir  = os.path.join(os.path.dirname(os.path.dirname(log_path)), "parsed")
    out_path = os.path.join(out_dir, step + ".json")
    os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2)
    new = sum(1 for i in ioctls if i["is_new"])
    print(f"[{step}] total={len(ioctls)} unique={len({i['request_code'] for i in ioctls})} new_codes={new}")
    return out_path


def _load_prev_codes(path):
    if path and os.path.exists(path):
        with open(path) as f:
            return {i["request_code"] for i in json.load(f)["ioctl_sequence"]}
    return set()


if __name__ == "__main__":
    prev = None
    for p in sys.argv[1:]:
        prev = parse(p, prev)
