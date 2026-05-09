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
