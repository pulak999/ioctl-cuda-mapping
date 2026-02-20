# CUDA → ioctl Mapping Agent Plan
## Goal
Automatically map CUDA API calls to their underlying ioctl sequences using static lookup against NVIDIA's open-source driver, with Cursor agent fallback for unknown codes.

## Environment Assumptions
- Linux machine with CUDA installed (or RCS servers: thor/ironman/hulk.cs.columbia.edu)
- Tools: `nvcc`, `strace`, `dmesg`, `python3`
- No external API credits required

---

## Directory Structure
```
cuda-ioctl-map/
  programs/          # minimal .cu source files
  traces/            # raw strace logs per CUDA call
  dmesg/             # raw dmesg captures per CUDA call
  parsed/            # structured JSON from TraceParser
  annotated/         # JSON with static + Cursor annotations
  schema/            # final mapping output
  lookup/            # static ioctl lookup table seeded from NVIDIA repo
  CUDA_IOCTL_MAP.md  # final human-readable report
```

---

## Agent 1: TraceCollector

**Responsibility:** Write, compile, and run cumulative minimal CUDA programs, collecting strace and dmesg for each step.

### CUDA call sequence (cumulative — each program includes all prior calls)
1. `cuInit`
2. `cuInit` → `cuDeviceGet`
3. `cuInit` → `cuDeviceGet` → `cuCtxCreate`
4. `cuInit` → ... → `cuMemAlloc`
5. `cuInit` → ... → `cuMemcpyHtoD`
6. `cuInit` → ... → `cuModuleLoad` + `cuLaunchKernel` (use a trivial kernel)
7. `cuInit` → ... → `cuMemcpyDtoH`
8. `cuInit` → ... → `cuMemFree`
9. `cuInit` → ... → `cuCtxDestroy`

> Important: each step should only introduce the target API operation for that step.
> Do not include teardown (`cuCtxDestroy`) in the `cuCtxCreate` step.

### Steps for each program

1. Generate a `.cu` file in `programs/<step_name>.cu`

2. Compile:
```bash
nvcc -lcuda programs/<step_name>.cu -o programs/<step_name>
```

3. Clear dmesg:
```bash
sudo dmesg -C
```

4. Run with strace — follow threads/children and track FD closes:
```bash
strace -f \
       -e trace=ioctl,openat,close,mmap,munmap,read,write \
       -o traces/<step_name>.log \
       ./programs/<step_name>
```
> `-f` follows forked children and spawned threads so no CUDA driver activity
> is missed (W6).  `close` is included so the parser can evict FDs from its
> live map and avoid mislabeling ioctls when an FD number is reused (W2).
> With `-f` each output line is prefixed with a PID (`12345 ioctl(...)`);
> `parse_trace.py` strips this prefix automatically.

5. Capture dmesg immediately after:
```bash
sudo dmesg > dmesg/<step_name>.log
```

6. (Optional) Compute a raw text diff against the previous step for debugging only:
```bash
diff traces/<prev_step>.log traces/<step_name>.log > traces/<step_name>.diff
```
This diff is not the source of truth for delta tagging because pointer addresses and mmap ranges create noise.

### Example: programs/cu_init.cu
```c
#include <cuda.h>
#include <stdio.h>

int main() {
    CUresult r = cuInit(0);
    if (r != CUDA_SUCCESS) {
        const char *err; cuGetErrorString(r, &err);
        printf("FAILED: %s\n", err); return 1;
    }
    printf("cuInit OK\n");
    return 0;
}
```

### Example: programs/cu_device_get.cu
```c
#include <cuda.h>
#include <stdio.h>

int main() {
    cuInit(0);
    CUdevice dev;
    CUresult r = cuDeviceGet(&dev, 0);
    if (r != CUDA_SUCCESS) {
        const char *err; cuGetErrorString(r, &err);
        printf("FAILED: %s\n", err); return 1;
    }
    printf("cuDeviceGet OK, device=%d\n", dev);
    return 0;
}
```

> Continue this pattern cumulatively for each step.

---

## Agent 2: TraceParser

**Responsibility:** Parse raw strace logs into structured JSON, resolving file descriptors to actual device paths.

### Steps

1. For each `traces/<step_name>.log`:

   a. Scan `openat` calls to build a `fd → device` map (e.g. `"3" → "/dev/nvidiactl"`, `"4" → "/dev/nvidia0"`)

   b. Extract every `ioctl` call and produce a record:
   ```json
   {
     "sequence_index": 0,
     "fd": "3",
     "device": "/dev/nvidiactl",
     "request_code": "0xC020462A",
     "args": "0xc020462a, 0x7ffd...",
     "return_value": "0"
   }
   ```

2. Tag `"is_new": true` for request codes that appear in this step but not in the previous parsed step.
   This is a request-code set delta (`current_codes - prev_codes`), not a text diff delta.

3. Output to `parsed/<step_name>.json`:
```json
{
  "cuda_call": "cuInit",
  "fd_map": {
    "3": "/dev/nvidiactl",
    "4": "/dev/nvidia0"
  },
  "ioctl_sequence": [
    {
      "sequence_index": 0,
      "fd": "3",
      "device": "/dev/nvidiactl",
      "request_code": "0xC020462A",
      "args": "...",
      "return_value": "0",
      "is_new": true
    }
  ]
}
```

### Parser script skeleton
```python
# parse_trace.py
import re, json, sys

def build_fd_map(lines):
    fd_map = {}
    for line in lines:
        m = re.search(r'openat\(.*"(/dev/nvidia[^"]*)".*=\s*(\d+)', line)
        if m:
            fd_map[m.group(2)] = m.group(1)
    return fd_map

def extract_ioctls(lines, fd_map):
    ioctls = []
    for i, line in enumerate(lines):
        m = re.search(r'ioctl\((\d+),\s*(0x[0-9a-fA-F]+),?\s*(.*)\)\s*=\s*(-?\d+)', line)
        if m:
            fd, req, args, ret = m.groups()
            ioctls.append({
                "sequence_index": len(ioctls),
                "fd": fd,
                "device": fd_map.get(fd, "unknown"),
                "request_code": req,
                "args": args.strip(),
                "return_value": ret,
                "is_new": False  # parser sets true for first seen request codes vs previous step
            })
    return ioctls

if __name__ == "__main__":
    log_path = sys.argv[1]
    with open(log_path) as f:
        lines = f.readlines()
    fd_map = build_fd_map(lines)
    ioctls = extract_ioctls(lines, fd_map)
    output = {
        "cuda_call": log_path.split("/")[-1].replace(".log", ""),
        "fd_map": fd_map,
        "ioctl_sequence": ioctls
    }
    out_path = log_path.replace("traces/", "parsed/").replace(".log", ".json")
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"Parsed {len(ioctls)} ioctls → {out_path}")
```

---

## Agent 3: StaticAnnotator

**Responsibility:** Annotate each parsed ioctl using a static lookup table seeded from NVIDIA's open-gpu-kernel-modules repo. Flag unknowns for Cursor review.

### Step 1: Seed the lookup table

Clone NVIDIA's open source kernel modules and extract known ioctl numbers:
```bash
git clone --depth=1 https://github.com/NVIDIA/open-gpu-kernel-modules.git
grep -r "NV_ESC\|NVOS\|_IOC\|_IOWR\|_IOW\|_IOR" \
     open-gpu-kernel-modules/src/nvidia/interface/ \
     > lookup/raw_ioctl_definitions.txt
```

Then seed `lookup/ioctl_table.json` manually or with a script. Start with these well-known codes:

```json
{
  "0xC020462A": {
    "name": "NV_ESC_RM_ALLOC",
    "description": "Allocate an RM object (root client, device, subdevice, channel, context)",
    "phase": "object allocation",
    "confidence": "high",
    "source": "nv-ioctl-numbers.h"
  },
  "0xC018462D": {
    "name": "NV_ESC_RM_FREE",
    "description": "Free a previously allocated RM object",
    "phase": "object deallocation",
    "confidence": "high",
    "source": "nv-ioctl-numbers.h"
  },
  "0xC004463A": {
    "name": "NV_ESC_RM_IDLE_CHANNELS",
    "description": "Idle or quiesce GPU channels before teardown",
    "phase": "context teardown",
    "confidence": "high",
    "source": "nv-ioctl-numbers.h"
  },
  "0x46C0": {
    "name": "NV_ESC_CARD_INFO",
    "description": "Query basic GPU card information",
    "phase": "device query",
    "confidence": "high",
    "source": "nv-ioctl-numbers.h"
  },
  "0x4646": {
    "name": "NV_ESC_CHECK_VERSION_STR",
    "description": "Check driver version string compatibility",
    "phase": "initialization",
    "confidence": "high",
    "source": "nv-ioctl-numbers.h"
  },
  "0x46C8": {
    "name": "NV_ESC_ATTACH_GPUS_TO_FD",
    "description": "Attach GPU devices to a file descriptor for subsequent RM calls",
    "phase": "initialization",
    "confidence": "high",
    "source": "nv-ioctl-numbers.h"
  }
}
```

> **Add to this table as you discover new codes during annotation.** It is a living document.

### Step 2: Annotate parsed JSON

See `annotate_static.py` in the repo.  Key logic outline:

```python
# W7: needs_review is set for THREE categories, not just unknowns:
#   1. UNKNOWN  — code absent from lookup table
#   2. low      — code in table with confidence="low"
#   3. none     — code in table with confidence="none"
LOW_CONFIDENCE = {"low", "none"}

for i in data["ioctl_sequence"]:
    if c in LOOKUP:
        ann = dict(LOOKUP[c])
        ann["needs_review"] = ann.get("confidence","none") in LOW_CONFIDENCE
        i["annotation"] = ann
    else:
        i["annotation"] = {"name":"UNKNOWN", ..., "confidence":"none", "needs_review":True}
```

The `confidence_summary` field in the final schema (built by `build_schema.py`) counts unique codes at each tier: `high`, `medium`, `low`, `none`.  The report renders a summary table per step so reviewers can immediately see the annotation quality distribution.

### Step 3: Cursor review for unknowns and low-confidence entries

After running `annotate_static.py`, open any `annotated/<step>.json` in Cursor and paste this prompt for each `"needs_review": true` entry (this now includes low-confidence entries in addition to unknowns):

> "This ioctl request code `<code>` was captured from a CUDA program that just called `<cuda_call>`. The device is `<device>`. Cross-reference with NVIDIA's open-gpu-kernel-modules (especially `src/nvidia/interface/nv-ioctl-numbers.h`, `src/nvidia/generated/`, and the nouveau driver source). What does this ioctl likely do? Provide: (1) a name, (2) a description, (3) which phase of execution it belongs to, (4) your confidence level. If confirmed, add it to `lookup/ioctl_table.json` so future runs pick it up automatically."

---

## Agent 4: SchemaBuilder

**Responsibility:** Aggregate all annotated JSONs into a final structured mapping and human-readable report.

### W4 — Dual delta metrics

Each step in the schema carries **two independent delta metrics**:

| Metric | Field | What it captures |
|--------|-------|-----------------|
| Code-set delta | `new_codes_vs_prev` | ioctl request codes that are *new to this step* — absent from every previous cumulative trace |
| Event-level delta | `event_delta_vs_prev` | Per-code frequency change — how many more/fewer times each code fires relative to the previous step, regardless of novelty |
| Event scalar | `net_new_events` | `total_ioctls[this] − total_ioctls[prev]` |

The event-level delta is important because a step like `cu_device_get` may introduce **zero new codes** while still triggering a different *number* of ioctl events for the same codes — a real behavioural signal that would be invisible if only code-set novelty were reported.

### Step 1: Build master schema

See `build_schema.py` in the repo.  Key logic outline:

```python
from collections import Counter
prev_codes  = set()
prev_counts = Counter()   # W4

for fpath in FILES:
    cur_codes  = {i["request_code"] for i in data["ioctl_sequence"]}
    cur_counts = Counter(i["request_code"] for i in data["ioctl_sequence"])  # W4
    new_codes  = cur_codes - prev_codes

    # W4: per-code frequency delta
    event_delta = {}
    for code in sorted(cur_codes | set(prev_counts)):
        c, p = cur_counts.get(code,0), prev_counts.get(code,0)
        if c != p:
            event_delta[code] = {"prev_count": p, "cur_count": c, "delta": c-p}

    master["cuda_to_ioctl_map"][call] = {
        ...
        "new_codes_vs_prev":   len(new_codes),
        "net_new_events":      len(data["ioctl_sequence"]) - sum(prev_counts.values()),
        "event_delta_vs_prev": event_delta,
    }
    prev_codes, prev_counts = cur_codes, cur_counts
```

### Step 2: Generate markdown report

See `generate_report.py`.  Each CUDA step section now contains two subsections:

1. **New ioctls introduced (code-set delta)** — table of first-seen codes with annotation
2. **Event-level changes vs prev (frequency delta)** — table of codes whose call count changed, with prev/cur/delta columns; shown even when code-set delta is zero

---

## Execution Order

```
Agent 1 (TraceCollector)
  → for each CUDA step: compile, strace, dmesg, optional text diff

Agent 2 (TraceParser)
  → can run per-file as traces come in, no need to wait for all

Agent 3 (StaticAnnotator)
  → run annotate_static.py per file
  → open unknowns in Cursor for manual review
  → update lookup/ioctl_table.json with new findings

Agent 4 (SchemaBuilder)
  → run after all files are annotated
  → produces master_mapping.json + CUDA_IOCTL_MAP.md
```

---

## Key References for Agent 3

Point Cursor at these when reviewing unknown ioctl codes:

- **NVIDIA open kernel modules:** https://github.com/NVIDIA/open-gpu-kernel-modules
- **Key file:** `src/nvidia/interface/nv-ioctl-numbers.h`
- **Generated headers:** `src/nvidia/generated/`
- **Nouveau driver** (reverse-engineered, good cross-reference): https://github.com/torvalds/linux/tree/master/drivers/gpu/drm/nouveau
- **Envytools docs:** https://envytools.readthedocs.io

---

---

## W9 — Reproducibility Checking

A single strace capture is not sufficient to trust the results: NVIDIA's UVM initialisation and some teardown paths can issue non-deterministic ioctls (e.g. internal bookkeeping codes that appear in some but not all runs).

### Running the checker

```bash
# after compiling programs/<step_name>:
python3 check_reproducibility.py programs/<step_name> <step_name> [--runs 3]
```

This:
1. Runs the binary under `strace` N times (default 3)
2. Parses each trace with the same temporal parser used by `parse_trace.py`
3. Computes per-code **occurrence rates** (fraction of runs in which each code appears)
4. Writes `parsed/<step_name>_repro_report.json`

### Interpreting the report

| Field | Meaning |
|-------|---------|
| `determinism_score` | Fraction of unique codes that appeared in **every** run (1.0 = fully deterministic) |
| `non_deterministic_codes` | Codes with occurrence rate < 1.0 — treat their delta attribution with extra caution |
| `per_run_unique_codes` | Quick sanity check — large variance signals instability |

### Integration with the schema

`build_schema.py` automatically picks up `parsed/<step>_repro_report.json` if it exists and merges it into `reproducibility` block in `master_mapping.json`.  The report then shows:
- Repro status (`✓ (N runs)` or `not checked`) in the step properties table
- `⚠ R/N` in the Repro column of the new-ioctls table for any non-deterministic code

---

## Tips

- Use request-code set deltas between adjacent cumulative steps as the canonical "new code introduced" metric.
- Run `check_reproducibility.py` with `--runs 5` on `cu_ctx_create` and `cu_launch_kernel` — those steps are most likely to have non-deterministic UVM teardown codes.
- As you fill in `lookup/ioctl_table.json`, later runs become fully automatic with no Cursor review needed.
- `cuLaunchKernel` is the most complex step — save it for last once your pipeline is solid.