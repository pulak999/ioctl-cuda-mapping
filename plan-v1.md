# plan-v1 — Raw Replay POC

## Goal

Capture full ioctl argument buffers via LD_PRELOAD, empirically discover handle
offsets by diffing two runs, then replay the captured sequence raw — with only
handle patching applied — without any CUDA library.

**No struct decoding. No schema registry. Just raw bytes + handle fixup.**

Scope: `cu_init` replay end-to-end. Later steps follow the same pipeline.

---

## Where we are

| Asset | Status |
|-------|--------|
| 4-stage pipeline (trace → parse → annotate → schema/report) | ✅ |
| All 4 cumulative CUDA programs traced and in full pipeline | ✅ |
| 31 unique ioctl codes, 0 unknowns | ✅ |
| 100% determinism on all 4 programs | ✅ |
| Baseline snapshot `baseline/20260220T224129Z/` | ✅ (stale — new one needed) |

### Critical gaps for replay

| Gap | Detail |
|-----|--------|
| No ioctl argument data | `args` field is a pointer address, not struct contents |
| No replay tool | Nothing that opens `/dev/nvidia*` and issues raw ioctls |
| No handle patching | Kernel returns new handles each run; replay must remap them |

---

## Architecture

```
Phase 1  Interposer      capture raw arg bytes (before + after) + open/close sequence
Phase 2  Offset finder   diff two captures → discover which byte offsets are handles
Phase 3  Replay tool     issue raw ioctls from capture, patch handles at known offsets
Phase 4  Validator       snapshot driver state before/after, diff for correctness
```

Phases 1 and 2 can be started in parallel if two captures are collected manually first.
Phase 3 requires output from both. Phase 4 requires Phase 3 passing.

```
Phase 1 ──→ Phase 2 ──→ Phase 3 ──→ Phase 4
```

---

## Phase 1 — LD_PRELOAD Interposer

**Goal:** For every ioctl on `/dev/nvidia*`, capture the full argument buffer
before and after the call. Also capture the open/close sequence verbatim
(including failed opens) so the replay tool can reproduce it without re-deriving
GPU topology.

### 1.1 File layout

```
intercept/
  nv_sniff.c          # LD_PRELOAD interposer
  Makefile            # builds libnv_sniff.so
  collect.sh          # runs all four programs under the interposer
```

### 1.2 nv_sniff.c — what it must do

Hook three libc functions via `dlsym(RTLD_NEXT, ...)`:

**`open` / `openat`**
- Call the real function.
- If the path matches `/dev/nvidia*`: record `fd → path` in an internal map.
- Always log the call: `{ "type": "open", "path": "...", "ret": <fd or errno> }`.
  Log failed opens too (ret < 0) — the replay needs to know `/dev/nvidia3`
  failed with EIO on a 3-GPU machine.

**`close`**
- Evict the fd from the internal map if present.
- Call the real function.

**`ioctl(fd, request, arg)`**
- If `fd` is not in the nvidia map: forward and return.
- Otherwise:
  1. Compute buffer size: `sz = _IOC_SIZE(request)`. If `sz == 0` (UVM flat
     codes), use `sz = 4096` as a safe fallback.
  2. Snapshot `arg` buffer → `before[sz]`.
  3. Call real `ioctl`.
  4. Snapshot `arg` buffer → `after[sz]`.
  5. Emit one JSON-lines record (see §1.3).

**Output** is written to the path in `$NV_SNIFF_LOG`. One file per program run.
Flush after every write — do not buffer.

### 1.3 Output format

One JSON-lines file. One line per event. Two event types:

```jsonl
{"type":"open","seq":0,"path":"/dev/nvidiactl","ret":3}
{"type":"open","seq":1,"path":"/dev/nvidia3","ret":-5}
{"type":"ioctl","seq":2,"fd":3,"dev":"/dev/nvidiactl","req":"0xC020462A","sz":32,"before":"0a00000000000000...","after":"0a00000001000000...","ret":0}
```

- `seq`: monotonically increasing across both opens and ioctls in the same run.
- `before` / `after`: lowercase hex, exactly `sz * 2` characters.
- `ret`: the raw return value of the syscall.

### 1.4 Size extraction detail

```c
#define _IOC_SIZEBITS  14
#define _IOC_SIZEMASK  ((1 << _IOC_SIZEBITS) - 1)
#define _IOC_SIZE(nr)  (((nr) >> 16) & _IOC_SIZEMASK)
```

`0xC020462A` → size = `0x20` = 32 bytes. ✓  
UVM codes like `0x30000001` → size = 0 → use 4096-byte fallback. ✓

### 1.5 Makefile

```makefile
CC      = gcc
CFLAGS  = -fPIC -shared -O2 -Wall
LDFLAGS = -ldl

all: libnv_sniff.so

libnv_sniff.so: nv_sniff.c
	$(CC) $(CFLAGS) -o $@ $< $(LDFLAGS)

clean:
	rm -f libnv_sniff.so
```

### 1.6 collect.sh

```bash
#!/usr/bin/env bash
set -euo pipefail
make -C intercept

mkdir -p sniffed

for step in cu_init cu_device_get cu_ctx_create cu_ctx_destroy; do
  echo "Collecting: $step"
  NV_SNIFF_LOG=sniffed/${step}.jsonl \
  LD_PRELOAD=intercept/libnv_sniff.so \
  ./programs/${step}
done
```

### 1.7 Acceptance criteria

- `sniffed/cu_init.jsonl` exists with ~230 ioctl lines.
- Every ioctl line has non-empty `before` and `after` fields.
- Log includes an `open` record for `/dev/nvidia3` with `ret` < 0.
- No lines where `sz > 0` but `before` is all zeros (would indicate a capture
  bug where the buffer wasn't read before the call).

---

## Phase 2 — Handle Offset Discovery

**Goal:** Identify which byte offsets within each ioctl's argument buffer contain
handle values, without reading any NVIDIA headers. Do this by diffing two
captures from the same program on the same machine.

### 2.1 Why this works

Handles are opaque 32-bit values assigned by the kernel's RM allocator. They
change between runs. Everything else in the buffer (class codes, flags,
fixed parameters) stays constant. Two runs → XOR the `before` buffers at each
sequence index → non-zero 4-byte windows are handle fields.

### 2.2 File layout

```
tools/
  collect_two_runs.sh     # runs cu_init twice, saves _a and _b captures
  find_handle_offsets.py  # diffs the two, emits handle_offsets.json
intercept/
  handle_offsets.json     # OUTPUT — consumed by Phase 3
```

### 2.3 collect_two_runs.sh

```bash
#!/usr/bin/env bash
set -euo pipefail

NV_SNIFF_LOG=sniffed/cu_init_a.jsonl \
LD_PRELOAD=intercept/libnv_sniff.so \
./programs/cu_init

NV_SNIFF_LOG=sniffed/cu_init_b.jsonl \
LD_PRELOAD=intercept/libnv_sniff.so \
./programs/cu_init
```

### 2.4 find_handle_offsets.py — logic

```
for each ioctl seq index that appears in both captures:
    req_code = same in both (sanity check — abort if not)
    before_a = bytes.fromhex(record_a["before"])
    before_b = bytes.fromhex(record_b["before"])
    for offset in range(0, min(len(before_a), len(before_b)) - 3, 4):
        val_a = u32(before_a, offset)
        val_b = u32(before_b, offset)
        if val_a != val_b and val_a != 0 and val_b != 0:
            mark offset as a handle field for this req_code
```

Aggregate across all matching ioctl records of the same `req_code`. An offset
is confirmed as a handle field if it varies in **more than half** of the records
for that code (handles are consistent within a run, so they'll vary in most
records for alloc-heavy codes).

### 2.5 Output format

```json
{
  "0xC020462A": {
    "name": "NV_ESC_RM_ALLOC",
    "handle_offsets": [0, 4, 8],
    "output_handle_offset": 8,
    "sample_count": 178
  },
  "0xC040462B": {
    "name": "NV_ESC_RM_ALLOC_large",
    "handle_offsets": [0, 4, 8, 12],
    "output_handle_offset": 8,
    "sample_count": 22
  }
}
```

`output_handle_offset` is the offset within `after` where the kernel writes the
newly allocated handle. For RM_ALLOC this is the `hObjectNew` field — the
offset that differs between `before` and `after` in the same record. Compute it
by XORing `before` and `after` from the same record.

### 2.6 Acceptance criteria

- `handle_offsets.json` exists.
- RM_ALLOC (`0xC020462A`) entry has at least 3 handle offsets.
- `output_handle_offset` is present for all alloc-type codes.
- Script prints a summary table: req_code | name | n_handle_fields | sample_count.

---

## Phase 3 — Raw Replay Tool

**Goal:** Open device files in the order the capture dictates, issue each ioctl
with the captured `before` bytes, patch handles at known offsets, and record
results. No struct decoding.

### 3.1 File layout

```
replay/
  replay.c         # main replay tool
  handle_map.h     # uint32_t → uint32_t open-addressed hash map
  Makefile
```

### 3.2 replay.c — logic

**Startup**
1. Read capture JSONL file (path = argv[1]).
2. Read `intercept/handle_offsets.json` (path = argv[2], or default).
3. Initialise empty handle map.

**Open sequence** — process all `type: open` records in seq order:
- For each successful open in the capture (`ret >= 0`): call `open(path, O_RDWR)`.
  Store `original_fd → replay_fd` mapping.
- For each failed open in the capture (`ret < 0`): attempt the open anyway,
  expect failure. Log `[open] /dev/nvidia3 → EIO (expected)`.

**Ioctl sequence** — process all `type: ioctl` records in seq order:

```
for each ioctl record:
    buf = bytes.fromhex(record.before)    // start with captured input
    offsets = handle_offsets[record.req]  // may be empty for non-alloc codes

    // patch input handles
    for each offset in offsets.handle_offsets:
        original_val = u32(buf, offset)
        if original_val in handle_map:
            write u32(handle_map[original_val], buf, offset)

    // issue the ioctl
    ret = ioctl(replay_fd_map[record.fd], record.req, buf)

    // if this is an alloc-type code, record the new output handle
    if offsets.output_handle_offset exists:
        original_out = u32(bytes.fromhex(record.after), offsets.output_handle_offset)
        replay_out   = u32(buf, offsets.output_handle_offset)
        handle_map[original_out] = replay_out

    // log
    status = (ret == 0) ? "OK" : "FAIL"
    printf("[%04d] req=%s ret=%d %s\n", seq, req_name, ret, status)
```

**Exit:** write the sentinel file `replay.ready` (so `run_validation.sh` knows the
ioctl sequence is complete before snapshotting driver state), then print summary
`N/N ioctls succeeded`. Exit 0 if all succeeded, 1 if any failed.

### 3.3 handle_map.h

Simple open-addressed hash map. Key: `uint32_t`. Value: `uint32_t`.
Fixed capacity (e.g. 4096 entries — more than enough for cuInit's ~200 allocs).
Operations: `hm_put(map, key, val)`, `hm_get(map, key, found_out)`.

### 3.4 Important: handle patching order

The RM object tree is strictly hierarchical:

```
hClient (root)
  └─ hDevice
       └─ hSubDevice
            └─ hChannel / hMemory / ...
```

Every RM_ALLOC carries `(hClient, hParent, hObject, hClass)` in its input buffer.
The seq order in the capture is already correct allocation order. Process records
strictly in seq order and the handle map will always be populated before it's needed.

**Gate:** get the root client allocation (first RM_ALLOC, hClass = NV01_ROOT_CLIENT)
patching correctly before debugging anything else. If seq 0 fails, nothing else will work.

### 3.5 Pointer fields (pAllocParms)

Some RM_ALLOC structs contain a `pAllocParms` pointer to a secondary parameter
struct. The interposer cannot follow pointers — it only captures the top-level
buffer. For the POC:

- Leave pointer fields as-is from `before` (they'll be stale virtual addresses).
- For most simple object types (NV01_ROOT_CLIENT, NV01_DEVICE_0, NV20_SUBDEVICE_0),
  the kernel ignores `pAllocParms == NULL` or a stale pointer.
- Track which ioctls fail with non-zero RM status and add pointer handling
  incrementally for those. Do **not** pre-emptively handle all pointer fields.

### 3.6 Makefile

```makefile
CC     = gcc
CFLAGS = -O2 -Wall -I.

all: replay

replay: replay.c handle_map.h
	$(CC) $(CFLAGS) -o $@ replay.c

clean:
	rm -f replay
```

### 3.7 Acceptance criteria

- `./replay/replay sniffed/cu_init.jsonl` runs to completion.
- Output shows 230/230 ioctls returning ret=0.
- No segfaults or memory errors (run under valgrind for final check).
- Handle map entries printed at exit show correct root→device→subdevice chain.

---

## Phase 4 — Driver State Validator

**Goal:** Confirm the replay produces the same driver-side object tree as real
`cu_init`. Compare snapshots taken after each, ignoring handle values and PIDs.

### 4.1 File layout

```
tools/
  snapshot_driver_state.sh   # captures driver state to a file
  compare_snapshots.py       # diffs two snapshots structurally
validation/
  run_validation.sh          # orchestrates both, prints PASS/FAIL
```

### 4.2 snapshot_driver_state.sh

Capture what the driver exposes at the process level:

```bash
#!/usr/bin/env bash
# Usage: snapshot_driver_state.sh <output_file>
OUT=$1

{
  echo "=== nvidia-smi ==="
  nvidia-smi -q 2>/dev/null || echo "nvidia-smi unavailable"

  echo "=== /proc/driver/nvidia/gpus ==="
  for f in /proc/driver/nvidia/gpus/*/information; do
    echo "--- $f ---"
    cat "$f" 2>/dev/null
  done

  echo "=== fd count ==="
  # count open /dev/nvidia* fds in the target process (passed as $2 if provided)
  if [ -n "${2:-}" ]; then
    ls -la /proc/$2/fd 2>/dev/null | grep nvidia | wc -l
  fi
} > "$OUT"
```

### 4.3 compare_snapshots.py

- Strips lines containing hex handles (patterns like `0x[0-9a-f]{6,}`),
  PIDs, and timestamps before diffing.
- Compares GPU count, memory used, device names.
- Exits 0 (PASS) if only handle/PID lines differ; exits 1 (FAIL) with diff
  output if structural fields differ.

### 4.4 run_validation.sh

```bash
#!/usr/bin/env bash
set -euo pipefail

echo "[1/4] Running real cu_init..."
./programs/cu_init
tools/snapshot_driver_state.sh validation/snapshot_real.txt

echo "[2/4] Running replay (kept alive for snapshot)..."
rm -f replay.ready
./replay/replay sniffed/cu_init.jsonl &
REPLAY_PID=$!
# Wait for replay to signal it has finished all ioctls (sentinel file written
# by replay.c before exit).  Poll every 100 ms for up to 10 s.
for i in $(seq 1 100); do
  [ -f replay.ready ] && break
  sleep 0.1
done
if [ ! -f replay.ready ]; then
  echo "ERROR: replay did not write replay.ready within 10 s" >&2
  kill $REPLAY_PID 2>/dev/null
  exit 1
fi
tools/snapshot_driver_state.sh validation/snapshot_replay.txt $REPLAY_PID
wait $REPLAY_PID
rm -f replay.ready

echo "[3/4] Comparing snapshots..."
python3 tools/compare_snapshots.py \
  validation/snapshot_real.txt \
  validation/snapshot_replay.txt

echo "[4/4] Done."
```

### 4.5 Acceptance criteria

- `run_validation.sh` completes without error.
- `compare_snapshots.py` exits 0 (PASS).
- If FAIL: the diff output clearly shows what structural state differs —
  this is actionable signal for fixing Phase 3, not a tooling bug.

---

## Directory structure (target state)

```
cuda-ioctl-map/
  programs/              # (existing) .cu sources + compiled binaries
  traces/                # (existing) strace logs
  parsed/                # (existing) parsed JSON from strace
  annotated/             # (existing) annotated JSON
  schema/                # (existing) master_mapping.json
  lookup/                # (existing) ioctl_table.json
  baseline/              # (existing) snapshots
  sniffed/               # (NEW) raw hex captures from interposer
    cu_init.jsonl
    cu_init_a.jsonl      # run A for handle offset discovery
    cu_init_b.jsonl      # run B for handle offset discovery
    cu_device_get.jsonl
    cu_ctx_create.jsonl
    cu_ctx_destroy.jsonl
  intercept/             # (NEW) LD_PRELOAD interposer
    nv_sniff.c
    Makefile
    collect.sh
    handle_offsets.json  # output of Phase 2
  tools/                 # (NEW) analysis + validation scripts
    collect_two_runs.sh
    find_handle_offsets.py
    snapshot_driver_state.sh
    compare_snapshots.py
  replay/                # (NEW) ioctl replay tool
    replay.c
    handle_map.h
    Makefile
  validation/            # (NEW) snapshot outputs
    run_validation.sh
  CUDA_IOCTL_MAP.md      # (existing) report
```

---

## Risks and mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| `_IOC_SIZE` returns 0 for UVM ioctls | Interposer captures wrong amount | Fall back to 4096-byte capture for size=0 codes |
| Pointer fields in NVOS21/NVOS64 (`pAllocParms`) | Replay sends stale pointers | Leave as-is; most simple class allocations ignore them. Fix incrementally per failing ioctl |
| Handle map populated out of order | Subsequent ioctls fail with wrong handles | Process strictly in seq order. Abort on first unpatchable handle miss |
| Root client alloc fails | Everything downstream fails | Debug seq=0 first before anything else |
| Two runs have different ioctl counts | Offset diffing misaligns | Abort `find_handle_offsets.py` if seq count differs between runs; check `check_reproducibility.py` first |
| UVM codes have flat numbering — can't XOR-diff cleanly | Offset finder produces noise for UVM codes | Limit offset discovery to `/dev/nvidiactl` ioctls for the POC; UVM structs are largely write-only anyway |
| Replay fd numbers differ from capture fd numbers | `fd` field in ioctl records maps to wrong replay fd | Maintain `original_fd → replay_fd` map from the open sequence replay |
| `decoded` field makes baseline schema non-diffable (XC3) | Regression diffs produce noise | For Phase 4 validation, diff only structural fields — not raw schema files |

---

## Pending before starting Phase 1

Save a fresh baseline now — the existing one reflects the old 4-GPU machine:

```bash
TS=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p baseline/$TS
cp -r annotated parsed schema CUDA_IOCTL_MAP.md baseline/$TS/
```

---

## Success metric

```
$ ./replay/replay sniffed/cu_init.jsonl
[open] /dev/nvidiactl → fd=3
[open] /dev/nvidia0   → fd=4
[open] /dev/nvidia1   → fd=5
[open] /dev/nvidia2   → fd=6
[open] /dev/nvidia3   → EIO (expected)
[0000] NV_ESC_CARD_INFO            req=0x46C0   ret=0  OK
[0001] NV_ESC_ATTACH_GPUS_TO_FD   req=0x46C8   ret=0  OK
...
[0229] UVM_MAP_EXTERNAL_ALLOCATION req=0x30000025 ret=0 OK
DONE — 230/230 succeeded, 0 failed
```

No `libcuda.so`. No `nvcc` at runtime. Just `open()` + `ioctl()` + captured bytes.

---

## Prior art

- **geohot/cuda_ioctl_sniffer** — LD_PRELOAD sniffer, same interception approach, no replay.
- **NVIDIA/open-gpu-kernel-modules** — definitive struct layouts and RM object model when pointer fields need to be handled.
- **nouveau** (linux/drivers/gpu/drm/nouveau) — reverse-engineered semantics, good cross-reference.

---

## Implementation discoveries (plan-v1 execution)

These facts were not known before running the pipeline. They update or refine
the assumptions in the plan above.

### D1 — Caller-specified handles (contradicts §2.5 and §3.4)

The plan assumed `NV_ESC_RM_ALLOC (0xC020462A)` would have the kernel write a
new handle into `hObjectNew` at the end of the call, making `before != after`
at that offset.  In practice **`before == after` for almost every RM_ALLOC
record**: libcuda pre-chooses the handle and writes it into the struct before
the call; the kernel validates and registers it rather than assigning one.

Consequence: `output_handle_offset` cannot be discovered by XOR-diffing
`before` and `after` within the same run.  Only two ioctl codes produce
kernel-assigned handles:

| Code | Name | Output offset |
|------|------|---------------|
| `0xC020462B` | `NV_ESC_RM_ALLOC_MEMORY` (small) | 8 |
| `0xC030462B` | `NV_ESC_RM_ALLOC_MEMORY` (large) | 8 |

These are the **root-client allocations** (one per GPU family fd).  Every other
RM object handle is pre-specified by libcuda as a deterministic constant.

### D2 — Only two handles vary between runs

Because libcuda pre-specifies almost all handles, the entire handle map at exit
contains only **4 entries** — the two root-client handles (one per
`/dev/nvidiactl` open) and two trivial identity mappings (`0x1`, `0x59`).
The sub-object handles (device, subdevice, memory, channel, …) are **identical
across runs** — libcuda uses the same constants every time.

Practical impact: the replay handle-patching problem is much simpler than
anticipated.  For `cu_init`, only `hRoot` and `hObjectParent` fields in
RM_ALLOC/RM_CONTROL calls needed live patching; all other handle fields were
already correct verbatim from the capture.

### D3 — NV_ESC_REGISTER_FD carries an fd number, not an RM handle

`NV_ESC_REGISTER_FD (0xC00446C9)` is issued on `/dev/nvidia{0,1,2}` with a
4-byte argument that is the **kernel fd number** of the associated
`/dev/nvidiactl` file descriptor.  This is not an RM handle and cannot be
discovered by XOR-diffing two runs (the fd number happens to be the same across
runs on a quiet machine).

Without patching, these 6 ioctls failed with `EINVAL` in the replay:

```
[0037] /dev/nvidia0  req=0xC00446C9  ret=-1  FAIL   (arg=0x0b → should be 0x07)
```

Fix: add a `fd_offsets` field to the `handle_offsets.json` schema.  For each
offset in `fd_offsets`, the replay looks up the captured fd value in `fd_map`
(the `original_fd → replay_fd` table) and patches the buffer.  The entry is
hard-coded in `find_handle_offsets.py` since it cannot be auto-detected:

```python
KNOWN_FD_OFFSETS = {
    "0xC00446C9": [0],  # NV_ESC_REGISTER_FD: 4-byte arg is the nvidiactl fd
}
```

With this fix the replay reached **230/230 ioctls succeeded, 0 failed**.

### D4 — UVM ioctl offset discovery is unreliable; filter to nvidiactl only

The 4096-byte fallback capture for size-0 UVM codes (`/dev/nvidia-uvm`) fills
the buffer with stack data that changes on every run.  Running XOR-diff on
these records produces dozens of spurious "handle" offsets (every aligned word
in the stack frame that happened to differ).

Fix: `find_handle_offsets.py` filters offset discovery to `/dev/nvidiactl`
ioctls only.  UVM ioctls are replayed verbatim from `before` without any
handle patching; they all succeeded in replay without modification.

### D5 — Driver state validation requires stripping hardware telemetry

`nvidia-smi -q` includes continuously-fluctuating fields (fan speed, power
draw, throughput counters) that differ between the two snapshots even on an
idle machine.  The initial `compare_snapshots.py` flagged these as structural
failures.

Fields stripped before diffing (in addition to handles, PIDs, timestamps):

- `Fan Speed`
- `Power Draw`
- `Throughput`
- Lines containing bare integers (from `grep -c` fd-count output)
- Lines containing `open nvidia fds in pid` (PID-specific section header)

With these stripped, `compare_snapshots.py` exits 0 (PASS) on every run.

### D6 — Final replay result

```
DONE — 230/230 succeeded, 0 failed
[handle_map] final state: 4 entries
  orig=0xC1D00415  →  replay=0xC1D00435
  orig=0x00000059  →  replay=0x00000059
  orig=0x00000001  →  replay=0x00000001
  orig=0xC1D00416  →  replay=0xC1D00436

PASS — snapshots are structurally identical.
```

No `libcuda.so`. No `nvcc` at runtime. Just `open()` + `ioctl()` + captured bytes.