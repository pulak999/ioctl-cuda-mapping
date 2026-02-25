#!/usr/bin/env python3
"""
replay.py — raw ioctl replay tool for NVIDIA driver reverse-engineering.

Reads a capture produced by libnv_sniff.so (JSONL format), re-opens the
device files in the same order, and re-issues every ioctl with the captured
'before' buffer.  Handle values in the input buffers are patched using a
mapping derived from intercept/handle_offsets.json.

Usage:
    sudo python3 replay/replay.py <capture.jsonl> [handle_offsets.json]

Exit code: 0 if all ioctls returned 0, 1 otherwise.
"""

import argparse
import fcntl
import json
import logging
import os
import sys
from pathlib import Path

from handle_map import FdMap, HandleMap, ReqSchema, load_schemas

log = logging.getLogger(__name__)

# Empty schema used as default when req has no entry in handle_offsets.json
EMPTY_SCHEMA = ReqSchema([], None)


def load_jsonl(path: Path) -> list[dict]:
    """Read JSONL line by line; hard exit on parse error."""
    events: list[dict] = []
    with open(path) as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"FATAL: {path}:{lineno}: JSON parse error: {e}",
                      file=sys.stderr)
                sys.exit(1)
    return events


def do_ioctl(fd: int, req: int, buf: bytearray) -> int:
    """
    Wrap fcntl.ioctl; returns 0 on success, -errno on failure.
    *buf* is mutated in-place by the kernel (pass mutate=True).
    """
    try:
        if len(buf) == 0:
            fcntl.ioctl(fd, req, 0)
        else:
            fcntl.ioctl(fd, req, buf, True)
        return 0
    except OSError as e:
        return -e.errno


def replay(capture_path: Path, offsets_path: Path) -> int:
    """
    Main replay loop.  Iterates events in seq order.
    Returns the number of failed ioctls.
    """
    events = load_jsonl(capture_path)
    schemas = load_schemas(offsets_path)

    fd_map = FdMap()
    hm = HandleMap()

    total = 0
    ok = 0
    failed = 0
    skipped = 0

    for event in events:
        etype = event.get("type")

        # ═══ open event ═══
        if etype == "open":
            path = event.get("path", "")
            ret = event.get("ret", -1)

            if ret < 0:
                # Failed open in capture — attempt anyway, expect failure
                try:
                    fd = os.open(path, os.O_RDWR)
                    os.close(fd)
                    print(f"[open] {path} → fd={fd} "
                          "(expected failure but succeeded)")
                except OSError as e:
                    print(f"[open] {path} → {e.strerror} (expected)")
            else:
                orig_fd = ret
                try:
                    live_fd = os.open(path, os.O_RDWR)
                    fd_map.learn_open(orig_fd, live_fd)
                    print(f"[open] {path} → fd={live_fd} (orig {orig_fd})")
                except PermissionError:
                    print("[open] FAILED: {}: Permission denied "
                          "(run as root or with CAP_SYS_ADMIN)".format(path),
                          file=sys.stderr)
                    sys.exit(1)
                except OSError as e:
                    print(f"[open] FAILED: {path}: {e.strerror}")
            continue

        # ═══ close event ═══
        if etype == "close":
            orig_fd = event.get("fd", -1)
            live_fd = fd_map.get(orig_fd)
            if live_fd >= 0:
                try:
                    os.close(live_fd)
                except OSError:
                    pass
            continue

        # ═══ ioctl event ═══
        if etype != "ioctl":
            continue

        seq = event.get("seq", -1)
        orig_fd = event.get("fd", -1)
        req_str = event.get("req", "0")
        req = int(req_str, 16)
        sz = event.get("sz", 0)

        live_fd = fd_map.get(orig_fd)
        if live_fd < 0:
            print(f"[{seq:04d}] req=0x{req:08X}  "
                  f"SKIP (fd {orig_fd} not mapped)")
            total += 1
            skipped += 1
            continue

        # Build working buffer from captured 'before'
        before_hex = event.get("before", "")
        buf = bytearray.fromhex(before_hex)

        # Look up schema and patch
        schema = schemas.get(req, EMPTY_SCHEMA)
        hm.patch_input(buf, schema)
        fd_map.patch_fds(buf, schema)

        # Issue the ioctl
        ret = do_ioctl(live_fd, req, buf)

        total += 1

        dev = event.get("dev", "(unknown)")

        if ret == 0:
            ok += 1
            print(f"[{seq:04d}] {dev:<30}  req=0x{req:08X}  "
                  f"fd={live_fd}  sz={sz}  ret=0  OK")
            # Learn output handles
            after_hex = event.get("after", "")
            hm.learn_output(after_hex, buf, schema)
        else:
            failed += 1
            err = -ret
            print(f"[{seq:04d}] {dev:<30}  req=0x{req:08X}  "
                  f"fd={live_fd}  sz={sz}  ret=-1  FAIL")
            print(f"         errno={err} ({os.strerror(err)})",
                  file=sys.stderr)
            log.warning("[%04d] FAIL req=0x%08X  errno=%d (%s)",
                        seq, req, err, os.strerror(err))

    # ── Summary ──
    print()
    print(f"DONE — {ok}/{total} succeeded, {failed} failed, "
          f"{skipped} skipped")
    hm.dump()

    return failed


def main():
    parser = argparse.ArgumentParser(
        description="Replay captured NVIDIA ioctls from a JSONL file.")
    parser.add_argument("capture", type=Path,
                        help="Path to the capture JSONL file")
    parser.add_argument("offsets", type=Path, nargs="?", default=None,
                        help="Path to handle_offsets.json "
                             "(default: <capture_dir>/../intercept/"
                             "handle_offsets.json)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Set logging to DEBUG")

    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level,
                        format="%(levelname)s:%(name)s:%(message)s")

    capture = args.capture.resolve()
    if args.offsets is not None:
        offsets = args.offsets.resolve()
    else:
        offsets = (capture.parent / ".." / "intercept"
                   / "handle_offsets.json").resolve()

    n_failed = replay(capture, offsets)
    sys.exit(0 if n_failed == 0 else 1)


if __name__ == "__main__":
    main()
