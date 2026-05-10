# Session log — handoff notes

Append-only log for multi-session work. **Do not put secrets or API key values here.**

---

## 2026-05-09 — plan-v2, CI, GEPA/Gemini

### Repo / branch

- Branch: `coding-agent-dev`
- Recent commits (chronological): CI workflow (`933f3f3`), validation/TODO for Gemini attempt (`737bae5`)

### Shipped earlier this session

- **GitHub Actions:** `.github/workflows/optimizer-plan-v2-phase0.yml` — on `push` / `pull_request` to `main` or `coding-agent-dev`, runs from `cuda-ioctl-map/`:

  `SKIP_LIVE=1 ./optimizer/scripts/smoke_plan_v2.sh`

  (unittest + `evaluate.py --dry-run`, no GPU.)

- **`gh` CLI:** `sudo apt install gh` was not usable non-interactively; home install hit **NFS disk quota**. A working `gh` binary was unpacked under `/tmp/gh-cli-$USER/gh` (ephemeral). `gh run list` needs `gh auth login` or `GH_TOKEN`. CI was confirmed green via public API: workflow **“Optimizer plan-v2 Phase 0”** — **success** on commit `933f3f3` (run URL under `pulak999/gopher` — repo rename/move from `ioctl-cuda-mapping`).

### Plan-v2 run (this machine)

Full smoke with Gemini path:

```bash
cd cuda-ioctl-map
export OPT_PY="$PWD/optimizer/.venv/bin/python"
export GEPA_USE_GEMINI=1
export GEPA_MAX_METRIC_CALLS=6
./optimizer/scripts/smoke_plan_v2.sh
```

- **Phase 0 / 4:** PASS — both `harness.yaml` and `harness.smoke2.yaml` reported `"ok": true`.
- **Phase 3 (GEPA + LiteLLM + Gemini):** Iteration 0 scored seed harness; every **reflection** step failed with **Gemini HTTP 429** (free-tier quota / `RESOURCE_EXHAUSTED` for `gemini-2.0-flash`). No new candidate; `best_candidate` stayed seed YAML. Exit code was still 0.

Key file for Gemini key (not committed): default resolution loads **`gpu-virt/gemini-key.txt`** (see `smoke_plan_v2.sh` header and `CLAUDE.md`).

### Docs updated

- **`VALIDATION.md`** — subsection **“Phase 3 (GEPA + Gemini)”** with commands, commit SHA, 429 outcome, reminder that strict plan milestone **3** needs **local vLLM** (`VLLM_API_BASE` + `GEPA_REFLECTION_MODEL`).
- **`TODO.md`** — Gemini attempt checked off as documented; local vLLM path still listed for strict plan-v2.
- **`LOG.md`** — matching session entry.

Canonical detail lives in **`VALIDATION.md`** and **`LOG.md`**; this file is the **pick-up-later** index.

### What you should do next

1. **Strict plan-v2 milestone 3:** Start vLLM (or compatible server), set `VLLM_API_BASE` and `GEPA_REFLECTION_MODEL=openai/<id>`, run `./optimizer/scripts/smoke_plan_v2.sh` **without** `SKIP_LIVE` and **without** `GEPA_USE_GEMINI`; append vLLM version + reflection yes/no to **`VALIDATION.md`**.
2. **Optional plan Phase 1:** Throwaway clone under `$HOME/ioctl-agent-scratch` (or elsewhere if home quota is tight); Phase 6 cleanup after.
3. **Merge:** When CI and live validation look good, merge `coding-agent-dev` → `main` (see **`TODO.md`**).

### References

- [plan-v2.md](plan-v2.md)
- [VALIDATION.md](VALIDATION.md)
- [TODO.md](TODO.md)
- [LOG.md](LOG.md)
