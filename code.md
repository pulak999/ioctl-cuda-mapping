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

---

## Plan cross-reference (`plan-v2.md`) — operational E2E

| plan-v2 phase | In repo / automated | Operator-only |
|----------------|---------------------|---------------|
| 0 | `optimizer/scripts/smoke_plan_v2.sh` with `SKIP_LIVE=1` | — |
| 1 | — | Fresh clone under `$HOME/ioctl-agent-scratch`, `uv venv` |
| 2 | Script curls `/v1/models` when `VLLM_API_BASE` set | Start vLLM, pick GPU |
| 3 | Script runs `gepa_runner` when `GEPA_REFLECTION_MODEL` + `VLLM_API_BASE` set | Model id from server |
| 4 | Script runs `evaluate.py` unless `SKIP_LIVE=1` | GPU + `/dev/nvidia*` access |
| 5 | Append [VALIDATION.md](VALIDATION.md) | Host notes, vLLM version |
| 6 | — | `rm -rf` scratch clone |

**Note:** Phases 2–3 require a running OpenAI-compatible server (e.g. vLLM on a
Titan); the repo cannot start that server for you from CI without GPU runners.

---

## Code Review — commit ecfc683271c7c8e13b5ee55777cb9cdfa9cf6ab2 (2026-05-09)

### Phase 1: Repo Overview

**Purpose:** Same as prior review — CUDA ioctl JSONL capture, handle-offset inference, replay without libcuda; **plus** an optimizer layer (`evaluate.py`, `metrics.py`, `gepa_runner.py`) that scores harness YAML by running the real pipeline.

**Layout:** `cuda-ioctl-map/` owns `run.sh`, `intercept/`, `replay/`, `tools/`, `programs/`, `sniffed/`, `optimizer/` (harness YAML, evaluator, GEPA driver, tests, `scripts/smoke_plan_v2.sh`). Repo root holds plans (`plan-v1.md`, `plan-v2.md`), `AGENT_SERVER_SETUP.md`, `VALIDATION.md`, `CLAUDE.md`, `ARCH.md`, `TODO.md`.

**Entry points:** `bash run.sh …`; `python3 replay/replay.py …`; `python3 optimizer/evaluate.py`; optional `optimizer/gepa_runner.py`; automation `optimizer/scripts/smoke_plan_v2.sh` (must `cd cuda-ioctl-map` or rely on script self-`cd`).

**Data flow:** Harness lists `.cu` programs → evaluator orchestrates capture/pair/infer/replay → JSON metrics; GEPA treats harness YAML as text and calls evaluator as metric.

**Patterns:** Subprocess-heavy evaluator; GEPA loads `evaluate.py` via `importlib` to avoid circular imports.

**Structural note:** `.github/workflows` is **absent** — no CI in-tree; validation is local + `VALIDATION.md`.

### Phase 2: File-by-File (plan-v2–relevant)

| File | Responsibility | Inputs / outputs | Risks / notes |
|------|----------------|------------------|---------------|
| `optimizer/scripts/smoke_plan_v2.sh` | Single entry for plan-v2 Phase 0, 4, optional 2–3 | `OPT_PY` / `OPT_VENV_PY`, `SKIP_LIVE`, `VLLM_API_BASE`, `GEPA_*` | Phase 4 uses `OPT_PY` (default `python3`), not necessarily `.venv` — matches plan manual which uses `.venv` for evaluate; operators should set `OPT_PY=optimizer/.venv/bin/python` if system Python lacks deps. |
| `optimizer/evaluate.py` | Full harness evaluation + `--dry-run` | harness path → JSON stdout | Long timeouts; needs CUDA + device access for live path. |
| `optimizer/gepa_runner.py` | `optimize_anything` over harness text | `--reflection-model`, `--api-base`, `--api-key` set `OPENAI_API_*` | If `--reflection-model` omitted, default GEPA model may hit `AuthenticationError` without cloud keys (documented in VALIDATION). |
| `optimizer/metrics.py` | Parse replay `DONE`, diff offsets | strings / dicts | Unit-tested. |
| `optimizer/tests/test_*.py` | Metrics gates | — | Fast; no GPU. |

**Name vs behavior:** `smoke_plan_v2.sh` is accurate; `curl` uses `$BASE/models` while plan text says `/v1/models` — with `VLLM_API_BASE=http://127.0.0.1:8000/v1`, `$BASE/models` resolves to `/v1/models` (correct).

### Phase 3: Cross-Cutting

1. **Python split:** Smoke script uses `python3` for unittest/evaluate but `optimizer/.venv/bin/python` for GEPA when `.venv` exists — consistent with plan Phase 3 vs Phase 0; **inconsistent** if someone creates `.venv` only for GEPA deps but `evaluate.py` needs the same venv on a minimal host — document `OPT_PY` (already in script comments / plan table).
2. **Missing CI:** No automated regression on PRs; `SKIP_LIVE=1` is the only portable gate.
3. **Likeliest failure:** Live replay permissions or nvcc path; second: LiteLLM + local OpenAI base + exact model id string.
4. **New engineer warning:** Run everything from `cuda-ioctl-map/`; replay failures do not always non-zero exit — always parse metrics JSON and `DONE` line semantics per `metrics.py`.

**Terminal summary (critical):**

- No GitHub Actions in this repo; rely on local `SKIP_LIVE=1` smoke.
- `smoke_plan_v2.sh` implements plan-v2 Phases 0, 4, and optional 2–3; Phases 1–2, 5–6 remain operator procedures.
- Set `OPT_PY` to the optimizer venv interpreter on hosts where system `python3` lacks PyYAML / deps for `evaluate.py`.
- GEPA reflection requires either cloud credentials for the default model or explicit `--reflection-model` + `--api-base` (local vLLM).
- `code.md` updated through Phase 3 for commit `ecfc683271c7c8e13b5ee55777cb9cdfa9cf6ab2`.

### Plan cross-reference (`plan-v2.md`) at this commit

| plan-v2 item | Status in repo | Notes |
|--------------|-----------------|-------|
| Automation `smoke_plan_v2.sh` | **Done** | Env: `SKIP_LIVE`, `VLLM_API_BASE`, `GEPA_REFLECTION_MODEL`, `GEPA_MAX_METRIC_CALLS`, `GEPA_API_KEY`, `OPT_VENV_PY`, `OPT_PY`. |
| Phase 0 unittest + dry-run | Script + verified PASS (agent run 2026-05-09) | `SKIP_LIVE=1`. |
| Phase 1 throwaway clone + uv venv | Operator | Not automatable in-repo. |
| Phase 2 vLLM | Operator + optional curl in script | Server not started by repo. |
| Phase 3 GEPA ≥1 reflection | Operator when env set | Script wires flags; needs live LLM. |
| Phase 4 live `evaluate.py` both harnesses | In script when not `SKIP_LIVE=1` | Needs GPU + `/dev/nvidia*`. |
| Phase 5 VALIDATION.md | Partial | plan-v2 section documents automation + Phase 0; full E2E row awaits server run. |
| Phase 6 cleanup | Operator | — |

**Conflicts / preconditions:** Plan assumes no sudo for routine steps; replay may still require group membership on `/dev/nvidia*` (not always “rootless”). Plan `GEPA_MAX_METRIC_CALLS` example uses `12`; script default `12`; README snippet showed `8` as optional — all consistent.

**Tests / CI survey:** `python3 -m unittest discover -s optimizer/tests -p 'test_*.py' -v` (6 tests). No `.github/workflows/`.
