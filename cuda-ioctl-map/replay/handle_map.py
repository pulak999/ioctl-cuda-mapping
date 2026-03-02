"""
handle_map.py — handle/fd patching module for NVIDIA ioctl replay.

Provides:
  FdMap         — maps captured fd numbers to live fd numbers
  ReqSchema     — describes which byte offsets in an ioctl buffer hold handles/fds
  HandleMap     — maps captured RM handles to live RM handles; patches buffers
  load_schemas  — loads handle_offsets.json
"""

import json
import logging
import struct
from pathlib import Path

log = logging.getLogger(__name__)

# Handle width: always 4 bytes, little-endian uint32 (NVIDIA RM convention)
_HANDLE_FMT = "<I"
_HANDLE_SZ = 4


class FdMap:
    """Maps original (captured) file-descriptor numbers to live ones."""

    def __init__(self):
        self._map: dict[int, int] = {}

    def learn_open(self, orig_fd: int, live_fd: int) -> None:
        """Register a captured→live fd mapping.  No-op for orig_fd < 0."""
        if orig_fd < 0:
            return
        self._map[orig_fd] = live_fd
        log.debug("FdMap: %d → %d", orig_fd, live_fd)

    def get(self, orig_fd: int) -> int:
        """Return live fd for *orig_fd*, or -1 if not mapped."""
        return self._map.get(orig_fd, -1)

    def patch_fds(self, buf: bytearray, schema: "ReqSchema") -> bytearray:
        """Patch embedded fd numbers at *schema.fd_offsets* using this fd map."""
        for off in schema.fd_offsets:
            if off + _HANDLE_SZ > len(buf):
                continue
            orig_val = struct.unpack_from(_HANDLE_FMT, buf, off)[0]
            live_val = self.get(int(orig_val))
            if live_val >= 0:
                struct.pack_into(_HANDLE_FMT, buf, off, live_val)
                log.debug("FdMap.patch_fds: off=%d  orig_fd=%d → live_fd=%d",
                          off, orig_val, live_val)
        return buf


class ReqSchema:
    """Describes handle/fd byte offsets for one ioctl request code."""

    def __init__(self, input_handle_offsets: list[int],
                 output_handle_offset: "int | None",
                 fd_offsets: "list[int] | None" = None):
        self.input_handle_offsets = input_handle_offsets
        self.output_handle_offset = output_handle_offset
        self.fd_offsets: list[int] = fd_offsets or []

    @classmethod
    def from_dict(cls, d: dict) -> "ReqSchema":
        """Parse one entry from handle_offsets.json."""
        return cls(
            input_handle_offsets=d.get("handle_offsets", []),
            output_handle_offset=d.get("output_handle_offset", None),
            fd_offsets=d.get("fd_offsets", []),
        )


def load_schemas(path: Path) -> dict[int, "ReqSchema"]:
    """
    Load handle_offsets.json.  Keys in the returned dict are ioctl
    request codes as ints.

    Returns empty dict (not an error) if the file does not exist.
    """
    path = Path(path)
    if not path.exists():
        log.warning("load_schemas: %s not found — no handle patching", path)
        return {}

    with open(path) as f:
        raw = json.load(f)

    schemas: dict[int, ReqSchema] = {}
    for req_hex, entry in raw.items():
        req_int = int(req_hex, 16)
        schemas[req_int] = ReqSchema.from_dict(entry)

    log.info("load_schemas: loaded %d req schemas from %s", len(schemas), path)
    return schemas


class HandleMap:
    """Maps captured NVIDIA RM handles to live handles; patches ioctl buffers."""

    def __init__(self):
        self._map: dict[int, int] = {}

    def learn(self, captured: int, live: int) -> None:
        """Explicitly register a captured→live handle mapping."""
        if captured == 0 or live == 0:
            return
        self._map[captured] = live
        log.debug("HandleMap.learn: 0x%08X → 0x%08X", captured, live)

    def learn_output(self, captured_after_hex: str, live_buf: bytearray,
                     schema: ReqSchema) -> None:
        """
        After a successful ioctl, read the kernel-written handle from
        *live_buf* and the captured handle from *captured_after_hex* at
        *schema.output_handle_offset*, then learn the mapping.
        """
        ooff = schema.output_handle_offset
        if ooff is None:
            return

        after_bytes = bytes.fromhex(captured_after_hex)
        if ooff + _HANDLE_SZ > len(after_bytes):
            return
        if ooff + _HANDLE_SZ > len(live_buf):
            return

        captured_val = struct.unpack_from(_HANDLE_FMT, after_bytes, ooff)[0]
        live_val = struct.unpack_from(_HANDLE_FMT, live_buf, ooff)[0]

        if captured_val != 0 and live_val != 0:
            self.learn(captured_val, live_val)

    def patch_input(self, buf: bytearray, schema: ReqSchema) -> bytearray:
        """
        Replace captured handles at *schema.input_handle_offsets* with live
        handles.  Logs WARNING (does not crash) for unknown handles.
        """
        for off in schema.input_handle_offsets:
            if off + _HANDLE_SZ > len(buf):
                continue
            orig_val = struct.unpack_from(_HANDLE_FMT, buf, off)[0]
            if orig_val == 0:
                continue
            live_val = self._map.get(orig_val)
            if live_val is not None:
                struct.pack_into(_HANDLE_FMT, buf, off, live_val)
                log.debug("HandleMap.patch: off=%d  0x%08X → 0x%08X",
                          off, orig_val, live_val)
            else:
                log.warning(
                    "HandleMap.patch: off=%d  unknown handle 0x%08X "
                    "— passing through", off, orig_val)
        return buf

    def dump(self) -> None:
        """Log final map state at INFO level."""
        log.info("[handle_map] final state: %d entries", len(self._map))
        for captured, live in sorted(self._map.items()):
            log.info("  orig=0x%08X  →  replay=0x%08X", captured, live)
