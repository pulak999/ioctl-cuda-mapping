# TODO — ioctl-cuda-mapping

## Done (plan-v1 baseline)

- [x] Optimizer harness: `cuda-ioctl-map/optimizer/harness.yaml`
- [x] Metrics: `optimizer/metrics.py` (replay summary parse, offset diff)
- [x] Evaluator CLI: `optimizer/evaluate.py`
- [x] GEPA runner stub: `optimizer/gepa_runner.py` + `requirements.txt`
- [x] Optimizer README + `runs/` gitignore
- [x] Unit tests for metrics parsing (`optimizer/tests/test_metrics.py`)

## Next (roadmap / follow-up)

### plan-v2 ([plan-v2.md](plan-v2.md)) — split: repo vs operator

- [x] **In repo:** `optimizer/scripts/smoke_plan_v2.sh` (Phase 0, 4, optional 2–3);
      `SKIP_LIVE=1` CI-friendly path; [VALIDATION.md](VALIDATION.md) plan-v2 stub
      + Phase 0 log.
- [x] **Phase 4 (live evaluate)** on dev clone: full `smoke_plan_v2.sh` without
      `SKIP_LIVE` — PASS; see [VALIDATION.md](VALIDATION.md) “Phase 4 (live
      evaluate)”.
- [ ] **On your GPU host (or here when you want full plan-v2):** Phases 1–2
      (scratch clone + vLLM), Phase 3 (**local** `VLLM_API_BASE` +
      `GEPA_REFLECTION_MODEL` — strict plan-v2 milestone), optional Phase 4 repeat
      in throwaway clone, Phase 5 row with vLLM version + reflection yes/no,
      Phase 6 (remove scratch clone).
- [x] **Phase 3 (Gemini path) — smoke attempt (2026-05-09):** documented in
      [VALIDATION.md](VALIDATION.md); reflection blocked by Gemini **429**
      (quota), not auth wiring.
- [ ] Phase 1 roadmap: generic sniffer device globs + extended JSONL fields
- [ ] Phase 2: `infer/classify.py` + emitted `spec.json` vs handwritten offsets
- [ ] Wire GEPA to richer candidate space (thresholds) once inference is configurable
- [x] CI workflow (optional): `.github/workflows/optimizer-plan-v2-phase0.yml` — `SKIP_LIVE=1` smoke (unittest + dry-run) on Ubuntu

## Branch

- Implement and iterate on `coding-agent-dev` when doing multi-session work;
  merge to `main` after live validation.
