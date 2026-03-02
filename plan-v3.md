# Agent Execution Plan: CUDA ioctl Replay Ladder
**Executor:** Opus 4.6 in Cursor  
**Autonomous execution:** proceed through all phases without waiting for human approval. Only stop on a failed validation.  
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

## Phase -3 — Environment Pre-flight

**Goal:** Verify the build environment before writing any code. Detect blockers early.

**Actions:**  
Run each of the following checks and record the results. If any **hard requirement** fails, STOP and report.

```bash
cd cuda-ioctl-map

echo "=== 1. GPU compute capability ==="
nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -1
# Record this value — it determines the PTX .target (e.g. 7.5 → sm_75)

echo "=== 2. nvcc path and version ==="
NVCC="${NVCC:-/usr/local/cuda-12.5/bin/nvcc}"
$NVCC --version 2>/dev/null || echo "NVCC NOT FOUND at $NVCC"

echo "=== 3. Device permissions ==="
ls -la /dev/nvidiactl 2>/dev/null
# If permissions are crw-rw-rw- (world-readable), sudo is NOT needed.
# If permissions are crw-rw---- (root/video only), sudo IS needed.

echo "=== 4. Sniffer library ==="
if [ -f intercept/libnv_sniff.so ]; then
    echo "libnv_sniff.so: exists"
else
    echo "libnv_sniff.so: MISSING — will build in Phase 0"
fi

echo "=== 5. Existing artifacts ==="
echo "replay/handle_map.py: $([ -f replay/handle_map.py ] && echo 'exists' || echo 'MISSING')"
echo "replay/replay.py:     $([ -f replay/replay.py ] && echo 'exists' || echo 'MISSING')"
echo "handle_offsets.json:   $([ -f intercept/handle_offsets.json ] && echo 'exists' || echo 'MISSING')"
ls programs/*.cu 2>/dev/null | wc -l | xargs -I{} echo "CUDA programs (.cu): {} files"
ls sniffed/*.jsonl 2>/dev/null | wc -l | xargs -I{} echo "Existing captures: {} files"
```

**Validation:**
- `nvidia-smi` returns a compute capability (e.g. `7.5`)
- `nvcc --version` prints a version string
- `/dev/nvidiactl` exists

**Hard requirements:**
- GPU must be present and accessible
- `nvcc` must be available at the configured path

**Outputs recorded for later phases:**
- `SM_TARGET`: derived from compute capability (e.g. `7.5` → `sm_75`, `8.0` → `sm_80`)
- `NEED_SUDO`: `true` if `/dev/nvidiactl` is NOT world-readable, `false` otherwise

**Pass:** all three hard requirements met, SM_TARGET and NEED_SUDO values recorded.  
**Fail:** report which requirement failed and the exact error.

---

## Phase -2 — Create handle_map.py

**Goal:** Create the handle/fd patching module that replay.py will import.

**Skip condition:** If `cuda-ioctl-map/replay/handle_map.py` already exists AND passes the validation test below, skip this phase entirely.

**Context:** This file owns all remapping logic. replay.py imports it but contains none of this logic itself. Keep concerns strictly separated.

**Actions:**  
Create `cuda-ioctl-map/replay/handle_map.py` with the following architecture:

```
handle_map.py
├── class FdMap
│     ├── learn_open(orig_fd, live_fd)   — called for each 'open' event in seq order
│     ├── get(orig_fd) -> int            — returns live_fd or -1 if not mapped
│     └── patch_fds(buf, schema) -> bytearray
│           — replaces captured fd values at schema.fd_offsets with live fds
│           — needed for NV_ESC_REGISTER_FD and similar ioctls that embed fd numbers
│
├── class ReqSchema
│     ├── input_handle_offsets: list[int]  — byte offsets to patch BEFORE ioctl
│     ├── output_handle_offset: int|None   — byte offset to learn handle from AFTER ioctl
│     ├── fd_offsets: list[int]            — byte offsets that hold kernel fd numbers (not RM handles)
│     └── from_dict(d) -> ReqSchema        — parses one entry from handle_offsets.json
│           — reads "handle_offsets" → input_handle_offsets
│           — reads "output_handle_offset" → output_handle_offset (None if absent)
│           — reads "fd_offsets" → fd_offsets (empty list if absent)
│
├── def load_schemas(path) -> dict[int, ReqSchema]
│     — loads handle_offsets.json; keys are ioctl req codes as ints
│     — returns empty dict (not an error) if file does not exist
│     — logs at WARNING (not INFO) if file is missing, so the message is visible
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
- `FdMap.patch_fds` reads uint32 values at each `schema.fd_offsets` position, looks up the live fd via `self.get()`, and writes the live fd back. Skips if offset is out of bounds or fd is not mapped.
- `HandleMap.patch_input` must not raise on unknown handle — log WARNING and pass through
- `load_schemas` must log at WARNING level (not INFO) if the file is missing, so it's visible even at default log level

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

# FdMap.patch_fds round-trip
schema_with_fd = ReqSchema([], None, fd_offsets=[0])
buf = bytearray(struct.pack('<I', 3))  # original fd=3
fm.patch_fds(buf, schema_with_fd)
assert struct.unpack('<I', buf)[0] == 7, f'expected 7, got {struct.unpack(chr(60)+chr(73), buf)[0]}'

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

**Skip condition:** If `cuda-ioctl-map/replay/replay.py` already exists AND passes the validation test below, skip this phase entirely.

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
│           5. fd_map.patch_fds(buf, schema)     ← IMPORTANT: also patch embedded fd numbers
│           6. ret = do_ioctl(live_fd, req, buf)
│           7. print "[seq] OK/FAIL/SKIP req=... fd=... ret=..."
│           8. on success: hm.learn_output(event['after'], buf, schema)
│           9. on failure: log WARNING with seq, req, errno
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
- `/dev/nvidia*` devices on this machine are world-readable (`crw-rw-rw-`), so `sudo` is **not** required. However, if `os.open` raises `PermissionError`, print a clear message: "Permission denied — run as root or check /dev/nvidia* permissions" and exit 1.
- Summary line format must be exactly: `DONE — {ok}/{total} succeeded, {failed} failed, {skipped} skipped`
- `EMPTY_SCHEMA = ReqSchema([], None)` — defined once at module level, used as default when req has no schema entry
- Step 5 in the ioctl handler (`fd_map.patch_fds`) is critical: some ioctls like `NV_ESC_REGISTER_FD` (0xC00446C9) embed a file descriptor number in the payload. Without fd patching, ctx_create and above will fail.

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
assert 'patch_fds' in src, 'must call fd_map.patch_fds for fd-offset patching'
print('replay.py: static checks passed')
"
```

**Pass:** prints `replay.py: static checks passed`, no exceptions.  
**Fail:** report the full traceback.

---

## Phase 0 — Verify files and build infrastructure

**Goal:** Confirm the replay engine files exist, build the sniffer library, and verify the repo is in a workable state.

**Actions:**
1. Confirm `cuda-ioctl-map/replay/replay.py` exists
2. Confirm `cuda-ioctl-map/replay/handle_map.py` exists
3. Confirm `replay.c` and `Makefile` are untouched (no modifications to committed files)
4. Build the sniffer library if it doesn't exist or is stale:
```bash
cd cuda-ioctl-map
make -C intercept --no-print-directory
```
5. Syntax-check both Python files:
```bash
python3 -c "import ast; ast.parse(open('replay/replay.py').read()); ast.parse(open('replay/handle_map.py').read()); print('syntax OK')"
```

**Validation:**
- `replay/replay.py` and `replay/handle_map.py` exist
- `intercept/libnv_sniff.so` exists (built or pre-existing)
- Syntax check prints `syntax OK`
- `replay.c` and `handle_map.h` have no uncommitted modifications (`git diff --name-only replay/replay.c replay/handle_map.h` returns empty)

**Pass:** all four checks pass.  
**Fail:** report which check failed.

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
./replay/replay sniffed/cu_init.jsonl 2>&1 | tail -5
```
2. Run the Python replay:
```bash
python3 replay/replay.py sniffed/cu_init.jsonl 2>&1
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
1. Check if `programs/cu_device_get` binary already exists and runs. If yes, skip to step 3.
2. Check if `programs/cu_device_get.cu` exists. If not, write it:
   - Call cuInit(0), then cuDeviceGet(&device, 0), then exit.
   - Minimal: no context, no alloc, no kernel.
   - Compile: `$NVCC -arch=native -O0 -lcuda -o programs/cu_device_get programs/cu_device_get.cu`
3. Capture (note: use `NV_SNIFF_LOG` env var, NOT stdout redirect):
```bash
cd cuda-ioctl-map
NV_SNIFF_LOG=sniffed/cu_device_get.jsonl \
LD_PRELOAD=./intercept/libnv_sniff.so \
./programs/cu_device_get
```
4. Inspect the capture:
```bash
python3 -c "
import json, sys
events = [json.loads(l) for l in open('sniffed/cu_device_get.jsonl')]
types = [e['type'] for e in events]
ioctl_count = sum(1 for t in types if t == 'ioctl')
open_count  = sum(1 for t in types if t == 'open')
print(f'open={open_count} ioctl={ioctl_count} total={len(events)}')
if ioctl_count == 0:
    print('ERROR: zero ioctls captured — capture failed', file=sys.stderr)
    sys.exit(1)
print('capture OK')
"
```

**Validation:**  
- File is non-empty (>0 lines)
- Contains at least one `open` event
- **ioctl count > 0** (critical: zero ioctls means the capture mechanism failed)
- ioctl count is >= the cu_init ioctl count (it is a superset)

**Pass:** non-empty capture with open+ioctl events, ioctl count > 0.  
**Fail:** report `wc -l` output and the full python inspection output. If ioctl count is 0, check that `NV_SNIFF_LOG` was set correctly and that `libnv_sniff.so` was loaded.

---

## Phase 3 — Step 1: Replay cu_device_get

**Goal:** Replay the cu_device_get capture and get 0 failed ioctls.

**Actions:**
```bash
cd cuda-ioctl-map
python3 replay/replay.py sniffed/cu_device_get.jsonl 2>&1
```

**Validation:**  
Summary line must show `0 failed`.

**Pass:** `0 failed` in summary.  
**Fail:** report the full output; note the first FAIL line's seq number, req code, and errno.

---

## Phase 4 — Write and capture cu_ctx_create

**Goal:** Produce a clean JSONL capture for cuCtxCreate.

**Actions:**
1. Check if `programs/cu_ctx_create` binary already exists and runs. If yes, skip to step 3.
2. Check if `programs/cu_ctx_create.cu` exists. If not, write it:
   - Call cuInit(0), cuDeviceGet(&device, 0), cuCtxCreate(&ctx, 0, device), cuCtxDestroy(ctx), exit.
   - Compile: `$NVCC -arch=native -O0 -lcuda -o programs/cu_ctx_create programs/cu_ctx_create.cu`
3. Capture:
```bash
cd cuda-ioctl-map
NV_SNIFF_LOG=sniffed/cu_ctx_create.jsonl \
LD_PRELOAD=./intercept/libnv_sniff.so \
./programs/cu_ctx_create
```
4. Run inspection (same script as Phase 2, adjusted for this file).

**Validation:** non-empty capture, ioctl count > 0, ioctl count > cu_device_get count.  
**Pass / Fail:** same criteria as Phase 2.

---

## Phase 5 — Step 2: Replay cu_ctx_create

**Goal:** Replay cu_ctx_create with 0 failed ioctls.

**Actions:**
```bash
cd cuda-ioctl-map
python3 replay/replay.py sniffed/cu_ctx_create.jsonl 2>&1
```

**Validation:** `0 failed` in summary.  

**Important:** cuCtxCreate introduces the first RM object handle (the context handle). If ioctls fail here, check two things:
1. Is the failing req code present in `intercept/handle_offsets.json`?
2. Does `handle_map.py` implement `FdMap.patch_fds` and is `replay.py` calling it? The `NV_ESC_REGISTER_FD` ioctl (0xC00446C9) embeds a file descriptor in the payload — without fd patching, this will fail.

Report:
- The failing req code
- The before/after hex for that seq in the JSONL
- Whether that req code is present in `intercept/handle_offsets.json`

**Pass:** `0 failed`.  
**Fail:** report as above.

---

## Phase 6 — Write and capture cu_mem_alloc

**Goal:** Produce a JSONL capture for a minimal cuMemAlloc + cuMemFree sequence.

**Actions:**
1. Check if `programs/cu_mem_alloc` binary exists. If yes, skip to step 3.
2. Check if `programs/cu_mem_alloc.cu` exists. If not, write it:
   - cuInit → cuDeviceGet → cuCtxCreate → cuMemAlloc(&ptr, 1024) → cuMemFree(ptr) → cuCtxDestroy → exit.
   - Compile: `$NVCC -arch=native -O0 -lcuda -o programs/cu_mem_alloc programs/cu_mem_alloc.cu`
3. Capture:
```bash
cd cuda-ioctl-map
NV_SNIFF_LOG=sniffed/cu_mem_alloc.jsonl \
LD_PRELOAD=./intercept/libnv_sniff.so \
./programs/cu_mem_alloc
```
4. Inspection:
```bash
python3 -c "
import json, sys
events = [json.loads(l) for l in open('sniffed/cu_mem_alloc.jsonl')]
ioctls = [e for e in events if e['type']=='ioctl']
if len(ioctls) == 0:
    print('ERROR: zero ioctls captured', file=sys.stderr); sys.exit(1)
diffs = [(e['seq'], e['req']) for e in ioctls if e['before'] != e['after']]
print(f'total ioctls: {len(ioctls)}, ioctls with kernel writes: {len(diffs)}')
print('capture OK')
"
```

**Validation:**  
- ioctl count > 0
- At least one ioctl with `before != after` (the kernel wrote a GPU virtual address back)

**Pass:** non-empty, at least one kernel-write ioctl exists.  
**Fail:** report inspection output.

---

## Phase 7 — Step 3: Replay cu_mem_alloc

**Goal:** Replay cu_mem_alloc with 0 failed ioctls.

**Actions:**
```bash
cd cuda-ioctl-map
python3 replay/replay.py sniffed/cu_mem_alloc.jsonl 2>&1
```

**Validation:** `0 failed`.

**Note:** Memory allocation ioctls return GPU virtual addresses in the response buffer. The replay does not need to *use* those addresses yet — it just needs the ioctls to return 0. The address patching (for kernel launch) comes later.

**Pass:** `0 failed`.  
**Fail:** report first FAIL line's seq, req, errno, and whether the req code is in `handle_offsets.json`. Then follow the **Handle Offset Recovery Procedure** at the end of this document.

---

## Phase 8 — Write and capture cu_module_load (PTX)

**Goal:** Produce a JSONL capture for loading a minimal PTX module.

**Actions:**
1. Check if `programs/cu_module_load` binary exists and runs. If yes, skip to step 3.
2. Check if `programs/cu_module_load.cu` exists. If not, write it:
   - Contains an embedded PTX string for a kernel that does nothing.
   - **CRITICAL:** The PTX `.target` must match the GPU from Phase -3. Use the `SM_TARGET` value:
     ```
     .version 6.4
     .target sm_75          ← use SM_TARGET from Phase -3 (e.g. sm_75 for compute 7.5)
     .address_size 64
     .visible .entry null_kernel() { ret; }
     ```
   - If the PTX target is higher than the GPU's compute capability, `cuModuleLoadData` will fail with `CUDA_ERROR_NO_BINARY_FOR_GPU`.
   - Calls: cuInit → cuDeviceGet → cuCtxCreate → cuModuleLoadData(&mod, ptx_string) → cuModuleUnload(mod) → cuCtxDestroy → exit.
   - Compile: `$NVCC -arch=native -O0 -lcuda -o programs/cu_module_load programs/cu_module_load.cu`
3. Capture:
```bash
cd cuda-ioctl-map
NV_SNIFF_LOG=sniffed/cu_module_load.jsonl \
LD_PRELOAD=./intercept/libnv_sniff.so \
./programs/cu_module_load
```
4. Inspection (same ioctl count > 0 check). Expect ioctl count to be significantly higher than cu_ctx_create due to PTX JIT compilation ioctls.

**Validation:** non-empty capture, ioctl count > 0, ioctl count noticeably larger than cu_ctx_create.  
**Pass / Fail:** standard criteria.

---

## Phase 9 — Step 4: Replay cu_module_load

**Goal:** Replay module load with 0 failed ioctls.

**Actions:**
```bash
cd cuda-ioctl-map
python3 replay/replay.py sniffed/cu_module_load.jsonl 2>&1
```

**Validation:** `0 failed`.

**Note:** This is the hardest step so far. Module load produces a module handle, which is an opaque kernel object. If this step fails, follow the **Handle Offset Recovery Procedure** below.

**Pass:** `0 failed`.  
**Fail:** detailed report + recovery procedure.

---

## Phase 9.5 — Regenerate handle_offsets.json from richer captures

**Goal:** Ensure `handle_offsets.json` covers all ioctl codes seen in the richer captures (module load, kernel launch, memory ops), not just the cu_init subset.

**Context:** The existing `handle_offsets.json` was generated from `cu_init` captures only. The new programs introduce ioctl codes for PTX JIT, kernel launch, and memory operations that may contain handle fields not yet in the schema. Replay may succeed today due to deterministic handle allocation on this machine, but this is fragile. Regenerating from a richer capture makes the schema robust.

**Actions:**
1. Capture the most complex program (cu_module_load or, if already available, a later program) twice:
```bash
cd cuda-ioctl-map
NV_SNIFF_LOG=sniffed/offset_discovery_a.jsonl \
LD_PRELOAD=./intercept/libnv_sniff.so \
./programs/cu_module_load

NV_SNIFF_LOG=sniffed/offset_discovery_b.jsonl \
LD_PRELOAD=./intercept/libnv_sniff.so \
./programs/cu_module_load
```
2. Run handle offset discovery:
```bash
python3 tools/find_handle_offsets.py \
    sniffed/offset_discovery_a.jsonl \
    sniffed/offset_discovery_b.jsonl \
    intercept/handle_offsets.json
```
3. Inspect the updated schema:
```bash
python3 -c "
import json
with open('intercept/handle_offsets.json') as f:
    schemas = json.load(f)
print(f'Total req schemas: {len(schemas)}')
for req, info in sorted(schemas.items()):
    ho = info.get('handle_offsets', [])
    oho = info.get('output_handle_offset', '-')
    fdo = info.get('fd_offsets', [])
    print(f'  {req}: handle_offs={ho}, output_off={oho}, fd_offs={fdo}')
"
```
4. Re-validate that cu_init still replays with 0 failed after the schema update:
```bash
python3 replay/replay.py sniffed/cu_init.jsonl 2>&1 | tail -3
```

**Validation:**
- `handle_offsets.json` has MORE entries than before (new ioctl codes discovered)
- cu_init replay still shows `0 failed`

**Pass:** schema grew, cu_init still passes.  
**Fail:** report the schema diff and any replay failures.

---

## Phase 10 — Write and capture cu_launch_null

**Goal:** Produce a JSONL capture for a complete kernel launch of a no-op kernel.

**Actions:**
1. Check if `programs/cu_launch_null` binary exists. If yes, skip to step 3.
2. Check if `programs/cu_launch_null.cu` exists. If not, write it:
   - Same null PTX kernel as Phase 8. **Use the same SM_TARGET from Phase -3.**
   - Full sequence: cuInit → cuDeviceGet → cuCtxCreate → cuModuleLoadData → cuModuleGetFunction(&fn, mod, "null_kernel") → cuLaunchKernel(fn, 1,1,1, 1,1,1, 0, NULL, NULL, NULL) → cuCtxSynchronize() → cuModuleUnload → cuCtxDestroy → exit.
   - Compile: `$NVCC -arch=native -O0 -lcuda -o programs/cu_launch_null programs/cu_launch_null.cu`
3. Capture:
```bash
cd cuda-ioctl-map
NV_SNIFF_LOG=sniffed/cu_launch_null.jsonl \
LD_PRELOAD=./intercept/libnv_sniff.so \
./programs/cu_launch_null
```
4. Inspection: report ioctl count (must be > 0) and number of ioctls with kernel writes.

**Validation:** non-empty capture, ioctl count > 0.  
**Pass / Fail:** standard criteria.

---

## Phase 11 — Step 5: Replay cu_launch_null

**Goal:** Replay the null kernel launch end-to-end with 0 failed ioctls.

**Actions:**
```bash
cd cuda-ioctl-map
python3 replay/replay.py sniffed/cu_launch_null.jsonl 2>&1
```

**Validation:** `0 failed`.

**Note:** The launch descriptor buffer likely contains the function handle and grid dimensions packed together. If ioctls fail here, report the failing req code, its full `before` hex, and the seq numbers of all surrounding ioctls for context. Follow the **Handle Offset Recovery Procedure** if needed.

**Pass:** `0 failed`.  
**Fail:** detailed report.

---

## Phase 12 — Write, capture, and replay cu_memcpy (read back)

**Goal:** Prove memory written by a kernel can be read back through replay.

**Actions:**
1. Check if `programs/cu_memcpy` binary exists. If yes, skip to step 3.
2. Check if `programs/cu_memcpy.cu` exists. If not, write it:
   - Allocate a buffer on GPU, launch null kernel (no writes), cuMemcpyDtoH into a host buffer, check host buffer is all zeros, exit 0 if correct else exit 1.
   - **Use SM_TARGET from Phase -3 for PTX.**
   - This validates the entire chain without requiring a real computation.
   - Compile: `$NVCC -arch=native -O0 -lcuda -o programs/cu_memcpy programs/cu_memcpy.cu`
3. Capture:
```bash
cd cuda-ioctl-map
NV_SNIFF_LOG=sniffed/cu_memcpy.jsonl \
LD_PRELOAD=./intercept/libnv_sniff.so \
./programs/cu_memcpy
```
4. Replay:
```bash
python3 replay/replay.py sniffed/cu_memcpy.jsonl 2>&1
```

**Validation:** `0 failed`.  
**Pass / Fail:** standard criteria.

---

## Phase 13 — Write, capture, and replay vector_add (first verifiable output)

**Goal:** Replay a kernel that produces verifiable numeric output.

**Actions:**
1. Check if `programs/vector_add` binary exists. If yes, skip to step 3.
2. Check if `programs/vector_add.cu` exists. If not, write it:
   - Two input arrays A[N], B[N] initialized to 1.0 and 2.0.
   - Kernel computes C[i] = A[i] + B[i].
   - cuMemcpyHtoD inputs, launch, cuMemcpyDtoH output, verify C[i] == 3.0 for all i.
   - Exit 0 if correct, 1 if not.
   - N = 64 (small; keep ioctl surface minimal).
   - **Use SM_TARGET from Phase -3 for PTX.**
   - **IMPORTANT:** Do not name any PTX register `%tid` — this shadows the special register `%tid.x` and causes a JIT failure. Use names like `%r0` or `%my_tid` instead.
   - Compile: `$NVCC -arch=native -O0 -lcuda -o programs/vector_add programs/vector_add.cu`
3. Capture:
```bash
cd cuda-ioctl-map
NV_SNIFF_LOG=sniffed/vector_add.jsonl \
LD_PRELOAD=./intercept/libnv_sniff.so \
./programs/vector_add
```
4. Replay:
```bash
python3 replay/replay.py sniffed/vector_add.jsonl 2>&1
```

**Validation:** `0 failed`.

**Note:** At this point replay.py cannot verify the *output* (it doesn't execute the host-side verification code, only the ioctls). `0 failed` is the pass criterion. Numeric correctness was validated when the CUDA program ran during capture.

**Pass:** `0 failed`.  
**Fail:** detailed report.

---

## Phase 14 — Write, capture, and replay matmul (target milestone)

**Goal:** Replay a matrix multiplication kernel end-to-end with 0 failed ioctls.

**Actions:**
1. Check if `programs/matmul` binary exists. If yes, skip to step 3.
2. Check if `programs/matmul.cu` exists. If not, write it:
   - Naive matmul, C = A × B, all float32.
   - Matrix size: 128×128 (balance between realistic and fast to capture/replay).
   - Initialize A and B to all 1.0 → C[i][j] should be 128.0.
   - cuMemcpyHtoD A and B, launch matmul kernel, cuMemcpyDtoH C.
   - Host-side verification: check C[i][j] == 128.0 for all i,j, exit 0/1.
   - **Use SM_TARGET from Phase -3 for PTX.**
   - Compile: `$NVCC -arch=native -O0 -lcuda -o programs/matmul programs/matmul.cu`
3. Capture:
```bash
cd cuda-ioctl-map
NV_SNIFF_LOG=sniffed/matmul.jsonl \
LD_PRELOAD=./intercept/libnv_sniff.so \
./programs/matmul
```
4. Verify the CUDA program itself succeeded (exit 0):
```bash
echo "CUDA program exit code: $?"
```
5. Replay:
```bash
python3 replay/replay.py sniffed/matmul.jsonl 2>&1
```

**Validation:** `0 failed`.

**Pass:** `0 failed` — this is the milestone.  
**Fail:** detailed report + recovery procedure.

---

## Phase 15 — Final regeneration of handle_offsets.json from matmul

**Goal:** Regenerate `handle_offsets.json` using the most complex program (matmul) to ensure maximum ioctl code coverage. This makes the schema robust for future use.

**Actions:**
1. Capture matmul twice:
```bash
cd cuda-ioctl-map
NV_SNIFF_LOG=sniffed/matmul_offset_a.jsonl \
LD_PRELOAD=./intercept/libnv_sniff.so \
./programs/matmul

NV_SNIFF_LOG=sniffed/matmul_offset_b.jsonl \
LD_PRELOAD=./intercept/libnv_sniff.so \
./programs/matmul
```
2. Regenerate:
```bash
python3 tools/find_handle_offsets.py \
    sniffed/matmul_offset_a.jsonl \
    sniffed/matmul_offset_b.jsonl \
    intercept/handle_offsets.json
```
3. Re-replay ALL programs to verify the new schema doesn't break anything:
```bash
for jsonl in sniffed/cu_init.jsonl sniffed/cu_device_get.jsonl sniffed/cu_ctx_create.jsonl sniffed/cu_mem_alloc.jsonl sniffed/cu_module_load.jsonl sniffed/cu_launch_null.jsonl sniffed/cu_memcpy.jsonl sniffed/vector_add.jsonl sniffed/matmul.jsonl; do
    if [ -f "$jsonl" ]; then
        echo "=== Replaying $(basename $jsonl) ==="
        python3 replay/replay.py "$jsonl" 2>&1 | grep "^DONE"
    fi
done
```

**Validation:** ALL replays show `0 failed`.

**Pass:** all replays pass with updated schema.  
**Fail:** report which replay broke and the diff in `handle_offsets.json`.

---

## Phase 16 — Update programs/Makefile

**Goal:** Update the Makefile so `make -C programs all` builds ALL programs, not just the original four.

**Actions:**
1. Read the current `programs/Makefile`.
2. Add all new targets to `TARGETS` and add corresponding build rules:
   - `cu_mem_alloc`, `cu_module_load`, `cu_launch_null`, `cu_memcpy`, `vector_add`, `matmul`
3. Add `-lcuda` to `NVCCFLAGS` so all programs link correctly:
   ```makefile
   NVCCFLAGS = -arch=native -O0 -lcuda
   ```
4. Use a pattern rule to reduce repetition:
   ```makefile
   %: %.cu
   	$(NVCC) $(NVCCFLAGS) -o $@ $<
   ```

**Validation:**
```bash
cd cuda-ioctl-map
make -C programs clean
make -C programs all
echo "Exit code: $?"
ls -1 programs/cu_init programs/cu_mem_alloc programs/matmul programs/vector_add 2>/dev/null | wc -l
```

**Pass:** `make all` exits 0, all 10 binaries exist.  
**Fail:** report the make error output.

---

## Phase 17 — Clean up discovery captures

**Goal:** Remove temporary offset-discovery JSONL files that are no longer needed.

**Actions:**
```bash
cd cuda-ioctl-map
rm -f sniffed/offset_discovery_a.jsonl sniffed/offset_discovery_b.jsonl
rm -f sniffed/matmul_offset_a.jsonl sniffed/matmul_offset_b.jsonl
```

**Pass:** files removed (or already absent).

---

## Summary Table

| Phase | Step | New challenge | Pass criterion |
|-------|------|---------------|----------------|
| -3 | environment pre-flight | detect blockers | GPU + nvcc present |
| -2 | create handle_map.py | fd_offsets patching | all assertions passed |
| -1 | create replay.py | fd patching in ioctl loop | static checks passed |
| 0 | verify + build sniffer | none | syntax OK, .so built |
| 1 | cu_init | fd mapping | Python == C: 0 failed |
| 2-3 | cu_device_get | none new | 0 failed |
| 4-5 | cu_ctx_create | first RM object handle + fd patching | 0 failed |
| 6-7 | cu_mem_alloc | GPU VA in response | 0 failed |
| 8-9 | cu_module_load | module handle, PTX JIT | 0 failed |
| 9.5 | regenerate handle_offsets | broader ioctl coverage | schema grew, cu_init still passes |
| 10-11 | cu_launch_null | function handle, launch descriptor | 0 failed |
| 12 | cu_memcpy | DtoH ioctl chain | 0 failed |
| 13 | vector_add | first real kernel | 0 failed |
| 14 | matmul | **target milestone** | 0 failed |
| 15 | final schema regeneration | robustness | all replays pass |
| 16 | update Makefile | build system consistency | make all succeeds |
| 17 | clean up temp files | hygiene | done |

---

## Agent Rules (always apply)

1. **Never proceed past a failed phase.** Stop and report.
2. **Never modify replay.c or handle_map.h.** The C replay is the reference; do not touch it.
3. **Never merge two phases into one run.** Each phase is a checkpoint.
4. **When writing .cu programs**, check the `programs/` directory first. If the file already exists and compiles, use it; do not overwrite.
5. **When a replay fails**, always report: (a) the full replay output, (b) the seq number and req code of the first FAIL line, (c) whether that req code appears in `intercept/handle_offsets.json`, (d) the raw `before`/`after` hex for that seq from the JSONL. Then follow the **Handle Offset Recovery Procedure**.
6. **handle_offsets.json is the fix lever.** If ioctls fail due to handle patching, the fix is to run `tools/find_handle_offsets.py` on two captures of the failing step, update `handle_offsets.json`, and re-run the replay. Do not patch replay.py to hardcode handle values.
7. **Never use `sudo`** unless Phase -3 determined `NEED_SUDO=true`. On this machine, `/dev/nvidia*` devices are world-readable (`crw-rw-rw-`), so `sudo` is not needed and will hang in non-interactive terminals.
8. **All capture commands must use `NV_SNIFF_LOG=<path>`**, not stdout redirect. The sniffer writes to the file specified by this env var, not stdout.
9. **All PTX strings must use `.target sm_XX`** where XX matches the GPU compute capability from Phase -3. Using a higher target than the GPU will cause `cuModuleLoadData` to fail.
10. **Never name a PTX register `%tid`** — this shadows the special register `%tid.x` and causes a JIT compilation failure. Use `%r0`, `%my_tid`, or similar.

---

## Handle Offset Recovery Procedure

When a replay fails and the failure is due to handle patching (typically `EINVAL` or `EPERM` on an ioctl that previously succeeded), follow this procedure:

1. **Identify the failing ioctl:**
```bash
# Find the first FAIL line
python3 replay/replay.py sniffed/<program>.jsonl 2>&1 | grep FAIL | head -1
```

2. **Check if the req code is in handle_offsets.json:**
```bash
python3 -c "
import json
with open('intercept/handle_offsets.json') as f:
    schemas = json.load(f)
req = '0xXXXXXXXX'  # ← paste the failing req code here
if req in schemas:
    print(f'FOUND: {schemas[req]}')
else:
    print(f'NOT FOUND — this ioctl has no handle schema')
"
```

3. **Capture the failing program twice for offset discovery:**
```bash
NV_SNIFF_LOG=sniffed/<program>_a.jsonl LD_PRELOAD=./intercept/libnv_sniff.so ./programs/<program>
NV_SNIFF_LOG=sniffed/<program>_b.jsonl LD_PRELOAD=./intercept/libnv_sniff.so ./programs/<program>
```

4. **Regenerate handle_offsets.json:**
```bash
python3 tools/find_handle_offsets.py \
    sniffed/<program>_a.jsonl \
    sniffed/<program>_b.jsonl \
    intercept/handle_offsets.json
```

5. **Re-capture the program** (the old capture used old handle values):
```bash
NV_SNIFF_LOG=sniffed/<program>.jsonl LD_PRELOAD=./intercept/libnv_sniff.so ./programs/<program>
```

6. **Re-replay:**
```bash
python3 replay/replay.py sniffed/<program>.jsonl 2>&1
```

7. **Verify earlier programs still work** (schema change might break them):
```bash
python3 replay/replay.py sniffed/cu_init.jsonl 2>&1 | grep "^DONE"
```

If the replay still fails after this procedure, STOP and report the full output. The issue may require manual struct analysis.
