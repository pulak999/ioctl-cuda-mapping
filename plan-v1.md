# plan-v1 — From Mapping to Reproduction

## Target

Take the existing CUDA → ioctl mapping pipeline and extend it until we can
**replay a captured ioctl sequence without any CUDA library**, producing the
same driver-side effects as the original CUDA program.

Scope for this plan: the three (soon four) cumulative programs already in the
repo — `cu_init`, `cu_device_get`, `cu_ctx_create`, `cu_ctx_destroy`.

---

## Where we are (end of plan-v0 + code review)

### What works

| Asset | Status |
|-------|--------|
| 4-stage pipeline (trace → parse → annotate → schema/report) | ✅ Working |
| **All 4 cumulative CUDA programs** compiled, traced, and in full pipeline | ✅ (cu_ctx_destroy added) |
| strace flags standardised: `-f -e trace=ioctl,openat,close` across all collection | ✅ (re-collected) |
| **31 unique ioctl codes, 0 unknowns**, 0 none-confidence entries | ✅ |
| Reproducibility checker, 100% determinism on all 4 programs | ✅ |
| Frequency-stability tracking added to repro reports and rendered in report | ✅ (C1 fix) |
| Lookup table with 35 entries, confidence-tiered (incl. NV_ESC_GPU_ENUM_BOUNDARY) | ✅ |
| `programs/Makefile` for reproducible binary builds | ✅ (PR1 fix) |
| Baseline snapshot for regression (`baseline/20260220T224129Z/`) | ✅ |
| B2 gap-warning: build_schema.py alerts when a step's predecessor is missing | ✅ |

### What's missing for replay

| Gap | Severity | Detail |
|-----|----------|--------|
| **No ioctl argument data** | 🔴 Critical | `args` field is a pointer address (`0x7ffd…`), not the struct contents. Cannot replay without knowing what data was passed. |
| **No struct-aware parsing** | 🔴 Critical | Even with argument bytes we'd need struct layouts to interpret them (handle IDs, class codes, control sub-commands). |
| **No replay tool** | 🔴 Critical | Nothing that opens `/dev/nvidia*` and issues raw ioctls from captured data. |
| **No handle-patching logic** | 🟡 High | The kernel returns new handle values on each run. Replay must remap original→new handles in subsequent calls. |
| **schema not diffable for baseline comparison** | 🟡 Medium | `decoded` field in master_mapping.json embeds raw strace lines with ASLR-varying pointer values; file-level diff against baseline is unusable for automation (XC3). |
| **No dmesg capture** | 🟠 Low (for now) | Not available on this machine. Can revisit later; strace + interposer cover the userspace side. |

> **Resolved since plan-v0:** strace flag inconsistency ✅, cu_ctx_create teardown contamination ✅ (fixed by `-f` flag — cu_ctx_create now correctly shows 25 unique codes, all 6 teardown codes appear only in cu_ctx_destroy's delta), cu_ctx_destroy not in pipeline ✅.

---

## Phased plan

```
Phase 0  Fix data quality       ← strace flags, re-collect, complete cu_ctx_destroy
Phase 1  LD_PRELOAD interposer  ← capture ioctl argument buffers
Phase 2  Struct registry        ← map ioctl codes → C struct layouts
Phase 3  Rich parse             ← decode argument bytes into structured JSON
Phase 4  Replay tool            ← open devices, issue ioctls, patch handles
Phase 5  Validate               ← compare replay trace against original
```

---

## Phase 0 — Fix data quality ✅ COMPLETE

**Goal:** Make sure every trace in the repo is collected with the same flags
and that the four in-scope programs all flow through the full pipeline.

### 0.1 Standardise strace invocation ✅

Canonical command confirmed and applied everywhere:

```bash
strace -f \
       -e trace=ioctl,openat,close \
       -o traces/<step_name>.log \
       ./programs/<step_name>
```

`check_reproducibility.py`'s `STRACE_CMD` matches exactly.

### 0.2 Re-collect primary traces ✅

All four programs re-traced on 3-GPU machine (TITAN RTX ×3) with standardised
flags. Machine has `/dev/nvidia0`–`2` (no `/dev/nvidia3`).

### 0.3 Re-run full pipeline ✅

All four steps parsed, annotated, schema rebuilt, report regenerated.

### 0.4 Verify consistency ✅

Confirmed results (3-GPU machine, current baseline):

| Step | Total ioctls | Unique codes | New codes vs prev | Net event delta |
|------|-------------|--------------|-------------------|-----------------|
| cu_init | **230** | **15** | 15 | +230 |
| cu_device_get | **230** | **15** | 0 | 0 |
| cu_ctx_create | **575** | **25** | 10 | +345 |
| cu_ctx_destroy | **776** | **31** | 6 | +201 |

- `cu_ctx_create` now shows **25 unique codes** — matches repro run counts. ✅
- `cu_ctx_destroy` introduces exactly **6 teardown codes** as predicted:
  `UVM_UNREGISTER_GPU`, `UVM_UNREGISTER_CHANNEL`, `UVM_UNMAP_EXTERNAL`,
  `UVM_PAGEABLE_MEM_ACCESS`, `NV_ESC_CHECK_VERSION_STR (variant)`,
  `NV_ESC_RM_MAP_MEMORY`. ✅
- Total unique codes across all four steps: **31**, 0 unknowns, 0 none-confidence. ✅
- `NV_ESC_GPU_ENUM_BOUNDARY` (`0xC00C46D1`) confirmed as normal GPU enumeration
  boundary signal — fires once in cu_init when `/dev/nvidia3` probe returns EIO.
  Normal on any machine with < 4 GPUs. Added to lookup table as `confidence: low`.

### 0.5 Save new baseline ⚠️ PENDING

```bash
TS=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p baseline/$TS
cp -r annotated parsed schema CUDA_IOCTL_MAP.md baseline/$TS/
```

The existing baseline (`baseline/20260220T224129Z/`) reflects the old 4-GPU
machine with un-standardised flags and is now stale. Save a new one before
starting Phase 1.

---

## Phase 1 — LD_PRELOAD ioctl interposer

**Goal:** Capture the full argument buffer (bytes in + bytes out) for every
ioctl call on `/dev/nvidia*` devices, without needing kernel access or dmesg.

### 1.1 How it works

Write a shared library `intercept/libnv_sniff.so` that:

1. Interposes `ioctl()` via `LD_PRELOAD`.
2. On every `ioctl(fd, request, arg)` call:
   a. Check if `fd` points to a `/dev/nvidia*` device (maintain an internal
      fd→path map by also interposing `open`/`openat`/`close`).
   b. If yes:
      - Compute the arg buffer size from the request code's `_IOC_SIZE()` bits.
      - Snapshot the arg buffer **before** calling the real ioctl (input data).
      - Call the real `ioctl()`.
      - Snapshot the arg buffer **after** (output data — many nvidia ioctls are
        `_IOC_READ|_IOC_WRITE`).
      - Log a record: `{ fd, device, request_code, arg_size, arg_before[hex],
        arg_after[hex], return_value }`.
   c. If no, just forward to real `ioctl()`.
3. Write the log to a file specified by `NV_SNIFF_LOG` env var.

### 1.2 Size extraction

The NVIDIA ioctl request codes encode direction + size in the standard Linux
`_IOC` format:

```c
#define _IOC_SIZEBITS   14
#define _IOC_SIZEMASK   ((1 << _IOC_SIZEBITS) - 1)
#define _IOC_SIZE(nr)   (((nr) >> 16) & _IOC_SIZEMASK)
```

For example `0xC020462A` → size = `0x20` = 32 bytes.

**Exception — UVM ioctls on `/dev/nvidia-uvm`:** these use a flat numbering
scheme (e.g. `0x30000001`, `0x00000025`) where `_IOC_SIZE()` returns 0.  For
these we need a separate size table (Phase 2 will build it from
`nvidia-uvm/uvm_linux_ioctl.h`).  Until then, fall back to a generous
fixed-size capture (e.g. 4096 bytes) for UVM codes, which is safe because
UVM ioctls pass a pointer to a flat struct that's always < 4 KB.

### 1.3 File layout

```
intercept/
  nv_sniff.c          # the LD_PRELOAD interposer
  Makefile            # builds libnv_sniff.so
  uvm_ioctl_sizes.h   # UVM ioctl code → struct size table (filled in Phase 2)
```

### 1.4 Output format

One JSON-lines file, one line per ioctl:

```jsonl
{"seq":0,"fd":10,"dev":"/dev/nvidiactl","req":"0xC020462A","sz":32,"before":"0a00000000000000...","after":"0a00000001000000...","ret":0}
{"seq":1,"fd":10,"dev":"/dev/nvidiactl","req":"0xC0104629","sz":16,"before":"...","after":"...","ret":0}
```

Hex-encoded raw bytes.  Struct decoding is Phase 3's job.

### 1.5 Collection

```bash
cd cuda-ioctl-map
make -C intercept

for step in cu_init cu_device_get cu_ctx_create cu_ctx_destroy; do
  NV_SNIFF_LOG=sniffed/${step}.jsonl \
  LD_PRELOAD=intercept/libnv_sniff.so \
  ./programs/${step}
done
```

We still keep the strace traces (Phase 0) as a cross-reference.  The sniffed
data is the new primary source for replay.

### 1.6 Verify capture

Quick sanity: for `cu_init`, the sniffed log should have ~333 records.  The
`req` codes should exactly match the parsed JSON from strace.  Write a small
`verify_sniff.py` that diffs the two.

**Exit criteria for Phase 1:** `sniffed/<step>.jsonl` files exist for all four
programs, record counts match strace, hex payloads are non-empty.

---

## Phase 2 — Struct registry

**Goal:** For every ioctl code observed in our traces, know the exact C struct
layout so we can decode the hex bytes from Phase 1 into named fields.

### 2.1 Source of truth

| Device | Source repo | Key header files |
|--------|-------------|-----------------|
| `/dev/nvidiactl` | `NVIDIA/open-gpu-kernel-modules` | `src/common/sdk/nvidia/inc/nvos.h` — NVOS21, NVOS54, NVOS00, NVOS02, etc. |
| `/dev/nvidiactl` | same | `src/nvidia/interface/nv-ioctl-numbers.h` — code→name mapping |
| `/dev/nvidia-uvm` | same | `kernel-open/nvidia-uvm/uvm_linux_ioctl.h` — UVM ioctl dispatch table |
| `/dev/nvidia-uvm` | same | `kernel-open/nvidia-uvm/uvm_ioctl.h` — UVM struct definitions |

### 2.2 Struct table

Build `intercept/struct_registry.json`:

```json
{
  "0xC020462A": {
    "name": "NV_ESC_RM_ALLOC",
    "struct": "NVOS21_PARAMETERS",
    "size": 32,
    "fields": [
      {"name": "hRoot",          "offset": 0,  "size": 4, "type": "handle"},
      {"name": "hObjectParent",  "offset": 4,  "size": 4, "type": "handle"},
      {"name": "hObjectNew",     "offset": 8,  "size": 4, "type": "handle"},
      {"name": "hClass",         "offset": 12, "size": 4, "type": "class_id"},
      {"name": "pAllocParms",    "offset": 16, "size": 8, "type": "pointer"},
      {"name": "paramsSize",     "offset": 24, "size": 4, "type": "uint32"},
      {"name": "status",         "offset": 28, "size": 4, "type": "uint32"}
    ]
  },
  "0xC0104629": {
    "name": "NV_ESC_RM_CONTROL",
    "struct": "NVOS54_PARAMETERS",
    "size": 16,
    "fields": [
      {"name": "hClient", "offset": 0, "size": 4, "type": "handle"},
      {"name": "hObject", "offset": 4, "size": 4, "type": "handle"},
      {"name": "cmd",     "offset": 8, "size": 4, "type": "control_cmd"},
      {"name": "status",  "offset": 12,"size": 4, "type": "uint32"}
    ]
  }
}
```

(This is illustrative — real field offsets must come from the headers with
sizeof/offsetof checks.  The 64-bit pointer and alignment padding fields
need careful handling.)

### 2.3 How to build it

1. Clone `open-gpu-kernel-modules` (already in the plan-v0 reference).
2. Write `intercept/build_struct_registry.py` that:
   - Parses the header files for each struct definition.
   - Computes field offsets (or just use a small C program that `#include`s
     the headers and prints `offsetof` / `sizeof` for each field).
   - Writes `struct_registry.json`.
3. For UVM ioctls, same approach with `uvm_ioctl.h` structs.
4. For ioctl codes that use inline/simple encoding (the `0x000000XX` UVM
   codes), record the struct and its size so Phase 1's interposer can
   capture the right number of bytes.

### 2.4 Priority order

Build struct definitions in this order (by frequency in our traces).
Counts are from the **current 3-GPU machine baseline** (see Phase 0.4):

| Priority | Code | Name | cu_init | cu_ctx_create | cu_ctx_destroy |
|----------|------|------|---------|---------------|----------------|
| P0 | `0xC020462A` | `NV_ESC_RM_ALLOC` | 178 | 292 | 296 |
| P0 | `0xC030462B` | `NV_ESC_RM_ALLOC` (large / NVOS64) | 22 | 123 | 123 |
| P0 | `0xC0104629` | `NV_ESC_RM_CONTROL` | 4 | 4 | 110 |
| P1 | `0xC038464E` | `NV_ESC_RM_VID_HEAP_CONTROL` | 3 | 30 | 30 |
| P1 | `0xC020464F` | `NV_ESC_RM_MAP_MEMORY` | 0 | 0 | 27 |
| P1 | `0x0000001C` | `UVM_UNMAP_EXTERNAL` | 0 | 0 | 20 |
| P1 | `0x0000001B` | `UVM_MAP_DYNAMIC_PARALLELISM_REGION` | 0 | 20 | 20 |
| P1 | `0x00000021` | `UVM_ALLOC_SEMAPHORE_POOL` | 0 | 24 | 24 |
| P1 | `0x00000049` | `UVM_MAP_EXTERNAL_SPARSE` | 0 | 24 | 24 |
| P1 | `0xC00446C9` | `NV_ESC_REGISTER_FD` | 6 | 14 | 14 |
| P2 | `0xC01046CE` | `NV_ESC_CHECK_VERSION_STR` | 0 | 8 | 8 |
| P2 | `0xC01046CF` | `NV_ESC_CHECK_VERSION_STR` (variant) | 0 | 0 | 8 |
| P2 | `0x00000017` | `UVM_MAP_EXTERNAL_ALLOCATION` | 1 | 10 | 10 |
| P2 | `0x00000022` | `UVM_PAGEABLE_MEM_ACCESS` | 0 | 0 | 26 |
| P2 | `0xC28465E`  | `NV_ESC_RM_DUP_OBJECT` | 0 | 1 | 1 |
| P2 | `0xC0384627` | `NV_ESC_RM_SHARE` | 0 | 5 | 5 |
| P3 | `0xC90046C8` | `NV_ESC_ATTACH_GPUS_TO_FD` | 2 | 2 | 2 |
| P3 | `0xC00846D6` | `NV_ESC_CARD_INFO` | 2 | 2 | 2 |
| P3 | Remaining low-count codes | | — | — | — |

**Exit criteria for Phase 2:** `struct_registry.json` covers every ioctl code
in the lookup table.  Sizes agree with `_IOC_SIZE()` for nvidiactl codes.
UVM struct sizes recorded.

---

## Phase 3 — Rich parse (decode argument bytes)

**Goal:** Turn the raw hex payloads from Phase 1 into structured JSON with
named fields, especially handle values and class IDs.

### 3.1 Script: `decode_sniff.py`

For each line in `sniffed/<step>.jsonl`:

1. Look up `req` in `struct_registry.json`.
2. Decode `before` hex bytes into field values using the struct layout.
3. Decode `after` hex bytes (output side).
4. For fields typed `handle`, record the value in a handle tracker.
5. For `NV_ESC_RM_ALLOC`, extract `hClass` to identify what object type was
   allocated (root client, device, subdevice, channel, etc.).
6. For `NV_ESC_RM_CONTROL`, extract `cmd` to identify the control sub-command
   (this is the inner command ID like `NV2080_CTRL_CMD_*`).

### 3.2 Output: `decoded/<step>.json`

```json
{
  "cuda_call": "cu_init",
  "ioctl_sequence": [
    {
      "seq": 0,
      "req": "0xC020462A",
      "name": "NV_ESC_RM_ALLOC",
      "device": "/dev/nvidiactl",
      "input": {
        "hRoot": "0x00000000",
        "hObjectParent": "0x00000000",
        "hObjectNew": "0x00000000",
        "hClass": "0x00000041",
        "hClass_name": "NV01_ROOT_CLIENT"
      },
      "output": {
        "hRoot": "0xDE130001",
        "hObjectParent": "0x00000000",
        "hObjectNew": "0xDE130001",
        "hClass": "0x00000041",
        "status": 0
      },
      "return_value": 0
    }
  ]
}
```

### 3.3 Handle graph

Build a tree of RM objects:

```
NV01_ROOT_CLIENT (0xDE130001)
  └── NV01_DEVICE_0 (0xDE130002)
        ├── NV20_SUBDEVICE_0 (0xDE130003)
        └── NV50_THIRD_PARTY_P2P (0xDE130005)
              └── ...
```

This handle graph is critical for Phase 4 — it tells the replay tool which
handles are parents of which.

### 3.4 RM_CONTROL sub-command decoding

`NV_ESC_RM_CONTROL` calls carry a 4-byte `cmd` field that identifies the
specific control operation.  These are defined in the `ctrl/` headers under
`open-gpu-kernel-modules/src/common/sdk/nvidia/inc/ctrl/`.  Example:

- `0x20800110` → `NV2080_CTRL_CMD_GPU_GET_INFO_V2`
- `0x00000301` → `NV0000_CTRL_CMD_GPU_GET_ATTACHED_IDS`

Build a secondary lookup table `intercept/rm_ctrl_cmds.json` mapping cmd
values to names.  Priority: only the ~96 unique cmds observed in our traces.

**Exit criteria for Phase 3:** `decoded/<step>.json` files for all four
programs.  Handle graph extracted.  RM_CONTROL sub-commands identified.

---

## Phase 4 — Replay tool

**Goal:** A C program that takes decoded ioctl data and replays it against
the real GPU driver without any CUDA library involvement.

### 4.1 Architecture

```
replay/
  replay.c              # main replay engine
  handle_map.h          # original-handle → replay-handle translation
  replay_cu_init.json   # input data (from Phase 3, possibly simplified)
  Makefile
```

### 4.2 Core loop

```c
int nvidiactl_fd = open("/dev/nvidiactl", O_RDWR);
// similarly open /dev/nvidia0, /dev/nvidia-uvm as needed

for (each ioctl in sequence) {
    // 1. Prepare arg buffer from captured input data
    memcpy(buf, captured_input, size);

    // 2. Patch handles: replace every handle field with its replay equivalent
    for (each handle field in struct) {
        uint32_t orig = read_field(buf, field);
        uint32_t mapped = handle_map_get(orig);
        if (mapped) write_field(buf, field, mapped);
    }

    // 3. Issue the ioctl
    int ret = ioctl(fd, request_code, buf);

    // 4. Extract output handles and update the map
    //    e.g. for RM_ALLOC, the kernel writes hObjectNew in the output
    uint32_t orig_out = captured_output.hObjectNew;
    uint32_t real_out = read_field(buf, hObjectNew_offset);
    handle_map_put(orig_out, real_out);

    // 5. Log result
    printf("[%d] %s ret=%d\n", seq, name, ret);
}
```

### 4.3 Handle patching — the key insight

When the original CUDA program called `NV_ESC_RM_ALLOC` and the kernel
returned handle `0xDE130001`, every subsequent ioctl that references that
handle used `0xDE130001`.  During replay the kernel will return a *different*
handle (say `0xAB070001`).  The replay tool must:

1. **On alloc-type ioctls (RM_ALLOC, RM_ALLOC_MEMORY):**
   Record `original_handle → replay_handle` in a map.

2. **On all subsequent ioctls:**
   Before calling ioctl, scan the input buffer for handle-typed fields and
   replace them using the map.

The struct registry (Phase 2) tells us exactly which fields at which offsets
are handles.

### 4.4 Pointer fields

Some structs contain pointer fields (`pAllocParms` in NVOS21/NVOS64).  These
point to **secondary parameter structs** that the kernel also reads.

Two approaches:
- **Option A (simple, good enough for POC):** The interposer in Phase 1
  cannot follow pointers (it only captures the top-level struct).  For the
  POC, null out pointer fields and see which ioctls succeed.  Many RM_ALLOC
  calls work with `pAllocParms = NULL` (the hClass alone is enough for
  simple object types like NV01_ROOT_CLIENT).
- **Option B (full fidelity):** Extend the interposer to follow known
  pointer fields (using the struct registry to know which fields are
  pointers and what struct they point to).  This is an enhancement after
  the basic replay works.

Start with Option A.  Track which ioctls fail with `status != 0` and
incrementally add pointer captures for those.

### 4.5 POC target: replay `cuInit`

`cuInit` is the simplest and highest-value first target:

- **230 total ioctls, 15 unique codes** (3-GPU machine baseline)
- Heavy on `NV_ESC_RM_ALLOC` (**178 calls**) and `NV_ESC_RM_ALLOC (large)` (**22 calls**)
- `NV_ESC_RM_CONTROL`: 4 calls (low in cuInit; spikes to 110 in cuCtxDestroy)
- Deterministic: presence-reproducibility 100%, frequency-stability pending re-run
- Touches `/dev/nvidiactl`, `/dev/nvidia0`–`2`, `/dev/nvidia-uvm`
- One `NV_ESC_GPU_ENUM_BOUNDARY` fires when `/dev/nvidia3` probe fails — replay
  must replicate this by probing sequentially and issuing the boundary ioctl after
  the first failed open.

Success criteria: `replay programs/cu_init` opens device files, issues ~230
ioctls, all return 0, and `nvidia-smi` or `/proc/driver/nvidia/` shows the
expected GPU state afterwards.

### 4.6 Incremental targets

After `cuInit` replays cleanly:

1. **`cu_device_get`** — should be trivial since it adds 0 new ioctls.
2. **`cu_ctx_create`** — adds ~15 new codes including channel registration,
   UVM setup, memory mapping.  This is the hard one.
3. **`cu_ctx_destroy`** — teardown codes (RM_FREE, UVM_UNREGISTER, etc.).

**Exit criteria for Phase 4:** `replay` tool runs for `cu_init`, all ioctls
return success (ret=0, status=0 in output structs), no kernel errors.

---

## Phase 5 — Validate

**Goal:** Prove the replay produces the same driver-side state as the original
CUDA program.

### 5.1 Trace-level comparison

Run the replay tool under strace and compare the ioctl sequence against the
original:

```bash
strace -f -e trace=ioctl,openat,close \
       -o traces/replay_cu_init.log \
       ./replay/replay decoded/cu_init.json

python3 parse_trace.py traces/replay_cu_init.log
# diff parsed/replay_cu_init.json vs parsed/cu_init.json
```

The request codes and their order should match.  Return values should match.
Handle values will differ (expected — that's what the handle map is for).

### 5.2 Driver state comparison

- `nvidia-smi` before and after replay — check GPU utilisation, memory,
  process list.
- `/proc/driver/nvidia/gpus/<pci-addr>/information` — device state.
- For cu_ctx_create: verify that a GPU context exists after replay (visible
  via RM_CONTROL query ioctls).

### 5.3 Round-trip test

The ultimate validation:

1. Run `cu_init` under the interposer → capture.
2. Replay the capture → should succeed.
3. Run `cu_init` again while the replayed state is still live → it should
   see that the driver is already initialised and behave accordingly.

---

## Directory structure (target)

```
cuda-ioctl-map/
  programs/              # (existing) .cu source + compiled binaries
  traces/                # (existing) strace logs
  parsed/                # (existing) parsed JSON from strace
  annotated/             # (existing) annotated JSON
  schema/                # (existing) master_mapping.json
  lookup/                # (existing) ioctl_table.json
  sniffed/               # (NEW) raw hex payloads from interposer
  decoded/               # (NEW) struct-decoded JSON with named fields
  intercept/             # (NEW) LD_PRELOAD interposer
    nv_sniff.c
    Makefile
    struct_registry.json
    uvm_ioctl_sizes.h
    rm_ctrl_cmds.json
    build_struct_registry.py
  replay/                # (NEW) ioctl replay tool
    replay.c
    handle_map.h
    Makefile
  baseline/              # (existing) snapshots
  CUDA_IOCTL_MAP.md      # (existing) report
```

---

## Execution order and dependencies

```
Phase 0 ──→ Phase 1 ──→ Phase 3 ──→ Phase 4 ──→ Phase 5
                │                       ↑
                └──→ Phase 2 ──────────┘
```

Phase 2 (struct registry) can be done in parallel with Phase 1 (interposer)
since they're independent work.  Phase 3 needs both.  Phase 4 needs Phase 3.

---

## Risks and mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| `_IOC_SIZE` returns 0 for UVM ioctls | Interposer captures wrong amount | Build UVM size table from `uvm_ioctl.h`; fall back to 4 KB capture |
| Pointer fields in NVOS21/NVOS64 (`pAllocParms`) | Replay sends wrong sub-params | Start with NULL pAllocParms (works for many classes); incrementally add pointer-following |
| Kernel returns different handle values | Subsequent ioctls fail | Handle-patching map in replay tool (Phase 4.3) |
| Multi-GPU system (3 GPUs on this machine) | Device file mapping complexity | Replay opens `/dev/nvidia{0..2}`, probes `/dev/nvidia3` (expect EIO), then fires `NV_ESC_GPU_ENUM_BOUNDARY` |
| RM_CONTROL sub-commands have their own param structs | Deeper pointer chasing | Decode `cmd` field first (Phase 3.4); add per-cmd param structs iteratively |
| Thread ordering in `-f` traces | Non-determinism in trace merging | All programs are single-logical-thread for CUDA API calls; internal threads are idempotent bookkeeping |
| **`is_new` flag / `new_codes` contract drift** (XC1) | `new_ioctls_vs_prev` silently wrong after adding intermediate steps | When adding any of the 5 missing STEP_ORDER steps, re-run full `strace → parse → annotate` for all downstream steps. The B2 warning in `build_schema.py` will fire to prompt this. |
| **`decoded` field makes schema non-diffable** (XC3) | Baseline regression comparison produces pure noise | For Phase 5 validation, diff only `unique_codes`, `new_codes_vs_prev`, and `confidence_summary` fields — not raw schema files. Strip `decoded` from schema in a future cleanup pass. |

---

## Prior art to reference

- **geohot/cuda_ioctl_sniffer** — LD_PRELOAD approach for capturing NVIDIA
  ioctl traffic.  Similar goal, different scope (focused on sniffer not
  replay).  Worth reading for interposer patterns.
- **NVIDIA/open-gpu-kernel-modules** — the definitive source for struct
  layouts, ioctl dispatch, and RM object model.
- **nouveau driver** (upstream Linux) — reverse-engineered NVIDIA driver,
  useful for cross-referencing ioctl semantics when NVIDIA headers are
  ambiguous.

---

## Success metric

The plan is complete when:

```
$ ./replay/replay decoded/cu_init.json
[replay] opened /dev/nvidiactl (fd=3)
[replay] opened /dev/nvidia0 (fd=4)
[replay] opened /dev/nvidia1 (fd=5)
[replay] opened /dev/nvidia2 (fd=6)
[replay] open /dev/nvidia3 → EIO (expected — 3-GPU machine, boundary)
[replay] ioctl 0/230: NV_ESC_CARD_INFO → ret=0
[replay] ioctl 1/230: NV_ESC_ATTACH_GPUS_TO_FD → ret=0
...
[replay] ioctl 229/230: UVM_MAP_EXTERNAL_ALLOCATION → ret=0
[replay] DONE — 230/230 ioctls succeeded, 0 failed
```

No `libcuda.so`.  No `nvcc`.  Just raw `open()` + `ioctl()` + our captured
data, producing the same driver state that `cuInit(0)` would.
