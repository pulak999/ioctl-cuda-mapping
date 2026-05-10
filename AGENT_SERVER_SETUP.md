# Agent + GPU server runbook (low-privilege, local LLM)

Goal: run `optimizer/evaluate.py` and `optimizer/gepa_runner.py` with **no
sudo**, from a **throwaway git clone** (or a dedicated directory under your
account), using either a **self-hosted** chat model on a Titan for GEPA
reflection or an optional **Gemini** API key (see §4b)—never commit keys.

This matches the trust model in
[cuda-ioctl-map/optimizer/README.md](cuda-ioctl-map/optimizer/README.md):
repo-local writes + real ioctl replay; **not** a defense against malicious
YAML—only use trusted harness files and trusted code in the clone.

---

## 0. Two ways to isolate (pick what your server allows)


| Approach                                | When to use                                                                                                                                                                |
| --------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A. Shared account + throwaway clone** | You **cannot** create new Unix users (typical shared GPU server). Use a **fresh clone per job** under your `$HOME` so you never point the agent at your main working tree. |
| **B. Dedicated Unix user**              | An admin can `adduser` and give that user GPU access; optional, see [appendix](#appendix-optional-dedicated-unix-user).                                                    |


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

- On many servers your login is already in `**video`** / `**render**` and
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


| Role        | Suggestion                                              | Notes                                                    |
| ----------- | ------------------------------------------------------- | -------------------------------------------------------- |
| **Primary** | **Meta-Llama-3.1-8B-Instruct**                          | Strong instruction following; comfortable on 24 GB.      |
| **Faster**  | **Qwen2.5-7B-Instruct** or **Mistral-7B-Instruct-v0.3** | Lower latency.                                           |
| **Heavier** | **Qwen2.5-14B-Instruct**                                | Tighter VRAM—shorten `--max-model-len`, low concurrency. |


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

Match `--reflection-model` to the `**id**` from `/v1/models`.

### 4a-hulk. Known working configuration on this host

Hulk (shared login, 3× NVIDIA TITAN RTX, driver 555.42.02, kernel 5.15.0-173):

- `/dev/nvidia*` permissions: `crw-rw-rw-` — no special group required; any user can replay.
- **vLLM venv:** `/home/pm3371/gitrepos/gpu-virt/vllm/.venv/` (Python 3.12, vLLM 0.6.1.post1).
  The `vllm` binary has a stale shebang; use the module form instead.
- **HF model cache** (already downloaded, no network needed):
  - `meta-llama/Llama-3.2-1B` — complete; used for GEPA reflection on this host.
  - `Qwen/Qwen2.5-7B-Instruct` — **incomplete** (xet-protocol download failed, `.incomplete` blobs only); do not use.
- **One-time venv fixes** (apply once; the venv is now fixed if you're on the same machine):
  ```bash
  # Fix 1: outlines 0.0.46 requires numpy<2; venv ships numpy 2.4.4
  /home/pm3371/gitrepos/gpu-virt/vllm/.venv/bin/python3 -m pip install "numpy<2" --quiet

  # Fix 2: PyPI pyairports 0.0.1 is a squatter (no actual module); stub it
  SITE=/home/pm3371/gitrepos/gpu-virt/vllm/.venv/lib/python3.12/site-packages
  mkdir -p "$SITE/pyairports"
  echo "" > "$SITE/pyairports/__init__.py"
  echo "AIRPORT_LIST = ()" > "$SITE/pyairports/airports.py"
  ```
  Verify: `/home/pm3371/gitrepos/gpu-virt/vllm/.venv/bin/python3 -c "from outlines import grammars; print('OK')"`
- **Chat template** for base models without a built-in tokenizer template:
  `cuda-ioctl-map/optimizer/scripts/llama_base_chat_template.jinja`

**Start vLLM on GPU 0 (separate terminal, from anywhere):**

```bash
export CUDA_VISIBLE_DEVICES=0
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
/home/pm3371/gitrepos/gpu-virt/vllm/.venv/bin/python3 \
  -m vllm.entrypoints.openai.api_server \
  --model meta-llama/Llama-3.2-1B \
  --dtype half \
  --max-model-len 8192 \
  --host 127.0.0.1 \
  --port 8000 \
  --chat-template /home/pm3371/gitrepos/gpu-virt/ioctl-cuda-mapping/cuda-ioctl-map/optimizer/scripts/llama_base_chat_template.jinja
```

Wait for `"Started server process"` / `"Application startup complete"` in the logs.
Confirm: `curl -s http://127.0.0.1:8000/v1/models | python3 -m json.tool`

**Run full smoke (GPU 1 for CUDA, GPU 0 for LLM) — from `cuda-ioctl-map/`:**

```bash
export CUDA_VISIBLE_DEVICES=1   # optional: isolate CUDA capture/replay
cd /home/pm3371/gitrepos/gpu-virt/ioctl-cuda-mapping/cuda-ioctl-map
export OPT_PY="$PWD/optimizer/.venv/bin/python"
export VLLM_API_BASE="http://127.0.0.1:8000/v1"
export GEPA_REFLECTION_MODEL="openai/meta-llama/Llama-3.2-1B"
export GEPA_MAX_METRIC_CALLS=8
./optimizer/scripts/smoke_plan_v2.sh
```

Append the vLLM version + reflection result to `VALIDATION.md` afterward.

---

### 4b. Gemini (optional — no local LLM, no large HF cache)

If **vLLM is impractical** (disk quota, VRAM, or bf16 on Turing), GEPA can use
**Google AI Studio** via LiteLLM’s `gemini/…` routes. Create a key in
[Google AI Studio](https://aistudio.google.com/app/apikey), then **do not
commit it**.

**One-line key file (recommended layout):** store the raw key in
`gpu-virt/gemini-key.txt` (sibling of the `ioctl-cuda-mapping` checkout, i.e.
one directory above this repo’s root next to `gemini-key.txt`). That path is
git-ignored at the repo root and auto-read by `smoke_plan_v2.sh` when
`GEPA_USE_GEMINI=1` is set (override path with `GEMINI_KEY_FILE`).

**Run GEPA with Gemini:**

```bash
cd "$HOME/ioctl-agent-scratch/work-*/gpu-virt/ioctl-cuda-mapping/cuda-ioctl-map"
export GEMINI_API_KEY="…"   # or rely on key file + GEPA_USE_GEMINI below

optimizer/.venv/bin/python optimizer/gepa_runner.py \
  --seed optimizer/harness.yaml \
  --max-metric-calls 12 \
  --reflection-model 'gemini/gemini-2.0-flash'
```

Use a `**gemini/**` model prefix so LiteLLM uses **AI Studio** (`GEMINI_API_KEY`),
not Vertex. Omit `--api-base` / `--api-key` for this path.

**Full smoke with Gemini Phase 3** (after Phase 4 live evaluate):

```bash
cd cuda-ioctl-map
GEPA_USE_GEMINI=1 ./optimizer/scripts/smoke_plan_v2.sh
```

With an explicit key path:

```bash
GEPA_USE_GEMINI=1 GEMINI_KEY_FILE=/path/to/gemini-key.txt ./optimizer/scripts/smoke_plan_v2.sh
```

Optional: `GEPA_REFLECTION_MODEL=gemini/gemini-2.5-flash-preview-05-20` if your
quota includes that model.

---

## 5. Extra isolation without new users (optional)

If the host has **rootless Podman** or **Docker** permission for your uid, you
can run the **throwaway clone** inside a container with the NVIDIA runtime and
a **read-only** mount of anything you do not want modified. That still does not
replace “trusted harness YAML,” but it bounds filesystem impact to the
container layer.

---

## 6. Resource and safety caps


| Knob        | Where                                                        | Purpose                               |
| ----------- | ------------------------------------------------------------ | ------------------------------------- |
| Wall time   | `harness.yaml` → `timeout_capture_sec`, `timeout_replay_sec` | Stop hung nvcc or replay.             |
| GEPA budget | `gepa_runner.py` → `--max-metric-calls`                      | Each call runs full live evaluator.   |
| LLM server  | vLLM limits / GPU split                                      | Avoid starving capture/replay.        |
| Network     | LLM on `127.0.0.1` only                                      | No public exposure of reflection API. |


---

## 7. Cleanup

```bash
rm -rf "$HOME/ioctl-agent-scratch/work-20260209-143022"
```

Rotate clones per job for a clean `sniffed/` and `optimizer/runs/`.

---

## 8. Checklist (shared account)

- Job uses a **new** `work-`* clone under `$HOME/ioctl-agent-scratch` (or similar), not your main repo.
- `groups` allows NVIDIA device access, or you accept replay will fail until an admin fixes it.
- `optimizer/.venv` installed; `evaluate.py --dry-run` passes.
- vLLM (or equivalent) on `127.0.0.1`; `gepa_runner` `--reflection-model` matches `/v1/models`.
- Harness `programs:` list is trusted; timeouts and `--max-metric-calls` are conservative.
- After the job, **delete** the `work-`* directory (or archive logs first).

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