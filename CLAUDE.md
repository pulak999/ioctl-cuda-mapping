# ioctl-cuda-mapping — agent notes

## Overview

This repository captures ioctl traffic from CUDA programs to NVIDIA driver
devices (`/dev/nvidia*`), infers which buffer bytes are handles, and replays
captures without `libcuda.so`. See [README.md](README.md) for the user-facing
story and [roadmap.md](roadmap.md) for the longer-term trace-driven toolkit.

## Build and run

Primary working directory: `cuda-ioctl-map/`.

```bash
cd cuda-ioctl-map
bash run.sh programs/matmul.cu          # compile, capture, replay
bash run.sh -c programs/cu_init.cu      # capture only → sniffed/cu_init.jsonl
bash run.sh sniffed/matmul.jsonl        # replay only (needs privileges)
```

Sniffer build: `make -C intercept` produces `intercept/libnv_sniff.so`.

Replay typically requires **root** or `CAP_SYS_ADMIN` for `/dev/nvidia*`
opens.

## Optimizer harness (plan-v1)

- **Throwaway clone (shared login ok) + local Titan LLM:** see
  [AGENT_SERVER_SETUP.md](AGENT_SERVER_SETUP.md).
- Config: `cuda-ioctl-map/optimizer/harness.yaml`
- Evaluator: `python3 optimizer/evaluate.py` (from `cuda-ioctl-map/`)
- GEPA driver (optional): `python3 optimizer/gepa_runner.py`
- Python deps: `pip install -r optimizer/requirements.txt`
- **plan-v2 smoke:** `cd cuda-ioctl-map && SKIP_LIVE=1 ./optimizer/scripts/smoke_plan_v2.sh`
  (Phase 0 only). The same command runs in CI (`.github/workflows/optimizer-plan-v2-phase0.yml`)
  on pushes and PRs to `main` / `coding-agent-dev`. Full plan-v2 (live evaluate + optional local LLM + GEPA) is
  [plan-v2.md](plan-v2.md); results belong in [VALIDATION.md](VALIDATION.md).

**`smoke_plan_v2.sh` environment (see plan-v2 “Automation” table):**

| Variable | Role |
|----------|------|
| `SKIP_LIVE=1` | Unittest + dry-run + precondition hints only. |
| `OPT_PY` | Interpreter for unittest / `evaluate.py` (default `python3`). Use `optimizer/.venv/bin/python` if system Python lacks deps. |
| `OPT_VENV_PY` | Interpreter for `gepa_runner.py` (defaults to `.venv` if present). |
| `VLLM_API_BASE` | e.g. `http://127.0.0.1:8000/v1` — after live evaluate, curls `…/models`. |
| `GEPA_REFLECTION_MODEL` | e.g. `openai/<id>` — with `VLLM_API_BASE`, runs GEPA reflection. |
| `GEPA_MAX_METRIC_CALLS`, `GEPA_API_KEY` | Optional; see script. |
| `GEPA_USE_GEMINI` | Set to `1` to run Phase 3 with **Gemini** (LiteLLM `gemini/…`). Loads `GEMINI_API_KEY` from env or from `GEMINI_KEY_FILE` / default `gpu-virt/gemini-key.txt` (sibling of `ioctl-cuda-mapping`). |
| `GEMINI_KEY_FILE` | Optional path to a one-line Gemini API key file (never commit). |

## Conventions

- Shell entry point assumes current directory is `cuda-ioctl-map/`.
- JSONL one JSON object per line; ioctl `req` is a hex string.
- Handle patching uses 4-byte little-endian fields per `handle_offsets.json`.
