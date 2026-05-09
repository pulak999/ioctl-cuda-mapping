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

## Conventions

- Shell entry point assumes current directory is `cuda-ioctl-map/`.
- JSONL one JSON object per line; ioctl `req` is a hex string.
- Handle patching uses 4-byte little-endian fields per `handle_offsets.json`.
