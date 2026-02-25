# Agent Execution Plan: CUDA ioctl Replay Ladder
**Executor:** Opus 4.6 in Cursor  
**Human checkpoint required:** before starting each Phase  
**Abort rule:** if a phase's validation fails, STOP. Do not proceed. Report the exact failure output and wait for human instruction.

---

## How to Read This Plan

Each phase has:
- **Goal** — one sentence of intent
- **Actions** — exact steps the agent takes
- **Validation** — exact command(s) to run and exact expected output
- **Pass criteria** — what "done" means; only then mark the phase complete
- **Fail criteria** — what to report if validation does not pass

The agent must not skip phases, merge phases, or proceed past a failed validation.

---

## Phase -2 — Create handle_map.py

**Goal:** Create the handle/fd patching module that replay.py will import.

**Context:** This file owns all remapping logic. replay.py imports it but contains none of this logic itself. Keep concerns strictly separated.

**Actions:**  
Create `cuda-ioctl-map/replay/handle_map.py` with the following architecture:

```
handle_map.py
├── class FdMap
│     ├── learn_open(orig_fd, live_fd)   — called for each 'open' event in seq order
│     └── get(orig_fd) -> int            — returns live_fd or -1 if not mapped
│
├── class ReqSchema
│     ├── input_handle_offsets: list[int]  — byte offsets to patch BEFORE ioctl
│     ├── output_handle_offset: int|None   — byte offset to learn handle from AFTER ioctl
│     └── from_dict(d) -> ReqSchema        — parses one entry from handle_offsets.json
│
├── def load_schemas(path) -> dict[int, ReqSchema]
│     — loads handle_offsets.json; keys are ioctl req codes as ints
│     — returns empty dict (not an error) if file does not exist
│
└── class HandleMap
      ├── learn(captured, live)                          — explicit registration
      ├── learn_output(captured_after_hex, live_buf, schema)
      │     — reads kernel-written handle from live_buf at schema.output_handle_offset
      │     — compares against captured_after_hex to learn captured->live mapping
      ├── patch_input(buf: bytearray, schema) -> bytearray
      │     — replaces captured handles at schema.input_handle_offsets with live handles
      │     — logs WARNING (does not crash) for unknown handles
      └── dump()  — logs final map state at INFO level
```

**Implementation details:**
- Handle width: always 4 bytes, little-endian uint32 (`struct.pack "<I"`), matching NVIDIA RM convention in the existing `replay/handle_map.h`
- Zero is never a real handle — skip it silently in all methods
- Use Python `logging` module throughout; no `print()` calls in this file
- `FdMap.learn_open` is a no-op for `orig_fd < 0` (failed open in capture)
- `HandleMap.patch_input` must not raise on unknown handle — log WARNING and pass through

**Validation:**
```bash
cd cuda-ioctl-map
python3 -c "
from replay.handle_map import FdMap, HandleMap, ReqSchema, load_schemas
import struct, logging
logging.basicConfig(level=logging.DEBUG)

# FdMap round-trip
fm = FdMap()
fm.learn_open(3, 7)
assert fm.get(3) == 7
assert fm.get(99) == -1

# HandleMap round-trip
hm = HandleMap()
hm.learn(0xABCD, 0x1234)
schema = ReqSchema([0], None)
buf = bytearray(struct.pack('<I', 0xABCD))
hm.patch_input(buf, schema)
assert struct.unpack('<I', buf)[0] == 0x1234

# load_schemas with missing file returns empty dict
assert load_schemas(__import__('pathlib').Path('/nonexistent')) == {}

print('handle_map.py: all assertions passed')
"
```

**Pass:** prints `handle_map.py: all assertions passed`, no exceptions.  
**Fail:** report the full traceback.

---

## Phase -1 — Create replay.py

**Goal:** Create the main replay engine that reads JSONL and issues live ioctls.

**Context:** replay.py is the entry point. It contains no patching logic — all of that is imported from handle_map.py.

**Actions:**  
Create `cuda-ioctl-map/replay/replay.py` with the following architecture:

```
replay.py
│
├── load_jsonl(path) -> list[dict]
│     — reads JSONL line by line; hard exits on parse error
│
├── do_ioctl(fd, req, buf) -> int
│     — wraps fcntl.ioctl; returns 0 on success, -errno on failure
│     — buf is mutated in-place by the kernel (pass mutate=True)
│
├── replay(capture_path, offsets_path) -> int (failed count)
│     — main loop: iterates events in seq order
│     — 'open'  events: os.open the device path, call fd_map.learn_open
│     — 'close' events: os.close the live fd (if present; not all captures have these)
│     — 'ioctl' events:
│           1. look up live fd via fd_map.get(orig_fd); SKIP if -1
│           2. build bytearray from event['before']
│           3. look up schema = schemas.get(req, EMPTY_SCHEMA)
│           4. hm.patch_input(buf, schema)
│           5. ret = do_ioctl(live_fd, req, buf)
│           6. print "[seq] OK/FAIL/SKIP req=... fd=... ret=..."
│           7. on success: hm.learn_output(event['after'], buf, schema)
│           8. on failure: log WARNING with seq, req, errno
│     — prints summary: "DONE — ok/total succeeded, N failed, N skipped"
│     — calls hm.dump()
│     — returns failed count
│
└── main()
      — argparse: positional <capture>, optional <offsets>
      — default offsets path: <capture_dir>/../intercept/handle_offsets.json (resolved absolute)
      — -v / --verbose flag sets logging to DEBUG
      — sys.exit(0 if failed == 0 else 1)
```

**Implementation details:**
- `fcntl.ioctl(fd, req, buf, True)` — the fourth argument `True` means "mutate buf in place"
- Requires root / CAP_SYS_ADMIN to open `/dev/nvidia*`; if `os.open` raises `PermissionError`, print a clear message and exit 1
- Summary line format must be exactly: `DONE — {ok}/{total} succeeded, {failed} failed, {skipped} skipped`
- `EMPTY_SCHEMA = ReqSchema([], None)` — defined once at module level, used as default when req has no schema entry

**Validation:**
```bash
cd cuda-ioctl-map
python3 -c "
import ast, sys
src = open('replay/replay.py').read()
ast.parse(src)

# Check imports
assert 'from handle_map import' in src or 'from replay.handle_map import' in src, 'must import from handle_map'
assert 'fcntl' in src, 'must use fcntl'
assert 'DONE' in src, 'must print DONE summary'
assert 'argparse' in src, 'must use argparse'
print('replay.py: static checks passed')
"
```

**Pass:** prints `replay.py: static checks passed`, no exceptions.  
**Fail:** report the full traceback.

---

## Phase 0 — Verify both files are in place and repo is clean

**Goal:** Confirm the two new files exist in the right location and nothing else was touched.

**Actions:**
1. Confirm `cuda-ioctl-map/replay/replay.py` exists (created in Phase -1)
2. Confirm `cuda-ioctl-map/replay/handle_map.py` exists (created in Phase -2)
3. Confirm `replay.c` and `Makefile` are untouched

**Validation:**
```bash
cd cuda-ioctl-map
git status
```
Expected: exactly two new untracked files (`replay/replay.py`, `replay/handle_map.py`), zero modifications to existing files.

```bash
python3 -c "import ast; ast.parse(open('replay/replay.py').read()); ast.parse(open('replay/handle_map.py').read()); print('syntax OK')"
```
Expected: `syntax OK`

**Pass:** git status clean except two new files, syntax check passes.
**Fail:** report exact git diff and any syntax errors.

---

## Phase 1 — Step 0: Validate replay.py on cu_init (baseline match)

**Goal:** Prove replay.py produces the same result as replay.c on the existing cu_init capture.

**Pre-check (agent must verify before running):**
- `sniffed/cu_init.jsonl` exists
- `intercept/handle_offsets.json` exists

**Actions:**
1. Run the C replay and capture its summary line:
```bash
cd cuda-ioctl-map
sudo ./replay/replay sniffed/cu_init.jsonl 2>&1 | tail -5
```
2. Run the Python replay:
```bash
sudo python3 replay/replay.py sniffed/cu_init.jsonl 2>&1
```
3. Compare the final summary line of both outputs.

**Validation:**  
Python replay summary must match C replay summary exactly on the three numbers: succeeded, failed, skipped.

Expected form: `DONE — 230/230 succeeded, 0 failed, 0 skipped`  
(The exact number 230 may differ on your machine; what matters is that Python matches C.)

**Pass:** Python succeeded/failed/skipped counts == C succeeded/failed/skipped counts, AND failed == 0.  
**Fail:** Report the full output of both runs side by side.

---

## Phase 2 — Write and capture cu_device_get

**Goal:** Produce a clean JSONL capture for cuDeviceGet and confirm it contains the expected new event types.

**Actions:**
1. Check if `programs/cu_device_get.cu` already exists. If yes, skip to step 3.
2. If it does not exist, write `programs/cu_device_get.cu`:
   - Call cuInit(0), then cuDeviceGet(&device, 0), then exit.
   - Minimal: no context, no alloc, no kernel.
   - Compile: `nvcc -o programs/cu_device_get programs/cu_device_get.cu -lcuda`
3. Capture:
```bash
cd cuda-ioctl-map
LD_PRELOAD=./intercept/libnv_sniff.so ./programs/cu_device_get 2>/dev/null > sniffed/cu_device_get.jsonl
```
4. Inspect the capture:
```bash
wc -l sniffed/cu_device_get.jsonl
python3 -c "
import json
events = [json.loads(l) for l in open('sniffed/cu_device_get.jsonl')]
types = [e['type'] for e in events]
print('event types:', set(types))
ioctl_count = sum(1 for t in types if t == 'ioctl')
open_count  = sum(1 for t in types if t == 'open')
print(f'open={open_count} ioctl={ioctl_count} total={len(events)}')
"
```

**Validation:**  
- File is non-empty (>0 lines)
- Contains at least one `open` event and at least one `ioctl` event
- ioctl count is >= the cu_init ioctl count (it is a superset)

**Pass:** non-empty capture with open+ioctl events, ioctl count >= cu_init count.  
**Fail:** report wc -l output and the full python inspection output.

---

## Phase 3 — Step 1: Replay cu_device_get

**Goal:** Replay the cu_device_get capture and get 0 failed ioctls.

**Actions:**
```bash
cd cuda-ioctl-map
sudo python3 replay/replay.py sniffed/cu_device_get.jsonl 2>&1
```

**Validation:**  
Summary line must show `0 failed`.

**Pass:** `0 failed` in summary.  
**Fail:** report the full output; note the first FAIL line's seq number, req code, and errno.

---

## Phase 4 — Write and capture cu_ctx_create

**Goal:** Produce a clean JSONL capture for cuCtxCreate.

**Actions:**
1. Check if `programs/cu_ctx_create.cu` already exists. If yes, skip to step 3.
2. If not, write `programs/cu_ctx_create.cu`:
   - Call cuInit(0), cuDeviceGet(&device, 0), cuCtxCreate(&ctx, 0, device), cuCtxDestroy(ctx), exit.
   - Compile: `nvcc -o programs/cu_ctx_create programs/cu_ctx_create.cu -lcuda`
3. Capture:
```bash
LD_PRELOAD=./intercept/libnv_sniff.so ./programs/cu_ctx_create 2>/dev/null > sniffed/cu_ctx_create.jsonl
```
4. Run the same inspection script as Phase 2.

**Validation:** non-empty capture, ioctl count > cu_device_get count.  
**Pass / Fail:** same criteria as Phase 2.

---

## Phase 5 — Step 2: Replay cu_ctx_create

**Goal:** Replay cu_ctx_create with 0 failed ioctls.

**Actions:**
```bash
sudo python3 replay/replay.py sniffed/cu_ctx_create.jsonl 2>&1
```

**Validation:** `0 failed` in summary.  

**Important:** cuCtxCreate introduces the first RM object handle (the context handle). If ioctls fail here, the issue is almost certainly a missing entry in `handle_offsets.json` for the context allocation ioctl. Report:
- The failing req code
- The before/after hex for that seq in the JSONL
- Whether that req code is present in `intercept/handle_offsets.json`

**Pass:** `0 failed`.  
**Fail:** report as above.

---

## Phase 6 — Write and capture cu_mem_alloc

**Goal:** Produce a JSONL capture for a minimal cuMemAlloc + cuMemFree sequence.

**Actions:**
1. Write `programs/cu_mem_alloc.cu`:
   - cuInit → cuDeviceGet → cuCtxCreate → cuMemAlloc(&ptr, 1024) → cuMemFree(ptr) → cuCtxDestroy → exit.
   - Compile: `nvcc -o programs/cu_mem_alloc programs/cu_mem_alloc.cu -lcuda`
2. Capture to `sniffed/cu_mem_alloc.jsonl`
3. Inspection:
```bash
python3 -c "
import json
events = [json.loads(l) for l in open('sniffed/cu_mem_alloc.jsonl')]
ioctls = [e for e in events if e['type']=='ioctl']
# Look for ioctls where before != after (kernel wrote something back)
diffs = [(e['seq'], e['req'], e['before'], e['after']) for e in ioctls if e['before'] != e['after']]
print(f'ioctls with kernel writes: {len(diffs)}')
for seq, req, b, a in diffs[:10]:
    print(f'  seq={seq} req={req}')
    print(f'    before: {b}')
    print(f'    after:  {a}')
"
```

**Validation:**  
- Non-empty capture
- At least one ioctl with `before != after` (the kernel wrote a GPU virtual address back)

**Pass:** non-empty, at least one kernel-write ioctl exists.  
**Fail:** report inspection output.

---

## Phase 7 — Step 3: Replay cu_mem_alloc

**Goal:** Replay cu_mem_alloc with 0 failed ioctls.

**Actions:**
```bash
sudo python3 replay/replay.py sniffed/cu_mem_alloc.jsonl 2>&1
```

**Validation:** `0 failed`.

**Note:** Memory allocation ioctls return GPU virtual addresses in the response buffer. The replay does not need to *use* those addresses yet — it just needs the ioctls to return 0. The address patching (for kernel launch) comes later.

**Pass:** `0 failed`.  
**Fail:** report first FAIL line's seq, req, errno, and whether the req code is in `handle_offsets.json`.

---

## Phase 8 — Write and capture cu_module_load (PTX)

**Goal:** Produce a JSONL capture for loading a minimal PTX module.

**Actions:**
1. Write `programs/cu_module_load.cu`:
   - Contains an embedded PTX string for a kernel that does nothing:
     ```
     .version 7.0
     .target sm_80
     .address_size 64
     .visible .entry null_kernel() { ret; }
     ```
   - Calls: cuInit → cuDeviceGet → cuCtxCreate → cuModuleLoadData(&mod, ptx_string) → cuModuleUnload(mod) → cuCtxDestroy → exit.
   - Compile: `nvcc -o programs/cu_module_load programs/cu_module_load.cu -lcuda`
2. Capture to `sniffed/cu_module_load.jsonl`
3. Run inspection — note the ioctl count; expect it to be significantly higher than cu_ctx_create due to PTX JIT compilation ioctls.

**Validation:** non-empty capture, ioctl count noticeably larger than cu_ctx_create.  
**Pass / Fail:** standard criteria.

---

## Phase 9 — Step 4: Replay cu_module_load

**Goal:** Replay module load with 0 failed ioctls.

**Actions:**
```bash
sudo python3 replay/replay.py sniffed/cu_module_load.jsonl 2>&1
```

**Validation:** `0 failed`.

**Note:** This is the hardest step so far. Module load produces a module handle, which is an opaque kernel object. If this step fails, the fix will likely require adding the module-load ioctl's output handle offset to `handle_offsets.json` and re-running `find_handle_offsets.py` on two cu_module_load captures. Report the failing req code and hex diff for analysis.

**Pass:** `0 failed`.  
**Fail:** detailed report as specified in Phase 5 fail path.

---

## Phase 10 — Write and capture cu_launch_null

**Goal:** Produce a JSONL capture for a complete kernel launch of a no-op kernel.

**Actions:**
1. Write `programs/cu_launch_null.cu`:
   - Same null PTX kernel as Phase 8.
   - Full sequence: cuInit → cuDeviceGet → cuCtxCreate → cuModuleLoadData → cuModuleGetFunction(&fn, mod, "null_kernel") → cuLaunchKernel(fn, 1,1,1, 1,1,1, 0, NULL, NULL, NULL) → cuCtxSynchronize() → cuModuleUnload → cuCtxDestroy → exit.
   - Compile with `-lcuda`.
2. Capture to `sniffed/cu_launch_null.jsonl`
3. Inspection: report ioctl count and number of ioctls with kernel writes.

**Validation:** non-empty capture.  
**Pass / Fail:** standard criteria.

---

## Phase 11 — Step 5: Replay cu_launch_null

**Goal:** Replay the null kernel launch end-to-end with 0 failed ioctls.

**Actions:**
```bash
sudo python3 replay/replay.py sniffed/cu_launch_null.jsonl 2>&1
```

**Validation:** `0 failed`.

**Note:** The launch descriptor buffer likely contains the function handle and grid dimensions packed together. If ioctls fail here, report the failing req code, its full `before` hex, and the seq numbers of all surrounding ioctls for context.

**Pass:** `0 failed`.  
**Fail:** detailed report.

---

## Phase 12 — Write, capture, and replay cu_memcpy (read back)

**Goal:** Prove memory written by a kernel can be read back through replay.

**Actions:**
1. Write `programs/cu_memcpy.cu`:
   - Allocate a buffer on GPU, launch null kernel (no writes), cuMemcpyDtoH into a host buffer, check host buffer is all zeros, exit 0 if correct else exit 1.
   - This validates the entire chain without requiring a real computation.
2. Capture to `sniffed/cu_memcpy.jsonl`
3. Replay:
```bash
sudo python3 replay/replay.py sniffed/cu_memcpy.jsonl 2>&1
```

**Validation:** `0 failed`.  
**Pass / Fail:** standard criteria.

---

## Phase 13 — Write, capture, and replay vector_add (first verifiable output)

**Goal:** Replay a kernel that produces verifiable numeric output.

**Actions:**
1. Write `programs/vector_add.cu`:
   - Two input arrays A[N], B[N] initialized to 1.0 and 2.0.
   - Kernel computes C[i] = A[i] + B[i].
   - cuMemcpyHtoD inputs, launch, cuMemcpyDtoH output, verify C[i] == 3.0 for all i.
   - Exit 0 if correct, 1 if not.
   - N = 64 (small; keep ioctl surface minimal).
2. Capture to `sniffed/vector_add.jsonl`
3. Replay:
```bash
sudo python3 replay/replay.py sniffed/vector_add.jsonl 2>&1
```

**Validation:** `0 failed`.

**Note:** At this point replay.py cannot verify the *output* (it doesn't execute the host-side verification code, only the ioctls). `0 failed` is the pass criterion. Numeric correctness will be validated in the matmul phase using a separate verification step.

**Pass:** `0 failed`.  
**Fail:** detailed report.

---

## Phase 14 — Write, capture, and replay matmul (target milestone)

**Goal:** Replay a matrix multiplication kernel end-to-end with 0 failed ioctls.

**Actions:**
1. Write `programs/matmul.cu`:
   - Naive or tiled matmul, C = A × B, all float32.
   - Matrix size: 128×128 (balance between realistic and fast to capture/replay).
   - Initialize A and B with known values (e.g. identity or ones).
   - cuMemcpyHtoD A and B, launch matmul kernel, cuMemcpyDtoH C.
   - Host-side verification: check C against expected result, exit 0/1.
2. Capture to `sniffed/matmul.jsonl`
3. Replay:
```bash
sudo python3 replay/replay.py sniffed/matmul.jsonl 2>&1
```

**Validation:** `0 failed`.

**Pass:** `0 failed` — this is the milestone.  
**Fail:** detailed report.

---

## Summary Table

| Phase | Step | New challenge | Pass criterion |
|-------|------|---------------|----------------|
| -2 | create handle_map.py | none | all assertions passed |
| -1 | create replay.py | none | static checks passed |
| 0 | verify files in place | none | syntax OK, git clean |
| 1 | cu_init | fd mapping | Python == C: 0 failed |
| 2-3 | cu_device_get | none new | 0 failed |
| 4-5 | cu_ctx_create | first RM object handle | 0 failed |
| 6-7 | cu_mem_alloc | GPU VA in response | 0 failed |
| 8-9 | cu_module_load | module handle, PTX JIT | 0 failed |
| 10-11 | cu_launch_null | function handle, launch descriptor | 0 failed |
| 12 | cu_memcpy | DtoH ioctl chain | 0 failed |
| 13 | vector_add | first real kernel | 0 failed |
| 14 | matmul | **target milestone** | 0 failed |

---

## Agent Rules (always apply)

1. **Never proceed past a failed phase.** Stop and report.
2. **Never modify replay.c or handle_map.h.** The C replay is the reference; do not touch it.
3. **Never merge two phases into one run.** Each phase is a checkpoint.
4. **When writing .cu programs**, check the `programs/` directory first. If the file already exists and compiles, use it; do not overwrite.
5. **When a replay fails**, always report: (a) the full replay output, (b) the seq number and req code of the first FAIL line, (c) whether that req code appears in `intercept/handle_offsets.json`, (d) the raw `before`/`after` hex for that seq from the JSONL.
6. **handle_offsets.json is the fix lever.** If ioctls fail due to handle patching, the fix is to run `tools/find_handle_offsets.py` on two captures of the failing step, update `handle_offsets.json`, and re-run the replay. Do not patch replay.py to hardcode handle values.