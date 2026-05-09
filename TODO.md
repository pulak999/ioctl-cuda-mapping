# TODO — ioctl-cuda-mapping

## Done (plan-v1 baseline)

- [x] Optimizer harness: `cuda-ioctl-map/optimizer/harness.yaml`
- [x] Metrics: `optimizer/metrics.py` (replay summary parse, offset diff)
- [x] Evaluator CLI: `optimizer/evaluate.py`
- [x] GEPA runner stub: `optimizer/gepa_runner.py` + `requirements.txt`
- [x] Optimizer README + `runs/` gitignore
- [x] Unit tests for metrics parsing (`optimizer/tests/test_metrics.py`)

## Next (roadmap / follow-up)

- [ ] Phase 1 roadmap: generic sniffer device globs + extended JSONL fields
- [ ] Phase 2: `infer/classify.py` + emitted `spec.json` vs handwritten offsets
- [ ] Wire GEPA to richer candidate space (thresholds) once inference is configurable
- [ ] CI workflow (optional): lint + `python3 -m unittest` on optimizer tests

## Branch

- Implement and iterate on `coding-agent-dev` when doing multi-session work;
  merge to `main` after live validation.
