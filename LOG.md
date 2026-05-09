## [2026-05-09] plan-v2 Phase 4 live smoke (dev clone)

### What ran

- Full `./optimizer/scripts/smoke_plan_v2.sh` from `cuda-ioctl-map/` (no
  `SKIP_LIVE`): unittest, dry-run, live `evaluate.py` on `harness.yaml` and
  `harness.smoke2.yaml`. Phases 2–3 skipped (no `VLLM_API_BASE`).

### Files changed

| File | What changed |
|------|-------------|
| [VALIDATION.md](VALIDATION.md) | Appended plan-v2 Phase 4 evidence (SHA, host, pass). |
| [TODO.md](TODO.md) | Mark Phase 4 live run done; clarify remaining operator steps. |

---

## [2026-05-09] plan-v2 implement-from-plan (smoke script + docs)

### What shipped

- `cuda-ioctl-map/optimizer/scripts/smoke_plan_v2.sh` — Phase 0 with
  `SKIP_LIVE=1`; optional Phase 4 live evaluate; optional Phase 2–3 when
  `VLLM_API_BASE` + `GEPA_REFLECTION_MODEL` are set.
- Doc updates: `plan-v2.md` (automation table), `VALIDATION.md` (plan-v2 stub +
  script PASS), `code.md` (plan-v2 cross-ref), `ARCH.md`, `CLAUDE.md`,
  `TODO.md`, `optimizer/README.md`.

### Local verify

- `SKIP_LIVE=1 ./optimizer/scripts/smoke_plan_v2.sh` from `cuda-ioctl-map/`:
  PASS.

---

## [2026-05-09] plan-v1 validation (live + GEPA partial)

### What ran

- Unit tests under `cuda-ioctl-map/optimizer/tests/`: PASS.
- Live evaluator on `programs/cu_init.cu` and `programs/cu_mem_alloc.cu`: PASS
  (see [VALIDATION.md](VALIDATION.md)).
- GEPA `gepa_runner.py` with `uv`-managed `optimizer/.venv`: evaluator scored
  seed harness; reflection blocked without `OPENAI_API_KEY` (litellm); see
  VALIDATION.md.

### Files changed (this follow-up)

| File | What changed |
|------|--------------|
| `VALIDATION.md` | New: commands and pass/fail notes. |
| `cuda-ioctl-map/optimizer/harness.smoke2.yaml` | New: smoke-2 harness for `cu_mem_alloc.cu`. |
| `cuda-ioctl-map/optimizer/requirements.txt` | Add `litellm`; pin `gepa` floor. |
| `cuda-ioctl-map/optimizer/.gitignore` | Ignore `optimizer/.venv/`. |
| `cuda-ioctl-map/optimizer/README.md` | uv venv install, GEPA env notes, link to VALIDATION.md. |

---

## [2026-05-09] GEPA Optimizer Harness (plan-v1)

### Features Implemented

- Live evaluator orchestrating double capture, `find_handle_offsets.py`, baseline vs candidate replay, JSON metrics, and skip-regression checks versus baseline replay.
- `metrics.py` for parsing `DONE` replay summaries and diffing `handle_offsets.json` entries.
- Optional `gepa_runner.py` calling `optimize_anything` over harness YAML text.
- JSON harness for dry-run without PyYAML; YAML harness for normal use.
- Unit tests for metrics parsing and scoring gates.

### Files Changed

| File | What changed |
|------|--------------|
| `cuda-ioctl-map/optimizer/metrics.py` | New: replay summary parse, offset diff, ASI builder, score gate. |
| `cuda-ioctl-map/optimizer/evaluate.py` | New: harness load, pipeline, CLI. |
| `cuda-ioctl-map/optimizer/gepa_runner.py` | New: GEPA driver with temp harness file per candidate. |
| `cuda-ioctl-map/optimizer/harness.yaml` | New: default single-program harness. |
| `cuda-ioctl-map/optimizer/harness.min.json` | New: JSON harness for dry-run / no PyYAML. |
| `cuda-ioctl-map/optimizer/requirements.txt` | New: `gepa`, `PyYAML`. |
| `cuda-ioctl-map/optimizer/README.md` | New: usage and prerequisites. |
| `cuda-ioctl-map/optimizer/runs/.gitignore` | New: ignore generated run artifacts. |
| `cuda-ioctl-map/optimizer/tests/test_metrics.py` | New: unittest suite. |
| `code.md` | New: review + plan cross-reference. |
| `CLAUDE.md` | New: agent-oriented project notes. |
| `ARCH.md` | New: architecture overview including optimizer layer. |
| `TODO.md` | New: done items and follow-ups. |

### Functions Written

| Function | File | Description |
|----------|------|-------------|
| `parse_replay_summary` | `optimizer/metrics.py` | Extract ok/total/failed/skipped from replay stdout. |
| `compare_handle_offsets` | `optimizer/metrics.py` | Compare baseline vs candidate handle offset lists per ioctl. |
| `score_gate` | `optimizer/metrics.py` | Enforce zero failures and skip regression bound. |
| `evaluate_harness` | `optimizer/evaluate.py` | Run full per-program evaluation pipeline. |
| `load_harness_file` | `optimizer/evaluate.py` | Load harness YAML/JSON from disk. |

### Data Structures Created

| Name | File | Description |
|------|------|-------------|
| `ReplaySummary` | `optimizer/metrics.py` | Dataclass for parsed `DONE` line fields. |

### Notes

- `gepa` and `PyYAML` are not verified installed in this environment; install via `optimizer/requirements.txt` before running `gepa_runner.py` or YAML harness loads.
- Full live evaluation requires NVIDIA CUDA capture and privileged replay; use `--dry-run` for smoke checks without hardware.
- Dynamic import of `metrics.py` registers a unique `sys.modules` name so `@dataclass` works under `importlib`.
