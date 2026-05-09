# Agent + GPU server runbook (low-privilege, local LLM)

Goal: run `optimizer/evaluate.py` and `optimizer/gepa_runner.py` as a **dedicated
Unix user** with **no sudo**, from a **throwaway git clone** (or a single
writable workdir), using a **self-hosted** chat model on a Titan for GEPA
reflection—no cloud API keys.

This matches the trust model discussed in
[cuda-ioctl-map/optimizer/README.md](cuda-ioctl-map/optimizer/README.md):
repo-local writes + real ioctl replay; **not** a defense against malicious
YAML—only use trusted harness files and trusted code in the clone.

---

## 1. Dedicated user

```bash
# as root (one-time provisioning on the server)
sudo adduser ioctl-opt --disabled-password --gecos ""
sudo mkdir -p /srv/ioctl-opt
sudo chown ioctl-opt:ioctl-opt /srv/ioctl-opt
```

Log in as `ioctl-opt` (SSH key or `sudo -u ioctl-opt -i`). **Routine work
never uses sudo.**

Optional: `chmod 750 /srv/ioctl-opt` and keep the clone only there.

---

## 2. Device access without sudo

Replay opens `/dev/nvidiactl`, `/dev/nvidia*`, `/dev/nvidia-uvm` with `O_RDWR`.

- If `ioctl-opt` is in groups that own those nodes (often **`video`** and/or
  **`render`** on Debian/Ubuntu), replay may work **without** root (your
  validation already succeeded that way on one host).
- If opens fail with `Permission denied`, an admin must fix **udev rules or
  group membership**—not grant blanket sudo to the agent user.

Check:

```bash
groups
ls -l /dev/nvidiactl /dev/nvidia0 2>/dev/null | head -5
```

---

## 3. Throwaway clone + workdir only

```bash
sudo -u ioctl-opt -i   # or SSH as ioctl-opt
cd /srv/ioctl-opt
git clone <YOUR_REPO_URL> work-20260209-a
cd work-20260209-a/gpu-virt/ioctl-cuda-mapping/cuda-ioctl-map
```

Treat `work-*` as **disposable**: delete the directory after a job or when disk
fills. Do not reuse a long-lived clone that accumulates secrets in `.env`.

**Git:** read-only remote is fine; push from a different account if needed.

---

## 4. Python environment (no system pip required)

Use **uv** (or pip in a venv owned by `ioctl-opt`):

```bash
cd /srv/ioctl-opt/work-*/gpu-virt/ioctl-cuda-mapping/cuda-ioctl-map
uv venv optimizer/.venv --python 3.10
uv pip install -p optimizer/.venv -r optimizer/requirements.txt
```

Smoke without GPU reflection server:

```bash
optimizer/.venv/bin/python optimizer/evaluate.py --harness optimizer/harness.min.json --dry-run
```

---

## 5. Local model on a Titan (GEPA reflection)

GEPA uses **LiteLLM**; any **OpenAI-compatible** HTTP API works. On a Titan
(**24 GB** VRAM typical for RTX Titan class), prefer **8B–14B** instruct
models so vLLM/SGLang has room for KV cache and concurrent short requests.

### Recommended default (good quality / fits one Titan)

| Role | Suggestion | Notes |
|------|------------|--------|
| **Primary** | **Meta-Llama-3.1-8B-Instruct** | Strong general instruction following; ~16 GB weights in FP16/BF16 plus overhead—comfortable on 24 GB. |
| **Faster / smaller** | **Qwen2.5-7B-Instruct** or **Mistral-7B-Instruct-v0.3** | Lower latency; still usable for YAML edits. |
| **Heavier (if you have headroom)** | **Qwen2.5-14B-Instruct** | Better reasoning; tighter on 24 GB—use shorter `--max-model-len` and modest concurrency. |

Avoid 70B on a single 24 GB card unless you use aggressive quantization and
accept slower reflection.

### Example: vLLM OpenAI server (separate shell, as same user or another)

Bind to **localhost** only; use a **dedicated GPU** for the LLM if you want
capture/replay on another Titan:

```bash
# Example: GPU 0 for LLM — adjust CUDA_VISIBLE_DEVICES
export CUDA_VISIBLE_DEVICES=0
optimizer/.venv/bin/python -m vllm.entrypoints.openai.api_server \
  --model meta-llama/Meta-Llama-3.1-8B-Instruct \
  --dtype auto \
  --max-model-len 8192 \
  --host 127.0.0.1 \
  --port 8000
```

Confirm the served id (often the HuggingFace repo id) via
`curl -s http://127.0.0.1:8000/v1/models`.

### GEPA runner pointed at localhost

In a **second** shell (optionally `CUDA_VISIBLE_DEVICES=1` for capture/replay
only):

```bash
cd /srv/ioctl-opt/work-*/gpu-virt/ioctl-cuda-mapping/cuda-ioctl-map
export CUDA_VISIBLE_DEVICES=1   # optional: Titan for CUDA only

optimizer/.venv/bin/python optimizer/gepa_runner.py \
  --seed optimizer/harness.yaml \
  --max-metric-calls 20 \
  --reflection-model 'openai/meta-llama/Meta-Llama-3.1-8B-Instruct' \
  --api-base 'http://127.0.0.1:8000/v1' \
  --api-key 'EMPTY'
```

Adjust `--reflection-model` to match **exactly** what vLLM lists as `id` under
`/v1/models` (LiteLLM uses the `openai/...` prefix when `OPENAI_API_BASE` is
set—`gepa_runner` sets that from `--api-base`).

---

## 6. Resource and safety caps

| Knob | Where | Purpose |
|------|--------|---------|
| Wall time | `harness.yaml` → `timeout_capture_sec`, `timeout_replay_sec` | Stop hung nvcc or replay. |
| GEPA budget | `gepa_runner.py` → `--max-metric-calls` | Each call runs full live evaluator—keep small on shared hardware. |
| LLM server | vLLM `--max-num-seqs`, `--gpu-memory-utilization` | Leave VRAM for CUDA if sharing one GPU (prefer splitting GPUs). |
| Network | LLM on `127.0.0.1` only | No inbound exposure of reflection API. |

---

## 7. Cleanup

```bash
rm -rf /srv/ioctl-opt/work-20260209-a
```

Rotate clones per job if you want a clean `sniffed/` and `optimizer/runs/`
every time.

---

## 8. Checklist before “agent runs unattended”

- [ ] User `ioctl-opt` exists; no sudo in agent workflow.
- [ ] Fresh clone under `/srv/ioctl-opt/work-…`.
- [ ] `groups` allows read/write to NVIDIA device nodes (or replay is known to fail until fixed).
- [ ] `optimizer/.venv` installed; `evaluate.py --dry-run` passes.
- [ ] vLLM (or equivalent) listening on `127.0.0.1` with chosen model.
- [ ] `gepa_runner.py` `--reflection-model` matches `/v1/models`.
- [ ] Harness `programs:` list is only ladder paths you trust.
- [ ] Timeouts and `--max-metric-calls` set conservatively.
