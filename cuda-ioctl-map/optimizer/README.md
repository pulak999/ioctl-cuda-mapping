# Optimizer harness

Implements [plan-v1.md](../../plan-v1.md) Phase 2.5: a live evaluator around
`run.sh`, `tools/find_handle_offsets.py`, and `replay/replay.py`, plus an
optional GEPA loop over the harness YAML.

## Prerequisites

- Working directory: **`cuda-ioctl-map/`** (same as `run.sh`).
- CUDA (`nvcc`) and NVIDIA devices for capture.
- Replay usually needs **root** or `CAP_SYS_ADMIN` for `/dev/nvidia*`, unless
  your user already has access to the relevant `/dev/nvidia*` nodes (some
  servers grant this via group membership).

## Sandboxing and blast radius (agents / shared servers)

The evaluator is **not** a security sandbox. It intentionally runs:

- `bash run.sh` → `nvcc`, your CUDA binaries, `LD_PRELOAD` of the sniffer,
  and `python3 replay/replay.py` (opens kernel device nodes).
- Writes under this repo: `sniffed/`, `optimizer/runs/`, and temp harness
  files.

On a **shared server with limited permissions**, this is *usually* acceptable
only if:

- The job runs as a **dedicated user** with **no sudo**, and you accept writes
  only inside a disposable clone of the repo (or a bind-mounted workdir).
- You cap **wall time** (`timeout_*` in the harness) and **metric calls** for
  GEPA so a run cannot loop forever.
- You do **not** point GEPA at a public `api_base` without TLS and auth.

For stronger isolation, run the same commands inside **Docker** or another
container with the NVIDIA runtime, a read-only root filesystem overlay where
possible, and **no host credentials** in the environment. The coding agent
should treat “run optimizer” like “run arbitrary compiled CUDA + driver ioctls”
— same trust boundary as normal development on that machine.

## Local LLM on a Titan (no OpenAI / cloud API)

GEPA uses **LiteLLM** for reflection. Any server that speaks the **OpenAI
Chat Completions** API works (common choices: **vLLM**, **SGLang**, **TGI**,
**llama.cpp** `--server` mode). Run the model server on `127.0.0.1` (or a
private interface), pick the **served model name** from that server’s `/v1/models`
response, then:

```bash
cd cuda-ioctl-map
optimizer/.venv/bin/python optimizer/gepa_runner.py \
  --seed optimizer/harness.yaml \
  --max-metric-calls 15 \
  --reflection-model 'openai/<served-model-id>' \
  --api-base 'http://127.0.0.1:8000/v1' \
  --api-key 'EMPTY'
```

Use the real model id your stack exposes (LiteLLM expects the `openai/…`
prefix when using `OPENAI_API_BASE`). If your server ignores API keys, a
placeholder `--api-key` value is still fine for LiteLLM.

**Note:** one Titan can host the **LLM server** while another is used for CUDA
capture/replay, or you time-share; both are heavy — budget GPU memory
accordingly.

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
