# plan-v2 — Operationalize agent setup and end-to-end test

## Date

2026-05-09 (draft)

## Purpose

Turn the documented workflows in [AGENT_SERVER_SETUP.md](AGENT_SERVER_SETUP.md)
and the implemented harness in [plan-v1.md](plan-v1.md) into a **repeatable
procedure**: scratch clone, Python env, local Titan LLM (OpenAI-compatible),
full live `evaluate.py`, and **successful GEPA reflection** (not only
iteration 0). Record evidence in [VALIDATION.md](VALIDATION.md) (append a
`plan-v2` section) or a dated `VALIDATION-v2-<date>.md` if you prefer not to
rewrite history.

This plan assumes **no new Unix users** and **no sudo** for routine steps, per
the shared-server constraints in AGENT_SERVER_SETUP.

---

## Preconditions (blockers if false)

| Check | Command / expectation |
|--------|------------------------|
| GPU visible | `nvidia-smi -L` |
| CUDA compile | `nvcc --version` (or path used by `run.sh`, e.g. `/usr/local/cuda-12.5/bin/nvcc`) |
| Device access | `groups` includes owners of `/dev/nvidiactl` (often `video`/`render`); test `python3 cuda-ioctl-map/replay/replay.py cuda-ioctl-map/sniffed/cu_init.jsonl` **without** sudo or accept admin fix |
| Git + network | `git clone` of this repo from the server |
| uv or pip | `uv --version` or user venv-capable `python3 -m pip` |
| Disk | Several GB for HF model cache + clones + traces |

If replay cannot open devices, **stop**—no amount of optimizer code fixes
kernel permissions.

---

## Phase 0 — Baseline from existing tree (optional sanity)

On your **normal** dev clone (not the throwaway), fast checks:

```bash
cd gpu-virt/ioctl-cuda-mapping/cuda-ioctl-map
python3 -m unittest discover -s optimizer/tests -p 'test_*.py' -v
python3 optimizer/evaluate.py --harness optimizer/harness.min.json --dry-run
```

**Pass criteria:** unittest OK; dry-run prints `"ok": true`.

---

## Phase 1 — Throwaway clone and optimizer venv

Per [AGENT_SERVER_SETUP.md §1](AGENT_SERVER_SETUP.md):

```bash
mkdir -p "$HOME/ioctl-agent-scratch"
cd "$HOME/ioctl-agent-scratch"
git clone <REPO_URL> "work-$(date +%Y%m%d-%H%M%S)"
cd work-*/gpu-virt/ioctl-cuda-mapping/cuda-ioctl-map
uv venv optimizer/.venv --python 3.10
uv pip install -p optimizer/.venv -r optimizer/requirements.txt
```

**Pass criteria:** `.venv` exists; `optimizer/.venv/bin/python -c "import gepa, litellm, yaml"` succeeds.

---

## Phase 2 — Local LLM server (vLLM) on one Titan

**Goal:** OpenAI-compatible `/v1` on `127.0.0.1`, model suitable for ~24 GB
VRAM (see model table in AGENT_SERVER_SETUP).

1. Choose **GPU id** for LLM only (e.g. `CUDA_VISIBLE_DEVICES=0`).
2. Create or reuse a **separate** venv that has `vllm` installed (may differ
   from optimizer `.venv` if dependency sets clash—both are fine).
3. Start server bound to **localhost**:

   ```bash
   export CUDA_VISIBLE_DEVICES=0
   vllm serve meta-llama/Meta-Llama-3.1-8B-Instruct \
     --host 127.0.0.1 --port 8000 \
     --max-model-len 8192
   ```

   Adjust CLI to your installed vLLM version (`vllm serve` vs
   `python -m vllm.entrypoints.openai.api_server`—use what `vllm --help`
   shows).

4. Record **`model id`** from:

   ```bash
   curl -s http://127.0.0.1:8000/v1/models | python3 -m json.tool
   ```

**Pass criteria:** HTTP 200; at least one model `id`; optional: one-off
`curl` chat completion smoke against `/v1/chat/completions`.

**Failure modes:** OOM → smaller model or lower `--max-model-len` /
`--gpu-memory-utilization`; port in use → change port and document it for
`--api-base`.

---

## Phase 3 — Wire GEPA to local server (reflection, not only eval)

In a **second** shell, from the **same throwaway** `cuda-ioctl-map/`:

```bash
export CUDA_VISIBLE_DEVICES=1   # optional: different GPU for CUDA workload
cd "$HOME/ioctl-agent-scratch/work-*/gpu-virt/ioctl-cuda-mapping/cuda-ioctl-map"

optimizer/.venv/bin/python optimizer/gepa_runner.py \
  --seed optimizer/harness.yaml \
  --max-metric-calls 12 \
  --reflection-model 'openai/<EXACT_ID_FROM_/v1/models>' \
  --api-base 'http://127.0.0.1:8000/v1' \
  --api-key 'EMPTY'
```

**Pass criteria:**

- Iteration 0 scores the seed harness (same order of magnitude as a direct
  `evaluate.py` run on `harness.yaml`).
- At least **one** reflection iteration completes **without**
  `AuthenticationError` / connection errors.
- Process exits; `best_candidate` is valid YAML (parse with `yaml.safe_load`
  in a one-liner).

**If reflection still fails:** verify `OPENAI_API_BASE` is not overridden
elsewhere; try literal model string from vLLM; check vLLM logs for 4xx from
LiteLLM.

---

## Phase 4 — Full live evaluator (regression harness)

Still in throwaway `cuda-ioctl-map/`:

```bash
optimizer/.venv/bin/python optimizer/evaluate.py --harness optimizer/harness.yaml
optimizer/.venv/bin/python optimizer/evaluate.py --harness optimizer/harness.smoke2.yaml
```

**Pass criteria:** JSON reports `"ok": true` for each; baseline and candidate
`DONE` lines show `0 failed` and no skip regression (already enforced in
code).

---

## Phase 5 — Document results

Append to [VALIDATION.md](VALIDATION.md) (or add `VALIDATION-plan-v2.md`):

- Host type (shared login / no new users).
- Model name, vLLM version, port, `CUDA_VISIBLE_DEVICES` split.
- Commit SHA of the throwaway clone tested.
- Whether GEPA reflection succeeded (yes/no + error snippet if no).
- Approximate wall time and `--max-metric-calls` used.

---

## Phase 6 — Cleanup and hygiene

```bash
rm -rf "$HOME/ioctl-agent-scratch/work-<that-run>"
```

Confirm no API keys were written into the clone. If you used HF tokens for
model download, prefer **cache outside** the clone (`HF_HOME`) or revoke
read-only tokens.

---

## Optional follow-ups (not required for v2 “done”)

1. **Small shell script** `cuda-ioctl-map/optimizer/scripts/smoke_local_llm.sh`
   that curls `/v1/models`, runs dry-run, then `gepa_runner` with env vars—reduces
   copy-paste errors.
2. **Rootless container** recipe (Podman + `--device nvidia.com/gpu=…`) if the
   host supports it.
3. **CI-style** job that only runs Phase 0 + Phase 1 + unittest on a headless
   runner (no GPU)—already partially covered by unittest.

---

## Success definition (plan-v2 complete)

| # | Milestone |
|---|-----------|
| 1 | Throwaway clone + `optimizer/.venv` installs cleanly. |
| 2 | vLLM (or equivalent) serves `/v1` on localhost; model id recorded. |
| 3 | `gepa_runner.py` completes ≥1 reflection step using **local** `--api-base`. |
| 4 | `evaluate.py` passes `harness.yaml` and `harness.smoke2.yaml` live. |
| 5 | Results written to VALIDATION (or sibling file). |
| 6 | Scratch clone removed or retained intentionally. |

---

## References

- [AGENT_SERVER_SETUP.md](AGENT_SERVER_SETUP.md) — layout, models, commands.
- [plan-v1.md](plan-v1.md) — optimizer code scope.
- [cuda-ioctl-map/optimizer/README.md](cuda-ioctl-map/optimizer/README.md) —
  blast radius and CLI flags.
- [VALIDATION.md](VALIDATION.md) — prior smoke log to extend.
