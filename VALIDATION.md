# plan-v1 validation log

Results from running the [plan-v1.md](plan-v1.md) validation items on this
machine (2026-05-09 session, continued).

## Unit tests (metrics)

```bash
cd cuda-ioctl-map
python3 -m unittest discover -s optimizer/tests -p 'test_*.py' -v
```

**Result:** PASS (6 tests).

## Live smoke 1 — `programs/cu_init.cu`

```bash
cd cuda-ioctl-map
python3 optimizer/evaluate.py --harness optimizer/harness.yaml
```

**Result:** PASS — `ok: true`, baseline and candidate replay
`230/230 succeeded, 0 failed, 0 skipped`, aggregate score ~0.78 (offset list
diff vs handwritten baseline is expected and non-blocking when replay passes).

## Live smoke 2 — `programs/cu_mem_alloc.cu`

```bash
cd cuda-ioctl-map
python3 optimizer/evaluate.py --harness optimizer/harness.smoke2.yaml
```

**Result:** PASS — `ok: true`, `781/781 succeeded, 0 failed, 0 skipped` for
both baseline and candidate replays.

Harness file: [cuda-ioctl-map/optimizer/harness.smoke2.yaml](cuda-ioctl-map/optimizer/harness.smoke2.yaml).

## Regression guard (built into evaluator)

The evaluator always replays the same trace with **checked-in**
`intercept/handle_offsets.json` and with **candidate**
`optimizer/runs/.../handle_offsets.json`, and enforces zero ioctl failures plus
no skip regression (`max_skip_regression`). **Covered** by the two live runs
above.

## GEPA smoke — `gepa_runner.py`

1. **Environment:** `uv venv optimizer/.venv` and
   `uv pip install -p optimizer/.venv -r optimizer/requirements.txt` plus
   `litellm` (now listed in `optimizer/requirements.txt`).

2. **Command:**

   ```bash
   cd cuda-ioctl-map
   optimizer/.venv/bin/python optimizer/gepa_runner.py \
     --seed optimizer/harness.yaml --max-metric-calls 2
   ```

3. **Result:** **Partial.** Iteration 0 ran the evaluator and reported valset
   score `0.777…` (matches live harness). Iteration 1 **reflection** failed with
   `litellm.AuthenticationError` (no `OPENAI_API_KEY` in this environment).
   Process exited **0** and printed `best_candidate` equal to the seed YAML.

**To complete full GEPA smoke:** set `OPENAI_API_KEY` (or other LiteLLM-backed
credentials for the default model GEPA uses) and re-run with a slightly larger
`--max-metric-calls` budget so a mutation step can succeed.
