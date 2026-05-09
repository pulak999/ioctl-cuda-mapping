# Optimizer harness

Implements [plan-v1.md](../../plan-v1.md) Phase 2.5: a live evaluator around
`run.sh`, `tools/find_handle_offsets.py`, and `replay/replay.py`, plus an
optional GEPA loop over the harness YAML.

## Prerequisites

- Working directory: **`cuda-ioctl-map/`** (same as `run.sh`).
- CUDA (`nvcc`) and NVIDIA devices for capture.
- Replay usually needs **root** or `CAP_SYS_ADMIN` for `/dev/nvidia*`.

## Python dependencies

With **pip** (if available on your Python):

```bash
cd cuda-ioctl-map
python3 -m pip install -r optimizer/requirements.txt
```

With **uv** (recommended when system Python has no `pip`):

```bash
cd cuda-ioctl-map
uv venv optimizer/.venv --python 3.10
uv pip install -p optimizer/.venv -r optimizer/requirements.txt
optimizer/.venv/bin/python optimizer/evaluate.py --harness optimizer/harness.min.json --dry-run
```

## Deterministic evaluator

Dry run (no GPU, validates harness file and imports):

```bash
python3 optimizer/evaluate.py --harness optimizer/harness.yaml --dry-run
```

Full live run (captures twice per program, infers offsets, replays):

```bash
sudo python3 optimizer/evaluate.py --harness optimizer/harness.yaml
```

Exit code `0` only if every program row reports `"ok": true` (zero replay
failures, no skip regression vs baseline, and inference succeeded).

Metrics JSON is printed to stdout.

Artifacts: `optimizer/runs/<run_id>/<stem>/` contains paired traces and
`handle_offsets.json` for that evaluation. The checked-in
`intercept/handle_offsets.json` is never overwritten.

## GEPA smoke

GEPA uses LiteLLM for the reflection model. Set credentials for your chosen
provider (example: `OPENAI_API_KEY` for OpenAI). Then:

```bash
cd cuda-ioctl-map
# use the same interpreter that has gepa + litellm installed, e.g.:
optimizer/.venv/bin/python optimizer/gepa_runner.py --seed optimizer/harness.yaml --max-metric-calls 15
```

This treats the entire harness YAML as the optimizable artifact. Each metric
call runs the full live evaluator (capture → infer → baseline vs candidate
replay). Without an API key, iteration 0 can still score the seed harness;
reflection may fail until keys are configured.

Recorded results for this repo: [VALIDATION.md](../../VALIDATION.md).

## Notes

- `run.sh` always writes `sniffed/<program_stem>.jsonl`; the evaluator copies
  each capture into the per-run directory before the next capture so pairs are
  preserved.
- Replay exit code ignores **skipped** ioctls; the evaluator parses the
  `DONE — …` line and fails if skips increase relative to baseline replay.
