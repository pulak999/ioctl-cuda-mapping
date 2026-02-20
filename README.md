# CUDA → ioctl Mapping

This repository maps CUDA Driver API calls to their underlying Linux ioctl system calls. It traces which ioctl operations are triggered by each CUDA function call, helping understand what the NVIDIA driver does under the hood.

## Overview

The project uses a cumulative analysis approach: each test program includes all previous CUDA calls, allowing us to identify the **delta** (new ioctls) introduced by each step. The pipeline processes strace logs to extract, annotate, and report on ioctl sequences.

**Key Results:**
- `cuInit`: Introduces 16 unique ioctl codes (initialization, device queries, UVM setup)
- `cuDeviceGet`: No new ioctls (uses existing device handles)
- `cuCtxCreate`: Introduces 15 new ioctl codes (context setup, channel registration, memory mapping)

See `cuda-ioctl-map/CUDA_IOCTL_MAP.md` for the full report.

## Repository Structure

```
cuda-ioctl-map/
├── programs/          # Minimal CUDA test programs (.cu source files + compiled binaries)
├── traces/            # Raw strace output logs (.log files) and diffs (.diff files)
├── parsed/            # Structured JSON extracted from traces + reproducibility reports
├── annotated/         # JSON with ioctl name/description annotations
├── schema/            # Master mapping schema (cumulative analysis)
├── lookup/            # Static lookup table for known ioctl codes
├── baseline/          # Timestamped snapshots of analysis results
├── parse_trace.py     # Extract ioctls from strace logs
├── annotate_static.py # Add human-readable annotations from lookup table
├── build_schema.py    # Aggregate annotated data into master mapping
├── generate_report.py # Generate markdown report from schema
├── check_reproducibility.py  # Run N times and check determinism (W9)
└── CUDA_IOCTL_MAP.md  # Final human-readable report
```

## How to Replicate Results

### Prerequisites

- Linux machine with CUDA installed
- Tools: `nvcc`, `strace`, `python3`
- NVIDIA GPU with driver installed
- Access to `/dev/nvidia*` devices (may require appropriate permissions)

### Step 1: Collect Traces

For each CUDA call sequence, compile and trace the program:

```bash
cd cuda-ioctl-map

# Example: cu_init
nvcc -lcuda programs/cu_init.cu -o programs/cu_init
strace -f -e trace=ioctl,openat,close \
       -o traces/cu_init.log \
       ./programs/cu_init
```

**Note:** The `-f` flag traces forked processes (required for multi-process CUDA programs). The minimal trace set `ioctl,openat,close` is sufficient for ioctl analysis. For more comprehensive tracing, you can also include `mmap,munmap,read,write`.


**Cumulative sequence** (each program includes all previous calls):
1. `cu_init.cu` → `cuInit(0)`
2. `cu_device_get.cu` → `cuInit` → `cuDeviceGet`
3. `cu_ctx_create.cu` → `cuInit` → `cuDeviceGet` → `cuCtxCreate`
4. `cu_ctx_destroy.cu` → `cuInit` → `cuDeviceGet` → `cuCtxCreate` → `cuCtxDestroy`
5. (Additional steps: `cuMemAlloc`, `cuMemcpyHtoD`, `cuLaunchKernel`, etc.)

### Step 2: Parse Traces

Extract ioctl calls from strace logs and convert to structured JSON:

```bash
python3 parse_trace.py traces/cu_init.log traces/cu_device_get.log traces/cu_ctx_create.log
```

This will:
- Build a file descriptor → device path map
- Extract all ioctl calls with request codes
- Tag which ioctl codes are "new" compared to previous steps
- Output JSON files to `parsed/` directory

### Step 3: Annotate ioctls

Enrich parsed data with human-readable names and descriptions:

```bash
python3 annotate_static.py parsed/cu_init.json parsed/cu_device_get.json parsed/cu_ctx_create.json
```

This uses `lookup/ioctl_table.json` to map ioctl codes to:
- Name (e.g., `NV_ESC_RM_ALLOC`)
- Description
- Phase (initialization, memory allocation, etc.)
- Confidence level (high/medium/low/none)

**Confidence system:**
- **High**: Ground truth from NVIDIA headers
- **Medium**: Likely correct, but may need verification
- **Low**: Uncertain, flagged for review ⚠
- **None**: Unknown code, requires manual investigation ⚠

Unknown codes and low-confidence entries are flagged for manual review. Outputs go to `annotated/` directory.

### Step 4: Check Reproducibility (Optional)

Verify that ioctl sequences are deterministic across multiple runs:

```bash
python3 check_reproducibility.py programs/cu_init cu_init --runs 5
python3 check_reproducibility.py programs/cu_device_get cu_device_get --runs 5
python3 check_reproducibility.py programs/cu_ctx_create cu_ctx_create --runs 5
```

This will:
- Run each binary N times under strace
- Parse each trace independently
- Compute occurrence rates for each ioctl code
- Generate reproducibility reports in `parsed/<step>_repro_report.json`
- Flag non-deterministic codes (codes that don't appear in every run)

The reports are automatically picked up by `build_schema.py` and included in the final mapping.

### Step 5: Build Schema and Generate Report

Aggregate all annotated data and generate the final report:

```bash
python3 build_schema.py
python3 generate_report.py
```

This creates:
- `schema/master_mapping.json` - Complete structured mapping with:
  - **Code-set delta**: New ioctl codes introduced by each step
  - **Event-level delta**: Frequency changes for existing codes
  - **Confidence summary**: Breakdown by confidence tier
  - **Reproducibility data**: Determinism scores and non-deterministic codes
- `CUDA_IOCTL_MAP.md` - Human-readable markdown report

### Quick Start (All Steps)

If you have existing trace files, run the full pipeline:

```bash
# Parse all traces
python3 parse_trace.py traces/*.log

# Annotate all parsed files
python3 annotate_static.py parsed/*.json

# (Optional) Check reproducibility for key steps
python3 check_reproducibility.py programs/cu_init cu_init --runs 3
python3 check_reproducibility.py programs/cu_ctx_create cu_ctx_create --runs 3

# Build final schema and report
python3 build_schema.py
python3 generate_report.py
```

## Understanding the Output

- **`parsed/*.json`**: Raw ioctl extraction with device mapping and sequence indices
- **`parsed/<step>_repro_report.json`**: Reproducibility analysis (if checked) with:
  - Occurrence rates per ioctl code
  - Non-deterministic codes list
  - Determinism score (fraction of codes that appear in every run)
- **`annotated/*.json`**: Enriched with names/descriptions from lookup table, confidence levels
- **`schema/master_mapping.json`**: Cumulative mapping with:
  - **Code-set delta**: New unique ioctl codes introduced by each step
  - **Event-level delta**: Per-code frequency changes (how many times each code appears)
  - **Confidence summary**: Counts by confidence tier (high/medium/low/none)
  - **Reproducibility**: Determinism metrics and non-deterministic codes
- **`CUDA_IOCTL_MAP.md`**: Human-readable report with tables, statistics, and warnings for low-confidence/unknown codes

### Delta Metrics Explained

The analysis tracks two types of deltas per CUDA call:

1. **Code-set delta**: Which new ioctl request codes appear for the first time
   - Example: `cuCtxCreate` introduces 15 new codes not seen in previous steps

2. **Event-level delta**: How the frequency of existing codes changes
   - Example: A code that appeared 3 times in `cuInit` might appear 5 times after `cuCtxCreate` (delta: +2)

## Adding New CUDA Calls

1. Create a new `.cu` file in `programs/` (cumulatively including previous calls)
2. Compile: `nvcc -lcuda programs/new_call.cu -o programs/new_call`
3. Trace: `strace -f -e trace=ioctl,openat,close -o traces/new_call.log ./programs/new_call`
   - Note: Use `-f` flag to trace forked processes (W6)
4. Run through the pipeline:
   ```bash
   python3 parse_trace.py traces/new_call.log
   python3 annotate_static.py parsed/new_call.json
   python3 check_reproducibility.py programs/new_call new_call --runs 3  # Optional
   python3 build_schema.py
   python3 generate_report.py
   ```

## Notes

- The lookup table (`lookup/ioctl_table.json`) is seeded from NVIDIA's open-source driver headers
- Unknown ioctl codes can be researched using NVIDIA's [open-gpu-kernel-modules](https://github.com/NVIDIA/open-gpu-kernel-modules) repository
- The cumulative approach ensures we see the true delta introduced by each CUDA call
- **Reproducibility checking (W9)**: Some ioctl codes may appear non-deterministically across runs (e.g., due to timing, device state, or driver internals). The reproducibility checker helps identify these cases.
- **File descriptor tracking (W2)**: The parser maintains a temporal FD→device map, correctly handling FD reuse after `close()` calls
- **Multi-process support (W6)**: The parser handles both single-process and multi-process (`strace -f`) output by stripping PID prefixes
- **Baseline snapshots**: The `baseline/` directory can store timestamped snapshots of analysis results for comparison over time