# Code Review — `ioctl-cuda-mapping / cuda-ioctl-map`

Reviewer level: L6-equivalent.  
Date: 2026-02-25.  
Scope: `cuda-ioctl-map/` (primary codebase) + `cuda_ioctl_sniffer/` (reference/prior art).

---

## Phase 1 — Repo Overview

### 1. Overall Purpose

This repository is a **CUDA ioctl reverse-engineering and replay toolkit**. Its two objectives are:

1. **Map**: Determine empirically which Linux `/dev/nvidia*` ioctl sequences are issued for each CUDA API call (`cuInit`, `cuDeviceGet`, `cuCtxCreate`, `cuCtxDestroy`), annotate them with names and confidence levels, and publish a human-readable report (`CUDA_IOCTL_MAP.md`).

2. **Replay**: Capture the full raw argument buffers of those ioctls via an LD_PRELOAD interposer, auto-discover which bytes are kernel handles, and replay the captured sequence without any CUDA library — just `open()` + `ioctl()` + captured bytes, with minimal handle patching.

The end state (already reached for `cuInit`) is `230/230 ioctls succeeded, 0 failed` against the live driver without `libcuda.so`.

---

### 2. Directory Structure

```
ioctl-cuda-mapping/
├── cuda_ioctl_sniffer/   ← geohot prior-art (reference only, NOT part of the main pipeline)
│
└── cuda-ioctl-map/       ← MAIN PROJECT
    │
    ├── programs/         Stage 0 — CUDA source programs (.cu) + compiled binaries
    │                     Each program is a minimal cumulative CUDA API caller:
    │                     cu_init → cu_device_get → cu_ctx_create → cu_ctx_destroy
    │
    ├── traces/           Stage 1 — Raw strace logs (.log files)
    ├── parsed/           Stage 2 — Parsed ioctl JSON from strace (parse_trace.py output)
    ├── annotated/        Stage 3 — Parsed JSON + annotation objects (annotate_static.py output)
    ├── schema/           Stage 4 — Aggregated master_mapping.json (build_schema.py output)
    │
    ├── lookup/           Static data — ioctl_table.json (manually curated name/confidence map)
    ├── baseline/         Frozen snapshots of the Stage 1–4 outputs at two timestamps
    │
    ├── sniffed/          Phase 1 output — raw hex capture JSONL files from interposer
    ├── intercept/        Phase 1 code — LD_PRELOAD interposer (nv_sniff.c + Makefile)
    │                     Also stores handle_offsets.json (Phase 2 output — misplaced, see §5)
    ├── tools/            Phase 2 + Phase 4 support scripts
    │                     (find_handle_offsets.py, collect_two_runs.sh,
    │                      snapshot_driver_state.sh, compare_snapshots.py)
    ├── replay/           Phase 3 — ioctl replay tool (replay.c, handle_map.h, Makefile)
    ├── validation/       Phase 4 — snapshot outputs + run_validation.sh
    │
    ├── parse_trace.py    Top-level: Stage 2 entry point
    ├── annotate_static.py Top-level: Stage 3 entry point
    ├── build_schema.py   Top-level: Stage 4 entry point
    ├── check_reproducibility.py  Orthogonal QA tool
    ├── generate_report.py Top-level: report renderer
    └── CUDA_IOCTL_MAP.md  Generated output (checked in)
```

**What each folder owns:**

| Folder | Owner/Role |
|--------|-----------|
| `programs/` | CUDA test binaries — sources of truth for "what does this API call do?" |
| `traces/` | Raw strace output — transient, machine-specific |
| `parsed/` | Structured ioctl sequences extracted from strace |
| `annotated/` | Same + human-curated name/description/confidence from `lookup/` |
| `schema/` | Master aggregation — single source for the report |
| `lookup/` | Hand-maintained ioctl name/confidence database |
| `baseline/` | Immutable point-in-time snapshots for regression testing |
| `sniffed/` | Binary-level captures from LD_PRELOAD interposer (hex buffers) |
| `intercept/` | Interposer build artifacts + `handle_offsets.json` (produced by Phase 2) |
| `tools/` | Analysis scripts (handle offset finder, snapshot, diff) |
| `replay/` | Replay tool source + compiled binary |
| `validation/` | Validation shell script + snapshot outputs |

---

### 3. Entry Points

There are **two separate sub-pipelines** with distinct entry points:

#### Sub-pipeline A — strace-based analysis (existing, complete)
```
strace -e trace=ioctl,openat,close ./programs/<step>
  └─▶ traces/<step>.log
        └─▶ parse_trace.py traces/<step>.log
              └─▶ parsed/<step>.json
                    └─▶ annotate_static.py parsed/<step>.json
                          └─▶ annotated/<step>.json
                                └─▶ build_schema.py
                                      └─▶ schema/master_mapping.json
                                            └─▶ generate_report.py
                                                  └─▶ CUDA_IOCTL_MAP.md
```

#### Sub-pipeline B — LD_PRELOAD replay (new, completed for cuInit)
```
intercept/collect.sh             → sniffed/*.jsonl
tools/collect_two_runs.sh        → sniffed/cu_init_{a,b}.jsonl
tools/find_handle_offsets.py     → intercept/handle_offsets.json
replay/replay <capture.jsonl>    → ioctls against live driver
validation/run_validation.sh     → PASS/FAIL driver state comparison
```

The two pipelines are **independent** — sub-pipeline A has no knowledge of sub-pipeline B's outputs, and vice versa.

---

### 4. Key Data Structures

| Structure | Where | What it is |
|-----------|-------|-----------|
| JSONL capture record (`open`) | `sniffed/*.jsonl` | `{type, seq, path, ret}` |
| JSONL capture record (`ioctl`) | `sniffed/*.jsonl` | `{type, seq, fd, dev, req, sz, before, after, ret}` — `before`/`after` are lowercase hex strings of `sz` bytes |
| `handle_offsets.json` entry | `intercept/` | `{name, handle_offsets[], output_handle_offset?, fd_offsets[]?, sample_count}` keyed by `"0xXXXXXXXX"` |
| `HandleMap` | `replay/handle_map.h` | Open-addressed hash map, `uint32 → uint32`, capacity 4096, sentinel `0xFFFFFFFF` |
| `ReqSchema` | `replay/replay.c` | In-memory parsed form of one `handle_offsets.json` entry |
| `fd_map[]` | `replay/replay.c` | `int[4096]`: `orig_fd → replay_fd`, index = original fd number |
| Parsed ioctl record | `parsed/*.json` | `{sequence_index, fd, device, request_code, decoded, args, return_value, is_new}` |
| Annotated ioctl record | `annotated/*.json` | Parsed record + `annotation: {name, description, phase, confidence, needs_review}` |
| `master_mapping.json` | `schema/` | Per-step aggregation: devices, total/unique counts, deltas, confidence summary, repro report, full sequence |

The central shared artifact between Phase 2 and Phase 3 is `handle_offsets.json`. Everything else is consumed only within a single stage.

---

### 5. Architectural Patterns

1. **Linear stage pipeline** (sub-pipeline A): Each stage reads the output of the previous and writes a new artifact. Clean, easy to re-run from any stage.

2. **LD_PRELOAD interposition** (sub-pipeline B, Phase 1): Classic `dlsym(RTLD_NEXT)` hook pattern. Hooks `open`, `openat`, `close`, `ioctl`.

3. **Empirical handle discovery** (Phase 2): XOR-diff of same-position records across two independent runs. No struct schema needed — any aligned 4-byte window that is non-zero in both runs but differs between them is a handle candidate.

4. **Raw replay with lazy patching** (Phase 3): Start with captured bytes verbatim; patch only the offsets that are known to vary. Identity handles (libcuda pre-specified constants) pass through untouched.

5. **Sentinel-file process coordination** (Phase 4): `replay.c` writes `replay.ready` to signal completion; `run_validation.sh` polls for it before snapshotting driver state. Simple but fragile (see §5 below).

---

### 6. Structural Flags and Surprises

The following are not per-file bugs but architectural observations that warrant attention before this codebase grows.

#### F1 — Two repos co-habiting `ioctl-cuda-mapping/`
`cuda_ioctl_sniffer/` (geohot's prior art) and `cuda-ioctl-map/` (the active project) live as siblings. There is no shared build system, no cross-reference between them in code, and no README at the top level explaining the relationship. A new engineer arriving at `ioctl-cuda-mapping/` has no obvious indicator of which directory to work in. The `.git` symlink/file inside `cuda_ioctl_sniffer/` suggests it may be a git submodule or worktree, but without a top-level `.gitmodules` the relationship is opaque.

#### F2 — `handle_offsets.json` ownership mismatch
`handle_offsets.json` is the **output of Phase 2** (`tools/find_handle_offsets.py`) but lives in `intercept/`, which is Phase 1's home. The plan was written with this layout, but it creates a confusing ownership boundary: `intercept/` contains both a build input (source code of the interposer) and a data artifact produced by a completely different script. If `intercept/` is ever cleaned (`make clean`) the expectation is that only the compiled `.so` is removed — but `handle_offsets.json` lives there too, and could be accidentally deleted or cause confusion about what that directory "owns."

#### F3 — `STEP_ORDER` in `build_schema.py` is aspirational, not actual
`build_schema.py` hard-codes a `STEP_ORDER` of nine steps including `cu_mem_alloc`, `cu_launch_kernel`, `cu_memcpy_htod`, etc. None of those programs exist in `programs/`. When `build_schema.py` runs today, it silently skips the missing steps — but the B2 warning logic only fires when a present step's *predecessor* is absent. If someone adds `cu_ctx_destroy` data and wonders why delta metrics look odd, the answer is buried in the missing intermediate steps. The aspirational step list should either be trimmed to what exists or accompanied by a comment.

#### F4 — Compiled artifacts committed to the repository
`intercept/libnv_sniff.so`, `replay/replay`, and the four binaries in `programs/` appear to be committed (they show no source extension, implying they are ELF binaries on disk alongside the source). Binaries committed to a repo are not reproducible on a different CUDA version or machine, and `git diff` on them produces meaningless output. This should be `.gitignore`'d.

#### ~~F5~~ — `replay.ready` sentinel is CWD-relative, validated from `$ROOT_DIR` ✅ FIXED
**Was:** `replay.c` wrote the sentinel with `fopen("replay.ready", "w")` — a path relative to whatever the process's CWD happened to be at invocation time. `run_validation.sh` polled `$ROOT_DIR/replay.ready`. These matched only when the script was run from `cuda-ioctl-map/` as instructed; invoking `replay` directly from `replay/` or from a test harness would silently diverge, causing the poll loop to time out after 10 s with no actionable error.

**Fix (`replay/replay.c`):** The sentinel path is now computed as an absolute path by calling `realpath()` on the capture file's parent-of-parent directory (the project root), then appending `/replay.ready`. This derivation is consistent with how `default_offsets` already locates `intercept/handle_offsets.json` — just made absolute. A fallback to the original CWD-relative behaviour is retained (with a loud warning) if `realpath()` fails. The sentinel write now also prints the resolved path on success and emits a named error on failure, rather than silently doing nothing.

#### F6 — The strace pipeline and the LD_PRELOAD pipeline are not wired together
Sub-pipeline A (strace) captures sequence number, request code, args pointer value, and return value. Sub-pipeline B (LD_PRELOAD) captures raw hex buffers. There is no tool that combines them — no way to answer "for seq index 42 in the annotated trace, what was the actual `before` buffer?" This gap is intentional for the POC but means that the two pipelines' outputs cannot be cross-validated or enriched by each other.

#### F7 — No top-level orchestration script
To go from zero to a complete replay requires manually running at minimum: `intercept/collect.sh`, `tools/collect_two_runs.sh`, `tools/find_handle_offsets.py`, and `validation/run_validation.sh`. There is no `run_all.sh` or `Makefile` at the repo root that chains these phases in order. For a new engineer, the correct invocation sequence is only documented in `plan-v1.md` (a planning document, not a usage guide).

---

---

## Phase 2 — File-by-File Drill-Down

Files reviewed in dependency order: programs → interposer → capture scripts → handle discovery → replay core → validation → strace pipeline → schema/report layer.

---

### `programs/cu_init.cu`

**Responsibility:** Minimal CUDA program that calls `cuInit(0)` and exits. Sole purpose is to be the traced/sniffed target for the cuInit stage.

**Input/Output:** None / exit code + stdout.

**Issues:**
- None. The only CUDA call is `cuInit`; the return value is checked. Correct.

---

### `programs/cu_device_get.cu`, `programs/cu_ctx_create.cu`, `programs/cu_ctx_destroy.cu`

**Responsibility:** Cumulative CUDA programs — each calls every API of the previous step plus one more. The design ensures that the strace delta between steps reflects only the new call's ioctls.

**Issues:**

- **`cuInit` return value is not checked in `cu_device_get`, `cu_ctx_create`, `cu_ctx_destroy`.** Only `cu_init.cu` checks it. If `cuInit` fails silently (e.g. wrong driver version), the subsequent calls succeed with undefined state, and the resulting trace is incomplete or garbage. For tracing purposes strace will still capture the ioctls, but the captured sequence may be shorter than a healthy run, causing `check_reproducibility.py` to flag non-determinism that isn't real. A `printf("WARN: cuInit failed\n")` + early return would make failures explicit.

- **`cu_ctx_create.cu` does not destroy the context it creates.** This is intentional (the binary is named for the "create" step) and the comment says so. But the binary name implies a complete lifecycle; a new engineer may find it surprising. The annotation in the printf helps, but it's easy to miss.

---

### `programs/Makefile`

**Responsibility:** Build all four CUDA programs with nvcc.

**Issues:**

- **`NVCC` is hardcoded to `/usr/local/cuda-12.5/bin/nvcc`.** The comment says "set NVCC to override" and gives an example path of `cuda-12.6`. If the installed CUDA version changes, the default silently fails with "file not found" rather than "CUDA version mismatch." Using `NVCC ?= nvcc` as the default (relying on PATH) and documenting the override would be more portable.

- **`-arch=native`** compiles for the local GPU architecture. This is correct for tracing — the binary must run — but it means the compiled binary in the repo cannot be used on a machine with a different GPU family without recompilation.

---

### `intercept/nv_sniff.c`

**Responsibility:** LD_PRELOAD interposer. Hooks `open`/`openat`/`close`/`ioctl` via `dlsym(RTLD_NEXT)`. For every ioctl on a `/dev/nvidia*` fd, snapshots the arg buffer before and after the call and emits a JSONL record.

**Input:** The target process's libc calls. `$NV_SNIFF_LOG` env var for output path.  
**Output:** JSONL file — one `open` or `ioctl` record per line, flushed after every write.

**Issues:**

1. **~~Data race on `nv_fd_active[]`.~~ ✅ FIXED.** Previously `fd_is_nvidia(fd)` read `nv_fd_active[fd]` without holding the mutex while `track_fd`/`untrack_fd` wrote under the lock, which is technically undefined in the C memory model. `fd_is_nvidia` now acquires `lock` around the read, so all accesses to `nv_fd_active` are consistently synchronised.

2. **`fd_path(fd)` requires the lock, but `fd_is_nvidia(fd)` (the gate to reaching `fd_path`) does not hold it.** If a concurrent `close` fires between `fd_is_nvidia` returning true and `fd_path` being called inside the lock, `nv_fd_path[fd]` could be NULL. The `fd_path` helper correctly returns `""` for NULL, so there is no crash — but the emitted record would have an empty `dev` field, silently breaking `find_handle_offsets.py`'s device filter.

3. **`NV_SNIFF_LOG` unset → silent no-op.** If the env var is not set, `log_fp` is NULL and everything passes through without a word. A process accidentally run without the env var produces no log and no diagnostic. The plan mitigates this by using `collect.sh` which sets the variable, but direct invocation of a program without the script would silently produce no capture.

4. **`_NV_IOC_SIZE` uses `unsigned` cast, not `unsigned long`.** The request code is `unsigned long` (64 bits on x86-64). The macro casts to `(unsigned)` (32 bits) before shifting. For NVIDIA ioctl codes the upper 32 bits are always zero (the codes fit in 32 bits), so this works. But it is a latent truncation if any future code uses the upper 32 bits.

5. **`open64` / `openat64` are aliased but `creat` is not hooked.** `creat(path, mode)` is equivalent to `open(path, O_CREAT|O_WRONLY|O_TRUNC, mode)` and is sometimes used by legacy code. Not an issue for CUDA (which uses `openat`), but worth noting for completeness.

---

### `intercept/Makefile`

**Responsibility:** Build `libnv_sniff.so`.

**Issues:**
- `make clean` removes only `libnv_sniff.so` and does NOT touch `handle_offsets.json`. Given that `handle_offsets.json` lives in this directory (flag F2), a developer running `make clean` here would correctly not delete the JSON. But they might expect a full clean to reset the directory; the asymmetry is undocumented.

---

### `intercept/collect.sh`

**Responsibility:** Build the interposer and run all four CUDA programs under it.

**Issues:**

1. **No check that `programs/` binaries exist before running.** If programs haven't been compiled, the loop fails with `bash: .../programs/cu_init: No such file or directory` and `set -e` exits. The error message is correct but not actionable ("run `make -C programs` first" would be helpful).

2. **No validation that the output JSONL is non-empty.** The script prints line counts but doesn't abort if a file has 0 lines (e.g. CUDA init failed, NV_SNIFF_LOG wasn't picked up). A subsequent `find_handle_offsets.py` run would then operate on empty captures and produce an empty `handle_offsets.json` with no warning.

---

### `tools/collect_two_runs.sh`

**Responsibility:** Run `cu_init` twice under the interposer, producing `cu_init_a.jsonl` and `cu_init_b.jsonl` for handle offset discovery.

**Issues:**

1. **Line count mismatch is a warning, not an abort.** If the two runs produce different line counts, `find_handle_offsets.py` will align by position and flag req mismatches. The Python script handles this gracefully, but the shell script's warning can be missed in automated runs. Aborting here is safer since proceeding with misaligned captures produces a degraded `handle_offsets.json`.

2. **No inter-run delay.** The two runs are executed back-to-back. If the driver has any per-process state that persists briefly after exit (e.g. deferred object cleanup), the second run might see different enumeration. In practice 230/230 succeeded, so this is not an active problem.

---

### `tools/find_handle_offsets.py`

**Responsibility:** XOR-diff two captures across aligned 4-byte windows to identify handle fields. Emit `handle_offsets.json`.

**Input:** Two JSONL capture files.  
**Output:** `intercept/handle_offsets.json`.

**Issues:**

1. **`aborts >= 5` threshold is dead code.** The req-mismatch handler does `aborts += 1` then immediately `break`. `aborts` can reach at most 1 in a single call. The `if aborts >= 5: sys.exit(1)` block never fires. The intent was to accumulate mismatches before aborting, but the `break` exits the loop on the first mismatch. Either remove the accumulator and always abort on first mismatch, or remove the `break` and let the loop continue pairing remaining records.

2. **`is_ptr_lower_half` range starts at `0x00007e00`, not `0x00007f00`.** The comment says "canonical x86-64 userspace pointer high word: 0x00007f00–0x00007fff" but the code checks `0x00007e00 <= v`. The extra 256-page buffer is conservative (safe direction — it over-rejects), but a handle whose value happened to land in `0x00007e00–0x00007eff` would be silently dropped from the confirmed set. Since RM handles are small positive integers in practice this has no observable effect, but the comment and the code should agree.

3. **`output_handle_offset` uses only run A's `after` buffer.** The zero-to-nonzero detection reads `after_a` but never `after_b`. If the zero-at-before is a coincidence in run A (e.g. a padding field that happened to be zero in that run), a false positive `output_handle_offset` could be emitted. Cross-checking with run B's after buffer would eliminate this. In practice the discovered offsets (`8` for `0xC020462B`) match the known `hObjectNew` field, so the current data is correct.

4. **`ioctl_table_path` derivation assumes `out_path` is exactly two levels from the repo root.** `Path(out_path).parent.parent / "lookup" / "ioctl_table.json"` works for `intercept/handle_offsets.json` but fails silently if the output path is absolute or at a different depth. When it fails, `name_map` is empty and all names in `handle_offsets.json` are raw hex strings (which is the current state of the file on disk — confirming this path has been hit).

5. **`NVIDIACTL_ONLY` has no CLI override.** Disabling the device filter to experiment with UVM offset discovery requires a code edit. Low priority for the POC but worth a `--all-devices` flag if the tool is reused.

---

### `intercept/handle_offsets.json`

**Responsibility:** Schema artifact consumed by `replay.c`. Maps request codes to byte offsets that require patching.

**Issues:**

1. **All `name` fields are raw hex strings, not human names.** The `find_handle_offsets.py` name-lookup path failed during the run that produced this file (ioctl table path derivation issue, above). This is cosmetic — `replay.c` only reads the hex key — but it makes the file harder to audit manually.

2. **`0xC90046C8` (`NV_ESC_ATTACH_GPUS_TO_FD`) has `output_handle_offset: 0`.** Offset 0 is also position of the first input handle field for RM structs. The zero-to-nonzero detector fired here, meaning position 0 was zero in `before_a` and non-zero in `after_a` for more than half the records. This could be a true kernel-assigned output handle at offset 0, or a false positive from a struct where position 0 happens to be written by the kernel as a status/count field. Given that replay succeeded 230/230, either the mapping is correct or the handle at offset 0 is a constant that maps to itself. Worth verifying against the struct definition in `open-gpu-kernel-modules`.

3. **`0xC030462B` (large RM_ALLOC) has `handle_offsets: [0, 4, 20]` but no `output_handle_offset`.** Consistent with discovery D1 (caller pre-specifies handles). The large variant behaves the same as the small variant for handle ownership. Correct.

---

### `replay/handle_map.h`

**Responsibility:** Fixed-capacity open-addressed hash map (`uint32_t → uint32_t`) for handle remapping during replay.

**Issues:**

1. **Sentinel value `0xFFFFFFFF` is an assumption, not an invariant.** The comment says it "must never appear as a real handle." NVIDIA RM handles are opaque allocator values; the comment is based on observed behaviour, not a documented guarantee. If a future driver version allocates `0xFFFFFFFF`, `hm_put` silently ignores it (returns 0) and `hm_get` returns "not found." The replay would then issue the ioctl with the uncorrected handle value and likely fail. A defensive `assert(key != HM_SENTINEL)` in `hm_put` would turn the silent failure into a loud one.

2. **No deletion support — acceptable for monotonic build-up.** Replay processes in seq order and never removes handles. If teardown sequences (RM_FREE) are added in a future phase, the map will need tombstone support or a rebuild strategy.

3. **`hm_dump` prints count redundantly.** `main` already prints `[handle_map] final state: N entries` before calling `hm_dump`, which then prints the same count again as its header. Minor noise in the output.

---

### `replay/replay.c`

**Responsibility:** Read a JSONL capture, re-open device files, re-issue each ioctl with the captured `before` buffer (patching handles and fd numbers at known offsets).

**Input:** `<capture.jsonl>` + optional `<handle_offsets.json>`.  
**Output:** Ioctl results to stdout, handle map to stdout on exit, `replay.ready` sentinel file.

**Issues:**

1. **Hand-rolled JSON parser is not general.** `json_str`/`json_long`/`json_u32hex`/`json_hexbuf` all use `strstr` on the raw line without respecting JSON object boundaries. If a hex value in the `before` field happened to contain the substring `"fd":` followed by digits, `json_long(line, "fd", ...)` would extract the wrong value. The probability for NVIDIA ioctl data is negligible, but this is a class of latent bug that would be eliminated by using any proper JSON library.

2. **`load_schemas` uses first `}` as the entry boundary.** The JSON parser for `handle_offsets.json` finds each entry's closing `}` with `strchr(p, '}')`. This is a shallow, non-nested search. Adding any nested object or array-of-objects inside a schema entry (e.g. `"metadata": {"source": "..."}`) would truncate the parse at the inner `}`, silently missing fields that appear after it.

3. **~~Failed open is counted as `failed` via ioctl SKIPs.~~ ✅ FIXED.** Originally, when an `open` call failed, `fd_map[orig_fd]` stayed at -1 and every subsequent ioctl on that fd hit `SKIP (fd %d not mapped)` while still incrementing `failed`. The exit code `(failed == 0) ? 0 : 1` then treated expected failures (e.g. `/dev/nvidia3` missing) as hard errors. **Now:** `replay.c` maintains a separate `skipped` counter; the `fd not mapped` path increments `total` and `skipped` but not `failed`, and the summary prints `DONE — ok/total succeeded, failed, skipped`. The process exit code still depends solely on `failed`, so expected skipped ioctls no longer cause a non-zero exit.

4. **`orig_fd >= MAX_FD_MAP` drops mapping silently.** If a process opened a device at a high fd number (e.g. after many `dup2` calls), `fd_map[orig_fd]` is never registered and the subsequent ioctl is silently skipped. The log says "SKIP (fd %d not mapped)" but doesn't distinguish between "fd was never opened" and "fd number exceeded MAX_FD_MAP." Easy fix: log the reason.

5. **`default_offsets` path is still relative (not absolutized).** Only `sentinel_path` was made absolute via `realpath`. `default_offsets` remains a path like `sniffed/../intercept/handle_offsets.json` — relative to CWD. If `replay` is invoked from a different CWD, `load_schemas` will print "not found — no handle patching" and proceed without patching, succeeding trivially (most ioctls will fail). The same `realpath` treatment applied to `sentinel_path` should also be applied to `default_offsets`.

---

### `replay/Makefile`

**Responsibility:** Build the `replay` binary.

**Issues:**
- None functional. `-Wall -Wextra` is present. The build is self-contained. No install target (not needed for a dev tool).

---

### `validation/run_validation.sh`

**Responsibility:** Orchestrate end-to-end validation: real cuInit snapshot → replay snapshot → structural diff.

**Issues:**

1. **No check that required binaries exist before starting.** If `programs/cu_init` or `replay/replay` is missing, `set -e` will abort with a bare "not found" error. A pre-flight check with descriptive messages would make failures actionable.

2. **Poll timeout is fixed at 10 seconds with no configuration.** 100 × 0.1 s. Fine for cuInit (230 ioctls, ~1 s). For a future longer capture (thousands of ioctls), this will spuriously time out. The timeout should be a configurable variable at the top of the script.

3. **`replay` exit code warning does not propagate to script exit.** If `replay` exits 1 (some ioctls failed), the script warns but continues and may exit 0 if the snapshot diff passes. A CI pipeline checking only the script's exit code would miss the ioctl failure. Consider exiting non-zero if `REPLAY_EXIT -ne 0`.

4. **Snapshot of real cuInit is taken after `cu_init` exits, not while it's alive.** The driver releases all RM objects when the process exits. By the time `snapshot_driver_state.sh` runs, the cuInit process is gone. The comparison is therefore not "real cuInit state vs replay state while both are alive" but "idle driver state after real cuInit vs idle driver state after replay." This is intentional for the POC and documented in the plan, but means the validator cannot catch object-tree differences that get cleaned up on exit.

---

### `tools/snapshot_driver_state.sh`

**Responsibility:** Write a driver state file: nvidia-smi output, /proc/driver/nvidia/gpus, /proc/driver/nvidia/params, optional fd count for a PID.

**Issues:**

1. **`ls -la /proc/$PID/fd | grep -c nvidia` counts symlink lines by path text.** If any fd symlink's display line contains the string "nvidia" somewhere other than the device path (e.g. a file named "nvidia-config.txt" in the process's open files), it would be counted. More precise: `ls -la /proc/$PID/fd 2>/dev/null | awk '{print $NF}' | grep -c '^/dev/nvidia'`.

2. **`/proc/driver/nvidia/params` was not in the plan spec.** The plan described capturing only `nvidia-smi` and `/proc/driver/nvidia/gpus`. The actual script additionally captures kernel module parameters. These are static across runs and benign, but they are not covered by `compare_snapshots.py`'s volatile-field strip logic. If a module parameter line contained a large decimal number (e.g. a memory address), `LARGE_NUM_PAT` would normalise it — but a parameter with a 7- or 8-digit value would not be stripped and could cause a false FAIL if it differed between machines.

---

### `tools/compare_snapshots.py`

**Responsibility:** Structurally diff two driver snapshots by stripping all volatile fields (handles, PIDs, timestamps, telemetry) and running a unified diff.

**Issues:**

1. **Empty snapshot produces false PASS.** If `snapshot_real.txt` or `snapshot_replay.txt` is empty (e.g. the snapshot script failed), `load_normalised` returns `[]` for both, the diff is empty, and the script exits 0. A minimum-line-count guard after loading would catch this.

2. **`LARGE_NUM_PAT` strips 9-digit decimal numbers.** This normalises memory sizes (8 GiB = 8,589,934,592 bytes = 10 digits → stripped). It also strips GPU serial numbers or other static large decimal fields. On a single machine comparing two runs, static fields are identical so stripping or not stripping them doesn't change the outcome. But the normalisation is lossy — if two fields that should differ (e.g. memory in use) happened to both be large numbers, the difference would be hidden.

3. **Line-order sensitivity.** `difflib.unified_diff` is order-sensitive. If `nvidia-smi -q` lists GPUs in a different order between two calls on a multi-GPU machine (possible if a GPU resets mid-run), every line of both GPU blocks would appear as changed. For the current single-machine use case this hasn't been an issue.

4. **`HEX_PAT` has a 6-char minimum.** `0x[0-9a-fA-F]{6,}` does not strip short hex values like `0x1234` (4 chars) or `0xC1D0` (4 chars). Short handle values wouldn't be present in nvidia-smi output but could appear in `/proc/driver/nvidia/params`. If they vary between runs, they'd cause a false FAIL.

---

### `parse_trace.py`

**Responsibility:** Single-pass strace log parser. Maintains a live fd→device map, reconstructs ioctl event sequence, marks first-seen codes as `is_new` relative to a previous step's output.

**Issues:**

1. **`is_new` is always `False` in `parse_lines()`.** The `is_new` field is only set in the `parse()` wrapper after comparing against a previous file. `check_reproducibility.py` calls `parse_lines()` directly, so repro-run JSONs always have `is_new: False`. This is a known design limitation (repro runs don't need delta marking), but it means the two kinds of JSON output in `parsed/` are structurally inconsistent in a non-obvious way.

2. **Multi-line strace output is silently dropped.** strace can wrap long argument lists across lines. The parser processes one line at a time; a split line matches neither regex and is silently skipped. For the `-e trace=ioctl,openat,close` filter with simple argument values, this hasn't occurred. If strace ever adds pretty-printing for a new NVIDIA ioctl's argument struct, records would vanish without warning.

3. **`OPENAT` regex only captures `/dev/nvidia*` paths.** Non-NVIDIA fds are never entered into `fd_map`. This is correct by design (we only care about NVIDIA fds), but it means `CLOSE` calls for non-NVIDIA fds silently `pop(None)` — harmless but could mask a bug if NVIDIA fds were incorrectly identified.

4. **`_load_prev_codes` returns an empty set if `prev_parsed` is `None` or the file doesn't exist.** This means running `parse_trace.py` without specifying a previous file treats all codes as new. Correct default — but if someone accidentally omits the `prev_parsed` argument when re-running a step, all `is_new` flags reset, and `build_schema.py` would compute incorrect delta metrics.

---

### `annotate_static.py`

**Responsibility:** Look up each ioctl request code in `ioctl_table.json` and attach an annotation object to every record in a parsed JSON.

**Issues:**

1. **`LOOKUP` is loaded at module import time.** `FileNotFoundError` on import if `lookup/ioctl_table.json` is missing. Any tool that imports this module (even just to use a helper) would fail immediately if the lookup file is absent.

2. **Annotation is a shallow dict copy.** `ann = dict(LOOKUP[c])` copies top-level keys only. The current table has all-flat values (strings), so this is safe. If a nested structure (e.g. `"aliases": [...]`) were added to the table, mutations to `ann` would corrupt the in-memory `LOOKUP` for all subsequent lookups in the same process run.

3. **No deduplication of annotation objects in the output JSON.** Every occurrence of the same request code gets its own copy of the annotation dict. For cuInit (230 records, ~16 unique codes), this produces ~214 redundant annotation copies. For a longer trace (thousands of records), the annotated JSON grows proportionally. The schema builder in `build_schema.py` embeds the full annotated sequence in `master_mapping.json`, compounding the bloat.

---

### `build_schema.py`

**Responsibility:** Aggregate all annotated per-step JSONs into `schema/master_mapping.json`.

**Issues:**

1. **`STEP_ORDER` contains five steps that do not exist.** `cu_mem_alloc`, `cu_memcpy_htod`, `cu_launch_kernel`, `cu_memcpy_dtoh`, `cu_mem_free` are in the list but have no corresponding files in `annotated/`. The B2 warning logic fires only when a *present* step's *immediate predecessor* is absent — it doesn't warn about steps that are entirely missing from `STEP_ORDER`'s tail. If `cu_mem_alloc` data were added later without adding the intermediate steps, deltas would be silently computed against `cu_ctx_destroy` (last present step) rather than any meaningful predecessor.

2. **`new_ioctls_vs_prev` can contain duplicates if a step is re-parsed without a `prev_parsed` argument.** The field is filtered by `i["is_new"]`, which would be True for all records in that case. The `generate_report.py` deduplication (`seen` set) defends at render time, but `master_mapping.json` itself would contain duplicate entries, which is a data quality issue.

3. **`master_mapping.json` embeds full ioctl sequences.** For four steps with ~300 records each, the schema file is already several hundred KB. At nine steps it will be multi-MB. The full sequence is needed for `generate_report.py`'s per-step event tables, but the schema is also the input for any programmatic consumer. Consider separating the summary schema from the full sequences.

---

### `check_reproducibility.py`

**Responsibility:** Run a binary N times under strace, measure ioctl presence-determinism and frequency-stability across runs.

**Issues:**

1. **`strace` availability is not checked before the loop.** If strace is absent, `subprocess.run` returns exit code 127 (not in `(0, 1)`), the warning is printed, and then `open(log_path)` fails with `FileNotFoundError` because the log was never written. The error message does not mention strace. A pre-flight `shutil.which("strace")` check would make the failure clear.

2. **`returncode not in (0, 1)` is too permissive.** Exit code 2 from strace means a strace argument error; 127 means command not found. Both should be fatal, not just warnings. The guard should be `returncode not in (0, 1)` → `stderr` warning; but for 127 specifically, it should abort.

3. **Repro trace files in `traces/` mix with canonical trace files.** `repro_cu_init_run0.log` etc. share the directory with `cu_init.log`. There is no cleanup between runs; re-running `check_reproducibility` accumulates files. The `parse/` directory similarly mixes repro JSONs with canonical JSONs. This isn't a bug but makes the directory state hard to reason about after multiple runs.

4. **`_synthetic_test()` does not exercise the C1 (frequency-stability) path.** The self-test validates occurrence-rate computation and determinism scoring, but the `frequency_unstable_codes` and `frequency_stability_score` fields added by the C1 fix are not tested. A test that provides per-run counts that differ (same code appearing 3 times in run 0 but 5 times in run 1) would confirm the C1 path is correct.

---

### `generate_report.py`

**Responsibility:** Render `schema/master_mapping.json` as `CUDA_IOCTL_MAP.md`.

**Issues:**

1. **`master_mapping.json` loaded at module level.** Same pattern as `annotate_static.py` — `FileNotFoundError` on import if the file doesn't exist.

2. **`_md_escape` is incomplete.** It escapes `|`, `*`, `` ` ``, `[` but not `]`, `_`, `#`, or `>`. A description containing `]` immediately following a `[` creates a broken Markdown link. A description containing `_word_` renders as italic in most renderers. The `ioctl_table.json` values currently don't trigger these, but the function gives a false sense of safety.

3. **No table of contents or anchor links.** The generated report concatenates all steps sequentially. For the current four steps this is manageable. At nine steps (full `STEP_ORDER`) the file will be long with no navigation. Adding `## Contents` links at the top is low-effort and high-value for document usability.

---

### `lookup/ioctl_table.json`

**Responsibility:** Static hand-curated ioctl name/description/phase/confidence database. Single source of truth for annotation.

**Issues:**

1. **No driver version field.** NVIDIA ioctl interfaces are version-specific. An entry like `NV_ESC_RM_ALLOC` may have different struct layouts or different request code values across driver versions. Without a `"driver_version"` or `"since_version"` field, the table silently becomes stale after a driver update, and `annotate_static.py` would attach wrong names with high confidence.

2. **UVM codes use flat 8-bit numbering and no device qualifier.** Entries like `"0x00000017"` (`UVM_MAP_EXTERNAL_ALLOCATION`) don't note that they are `/dev/nvidia-uvm` ioctls, not `/dev/nvidiactl` ioctls. A consumer of the table comparing against the full ioctl sequence could misattribute a UVM code seen on `/dev/nvidiactl` (if such a thing occurred) as a UVM memory-mapping operation.

3. **~~`0xC030462B` confidence is "medium" despite being empirically confirmed.~~ ✅ FIXED.** Discovery D1 in `plan-v1.md` confirms this is the large variant of RM_ALLOC with the same struct layout as the small variant. The `lookup/ioctl_table.json` entry for `0xC030462B` now has `\"confidence\": \"high\"`, bringing the static table into line with the empirical evidence from the replay pipeline.

4. **`0xC00C46D1` has a multi-sentence description that makes the table cell in the generated report very wide.** `_md_escape` doesn't truncate it. The Markdown table cell renders as a wall of text. A short `description` and a separate `notes` field (suppressed in table cells) would be more maintainable.

---

## Phase 3 — Cross-Cutting Issues

### 1. Inconsistencies Between Files

- **Exit semantics vs. log semantics in replay and validation**  
  - `replay/replay.c` now distinguishes `failed` vs `skipped` ioctls and exits non-zero only on true failures, while `validation/run_validation.sh` still treats *any* non-zero replay exit as a warning but does not propagate it to its own exit code. The log and exit codes are therefore slightly out of sync: an engineer looking only at `run_validation.sh`'s exit status could miss a non-zero `failed` count in replay; an engineer looking only at replay's summary might assume CI caught it.

- **Relative vs. absolute paths in Phase 3 tooling**  
  - `replay.c` now writes `replay.ready` using an absolute path derived from `capture_path`, but `default_offsets` for `handle_offsets.json` still uses a relative path (`capture_dir/../intercept/...`). `run_validation.sh` assumes both artifacts live at `$ROOT_DIR`, and that is currently true only when the script is run from the project root. Mixing absolute and relative paths across tools is a source of subtle misconfiguration bugs.

- **Schema vs. implementation confidence**  
  - `lookup/ioctl_table.json` is the single source of truth for ioctl names & confidence, but actual confidence in some entries (e.g. `0xC030462B`) was implicitly higher based on Phase 2/3 replay results. Until we updated the table, `annotate_static.py` and `generate_report.py` were telling a more conservative story than the replay tooling. This divergence indicates that schema updates are not yet a first-class part of the replay workflow.

### 2. Missing Pieces the Architecture Implies

- **No end-to-end driver replay for steps beyond `cuInit`**  
  - The plan and directory structure strongly suggest that `cu_device_get`, `cu_ctx_create`, and `cu_ctx_destroy` will eventually get full Phase 1–4 treatment, but only `cu_init` has a completed replay + validation loop. The current `validation/run_validation.sh` is hard-coded to `cu_init.jsonl`. Extending this to a generalized `run_validation.sh <step>` script (or Make target) is the obvious missing piece.

- **No linkage between strace and LD_PRELOAD pipelines**  
  - The strace-based pipeline (parsed/annotated/schema/report) and the LD_PRELOAD-based pipeline (sniffed/handle_offsets/replay/validation) operate on logically the same events but in completely separate representations. There is no tool to join on `seq` or `req` to answer questions like “for this annotated `NV_ESC_RM_ALLOC` in `CUDA_IOCTL_MAP.md`, what were the raw `before/after` buffers?” The architecture implies such a join is possible, but it does not exist yet.

- **No story for versioning and regeneration of derived artifacts**  
  - Baselines, `schema/master_mapping.json`, `CUDA_IOCTL_MAP.md`, `sniffed/*.jsonl`, and `intercept/handle_offsets.json` are all checked into the repo. There is no `Makefile` or script that states the dependency graph between them. When a low-level tool changes (e.g. `find_handle_offsets.py`, `parse_trace.py`), there is no single command that regenerates all affected artifacts, and nothing in the repo indicates which files are now stale.

### 3. Most Likely Places for Bugs or Failures

- **Replay handle and fd patching logic**  
  - `replay.c` is the highest-risk component: it manually parses JSON, decodes hex buffers, applies handle and fd remapping by offset, and issues live ioctls. Bugs here either silently corrupt the driver state or cause sporadic `EINVAL`/`EPERM` failures. Specifically, the interaction between `handle_offsets.json` (derived heuristically from Phase 2) and the patching loops in replay is fragile: an off-by-4 in offsets, or an undetected new handle field in a future driver version, will cause replay to diverge without any structural change in the capture format.

- **Ad-hoc JSON parsing in C**  
  - Both `replay.c` and `load_schemas` in the same file perform brittle substring-based parsing across large JSON/JSONL blobs. Any future change to the capture format (additional fields, different ordering, nested structures) risks silently breaking the parser. Unlike the Python pipeline, which uses `json.loads`, the C side has no schema validation and no unit tests.

- **Heuristic filtering in `compare_snapshots.py`**  
  - The normalisation rules are tuned to one machine: which fields are stripped as volatile, which are left, and what constitutes a structural diff. A small change in `nvidia-smi -q` output format, a new line in `/proc/driver/nvidia/params`, or a future metric field that looks “structural” but is actually noisy could turn previously passing comparisons into spurious FAILs, or worse, hide real differences.

### 4. Onboarding Warnings for a New Engineer

- **Be explicit about which pipeline you are touching.**  
  Changing the strace pipeline (e.g. `parse_trace.py`, `annotate_static.py`, `build_schema.py`) does *not* affect replay, and vice versa. When debugging a behavioural change, always confirm which half of the system you’re modifying and which artifacts you expect to change (`annotated/` vs `sniffed/` vs `schema/` vs `validation/`).

- **Do not trust paths or environment defaults; run from the repo root.**  
  Many scripts assume they are run from `cuda-ioctl-map/` and that `NV_SNIFF_LOG` is set by wrapper scripts. Running binaries or tools directly from subdirectories without those assumptions will usually “work” but produce empty or stale artifacts. Until paths are systematically made absolute and pre-flight checks are added, stick to the documented entrypoints (`collect.sh`, `collect_two_runs.sh`, `check_reproducibility.py`, `run_validation.sh`) and run them from the project root.

- **Treat `lookup/ioctl_table.json` as code, not just data.**  
  The correctness of annotations, confidence levels, and even some report logic depends on this table. When you learn something new from replay (e.g. that a code is actually high confidence), update the table immediately and rerun `annotate_static.py` + `build_schema.py` + `generate_report.py`. Leaving the table stale is the easiest way to drift into misleading documentation.

- **Expect driver and CUDA version churn to break assumptions.**  
  The plan and the current code are tuned to a specific NVIDIA driver and CUDA 12.5/12.6 environment. New driver drops can change ioctl codes, struct layouts, or even `nvidia-smi` output. When upgrading, rerun `check_reproducibility.py` first, then regenerate captures and schemas before trusting any old baselines or reports.

