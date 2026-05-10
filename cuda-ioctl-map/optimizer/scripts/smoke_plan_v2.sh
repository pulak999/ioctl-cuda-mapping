#!/usr/bin/env bash
# plan-v2 smoke — run from cuda-ioctl-map/ (or any cwd; script cd's itself).
# See ../../plan-v2.md and optimizer/README.md.
#
# Usage:
#   SKIP_LIVE=1 ./optimizer/scripts/smoke_plan_v2.sh     # Phase 0 only (CI-friendly)
#   ./optimizer/scripts/smoke_plan_v2.sh                 # + live evaluate (needs GPU + device access)
#
# With local vLLM (Phase 2–3), set:
#   VLLM_API_BASE=http://127.0.0.1:8000/v1
#   GEPA_REFLECTION_MODEL=openai/<id-from-/v1/models>
#   GEPA_MAX_METRIC_CALLS=8   (optional)
#
# With Google Gemini (Phase 3, no local LLM): set GEPA_USE_GEMINI=1 and either
# export GEMINI_API_KEY, or place a one-line key at gpu-virt/gemini-key.txt
# (sibling of ioctl-cuda-mapping) or set GEMINI_KEY_FILE. Do not commit keys.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CUDA_IOCTL_MAP="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$CUDA_IOCTL_MAP"

PY="${OPT_PY:-python3}"
if [[ -x "$CUDA_IOCTL_MAP/optimizer/.venv/bin/python" ]]; then
  DEFAULT_VENV_PY="$CUDA_IOCTL_MAP/optimizer/.venv/bin/python"
else
  DEFAULT_VENV_PY="$PY"
fi
VENV_PY="${OPT_VENV_PY:-$DEFAULT_VENV_PY}"

echo "== plan-v2 Phase 0: unit tests ($PY)"
"$PY" -m unittest discover -s optimizer/tests -p 'test_*.py' -v

echo "== plan-v2 Phase 0: dry-run ($PY)"
"$PY" optimizer/evaluate.py --harness optimizer/harness.min.json --dry-run

echo "== Preconditions (non-fatal)"
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi -L 2>/dev/null | head -5 || true
else
  echo "WARN: nvidia-smi not in PATH"
fi
if command -v nvcc >/dev/null 2>&1; then
  nvcc --version 2>/dev/null | head -1
else
  echo "WARN: nvcc not in PATH (run.sh may still use /usr/local/cuda-*/bin/nvcc)"
fi
echo "groups: $(groups 2>/dev/null || true)"

# Optional: load Gemini API key from file when GEPA_USE_GEMINI=1 (never printed).
if [[ "${SKIP_LIVE:-}" != "1" && -n "${GEPA_USE_GEMINI:-}" ]]; then
  if [[ -z "${GEMINI_API_KEY:-}" ]]; then
    _ioctl_repo="$(cd "$CUDA_IOCTL_MAP/.." && pwd)"
    _gemini_default="$(cd "$_ioctl_repo/.." && pwd)/gemini-key.txt"
    _key_path="${GEMINI_KEY_FILE:-$_gemini_default}"
    if [[ -f "$_key_path" ]]; then
      export GEMINI_API_KEY="$(tr -d '\n\r' < "$_key_path")"
      echo "== plan-v2: GEMINI_API_KEY set from file (GEMINI_KEY_FILE or default sibling gemini-key.txt)"
    else
      echo "WARN: GEPA_USE_GEMINI set but no GEMINI_API_KEY and no key file at $_key_path" >&2
    fi
  fi
fi

if [[ "${SKIP_LIVE:-}" == "1" ]]; then
  echo "SKIP_LIVE=1 — skipping live evaluate + GEPA (set unset or 0 to run)"
  exit 0
fi

echo "== plan-v2 Phase 4: live evaluate (harness.yaml)"
"$PY" optimizer/evaluate.py --harness optimizer/harness.yaml

echo "== plan-v2 Phase 4: live evaluate (harness.smoke2.yaml)"
"$PY" optimizer/evaluate.py --harness optimizer/harness.smoke2.yaml

if [[ -n "${VLLM_API_BASE:-}" ]]; then
  BASE="${VLLM_API_BASE%/}"
  echo "== plan-v2 Phase 2: GET $BASE/models"
  curl -sfS "$BASE/models" | "$PY" -m json.tool | head -60
  if [[ -z "${GEPA_REFLECTION_MODEL:-}" ]]; then
    echo "WARN: VLLM_API_BASE set but GEPA_REFLECTION_MODEL empty — skipping gepa_runner"
  else
    echo "== plan-v2 Phase 3: gepa_runner (OpenAI-compatible, $VENV_PY)"
    MAX="${GEPA_MAX_METRIC_CALLS:-12}"
    "$VENV_PY" optimizer/gepa_runner.py \
      --seed optimizer/harness.yaml \
      --max-metric-calls "$MAX" \
      --reflection-model "$GEPA_REFLECTION_MODEL" \
      --api-base "$VLLM_API_BASE" \
      --api-key "${GEPA_API_KEY:-EMPTY}"
  fi
elif [[ -n "${GEPA_USE_GEMINI:-}" && -n "${GEMINI_API_KEY:-}" ]]; then
  echo "== plan-v2 Phase 3: gepa_runner (Gemini via LiteLLM, $VENV_PY)"
  MAX="${GEPA_MAX_METRIC_CALLS:-12}"
  GEMM="${GEPA_REFLECTION_MODEL:-gemini/gemini-2.0-flash}"
  # Avoid LiteLLM routing to Vertex / OpenAI when using gemini/… models.
  ( unset OPENAI_API_BASE OPENAI_API_KEY 2>/dev/null || true
    export GEMINI_API_KEY
    "$VENV_PY" optimizer/gepa_runner.py \
      --seed optimizer/harness.yaml \
      --max-metric-calls "$MAX" \
      --reflection-model "$GEMM"
  )
else
  echo "Hint: Phase 3 — VLLM_API_BASE + GEPA_REFLECTION_MODEL=openai/<id>,"
  echo "      or GEPA_USE_GEMINI=1 + GEMINI_API_KEY (or key file; see script header)."
fi

echo "== smoke_plan_v2.sh finished OK"
