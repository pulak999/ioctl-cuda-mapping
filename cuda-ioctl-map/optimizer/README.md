# Optimizer harness

Implements [plan-v1.md](../../plan-v1.md) Phase 2.5: a live evaluator around
`run.sh`, `tools/find_handle_offsets.py`, and `replay/replay.py`, plus an
optional GEPA loop over the harness YAML.

## Prerequisites

- Working directory: **`cuda-ioctl-map/`** (same as `run.sh`).
- CUDA (`nvcc`) and NVIDIA devices for capture.
- Replay usually needs **root** or `CAP_SYS_ADMIN` for `/dev/nvidia*`.

## Python dependencies

```bash
cd cuda-ioctl-map
python3 -m pip install -r optimizer/requirements.txt
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

```bash
cd cuda-ioctl-map
python3 optimizer/gepa_runner.py --seed optimizer/harness.yaml --max-metric-calls 15
```

This treats the entire harness YAML as the optimizable artifact. Start with a
small `--max-metric-calls` budget; each call runs the full live pipeline.

## Notes

- `run.sh` always writes `sniffed/<program_stem>.jsonl`; the evaluator copies
  each capture into the per-run directory before the next capture so pairs are
  preserved.
- Replay exit code ignores **skipped** ioctls; the evaluator parses the
  `DONE — …` line and fails if skips increase relative to baseline replay.
