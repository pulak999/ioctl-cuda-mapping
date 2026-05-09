# Agent + GPU server runbook (low-privilege, local LLM)

Goal: run `optimizer/evaluate.py` and `optimizer/gepa_runner.py` with **no
sudo**, from a **throwaway git clone** (or a dedicated directory under your
account), using a **self-hosted** chat model on a Titan for GEPA
reflection—no cloud API keys.

This matches the trust model in
[cuda-ioctl-map/optimizer/README.md](cuda-ioctl-map/optimizer/README.md):
repo-local writes + real ioctl replay; **not** a defense against malicious
YAML—only use trusted harness files and trusted code in the clone.

---

## 0. Two ways to isolate (pick what your server allows)

| Approach | When to use |
|----------|----------------|
| **A. Shared account + throwaway clone** | You **cannot** create new Unix users (typical shared GPU server). Use a **fresh clone per job** under your `$HOME` so you never point the agent at your main working tree. |
| **B. Dedicated Unix user** | An admin can `adduser` and give that user GPU access; optional, see [appendix](#appendix-optional-dedicated-unix-user). |

Everything below assumes **A** unless you explicitly use **B**.

---

## 1. Shared account: layout without `adduser`

Use a **scratch parent** only for agent jobs (easy to delete in bulk):

```bash
mkdir -p "$HOME/ioctl-agent-scratch"
cd "$HOME/ioctl-agent-scratch"
git clone <YOUR_REPO_URL> "work-$(date +%Y%m%d-%H%M%S)"
cd "work-*/gpu-virt/ioctl-cuda-mapping/cuda-ioctl-map"
```

Optional: `chmod 700 "$HOME/ioctl-agent-scratch"` so other logins on the box
cannot list your job dirs (does not stop root).

**Rule:** the coding agent’s config should **only** ever `cd` into these
`work-*` paths—not into your personal dev clone with uncommitted work or
tokens in `.env`.

---

## 2. Device access without sudo

Replay opens `/dev/nvidiactl`, `/dev/nvidia*`, `/dev/nvidia-uvm` with `O_RDWR`.

- On many servers your login is already in **`video`** / **`render`** and
  replay works **without** root (same as your successful non-sudo validation).
- If you get `Permission denied`, only a **host admin** can fix udev/groups—you
  cannot solve that from user space without elevated rights.

Check:

```bash
groups
ls -l /dev/nvidiactl /dev/nvidia0 2>/dev/null | head -5
```

---

## 3. Python environment (no system pip required)

Use **uv** (or a user-owned venv):

```bash
cd "$HOME/ioctl-agent-scratch/work-*/gpu-virt/ioctl-cuda-mapping/cuda-ioctl-map"
uv venv optimizer/.venv --python 3.10
uv pip install -p optimizer/.venv -r optimizer/requirements.txt
```

**vLLM note:** installing `vllm` may require its own venv or system packages your
host provides; it is fine to run vLLM from a **different** directory/venv than
the optimizer, as long as GEPA can reach `http://127.0.0.1:…`.

Smoke without a reflection server:

```bash
optimizer/.venv/bin/python optimizer/evaluate.py --harness optimizer/harness.min.json --dry-run
```

---

## 4. Local model on a Titan (GEPA reflection)

GEPA uses **LiteLLM**; any **OpenAI-compatible** HTTP API works. On a Titan
(**~24 GB** VRAM typical), prefer **8B–14B** instruct models so vLLM/SGLang
has room for KV cache.

### Model suggestions

| Role | Suggestion | Notes |
|------|------------|--------|
| **Primary** | **Meta-Llama-3.1-8B-Instruct** | Strong instruction following; comfortable on 24 GB. |
| **Faster** | **Qwen2.5-7B-Instruct** or **Mistral-7B-Instruct-v0.3** | Lower latency. |
| **Heavier** | **Qwen2.5-14B-Instruct** | Tighter VRAM—shorten `--max-model-len`, low concurrency. |

### Example: vLLM on `127.0.0.1` (separate shell)

Use one GPU for the LLM and another for CUDA capture/replay when possible:

```bash
export CUDA_VISIBLE_DEVICES=0
/path/to/vllm-venv/bin/python -m vllm.entrypoints.openai.api_server \
  --model meta-llama/Meta-Llama-3.1-8B-Instruct \
  --dtype auto \
  --max-model-len 8192 \
  --host 127.0.0.1 \
  --port 8000
```

Confirm the model id: `curl -s http://127.0.0.1:8000/v1/models`

### GEPA pointed at that server

```bash
cd "$HOME/ioctl-agent-scratch/work-*/gpu-virt/ioctl-cuda-mapping/cuda-ioctl-map"
export CUDA_VISIBLE_DEVICES=1   # optional: Titan for CUDA only

optimizer/.venv/bin/python optimizer/gepa_runner.py \
  --seed optimizer/harness.yaml \
  --max-metric-calls 20 \
  --reflection-model 'openai/meta-llama/Meta-Llama-3.1-8B-Instruct' \
  --api-base 'http://127.0.0.1:8000/v1' \
  --api-key 'EMPTY'
```

Match `--reflection-model` to the **`id`** from `/v1/models`.

---

## 5. Extra isolation without new users (optional)

If the host has **rootless Podman** or **Docker** permission for your uid, you
can run the **throwaway clone** inside a container with the NVIDIA runtime and
a **read-only** mount of anything you do not want modified. That still does not
replace “trusted harness YAML,” but it bounds filesystem impact to the
container layer.

---

## 6. Resource and safety caps

| Knob | Where | Purpose |
|------|--------|---------|
| Wall time | `harness.yaml` → `timeout_capture_sec`, `timeout_replay_sec` | Stop hung nvcc or replay. |
| GEPA budget | `gepa_runner.py` → `--max-metric-calls` | Each call runs full live evaluator. |
| LLM server | vLLM limits / GPU split | Avoid starving capture/replay. |
| Network | LLM on `127.0.0.1` only | No public exposure of reflection API. |

---

## 7. Cleanup

```bash
rm -rf "$HOME/ioctl-agent-scratch/work-20260209-143022"
```

Rotate clones per job for a clean `sniffed/` and `optimizer/runs/`.

---

## 8. Checklist (shared account)

- [ ] Job uses a **new** `work-*` clone under `$HOME/ioctl-agent-scratch` (or similar), not your main repo.
- [ ] `groups` allows NVIDIA device access, or you accept replay will fail until an admin fixes it.
- [ ] `optimizer/.venv` installed; `evaluate.py --dry-run` passes.
- [ ] vLLM (or equivalent) on `127.0.0.1`; `gepa_runner` `--reflection-model` matches `/v1/models`.
- [ ] Harness `programs:` list is trusted; timeouts and `--max-metric-calls` are conservative.
- [ ] After the job, **delete** the `work-*` directory (or archive logs first).

---

## Appendix: optional dedicated Unix user

If an admin **can** create a user, this adds OS-level separation between your
interactive account and the agent:

```bash
sudo adduser ioctl-opt --disabled-password --gecos ""
sudo mkdir -p /srv/ioctl-opt && sudo chown ioctl-opt:ioctl-opt /srv/ioctl-opt
```

Then use `/srv/ioctl-opt/work-*` the same way as `$HOME/ioctl-agent-scratch`
above. Routine agent work should still avoid sudo.
