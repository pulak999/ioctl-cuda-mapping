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

4. Run with strace (filter to relevant syscalls only):
```bash
strace -e trace=ioctl,openat,mmap,munmap,read,write \
       -o traces/<step_name>.log \
       ./programs/<step_name>
```

5. Capture dmesg immediately after:
```bash
sudo dmesg > dmesg/<step_name>.log
```

6. Compute a diff against the previous step's strace log to isolate new ioctl calls:
```bash
diff traces/<prev_step>.log traces/<step_name>.log > traces/<step_name>.diff
```

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

2. Also extract new ioctls from the `.diff` file — tag these as `"is_new": true` for easy filtering.

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
                "is_new": False  # TraceCollector sets this from diff
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

```python
# annotate_static.py
import json, sys

with open("lookup/ioctl_table.json") as f:
    LOOKUP = json.load(f)

def annotate(parsed_path):
    with open(parsed_path) as f:
        data = json.load(f)

    unknown = []
    for ioctl in data["ioctl_sequence"]:
        code = ioctl["request_code"]
        if code in LOOKUP:
            ioctl["annotation"] = LOOKUP[code]
            ioctl["annotation"]["needs_review"] = False
        else:
            ioctl["annotation"] = {
                "name": "UNKNOWN",
                "description": "",
                "phase": "",
                "confidence": "none",
                "needs_review": True
            }
            unknown.append(code)

    out_path = parsed_path.replace("parsed/", "annotated/")
    with open(out_path, "w") as f:
        json.dump(data, f, indent=2)

    print(f"Annotated {parsed_path}")
    print(f"  Known: {len(data['ioctl_sequence']) - len(unknown)}")
    print(f"  Unknown (needs Cursor review): {len(set(unknown))}")
    for code in set(unknown):
        print(f"    {code}")

if __name__ == "__main__":
    annotate(sys.argv[1])
```

### Step 3: Cursor review for unknowns

After running `annotate_static.py`, open any `annotated/<step>.json` in Cursor and paste this prompt for each `"needs_review": true` entry:

> "This ioctl request code `<code>` was captured from a CUDA program that just called `<cuda_call>`. The device is `<device>`. Cross-reference with NVIDIA's open-gpu-kernel-modules (especially `src/nvidia/interface/nv-ioctl-numbers.h`, `src/nvidia/generated/`, and the nouveau driver source). What does this ioctl likely do? Provide: (1) a name, (2) a description, (3) which phase of execution it belongs to, (4) your confidence level. If confirmed, add it to `lookup/ioctl_table.json` so future runs pick it up automatically."

---

## Agent 4: SchemaBuilder

**Responsibility:** Aggregate all annotated JSONs into a final structured mapping and human-readable report.

### Step 1: Build master schema

```python
# build_schema.py
import json, os, glob

master = {"cuda_to_ioctl_map": {}}

files = sorted(glob.glob("annotated/*.json"))
prev_codes = set()

for fpath in files:
    with open(fpath) as f:
        data = json.load(f)

    call = data["cuda_call"]
    current_codes = {i["request_code"] for i in data["ioctl_sequence"]}
    new_codes = current_codes - prev_codes

    master["cuda_to_ioctl_map"][call] = {
        "devices_touched": list(data["fd_map"].values()),
        "total_ioctls": len(data["ioctl_sequence"]),
        "new_ioctls_vs_prev": [
            i for i in data["ioctl_sequence"] if i["request_code"] in new_codes
        ],
        "full_sequence": data["ioctl_sequence"]
    }
    prev_codes = current_codes

with open("schema/master_mapping.json", "w") as f:
    json.dump(master, f, indent=2)
print("Master schema written to schema/master_mapping.json")
```

### Step 2: Generate markdown report

```python
# generate_report.py
import json

with open("schema/master_mapping.json") as f:
    master = json.load(f)

lines = ["# CUDA → ioctl Mapping Report\n"]

for cuda_call, data in master["cuda_to_ioctl_map"].items():
    lines.append(f"## `{cuda_call}`\n")
    lines.append(f"**Devices touched:** {', '.join(set(data['devices_touched']))}\n")
    lines.append(f"**New ioctls introduced:** {len(data['new_ioctls_vs_prev'])}\n")
    lines.append("\n### New ioctl sequence\n")
    lines.append("| # | Device | Request Code | Name | Description | Phase | Confidence |")
    lines.append("|---|--------|-------------|------|-------------|-------|------------|")
    for i, ioctl in enumerate(data["new_ioctls_vs_prev"]):
        ann = ioctl.get("annotation", {})
        lines.append(
            f"| {i+1} | {ioctl['device']} | `{ioctl['request_code']}` "
            f"| {ann.get('name','?')} | {ann.get('description','?')} "
            f"| {ann.get('phase','?')} | {ann.get('confidence','?')} |"
        )
    lines.append("")

with open("CUDA_IOCTL_MAP.md", "w") as f:
    f.write("\n".join(lines))
print("Report written to CUDA_IOCTL_MAP.md")
```

---

## Execution Order

```
Agent 1 (TraceCollector)
  → for each CUDA step: compile, strace, dmesg, diff

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

## Tips

- The diff-based approach is critical — `cuInit` alone may produce 50+ ioctls. You only care about the delta each new CUDA call introduces.
- As you fill in `lookup/ioctl_table.json`, later runs become fully automatic with no Cursor review needed.
- `cuLaunchKernel` is the most complex step — save it for last once your pipeline is solid.