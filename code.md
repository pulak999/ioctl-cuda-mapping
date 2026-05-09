# Code Review — commit f0cb9cc3a475e65fd807aee56439bff7d29d5ba5 (2026-05-09)

## Phase 1: Repo Overview

**Purpose:** Capture NVIDIA CUDA driver ioctls as JSONL, infer handle byte offsets from paired runs, replay ioctls without libcuda to validate the protocol.

**Layout (relevant to plan-v1):**

- `cuda-ioctl-map/` — primary pipeline: `run.sh`, `intercept/nv_sniff.c`, `replay/replay.py`, `tools/find_handle_offsets.py`, `programs/`, `sniffed/`.
- `README.md` — user-facing quick start.
- `roadmap.md`, `plan-v1.md` — design and implementation plan.
- `cuda_ioctl_sniffer/`, `open-gpu-kernel-modules/` — submodules / reference, not part of the optimizer harness.

**Entry points:** `bash cuda-ioctl-map/run.sh …`; `python3 cuda-ioctl-map/replay/replay.py …`; `python3 cuda-ioctl-map/tools/find_handle_offsets.py …`.

**Data flow:** CUDA binary → LD_PRELOAD sniffer → JSONL → (optional inference) → `handle_offsets.json` → replay patches → kernel.

**Patterns:** Linear shell-orchestrated pipeline; Python replay is imperative event loop over JSONL.

## Phase 2: Drill-Down (optimizer-relevant)

| File | Role | Notes for harness |
|------|------|-------------------|
| `cuda-ioctl-map/run.sh` | compile/capture/replay | `-c` capture-only; capture always writes `sniffed/<NAME>.jsonl` (same path overwrites). Harness must copy traces between captures. |
| `intercept/nv_sniff.c` | record open/ioctl | `/dev/nvidia*` only; max 4096-byte snapshot. |
| `replay/replay.py` | replay + patch | `replay()` returns `failed` count only; exit 0 if `failed==0` even when `skipped>0`. Evaluator must parse `DONE` line. |
| `replay/handle_map.py` | schemas + maps | `load_schemas(path)`; 4-byte LE handles. |
| `tools/find_handle_offsets.py` | diff two JSONL → offsets | Writes merged JSON; pairs by position; nvidiactl-focused. |

## Phase 3: Cross-Cutting

- **Capture success vs trace quality:** `run.sh` ignores program exit code during capture; empty or stale JSONL must be validated by size/event count.
- **Skip vs fail:** replay exit code does not encode skips; metrics must treat extra skips as regression vs baseline.
- **No CI:** `.github/workflows` absent in this repo; local tests only unless added later.

---

## Plan cross-reference (`plan-v1.md`)

| Plan item | Repo state | Notes |
|-----------|--------------|-------|
| `optimizer/harness.yaml` | Not present → implement | |
| `evaluate.py`, `metrics.py`, `gepa_runner.py` | Not present → implement | |
| Wrap `run.sh`, `find_handle_offsets.py`, `replay.py` | Exists | Subprocess from `cuda-ioctl-map/` cwd |
| Candidate offsets under `optimizer/runs/` | Not present → implement | Do not overwrite `intercept/handle_offsets.json` |
| GEPA `optimize_anything` | External `gepa` package | `optimizer/requirements.txt`; runner fails fast with install hint |
| Branch `coding-agent-dev` | Not in remote list | Create at implementation time |

**Conflicts / preconditions:** Live NVIDIA + privileged replay required for full evaluator; unit tests cover metrics parsing only.
