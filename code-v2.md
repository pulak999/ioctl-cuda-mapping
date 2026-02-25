# Code Review v2 — `ioctl-cuda-mapping / cuda-ioctl-map`

Reviewer level: L6-equivalent.  
Date: 2026-02-25.  
Scope: **Delta only** — files new or modified since the code-v1 review (commit `272d7b7`).

New files under review:
- `cuda-ioctl-map/replay/replay.py` — Python replay engine
- `cuda-ioctl-map/replay/handle_map.py` — Python handle/fd mapping module
- `cuda-ioctl-map/run.sh` — End-to-end orchestration script
- `cuda-ioctl-map/programs/cu_mem_alloc.cu` — cuMemAlloc test program
- `cuda-ioctl-map/programs/cu_module_load.cu` — cuModuleLoadData test program
- `cuda-ioctl-map/programs/cu_launch_null.cu` — cuLaunchKernel (null kernel)
- `cuda-ioctl-map/programs/cu_memcpy.cu` — cuMemcpyDtoH test program
- `cuda-ioctl-map/programs/vector_add.cu` — real compute kernel (A+B=C)
- `cuda-ioctl-map/programs/matmul.cu` — 128×128 matrix multiply (milestone)
- `README.md` — updated documentation

Previously reviewed files with no code changes: `nv_sniff.c`, `handle_map.h`, `replay.c`, `handle_offsets.json`, `find_handle_offsets.py`, `compare_snapshots.py`, all shell scripts. Not re-reviewed.

---

## Phase 1 — Repo Overview (Delta)

### 1. What Changed Since v1

The v1 codebase had:
- A complete strace-based analysis pipeline (Sub-pipeline A)
- An LD_PRELOAD interposer (`nv_sniff.c`) + C replay engine (`replay.c`)
- Validated replay for `cuInit` only (230/230 ioctls, 0 failed)

The v2 delta adds:
- **A Python replay engine** (`replay.py` + `handle_map.py`) that replaces the C replay for day-to-day use
- **Six new CUDA test programs** that climb a progressive complexity ladder from memory allocation through matrix multiplication
- **An end-to-end orchestration script** (`run.sh`) that addresses flag F7 from v1 ("no top-level orchestration script")
- **Full replay coverage** from `cuInit` through `matmul` (128×128 matrix multiply), all at 0 failed ioctls

The architecture has shifted from "C replay is the tool, Python is for analysis" to "Python replay is the primary tool, C replay is a reference implementation."

### 2. Structural Changes

| What | v1 | v2 |
|------|----|----|
| Replay engine | C only (`replay/replay.c`) | Python primary (`replay/replay.py`), C reference |
| Test programs | 4 (`cu_init` through `cu_ctx_destroy`) | 10 (through `matmul`) |
| Entry point | Manual multi-step | `run.sh` single command |
| Validated API coverage | `cuInit` | `cuInit` through `cuLaunchKernel` + `cuMemcpy` + real compute |
| Programs Makefile | Lists all 4 targets | **Still lists only 4 targets** (new programs compiled by `run.sh` directly) |

### 3. Entry Points (v2)

The primary entry point is now:
```
bash run.sh programs/<name>.cu       # compile → capture → replay
bash run.sh programs/<name>          # capture → replay (pre-compiled)
bash run.sh sniffed/<name>.jsonl     # replay only
```

The old entry points (`collect.sh`, `collect_two_runs.sh`, `run_validation.sh`, `replay/replay`) still work but are no longer the primary workflow.

### 4. Key Data Structures (v2 additions)

| Structure | Where | What it is |
|-----------|-------|-----------|
| `FdMap` | `replay/handle_map.py` | `dict[int, int]`: captured fd → live fd |
| `HandleMap` | `replay/handle_map.py` | `dict[int, int]`: captured RM handle → live RM handle |
| `ReqSchema` | `replay/handle_map.py` | Per-ioctl-code description: `input_handle_offsets[]`, `output_handle_offset`, `fd_offsets[]` |
| `EMPTY_SCHEMA` | `replay/replay.py` | Sentinel no-op schema for unknown ioctl codes |

These are Python equivalents of the C structures (`HandleMap` in `handle_map.h`, `ReqSchema`/`fd_map[]` in `replay.c`). The Python versions are more readable and have richer logging, but the underlying data model is identical.

### 5. Flags Resolved from v1

| v1 Flag | Status | Resolution |
|---------|--------|-----------|
| F7 — No top-level orchestration script | ✅ Resolved | `run.sh` provides single-command end-to-end flow |
| F4 — Compiled artifacts committed | ⚠️ Partially worse | 6 new compiled binaries now exist untracked (not committed yet, but no `.gitignore` to prevent it) |
| F2 — `handle_offsets.json` ownership | ❌ Unchanged | Still in `intercept/`, still produced by `tools/` |
| F3 — `STEP_ORDER` in `build_schema.py` aspirational | ❌ Unchanged | Still lists non-existent programs |

### 6. New Structural Flags

#### F8 — Programs Makefile is stale
The `programs/Makefile` lists only four targets: `cu_init`, `cu_device_get`, `cu_ctx_create`, `cu_ctx_destroy`. The six new programs (`cu_mem_alloc`, `cu_module_load`, `cu_launch_null`, `cu_memcpy`, `vector_add`, `matmul`) are **not** in the Makefile. They are built by `run.sh` invoking `nvcc` directly. This creates two parallel build systems: `make -C programs` builds only the old four; `run.sh` builds any `.cu` file ad hoc. Running `make -C programs all` gives no indication that six other programs exist. A new engineer running `make clean` in `programs/` would not clean the new binaries.

#### F9 — Two replay engines, no shared test
`replay.c` (C) and `replay.py` (Python) implement the same algorithm independently. There is no test or CI job that verifies they produce identical results on the same capture. If handle patching logic diverges (e.g. a bug fix applied to one but not the other), the divergence would go undetected. The C replay also has no close-event handling (it doesn't process `"type":"close"` records), while the Python replay does. This means the C replay leaks file descriptors during long captures.

#### F10 — `run.sh` compilation flags differ from Makefile
`run.sh` compiles with `$NVCC $NVCCFLAGS -o "$BINARY" "$INPUT"` where `NVCCFLAGS` defaults to `-arch=native -O0 -lcuda`. The programs Makefile uses `-arch=native -O0` without `-lcuda`. The new programs use the CUDA Driver API directly (`cuInit`, `cuMemAlloc`, etc.) and need `-lcuda` to link. The old programs (which use the same Driver API) are linked without it — they work because `nvcc` links `libcuda` implicitly on some systems. If the system `nvcc` doesn't auto-link `libcuda`, the Makefile build will fail while `run.sh` succeeds, creating confusion about which build path is canonical.

#### F11 — `replay.py` uses relative import, not package import
`replay.py` does `from handle_map import FdMap, HandleMap, ReqSchema, load_schemas` — a bare module import that only works if `replay/` is on `sys.path`. `run.sh` invokes `python3 replay/replay.py`, which adds `replay/` to `sys.path` automatically (Python adds the script's directory). But importing `replay.py` as a module from another directory (e.g. a test harness) or running `python3 -m replay.replay` from the project root would fail with `ModuleNotFoundError`. This limits reusability.

---

---

## Phase 2 — File-by-File Drill-Down

Reviewed in dependency order: handle_map.py → replay.py → run.sh → CUDA programs (ascending complexity).

---

### `replay/handle_map.py`

**Responsibility:** Encapsulate all handle/fd patching logic for the Python replay engine. Three public classes (`FdMap`, `HandleMap`, `ReqSchema`) and one loader function (`load_schemas`).

**Input:** `handle_offsets.json` (via `load_schemas`), captured hex buffers (via `patch_input`/`patch_fds`/`learn_output`).  
**Output:** Mutated `bytearray` buffers with patched handles/fds.

**Key Functions:**

| Function | Role |
|----------|------|
| `FdMap.learn_open(orig, live)` | Register captured→live fd mapping |
| `FdMap.get(orig)` | Retrieve live fd; returns -1 if unmapped |
| `FdMap.patch_fds(buf, schema)` | Replace captured fd values at `schema.fd_offsets` with live fds |
| `HandleMap.learn(captured, live)` | Register captured→live handle mapping |
| `HandleMap.learn_output(after_hex, live_buf, schema)` | Extract kernel-written output handle from both buffers and learn the mapping |
| `HandleMap.patch_input(buf, schema)` | Replace captured handles at `schema.input_handle_offsets` with live handles |
| `load_schemas(path)` | Parse `handle_offsets.json` into `dict[int, ReqSchema]` |

**Issues:**

1. **`FdMap.patch_fds` patches fd values as uint32 but fds are signed ints.**  
   Line 46: `orig_val = struct.unpack_from(_HANDLE_FMT, buf, off)[0]` unpacks as unsigned (`<I`). File descriptors are signed ints (`int` in C, typically small positive numbers). This works in practice because valid fds are positive and fit in uint32. But if the captured buffer ever contains a negative fd value (e.g. -1 as a sentinel), `orig_val` would be `0xFFFFFFFF` (4294967295 as unsigned), `self.get(4294967295)` would return -1 (not mapped), and the patch would silently skip — which is accidentally correct. The code works by coincidence, not by intent.

2. **`HandleMap.learn_output` decodes `captured_after_hex` from hex on every call.**  
   Line 122: `after_bytes = bytes.fromhex(captured_after_hex)`. For a capture with 781 ioctls, this decodes hundreds of multi-KB hex strings even when `schema.output_handle_offset is None` would have exited immediately. The early return at line 119 catches the `None` case, so the decode only happens for schemas with an output offset. This is fine — not a bug, but the parameter ordering puts the expensive decode before the cheap check only in the caller's mental model.

3. **`load_schemas` returns empty dict for missing file — silently disables all patching.**  
   Line 82-84: If `handle_offsets.json` doesn't exist, `load_schemas` returns `{}` and logs at INFO level. Downstream in `replay.py`, every ioctl gets `EMPTY_SCHEMA` (no patching). The replay would then issue ioctls with stale handles, failing almost every one. The failure mode is loud (FAIL on every ioctl), so it's detectable — but the root cause message ("not found — no handle patching") appears as an INFO log that could be missed if logging is at WARNING level. Consider logging at WARNING.

4. **`patch_input` warns on unknown handles but does not track the frequency.**  
   Line 151: Every unrecognized handle emits a WARNING. For a capture with many ioctls referencing the same unknown handle (e.g. a constant identity handle like `0x00000001`), the log is flooded with identical warnings. Consider deduplicating or counting.

5. **No `__repr__` or `__str__` on any class.**  
   `FdMap`, `HandleMap`, and `ReqSchema` have no string representation. Printing them in a debugger shows `<FdMap object at 0x...>`. `HandleMap.dump()` partially addresses this but only at INFO level. Adding `__repr__` would improve debuggability.

6. **Type annotations use `"int | None"` string syntax.**  
   Line 58: `output_handle_offset: "int | None"`. This is a forward reference string to work with Python < 3.10. Since the README requires Python 3.10+, this could use the native `int | None` syntax without the string wrapper. Not a bug, but inconsistent with the stated requirements.

---

### `replay/replay.py`

**Responsibility:** Main replay engine. Reads JSONL captures, opens device files, patches handles/fds, issues ioctls via `fcntl.ioctl`.

**Input:** JSONL capture file + optional `handle_offsets.json` path.  
**Output:** Per-ioctl status to stdout, summary line, exit code 0 (all OK) or 1 (failures).

**Key Functions:**

| Function | Role |
|----------|------|
| `load_jsonl(path)` | Read entire capture into memory as `list[dict]` |
| `do_ioctl(fd, req, buf)` | Wrap `fcntl.ioctl`, return 0 or -errno |
| `replay(capture_path, offsets_path)` | Main loop: process open/close/ioctl events |
| `main()` | Argparse + logging setup |

**Issues:**

1. **`load_jsonl` loads entire capture into memory at once.**  
   Line 34: All events are read into a list before processing begins. For the current captures (834 lines for matmul), this is trivially fine. For future large captures (e.g. a long-running CUDA application with millions of ioctls), this will OOM. The C replay processes line-by-line (`fgets` in a loop). The Python version could easily use a generator (`yield` per line) without changing the replay loop.

2. **`do_ioctl` loses errno granularity for empty buffers.**  
   Lines 55-56: When `len(buf) == 0`, the function calls `fcntl.ioctl(fd, req, 0)`. The `0` is passed as the third argument (the "arg" pointer). `fcntl.ioctl` with an integer arg does `ioctl(fd, req, 0)` — which is correct for pointer-less ioctls. But if the ioctl expected a pointer and the capture had `sz=0` due to a capture bug, the kernel would receive NULL, likely causing EFAULT. The error would appear as a generic failure. Consider logging a DEBUG note when issuing a zero-length ioctl.

3. **`replay` function is 117 lines with three event types, no decomposition.**  
   The function handles `open`, `close`, and `ioctl` events in a single long method. Each branch is clear, but the function is doing three different things. Extracting `_handle_open`, `_handle_close`, `_handle_ioctl` methods would improve testability and readability without changing behaviour.

4. **Failed open exits the process immediately.**  
   Lines 103-107: If `os.open` raises `PermissionError`, the script calls `sys.exit(1)`. This is correct for the primary use case (user forgot to check permissions) but prevents batch testing or wrapping the replay in a larger orchestration. A dedicated exception would allow callers to decide whether to abort.

5. **`close` event handling does not remove the fd from `fd_map`.**  
   Lines 113-121: When a `close` event is processed, the live fd is closed via `os.close`, but `fd_map._map` still contains the mapping `orig_fd → live_fd`. If a later `open` reuses the same original fd number (common in long-lived processes), `fd_map.learn_open` will overwrite correctly. But between the close and the re-open, any stale ioctl on that fd would use the (now-closed) live fd and get EBADF. This is unlikely in practice (events are processed in seq order), but it's a latent ordering bug.

6. **No `close` event logging.**  
   Close events are handled silently — no print, no log. For debugging replay failures, knowing which fds were closed and when is valuable. Even a `log.debug("[close] fd %d", orig_fd)` would help.

7. **`offsets` default path derivation is fragile.**  
   Lines 205-206: `(capture.parent / ".." / "intercept" / "handle_offsets.json").resolve()`. This assumes the capture file is in `sniffed/` which is a sibling of `intercept/`. If the capture is in a different directory (e.g. user provides an absolute path to a copy), the derivation silently resolves to the wrong path or a non-existent path. Same issue as v1 flag on `replay.c`'s `default_offsets`, but now in the Python version too.

8. **`req` is parsed from hex string on every ioctl event.**  
   Line 130: `int(req_str, 16)` is called for every ioctl. The capture format stores `req` as `"0xC020462A"` (string). This is O(n) overall — fine. But the C version stores req as an integer in the JSON via `json_u32hex`. If the capture format ever changes (e.g. to integer), this would need updating. Low risk.

9. **Summary includes `hm.dump()` which logs at INFO — this goes to stderr.**  
   Line 178: `hm.dump()` calls `log.info(...)`, which outputs to stderr (via logging). The preceding summary `print(...)` goes to stdout. If a user pipes stdout to a file, the handle map dump appears on the terminal while the summary goes to the file. This split output is mildly confusing.

---

### `cuda-ioctl-map/run.sh`

**Responsibility:** End-to-end orchestration: compile `.cu` → capture ioctls → replay. Addresses v1 flag F7.

**Input:** A `.cu` file, compiled binary, or `.jsonl` capture. Flags: `-v` (verbose), `-c` (capture only), `-r` (replay only).  
**Output:** Compilation artifacts, JSONL capture, replay stdout.

**Issues:**

1. **`2>/dev/null || true` on the capture line suppresses all errors.**  
   Line 97: `NV_SNIFF_LOG="$CAPTURE" LD_PRELOAD="$SNIFF_LIB" "$BINARY" 2>/dev/null || true`. The `2>/dev/null` discards all stderr, including CUDA error messages. The `|| true` swallows non-zero exit codes from the program. If the CUDA program crashes or fails (e.g. no GPU, wrong driver), the script silently proceeds to replay an incomplete or empty capture. The JSONL would have few/no ioctl events, and replay would report "0/0 succeeded" — a false success. The stderr redirect was likely added to suppress sniffer diagnostic noise, but it also suppresses the CUDA runtime's error output.

   **Recommendation:** Redirect only the sniffer's stderr (which goes to the same fd), or at minimum, check that the JSONL has a non-zero ioctl count before proceeding to replay. The ioctl-counting Python one-liner on line 99 is already there — add a guard.

2. **Inline Python for ioctl counting is a latent fragility.**  
   Lines 99-103: A Python one-liner reads the capture file to count ioctls. If the JSONL is malformed (e.g. binary crashed mid-write), `json.loads` will throw a `JSONDecodeError` on the bad line, the Python process exits non-zero, and `set -e` kills the script with an unhelpful error. Using `grep -c '"type":"ioctl"' "$CAPTURE"` (already done in the README example) would be more robust.

3. **`NVCC` default path is hardcoded to `/usr/local/cuda-12.5/bin/nvcc`.**  
   Line 20. Same issue as v1 flag on the Makefile. If the user has a different CUDA version, `NVCC` must be explicitly set. `run.sh` does use `${NVCC:-...}` so the env var override works, but the default is machine-specific.

4. **`-c` and `-r` flags are not mutually exclusive.**  
   Lines 27-28: Both `CAPTURE_ONLY` and `REPLAY_ONLY` can be set to true simultaneously via `-c -r`. The result: line 91 skips capture (`REPLAY_ONLY=true`), line 109 skips replay (`CAPTURE_ONLY=true`), and the script does nothing useful. No validation or error message.

5. **`.jsonl` input overrides `-c` flag.**  
   Line 63: If the user passes `run.sh -c sniffed/matmul.jsonl`, the `.jsonl` detection sets `REPLAY_ONLY=true`, overriding the `-c` (capture-only) flag. The script would replay instead of doing nothing. The flag parsing order and override semantics are not documented.

6. **`$VERBOSE` is unquoted in the replay command.**  
   Line 113: `python3 replay/replay.py $VERBOSE "$CAPTURE"`. When `VERBOSE` is empty, the unquoted expansion becomes nothing (correct due to word splitting). But if `VERBOSE` accidentally contained spaces (e.g. `VERBOSE="-v --debug"`), it would split into two arguments. Using `${VERBOSE:+"$VERBOSE"}` would be safer.

7. **No `set -o pipefail` equivalent for the inline Python.**  
   Lines 99-103 use command substitution `$(python3 -c "...")`. If the Python fails, `$?` captures the error and `set -e` aborts. But the `IOCTLS=` assignment itself succeeds (variable is set to empty string), and the abort happens on the *next* command. This race is academic with `set -e`, but worth noting.

8. **Sniffer build check is `[ ! -f "$SNIFF_LIB" ]` — stale `.so` is not rebuilt.**  
   Line 83: The script only builds the sniffer if the `.so` file doesn't exist. If `nv_sniff.c` has been edited since the last build, the stale `.so` is used. `make -C "$INTERCEPT"` would handle this correctly (Make checks timestamps), so the optimization of skipping `make` when the `.so` exists actually defeats Make's staleness detection.

---

### `programs/cu_mem_alloc.cu`

**Responsibility:** Test `cuMemAlloc` + `cuMemFree`. Allocates 1024 bytes, frees them, destroys context.

**Issues:**

1. **`cuMemFree` failure is not checked before proceeding.**  
   Lines 37-41: If `cuMemFree` fails, the error is printed but execution continues to `cuCtxDestroy`. This is defensively correct (destroy context to clean up), but the exit code is still 0 even though `cuMemFree` failed. For capture validation purposes this matters — a capture with a failed `cuMemFree` would have a different ioctl sequence than a clean run.

2. **1024-byte allocation is small and may not trigger UVM ioctls on all drivers.**  
   The allocation size is below the threshold where some drivers switch from RM-managed to UVM-managed memory. If the goal is to exercise the full memory path, a larger allocation (e.g. 1 MiB) would hit more code paths.

---

### `programs/cu_module_load.cu`

**Responsibility:** Test `cuModuleLoadData` with a minimal PTX kernel, then immediately unload.

**Issues:**

1. **PTX targets `sm_75` but the program is compiled with `-arch=native`.**  
   The embedded PTX string targets `sm_75` (Turing). If the GPU is older (e.g. Volta, sm_70), PTX JIT will still succeed because the target in PTX is a minimum. If the GPU is newer (e.g. Hopper, sm_90), the JIT upcompiles. This works but could mask architecture-specific ioctl differences. Consider making the PTX target match the compile target, or at least documenting the mismatch.

2. **Module load + immediate unload doesn't exercise the code path fully.**  
   `cuModuleLoadData` triggers PTX JIT compilation which is a complex ioctl sequence. But `cuModuleGetFunction` is not called here (that's in `cu_launch_null.cu`). This is by design (incremental ladder), but worth noting that the replay of `cu_module_load` validates JIT but not function resolution.

---

### `programs/cu_launch_null.cu`

**Responsibility:** Launch a no-op kernel (`ret` instruction) with 1×1 grid and 1×1 block.

**Issues:**

1. **Clean.** Error checking on every CUDA call, cleanup on failure paths, matches its stated purpose. No issues found.

---

### `programs/cu_memcpy.cu`

**Responsibility:** Allocate GPU memory, zero it with `cuMemsetD8`, launch a null kernel, copy back with `cuMemcpyDtoH`, verify all zeros.

**Issues:**

1. **Clean.** Error handling is thorough, verification logic is correct. The null kernel doesn't touch the buffer, so the verification is really testing that `cuMemsetD8` + `cuMemcpyDtoH` work — which is the point.

2. **Includes `string.h` for `memset` but `memset` is called on host-side array initialization only.**  
   The `memset(h_buf, 0xFF, sizeof(h_buf))` at line 92 fills the host buffer with non-zero to detect copy failures. Correct usage.

---

### `programs/vector_add.cu`

**Responsibility:** Real compute: A[i] + B[i] = C[i] for 64 elements, using embedded PTX.

**Issues:**

1. **PTX uses `%tid.x` directly — this was the source of the bug fixed during development.**  
   The original code used `.reg .u32 %tid` which shadowed the special register `%tid.x`. The fix renamed it to `%r0`. The current PTX (line 24: `mov.u32 %r0, %tid.x`) is correct. However, the naming `%r0` is less self-documenting than `%my_tid` or `%thread_id`. Minor style point.

2. **Hardcoded N=64 in both the host code and the PTX.**  
   Line 25: `setp.ge.u32 %p, %r0, 64` — the PTX hardcodes the bounds check to 64. If someone changes `#define N` on line 49 without updating the PTX, the kernel would read/write out of bounds. A comment tying the two together would help. Alternatively, pass N as a kernel parameter (as `matmul.cu` does).

3. **Verification tolerance is `1e-5f`.**  
   Line 135: `fabsf(h_C[i] - 3.0f) > 1e-5f`. For single-precision addition of 1.0 + 2.0, the result is exactly representable (no floating-point error). The tolerance is unnecessarily loose but harmless.

---

### `programs/matmul.cu`

**Responsibility:** 128×128 matrix multiply using embedded PTX. This is the capstone milestone — if replay works for this, the system handles JIT, launches, memory allocation, HtoD, DtoH, and real compute.

**Issues:**

1. **PTX uses `fma.rn.f32` for accumulation — numerically correct for this test case.**  
   All-ones matrix × all-ones matrix = N for every element. The FMA accumulation is exact for small integer sums (128 × 1.0 × 1.0 = 128.0, which is exactly representable in float32). The `0.5f` tolerance on line 190 is appropriate for this.

2. **Grid launch is N×N blocks of 1 thread each = 16384 blocks.**  
   `cuLaunchKernel(fn, N, N, 1, 1, 1, 1, ...)` — each block has one thread, and there are 128×128 = 16384 blocks. This is an unusual launch configuration (normally you'd use larger blocks for occupancy). For tracing purposes it's fine — the ioctl sequence is the same regardless of grid geometry. But it generates a capture that's specific to this odd geometry.

3. **Memory leak on failure paths.**  
   Lines 151-155: If `cuMemAlloc(&d_B, sz)` fails, `d_A` is not freed before returning. Similarly, if `cuMemAlloc(&d_C, sz)` fails, both `d_A` and `d_B` are leaked. The host `malloc`'s (`h_A`, `h_B`, `h_C`) are also not freed on early returns (lines 98 onward). For a test program that exits immediately on failure this is irrelevant, but it's worth noting for completeness.

4. **`return 1` on cuMemAlloc failure doesn't destroy the context.**  
   Lines 151-155: Three separate `cuMemAlloc` calls each return 1 on failure without calling `cuCtxDestroy`. The GPU context leaks. Again, the process is about to exit so the driver cleans up, but it means the captured ioctl trace for a failure case would not include the cleanup sequence.

5. **PTX targets `sm_75` but host compiles `-arch=native`.**  
   Same mismatch noted in `cu_module_load.cu`. The PTX `sm_75` target means the JIT will generate code for `sm_75` regardless of the actual GPU, then the driver may need to re-JIT for the real architecture. This is actually *desirable* for tracing (it exercises the JIT path), but the implicit dependence on the JIT path being successful is undocumented.

---

### `README.md`

**Responsibility:** Updated to reflect the v2 state: new programs, `run.sh`, Python replay, explanation of replay mechanism.

**Issues:**

1. **Ioctl counts in the test program table may become stale.**  
   Lines 155-165: The table lists specific ioctl counts (e.g. "781" for matmul). These counts are driver-version-specific. A driver update could change the counts. The table should either be auto-generated or include a caveat.

2. **"Prerequisites" lists Python 3.10+ but the code uses string-quoted type unions.**  
   Line 150: "Python 3.10+" — but `handle_map.py` uses `"int | None"` (string form) which is a Python 3.9 pattern. The actual minimum requirement is 3.10 for `dict[int, int]` without `from __future__ import annotations`. This is correct but the defense-in-depth string quoting is confusing.

3. **No mention of the C replay's limitations relative to the Python replay.**  
   The "Use the C replay instead" section at the bottom doesn't note that the C replay doesn't handle `close` events, uses a hand-rolled JSON parser, or has any of the limitations documented in code-v1. A user switching to the C replay might be surprised by different behaviour.

---

---

## Phase 3 — Cross-Cutting Issues

### 1. Inconsistencies Between Files

- **Two replay engines with divergent feature sets.**  
  `replay.c` handles `open` + `ioctl` events. `replay.py` handles `open` + `close` + `ioctl` events. The C version writes a `replay.ready` sentinel file; the Python version does not. The C version has a hard-coded `LINE_BUF_SZ` of 128 KiB; the Python version reads lines of unlimited length. The C version uses `strtol`-based JSON parsing; the Python version uses `json.loads`. Both claim to be replay tools, but they are not substitutable — `run_validation.sh` uses the C replay, while `run.sh` uses the Python replay. There is no documentation of which to use when, or which is considered authoritative.

- **Makefile vs. `run.sh` compilation flags.**  
  `programs/Makefile`: `NVCCFLAGS = -arch=native -O0` (no `-lcuda`).  
  `run.sh`: `NVCCFLAGS = -arch=native -O0 -lcuda`.  
  The six new programs require `-lcuda` because they use the Driver API (`cuInit`, etc.). The four old programs also use the Driver API but happen to link successfully without explicit `-lcuda` on this particular system. This is a ticking time bomb — move to a different machine or CUDA version and the Makefile builds will fail.

- **Handle offset schema is not versioned or validated.**  
  `handle_offsets.json` is consumed by both `replay.c` and `replay.py`. The Python version validates field names (`handle_offsets`, `output_handle_offset`, `fd_offsets`) in `ReqSchema.from_dict`. The C version parses by substring search and doesn't validate. If a new field is added to the JSON (e.g. `"output_fd_offset"`), the C replay silently ignores it while the Python replay would need an update to `from_dict`. There is no schema version number to detect this.

### 2. Missing Pieces

- **No automated test for replay correctness.**  
  The test ladder (cu_init through matmul) is validated manually by running `run.sh` and checking the "0 failed" output. There is no script that runs all 10 programs end-to-end and reports PASS/FAIL. A `test_all.sh` that iterates through the programs directory and checks exit codes would close this gap.

- **No `.gitignore` for compiled artifacts.**  
  Six compiled binaries, `__pycache__/`, `libnv_sniff.so`, `replay/replay` (C binary), and `replay.ready` sentinel are all in the working tree. None are in a `.gitignore`. The next `git add .` will commit all of them.

- **`run_validation.sh` still only works with the C replay.**  
  Lines 30-31 of `run_validation.sh`: `"$ROOT_DIR/replay/replay" "$CAPTURE" "$OFFSETS" &`. This invokes the C replay binary. The Python replay (`replay.py`) is not an option for validation because it doesn't write `replay.ready`. This means the validation pipeline — the only automated correctness check — is locked to the C replay, while the primary workflow uses the Python replay.

- **No JSONL capture format documentation.**  
  The JSONL format is the central data artifact shared between capture and replay. Its schema is implicitly defined by what `nv_sniff.c` emits and what `replay.py`/`replay.c` parse. There is no explicit schema document. Adding a `close` event type (which `replay.py` handles) extended the schema without updating any documentation. Future extensions (e.g. `mmap` events, metadata records) have no defined process.

### 3. Single Most Likely Place for a Bug or Failure

**`handle_offsets.json` — incomplete coverage for new ioctl codes.**

The current `handle_offsets.json` was generated from `cu_init` captures only (diffing `cu_init_a.jsonl` vs `cu_init_b.jsonl`). It contains 9 entries. The new programs (matmul, vector_add, etc.) issue additional ioctl codes not present in `cu_init` — for example, those related to PTX JIT, kernel launch, and memory copy. These new codes may contain handle fields that are not in the schema.

The reason replay currently succeeds (0 failed) is likely one or more of:
- The new ioctls don't carry handles (some are stateless)
- The handle values happen to be at offsets already covered by existing schemas
- The handles are identity-mapped (same value in capture and replay) because the allocation sequence is deterministic *enough* on this machine

**If any of these assumptions break** (e.g. different driver version, different GPU, machine under load causing non-deterministic allocation), replay will fail with `EINVAL` on ioctls where un-patched stale handles are sent to the kernel. The failure mode is silent corruption or mysterious errors deep in the replay, not a clear "missing schema entry" message.

**Recommendation:** Re-run `find_handle_offsets.py` using captures from `matmul` (not just `cu_init`) to discover handle fields in the new ioctl codes:
```bash
NV_SNIFF_LOG=sniffed/matmul_a.jsonl LD_PRELOAD=./intercept/libnv_sniff.so ./programs/matmul
NV_SNIFF_LOG=sniffed/matmul_b.jsonl LD_PRELOAD=./intercept/libnv_sniff.so ./programs/matmul
python3 tools/find_handle_offsets.py sniffed/matmul_a.jsonl sniffed/matmul_b.jsonl intercept/handle_offsets.json
```

### 4. Warnings for a New Engineer

1. **`run.sh` is the happy path — but it hides errors.** The `2>/dev/null || true` on the capture line means your CUDA program can crash and the script will cheerfully proceed to replay an empty/partial capture. Always check the capture line count before trusting a "0 failed" replay result.

2. **The Python replay is the tool you should use day-to-day.** The C replay exists as a reference implementation and is used by the validation script, but it has a weaker JSON parser, no `close` handling, and no plans to be maintained as the primary tool. If you need to fix a replay bug, fix it in Python first.

3. **Handle offsets are heuristic and machine-specific.** `handle_offsets.json` was derived from diffing two runs on one machine with one driver version. It is correct *for this configuration*. Changing the GPU, driver version, or even CUDA version may introduce new ioctl codes or change struct layouts. When moving to a new environment, regenerate `handle_offsets.json` first.

4. **PTX targets are hardcoded to `sm_75`.** All embedded PTX strings target Turing architecture. The host binaries compile with `-arch=native`. If you run on a Pascal (sm_60) or older GPU, PTX JIT will fail because the PTX target is newer than the device. Update the `.target` line in the PTX strings to match your GPU.

5. **The programs Makefile only builds 4 of 10 programs.** `make -C programs` gives you `cu_init` through `cu_ctx_destroy`. The other 6 programs are only built via `run.sh`. Don't assume `make all` in `programs/` gives you a complete build.

6. **Replay does not verify compute correctness — only ioctl success.** A replay that reports "781/781 succeeded, 0 failed" means all ioctls returned 0 (no kernel error). It does **not** mean the GPU computed the right answer. The replay issues the same ioctl bytes; the GPU does whatever those bytes tell it to do. But there is no readback verification during replay (no host-side check of C[i][j]==128). Replay proves protocol correctness, not compute correctness.

---

*End of code-v2 review.*
