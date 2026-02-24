# Code Review — `cuda-ioctl-map`
> Reviewer: Senior Systems Engineer (L6-equivalent)
> Date: 2026-02-23
> Scope: Full codebase review, 3 phases. Logical errors, edge cases, future breakpoints.

---

## Phase 1 — Repo Overview

### 1. Overall Purpose

This is a **reverse-engineering research tool** that maps CUDA Driver API calls to the underlying Linux kernel `ioctl` system calls they emit. The method is empirical: minimal CUDA programs that each exercise one API call are run under `strace`, the raw system call trace is parsed into structured JSON, ioctl request codes are annotated with human-readable descriptions via a static lookup table, reproducibility is measured across multiple runs, and all of this is aggregated into a machine-readable schema and a human-readable Markdown report.

The core insight driving the design is **cumulative programs**: each program in the sequence builds on the previous one (e.g., `cu_ctx_create` also does `cuInit` + `cuDeviceGet` internally), so delta analysis is used to isolate which ioctl calls are uniquely attributable to each API call.

---

### 2. Directory Structure

```
cuda-ioctl-map/
├── programs/          Entry points — minimal CUDA .cu sources + compiled binaries
│                      (cu_init, cu_device_get, cu_ctx_create, cu_ctx_destroy)
│
├── traces/            Raw strace output (.log) for each program, plus
│                      reproducibility run logs (repro_<step>_run{N}.log),
│                      and unexplained .diff files alongside each primary log
│
├── parsed/            Output of parse_trace.py — structured JSON per step
│                      (cu_init.json, cu_device_get.json, cu_ctx_create.json)
│                      Plus reproducibility run JSONs and repro_report.json files
│
├── annotated/         Output of annotate_static.py — parsed JSONs enriched with
│                      human-readable labels from the lookup table
│                      (cu_init.json, cu_device_get.json, cu_ctx_create.json ONLY)
│
├── schema/            Output of build_schema.py — single master_mapping.json
│                      aggregating all annotated steps
│
├── lookup/            Static ground truth — ioctl_table.json, a hand-curated
│                      mapping of request codes to names/descriptions/confidence
│
├── baseline/          Timestamped snapshot (20260220T224129Z/) of a prior
│                      complete pipeline run — traces, parsed, annotated, schema,
│                      and report. Acts as a regression reference.
│
├── parse_trace.py     Pipeline stage 1: strace log → structured JSON
├── annotate_static.py Pipeline stage 2: parsed JSON → annotated JSON
├── build_schema.py    Pipeline stage 3: annotated JSONs → master_mapping.json
├── generate_report.py Pipeline stage 4: master_mapping.json → CUDA_IOCTL_MAP.md
├── check_reproducibility.py  Side-channel tool: run binary N×, measure ioctl variance
└── CUDA_IOCTL_MAP.md  Final human-readable output (currently covers 3 steps)
```

**Each folder has a clear single owner in the pipeline. No folder serves dual purposes — this is a clean separation of concerns.**

---

### 3. Entry Points

There are two distinct usage modes:

**A. Main pipeline (run in order manually):**
```
strace → parse_trace.py → annotate_static.py → build_schema.py → generate_report.py
```
There is **no orchestrator script** (no `Makefile`, no `run_pipeline.sh`). Each stage must be invoked manually. The pipeline is implied, not enforced.

**B. Reproducibility side-channel (independent):**
```
check_reproducibility.py <binary> <step_name> [--runs N]
```
This runs a binary under strace N times, writes repro logs + parsed JSONs, and emits a `<step>_repro_report.json` which `build_schema.py` automatically picks up if present.

---

### 4. Key Data Structures

Three major JSON shapes flow through the system:

**A. Parsed ioctl event** (produced by `parse_trace.py`):
```json
{
  "sequence_index": 42,
  "fd": "3",
  "device": "/dev/nvidiactl",
  "request_code": "0xC020462A",
  "decoded": "<raw strace line>",
  "args": "<ioctl args string>",
  "return_value": "0",
  "is_new": true
}
```

**B. Annotated ioctl event** (enriched by `annotate_static.py`):
Same as above, plus:
```json
{
  "annotation": {
    "name": "NV_ESC_RM_ALLOC",
    "description": "Allocate an RM object ...",
    "phase": "object allocation",
    "confidence": "high",
    "needs_review": false,
    "source": "nv-ioctl-numbers.h"
  }
}
```

**C. Master schema entry** (produced by `build_schema.py`):
```json
{
  "devices_touched": [...],
  "total_ioctls": 814,
  "unique_codes": 31,
  "new_codes_vs_prev": 15,
  "new_ioctls_vs_prev": [...],      // first occurrences of new codes only
  "net_new_events": 481,
  "event_delta_vs_prev": {...},
  "confidence_summary": {"high": 9, "medium": 14, "low": 8, "none": 0},
  "reproducibility": {...},
  "full_sequence": [...]             // entire ioctl trace, annotated
}
```

**D. Repro report** (produced by `check_reproducibility.py`):
```json
{
  "step": "cu_init", "binary": "...", "runs": 3, "checked": true,
  "code_occurrence_rate": {"0xC020462A": 1.0, ...},
  "non_deterministic_codes": [],
  "determinism_score": 1.0,
  "per_run_unique_codes": [16, 16, 16]
}
```

---

### 5. Architectural Patterns

- **Linear ETL pipeline** — parse → annotate → aggregate → render. Classic data pipeline pattern.
- **Cumulative diffing** — each pipeline stage compares against the prior step's code-set to isolate attributable ioctls. This is the core methodological choice.
- **Static lookup table as ground truth** — `ioctl_table.json` is the authoritative mapping. Annotation is a pure lookup pass; no inference happens at runtime.
- **Confidence tiers** — `high / medium / low / none` explicitly propagate through every layer, surfacing uncertainty in the final report. This is good design.
- **Reproducibility as an optional enrichment layer** — repro reports augment but don't gate the main pipeline; their presence is optional and checked gracefully.

---

### 6. ⚠️ Structural Flags

The following are structurally odd or unexpected before any file-level analysis:

| # | Flag | Severity |
|---|------|----------|
| F1 | **`annotated/` has only 3 steps** (`cu_init`, `cu_device_get`, `cu_ctx_create`), but `build_schema.py`'s `STEP_ORDER` lists 9 steps and `programs/` has 4 compiled binaries. The pipeline is clearly incomplete — 6 steps defined in `STEP_ORDER` have no data at all. | HIGH |
| F2 | **`cu_ctx_destroy` has a repro report** (`parsed/cu_ctx_destroy_repro_report.json`, 5 runs, determinism_score=1.0) but **no primary trace log**, **no `parsed/cu_ctx_destroy.json`**, and **no `annotated/cu_ctx_destroy.json`**. Reproducibility was checked on a step that was never fully processed through the pipeline. | HIGH |
| F3 | **No pipeline orchestration script** — stages must be run manually in the correct order with the correct arguments. This makes the pipeline fragile to human error. The intended invocation sequence is not documented anywhere in the code. | MEDIUM |
| F4 | **`.diff` files in `traces/`** (`cu_init.diff`, `cu_device_get.diff`, `cu_ctx_create.diff`) — these exist alongside the primary `.log` files but are never referenced in any Python script. Their purpose and origin are unclear. | MEDIUM |
| F5 | **`baseline/` is a manual snapshot** with no automation. There is no mechanism to update it, verify it, or use it for regression testing programmatically. It could silently become stale. | LOW |
| F6 | **`CUDA_IOCTL_MAP.md` (the live report) only reflects 3 steps**, matching `annotated/`. The `STEP_ORDER` in `build_schema.py` implies the report is meant to eventually cover 9 steps, but this is not communicated to readers of the Markdown. | LOW |

---

> ✋ **Phase 1 complete. Awaiting approval to proceed to Phase 2 (file-by-file drill-down).**

---

## Resolved Flags — F2 & F4

### F2 — `cu_ctx_destroy` missing from pipeline ✅ RESOLVED

**Actions taken:**
1. Captured primary strace trace: `strace -f -e trace=ioctl,openat,close -o traces/cu_ctx_destroy.log programs/cu_ctx_destroy`
2. Parsed against `cu_ctx_create` as prev: `parsed/cu_ctx_destroy.json` (776 total ioctls, 31 unique codes, 1 new code)
3. Annotated: `annotated/cu_ctx_destroy.json`
4. Rebuilt schema: `schema/master_mapping.json` (now covers all 4 completed steps)
5. Regenerated report: `CUDA_IOCTL_MAP.md` (now covers `cu_init`, `cu_device_get`, `cu_ctx_create`, `cu_ctx_destroy`)

**Results:**
| Step | Total ioctls | Unique codes | New codes vs prev | Net new events | Repro |
|------|-------------|--------------|-------------------|----------------|-------|
| cu_init | 333 | 16 | 16 | +333 | ✓ (3 runs) |
| cu_device_get | 333 | 16 | 0 | 0 | ✓ (3 runs) |
| cu_ctx_create | 814 | 31 | 15 | +481 | ✓ (5 runs) |
| cu_ctx_destroy | 776 | 31 | **1** | **−38** | ✓ (5 runs) |

**Secondary finding — `0xC00C46D1` (new code in `cu_ctx_destroy`, later confirmed as universal):**

Initially identified as a new code in `cu_ctx_destroy`. Investigation revealed it fires immediately after `openat("/dev/nvidia3") = -1 EIO`. This was initially misclassified as error-recovery for a sick GPU.

**Correction (discovered on re-run):** After re-running all experiments on a 3-GPU machine (TITAN RTX ×3), `0xC00C46D1` appeared in **every step including `cu_init`** — 100% occurrence rate across all repro runs. `/dev/nvidia3` fails with EIO on this machine not because a GPU is sick, but because there is no 4th GPU. `libcuda.so` probes device nodes sequentially (`/dev/nvidia0`, `/dev/nvidia1`...`/dev/nvidiaN`) until one fails, then fires `0xC00C46D1` to notify the RM of the enumeration boundary. **This is normal behavior on any machine with fewer than 4 GPUs.**

- Decoded: `_IOC(_IOC_READ|_IOC_WRITE, 0x46, 0xD1, 0xC)` — NVIDIA nvidiactl, nr=0xD1, 12-byte struct
- **Present in `cu_init` on the 3-GPU machine** — fires during initial GPU enumeration
- **Absent on the old 4-GPU machine** — `/dev/nvidia3` opened successfully there, so the boundary was never hit
- Updated in `lookup/ioctl_table.json` from `confidence: "none"` → `confidence: "low"` with corrected description

---

### F4 — `.diff` files in `traces/` ✅ RESOLVED (documented)

**What they are:** The three `.diff` files (`cu_init.diff`, `cu_device_get.diff`, `cu_ctx_create.diff`) are **full, unfiltered strace captures** — they include all syscall families (`mmap`, `read`, `write`, `openat`, `ioctl`, etc.), in contrast to the pipeline `.log` files which are filtered to `-e trace=ioctl,openat,close`.

**Why the name is misleading:** "diff" here does not mean a Unix diff or a delta between steps. These are likely named to suggest "what else is happening" (the differential full-syscall view) alongside the ioctl-focused `.log`. The name should be `.full.log` or `.syscall.log`.

**Why they're orphaned:** No Python script references them. `parse_trace.py` can actually parse them correctly (it ignores non-ioctl lines), but they are never wired into the pipeline.

**Value they contain:** They show the full driver interaction: library loading paths, `mmap` patterns for GPU memory, `/proc` reads during init, etc. Useful for deeper driver internals research but out of scope for the current ioctl-mapping objective.

**Status:** Not referenced by the pipeline. Their presence is benign (no correctness risk) but creates confusion about whether the pipeline uses them.

**Recommendation:** Either rename to `*.full.log` and add a README note about their purpose, or delete them if the broader syscall profile is not needed. Do not wire them into `parse_trace.py` — the pipeline's ioctl-only scope is correct.

---

## Phase 2 — File-by-File Drill Down

Review order: most foundational → most peripheral. Files reviewed: `parse_trace.py`, `annotate_static.py`, `build_schema.py`, `generate_report.py`, `check_reproducibility.py`, `lookup/ioctl_table.json`, `programs/*.cu`.

---

### `parse_trace.py`

**Single responsibility:** Convert raw strace output into a structured JSON sequence of ioctl events.

**Input → Output:** `traces/<step>.log` + optional previous step's `parsed/<prev>.json` → `parsed/<step>.json`

**Key functions:**
- `_ioc(d, t, n, s)` — reconstructs a 32-bit ioctl request code from `_IOC()` components
- `strip_pid(line)` — strips the leading PID added by `strace -f`
- `parse_lines(lines)` — core single-pass parser; returns `(ioctls, fd_snap)`; no file I/O
- `parse(log_path, prev_parsed)` — calls `parse_lines()`, marks `is_new` flags, writes output
- `_load_prev_codes(path)` — loads the previous step's code set for delta comparison

**Does it do what it says?** Yes — clean, well-commented, correctly handles both `_IOC(...)` and hex-literal ioctl forms.

**Logic errors and edge cases:**

| # | Issue | Severity |
|---|-------|----------|
| P1 | **`out_dir` path construction is fragile.** `os.path.dirname(os.path.dirname(log_path))` assumes log files live exactly one level deep inside a `traces/` sibling of `parsed/`. If `log_path` has no directory component (e.g. `cu_init.log` passed from a different cwd), both `dirname()` calls return `""`, and `parsed/` is created relative to whatever the current working directory is — silently, in the wrong place. | MEDIUM |
| P2 | ✅ **FIXED** — Added explicit stderr warning when `DIR_MAP` receives an unrecognized direction token (e.g. a future `_IOC_` variant). Previously silently defaulted to `_IOC_NONE` (0), producing a wrong request code with no diagnostic. | MEDIUM |
| P3 | ✅ **FIXED** — `IOCTL_HEX` and `IOCTL_IOC` return-value pattern now matches `-?\d+`, `0x[hex]+`, and `?`. Previously the pattern required an integer; strace can emit hex pointers or `?` on signal interruption — those lines were silently dropped. | LOW |
| P4 | **No handling of `dup`/`dup2`/`fcntl(F_DUPFD)`.** The fd→device map only updates on `openat` and `close`. If a CUDA thread duplicates a device fd, the duplicate will resolve to the correct device (since the original fd is still in the map), but if the original is then closed, the duplicate becomes `"unknown"`. Unlikely in CUDA's usage pattern, but a known gap. | LOW |
| P5 | **`parse()` output path depends on `parse_lines()` being called with a `traces/`-structured path.** The pipeline works because the caller always passes `traces/<step>.log`, but there is no enforcement of this contract. | LOW |

---

### `annotate_static.py`

**Single responsibility:** Look up every ioctl request code in the static table and attach a human-readable annotation object to each event.

**Input → Output:** `parsed/<step>.json` + `lookup/ioctl_table.json` → `annotated/<step>.json`

**Key functions:**
- Module-level: `LOOKUP` loaded at import time from `lookup/ioctl_table.json`
- `annotate(parsed_path)` — enriches all ioctl events, writes output, returns `(out_path, unknown_codes)`

**Does it do what it says?** Yes — a clean lookup pass with confidence-tier flagging.

**Logic errors and edge cases:**

| # | Issue | Severity |
|---|-------|----------|
| A1 | **`LOOKUP` is loaded at module import time.** Any script that `import annotate_static` will immediately attempt to open `lookup/ioctl_table.json` relative to `annotate_static.py`'s location. This is fine for CLI use, but makes the module impossible to import in a test environment or from a different repo layout without the lookup file present. | MEDIUM |
| A2 | **`ann = dict(LOOKUP[c])` is a shallow copy.** If any future lookup entry contains a nested object (e.g., an array of sources), mutating `ann` would silently corrupt the global `LOOKUP` dict for subsequent calls in the same process run. Currently all values are flat strings — safe for now, fragile by design. | LOW |
| A3 | **`out_dir` path construction has the same fragility as `parse_trace.py` (P1)** — assumes `parsed/<step>.json` structure. | LOW |
| A4 | **No schema validation on lookup entries.** If a `LOOKUP` entry is missing the `"confidence"` key, `ann.get("confidence", "none")` defaults to `"none"` — silently promoting the annotation to a flagged-for-review status. A malformed lookup entry would degrade the confidence summary without any error. | LOW |
| A5 | **List comprehension used for side effects** — `[print(f"  ? {c}") for c in unk_u]`. Using a list comprehension purely for `print()` calls is a Python antipattern; the list is created and immediately discarded. Should be a `for` loop. | LOW (style) |

---

### `build_schema.py`

**Single responsibility:** Aggregate all annotated per-step JSONs into a single `master_mapping.json` with delta metrics, confidence summaries, and reproducibility data.

**Input → Output:** `annotated/*.json` + optional `parsed/<step>_repro_report.json` files → `schema/master_mapping.json`

**Key functions:** All logic is at module level — no functions. A single loop iterates over `FILES` and builds the master dict.

**Does it do what it says?** Mostly yes. The delta logic is correct. But there are several significant issues.

**Logic errors and edge cases:**

| # | Issue | Severity |
|---|-------|----------|
| B1 | ✅ **FIXED** — Field renamed from `net_new_events` to `net_event_delta` in `build_schema.py` and `generate_report.py`. The old name implied the value was always additive; it can be negative (step shrinks total ioctl count). | MEDIUM |
| B2 | ✅ **FIXED** — `build_schema.py` now emits a `WARNING [B2]` to stderr for each step whose canonical predecessor in `STEP_ORDER` is absent. The warning names the missing step and the closest available predecessor that deltas are actually computed against. Engineers adding a previously-missing step will see an actionable prompt to re-run parse+annotate for the affected downstream steps. | HIGH |
| B3 | **No `if __name__ == "__main__"` guard.** All logic executes at module level. `import build_schema` runs the entire aggregation and overwrites `master_mapping.json`. Makes the module impossible to use as a library or to test in isolation. | MEDIUM |
| B4 | **`FILES` construction silently includes unexpected annotated files.** Any `.json` file dropped into `annotated/` that is not in `STEP_ORDER` gets appended to `FILES` in alphabetical order. A backup file, a temp file, or a misnamed file would be silently ingested into the schema. | MEDIUM |
| B5 | **`new_ioctls_vs_prev` stores full event objects including `decoded` (the raw strace line).** The `full_sequence` field also stores this. The master schema therefore embeds raw strace text — memory addresses, pointer values, etc. — that are non-reproducible across runs. This makes the schema non-diffable and bloated. | LOW |

---

### `generate_report.py`

**Single responsibility:** Render `master_mapping.json` as a human-readable `CUDA_IOCTL_MAP.md` Markdown report.

**Input → Output:** `schema/master_mapping.json` → `CUDA_IOCTL_MAP.md`

**Key functions:**
- Module-level: `master` loaded at import time
- `repro_cell(code)` — inner closure per step, returns the reproducibility cell string for a given code
- Main loop: iterates over `master["cuda_to_ioctl_map"]` and builds a list of Markdown lines

**Does it do what it says?** Yes — the output is accurate and well-structured.

**Logic errors and edge cases:**

| # | Issue | Severity |
|---|-------|----------|
| G1 | **`repro_cell` is redefined on every loop iteration.** The function captures `repro_checked`, `non_det_codes`, `occ_rates`, `repro_runs` from the enclosing loop scope. In Python this is safe because the function is defined and fully consumed within the same iteration before the variables change. However, if the code were ever refactored to call `repro_cell` after the loop (e.g., storing functions in a list), all stored closures would reference the last iteration's values — a classic Python closure-in-loop trap. | LOW (latent) |
| G2 | **No `if __name__ == "__main__"` guard** — same issue as `build_schema.py`. Importing this module reads and processes the entire master schema. | MEDIUM |
| G3 | ✅ **FIXED** — Introduced `_md_escape()` helper that escapes `\|`, `\*`, `` \` ``, and `\[` in description strings. Previously only pipe was escaped; asterisks, backticks, and open brackets in future table entries could break Markdown rendering. | LOW |
| G4 | **`event_delta_vs_prev` table has no sort order guarantee in the rendered output.** The dict is iterated as-is. In practice `build_schema.py` inserts codes in `sorted()` order, so the report is sorted — but this is an implicit dependency on build_schema's behavior, not an explicit sort here. | LOW |

---

### `check_reproducibility.py`

**Single responsibility:** Run a binary N times under strace and measure how consistently each ioctl code appears across runs.

**Input → Output:** `<binary>` + `<step_name>` → `traces/repro_<step>_run{N}.log` + `parsed/repro_<step>_run{N}.json` + `parsed/<step>_repro_report.json`

**Key functions:**
- `run_once(binary, log_path)` — runs binary under strace, parses result
- `check(binary, step, runs)` — orchestrates N runs, computes occurrence rates, writes report
- `_synthetic_test()` — self-contained unit test with synthetic data

**Does it do what it says?** Yes, and the `_synthetic_test()` is a nice addition. But there are several correctness gaps.

**Logic errors and edge cases:**

| # | Issue | Severity |
|---|-------|----------|
| C1 | ✅ **FIXED** — `per_run_counts` is now consumed. `check_reproducibility.py` computes per-code count stability across runs and adds three new fields to the report: `frequency_unstable_codes` (code → `{min, max, per_run}`), `frequency_stable_codes` (code → constant count), and `frequency_stability_score` (fraction of codes with stable count). Summary output now separately reports presence-determinism and frequency-stability. | HIGH |
| C2 | **`run_once` does not handle strace failure gracefully.** If strace exits with code 2 (usage error) or fails to create the log file, `open(log_path)` raises `FileNotFoundError`. The only guard is a warning print for exit codes not in `{0, 1}`, which doesn't abort the run. The subsequent `parse_lines()` call on an empty/missing file would produce an empty ioctl list, silently inflating `non_deterministic_codes` for that run. | MEDIUM |
| C3 | **`binary` is stored as a relative path in the report.** The report stores whatever string the user passed as `args.binary` (e.g., `programs/cu_ctx_destroy`). If the report is read from a different working directory, the binary path is not re-runnable. Should canonicalize to absolute path with `os.path.abspath()`. | LOW |
| C4 | **Repro runs do not chain to a `prev` step.** `parse_lines()` is called directly, so all `is_new` flags are False. This is intentional and correct for reproducibility measurement, but it means the per-run JSON files in `parsed/` are not compatible with the main pipeline's `_load_prev_codes()` delta logic. A future engineer picking up `repro_cu_ctx_create_run0.json` and passing it to `parse_trace.py` as a prev file would get incorrect deltas. | LOW |
| C5 | **`tempfile` is imported at module level AND again inside `_synthetic_test()`.** Duplicate import, minor but sloppy. | LOW (style) |

---

### `lookup/ioctl_table.json`

**Single responsibility:** Static ground truth — maps ioctl request codes to human-readable names, descriptions, phases, confidence tiers, and sources.

**Does it do what it says?** Yes — well-structured, easy to extend.

**Issues:**

| # | Issue | Severity |
|---|-------|----------|
| L1 | **No schema enforcement.** Any consumer (`annotate_static.py`) uses `.get()` with defaults for every field. A missing or mistyped key in any entry degrades silently. There is no JSON Schema, no validation script, and no test that checks all required fields are present. | MEDIUM |
| L2 | **Two entries share the name `NV_ESC_RM_ALLOC`.** `0xC020462A` = `"NV_ESC_RM_ALLOC"` and `0xC030462B` = `"NV_ESC_RM_ALLOC (large)"`. The disambiguation `(large)` is in the name string, not a separate field. If a consumer filters by name, they get two different results for what appears to be the same call. Should use a `variant` or `size_bytes` field. | LOW |
| L3 | **Two entries for `NV_ESC_CHECK_VERSION_STR`.** `0xC01046CE` and `0xC01046CF` differ only by size field (0x10 vs 0x10+1). Similarly for `NV_ESC_RM_ALLOC`. These "variants" arise from struct size differences across driver versions. The table treats them as separate entries but doesn't document *why* they differ, making it hard to maintain as the driver evolves. | LOW |
| L4 | **`source` field mixes reference types.** Some entries cite a header file (`"nv-ioctl-numbers.h"`), others cite observations (`"observed in cuInit trace"`). There is no way to programmatically distinguish confirmed-from-source vs inferred-from-observation. A `source_type` field (`"header"` vs `"observed"`) would help. | LOW |

---

### `programs/*.cu`

**Single responsibility:** Minimal CUDA programs that each exercise one cumulative API call sequence for tracing purposes.

**Does each file do what its name suggests?**

| File | Does what it says? | Notes |
|------|--------------------|-------|
| `cu_init.cu` | ✓ | Calls only `cuInit(0)`. Clean. |
| `cu_device_get.cu` | ✓ | Calls `cuInit` + `cuDeviceGet`. Always device 0 hardcoded. |
| `cu_ctx_create.cu` | ⚠ | Says "no destroy in this step" in `printf`. Correct but the note is in the binary output, not a comment — easy to miss. |
| `cu_ctx_destroy.cu` | ✓ | Full create + destroy cycle. |

**Issues:**

| # | Issue | Severity |
|---|-------|----------|
| PR1 | ✅ **FIXED** — `programs/Makefile` created. Builds all four binaries with `make`. Defaults to `/usr/local/cuda-12.5/bin/nvcc`; overridable via `NVCC=<path> make`. Includes `make clean` target. | MEDIUM |
| PR2 | **Device 0 is hardcoded** in `cu_device_get.cu` and `cu_ctx_create.cu` (`cuDeviceGet(&dev, 0)`). On a multi-GPU machine, this always maps to the first GPU regardless of which is most available or which one an experiment intends to profile. Not wrong for tracing purposes, but worth documenting. | LOW |
| PR3 | **No error handling in `cu_init.cu` and `cu_ctx_create.cu` for `cuInit`.** `cu_ctx_create.cu` calls `cuInit(0)` without checking the return value. If `cuInit` fails, the program proceeds to `cuDeviceGet` and `cuCtxCreate`, which will also fail — but the error message will blame `cuCtxCreate`, not `cuInit`. Misleading for debugging. | LOW |
| PR4 | **Compiled binaries are committed alongside source** (the directory contains both `cu_init` and `cu_init.cu`). Binaries are platform-specific — the committed binaries were built on a different machine and will not work here without recompilation. This caused the recompile step needed earlier in this review. | MEDIUM |

---

> ✋ **Phase 2 complete. Awaiting approval to proceed to Phase 3 (cross-cutting issues).**

---

## Phase 3 — Cross-Cutting Issues

---

### 1. Inter-file Inconsistencies

These are places where one file makes an assumption that another file does not guarantee.

---

#### XC1 — `is_new` contract between `parse_trace.py` and `build_schema.py` ⚠️ HIGH

`parse_trace.py` sets `is_new=True` on the first occurrence of each code that wasn't seen in the **prev JSON passed at parse time**. `build_schema.py` independently computes `new_codes` by diffing against **`prev_codes` accumulated in its own loop**. These two "prev" references must agree to produce a correct `new_ioctls_vs_prev` list.

They agree today because the pipeline has only been run correctly in sequence. But the invariant is implicit and unenforced:

- Re-parse a step with the wrong `--prev` argument → `is_new` flags in the JSON diverge from what `build_schema.py` expects → `new_ioctls_vs_prev` silently includes wrong events or omits real ones.
- Add a missing intermediate step later → `build_schema.py` recomputes `new_codes` correctly (because it now has the new step in its loop), but the existing downstream step JSONs still carry the old `is_new` flags. The report will be wrong until all downstream steps are re-parsed.

There is no cross-check between the two `prev` references. No file validates that the `is_new` flag in a JSON was set against the same step that `build_schema.py` is using as its prior.

---

#### XC2 — `annotate_static.py` output path vs. `build_schema.py` input path ⚠️ MEDIUM

`annotate_static.py` writes to `annotated/` by computing `os.path.dirname(os.path.dirname(parsed_path))` — the output directory is derived from the **input path**, not from `__file__`. If someone runs `annotate_static.py /tmp/scratch/parsed/cu_init.json`, the output lands in `/tmp/scratch/annotated/`.

`build_schema.py` reads from `glob(os.path.join(BASE, "annotated", "*.json"))` where `BASE = os.path.dirname(__file__)` — always the repo's own `annotated/` directory.

These two can silently diverge. If annotated files land in the wrong directory, `build_schema.py` processes stale or absent data without any error. The fix in P2 for `parse_trace.py` applies equally here but was not applied.

---

#### XC3 — `decoded` field breaks schema diffability across runs ⚠️ MEDIUM

`parse_trace.py` stores the raw strace line (`"decoded"`) in every ioctl event. `build_schema.py` embeds these events verbatim inside `new_ioctls_vs_prev` and `full_sequence` in `master_mapping.json`. Raw strace lines contain **memory addresses and pointer values** that change between runs.

`generate_report.py` reads `master_mapping.json` at module import time. Any tooling that diffs two `master_mapping.json` files (e.g. comparing a new run to `baseline/`) will produce enormous noise from these runtime-varying address fields, masking real ioctl-level differences. The `baseline/` directory exists as a regression reference but is unusable for automated diffing in this state.

---

#### XC4 — New repro fields in `check_reproducibility.py` are invisible in the final report ✅ FIXED

The C1 fix added `frequency_unstable_codes`, `frequency_stable_codes`, and `frequency_stability_score` to the repro report JSON. `build_schema.py` copies the entire repro report into `"reproducibility"` in the master schema — so the data was there but never rendered.

**Fix applied to `generate_report.py`:**
- **Properties table** gains a `Frequency stability` row alongside `Presence reproducibility`. Shows `✓ 100.00% (all codes fire stable count)` or `⚠ 94.12% (1/16 codes vary in count)` as appropriate.
- **Detail table** (`#### Frequency-unstable codes ⚠`) is conditionally rendered after the confidence summary whenever at least one unstable code exists. Columns: request code, name, min count, max count, per-run counts.
- **Graceful fallback**: if a repro report predates the C1 fix (missing `frequency_stability_score`), the row shows `not checked` rather than crashing or showing misleading data.

> **Note:** Existing on-disk repro reports were generated before the C1 fix and do not carry the new fields. Re-run `check_reproducibility.py` for any step to populate frequency stability data and see it rendered in the report.

---

#### XC5 — `source` annotation field never rendered ⚠️ LOW

`ioctl_table.json` has a `source` field on every entry (`"nv-ioctl-numbers.h"`, `"observed in cuInit trace"`, etc.) that `annotate_static.py` faithfully passes through to the annotated JSONs and into the master schema. `generate_report.py` renders `name`, `description`, `phase`, and `confidence` — but never `source`. The distinction between header-confirmed entries and empirically-observed ones is invisible in the final Markdown. This defeats the purpose of the `source` field and makes the report less trustworthy than it should be.

---

#### XC6 — B1 rename breaks any existing schema on disk ⚠️ LOW (immediate)

The B1 fix renamed `net_new_events` → `net_event_delta` in `build_schema.py` and `generate_report.py`. Any existing `schema/master_mapping.json` built before this change still uses the old key name. Running `generate_report.py` against a stale schema will raise a `KeyError: 'net_event_delta'`. `build_schema.py` must be re-run first to regenerate the schema before the report can be regenerated. There is no guard or migration path.

---

### 2. Missing Pieces

Things the architecture implies should exist but don't.

---

| # | Missing piece | Impact |
|---|---------------|--------|
| XM1 | **No pipeline runner.** `strace → parse → annotate → schema → report` is the design, but there is no `run_pipeline.sh` or `Makefile` at the repo root to execute it. Each stage requires knowing the correct invocation, the correct `--prev` arguments, and the correct order. This is the single biggest operational gap. | HIGH |
| XM2 | **No validation gate.** Nothing halts the pipeline when the confidence-none count is high or when unknown codes exceed a threshold. The pipeline produces a polished Markdown report regardless of how many `?`-confidence entries it contains. An automated check (`assert conf_summary["none"] == 0`) before schema export would prevent low-quality runs from overwriting good ones. | MEDIUM |
| XM3 | **No baseline comparison script.** `baseline/` exists as a regression reference but there is no tooling to compare a new run against it. Given the `decoded` field issue (XC3), even a manual `diff` is noisy. A purpose-built comparator that diffs only code-sets and confidence summaries (ignoring raw strace text) is implied but absent. | MEDIUM |
| XM4 | **No test suite.** `check_reproducibility.py` has a `_synthetic_test()` for its own logic, but there are no tests for `parse_trace.py`'s regex patterns, `annotate_static.py`'s lookup logic, or `build_schema.py`'s delta computation. Given that `is_new` contract (XC1) is the core of the methodology, a unit test that runs parse → annotate → build on a synthetic trace and asserts the delta is correct would catch most regression scenarios. | MEDIUM |
| XM5 | ✅ **FIXED** (see XC4) — `frequency_stability_score` and the unstable-codes detail table are now rendered in `CUDA_IOCTL_MAP.md`. | LOW |
| XM6 | **`source` field not rendered** (see XC5). The data exists; needs one column in the new-ioctls table. | LOW |

---

### 3. Most Likely Bug Location

**The `is_new` flag / `new_codes` dual-derivation in `build_schema.py` (XC1).**

Here is the exact failure scenario:

1. You add the 5 missing intermediate steps (`cu_mem_alloc` → `cu_mem_free`).
2. You run `annotate_static.py` on each new step.
3. You run `build_schema.py`.
4. `build_schema.py` now correctly computes `new_codes` for `cu_ctx_destroy` as `{codes in cu_ctx_destroy} - {codes in cu_mem_free}`.
5. But `parsed/cu_ctx_destroy.json` still has `is_new=True` stamped against `{codes in cu_ctx_create}` — the old prev.
6. The filter `[i for i in ... if i["request_code"] in new_codes and i["is_new"]]` requires **both** conditions. With a different prev, `new_codes` ≠ `{codes where is_new=True}`.
7. `new_ioctls_vs_prev` for `cu_ctx_destroy` will be empty or wrong — silently. The total ioctl count and unique code count are still correct. Only the "new ioctls introduced" table in the report is wrong.

This is the most likely place for a bug because it's the most central mechanism (delta attribution), the failure is silent (no error, just wrong output), and it's triggered by a perfectly reasonable future action (completing the pipeline).

---

### 4. Warnings for a New Engineer

> **Read this before touching anything.**

**1. The pipeline has no orchestrator and order is absolute.**
Running `build_schema.py` before `annotate_static.py` finishes, or running `parse_trace.py` with the wrong `--prev`, produces plausible-looking but wrong output with no error message. There is no Makefile at the repo root to enforce order. Always run: `strace → parse_trace.py (with correct --prev) → annotate_static.py → build_schema.py → generate_report.py`.

**2. The `is_new` flag is a contract, not just metadata.**
`build_schema.py` uses `is_new` from the parsed JSON **and** independently recomputes `new_codes`. Both must refer to the same prev step. If you ever re-parse a step, you must re-parse every downstream step too, then re-annotate and re-build. There is nothing that tells you when this is necessary.

**3. `build_schema.py` and `generate_report.py` run at import time.**
`import build_schema` will overwrite `schema/master_mapping.json`. `import generate_report` will overwrite `CUDA_IOCTL_MAP.md`. Do not import these in notebooks, tests, or other scripts.

**4. The 5 missing intermediate steps make `cu_ctx_destroy`'s deltas wrong right now.**
`cu_ctx_destroy` is in `STEP_ORDER` at position 9 but only 4 steps have data. Its "net event delta" and "new codes" figures are computed against `cu_ctx_create`, not `cu_mem_free`. When you add the missing steps, `cu_ctx_destroy` must be re-run through the entire pipeline from `strace` capture forward.

**5. Trace output is machine-specific.**
The ioctl set varies by GPU count, driver version, and whether UVM is active. `0xC00C46D1` (`NV_ESC_GPU_ENUM_BOUNDARY`) fires on any machine with fewer GPUs than the highest probed device node index. Running on a 4-GPU machine will produce a different ioctl set than a 3-GPU machine — neither is wrong, but they are not directly comparable without this context.

**6. `decoded` contains live memory addresses — never diff the schema files raw.**
`schema/master_mapping.json` and the baseline copy both contain raw strace lines with pointer values that change every run. Use the `confidence_summary` and `unique_codes` fields for comparison, not file-level diff.

**7. `lookup/ioctl_table.json` is the single point of truth — and has no validation.**
A typo in a key, a missing `"confidence"` field, or a wrong hex code silently downgrades all affected annotations to `"none"` confidence. Before adding entries, manually verify the hex decoding with `_IOC()` arithmetic and cross-check against a kernel header if possible.

---

> ✋ **Phase 3 complete. Full review documented in `code.md`.**
