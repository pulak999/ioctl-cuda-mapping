#!/usr/bin/env python3
"""
Live evaluator: capture → pair traces → infer candidate handle_offsets.json
→ replay with baseline vs candidate offsets → JSON metrics on stdout.

Run from cuda-ioctl-map/:

  python3 optimizer/evaluate.py --harness optimizer/harness.yaml

Or pass a directory containing harness.yaml:

  python3 optimizer/evaluate.py --harness optimizer/runs/abc/

Requires: bash, CUDA nvcc (for .cu), sniffer .so, and privileges for replay
when not using --dry-run-metrics (dry run only validates harness + imports).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

# cuda-ioctl-map root (parent of optimizer/)
ROOT = Path(__file__).resolve().parent.parent


def _load_metrics():
    p = Path(__file__).resolve().parent / "metrics.py"
    mod_name = "ioctl_cuda_optimizer_metrics"
    spec = importlib.util.spec_from_file_location(mod_name, p)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load metrics.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_harness_file(path: Path) -> dict[str, Any]:
    """Load harness.yaml / harness.json into a dict (public API for gepa_runner)."""
    return _load_yaml_or_json(path)


def _load_yaml_or_json(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
        except ImportError as e:
            raise SystemExit(
                "PyYAML is required for .yaml harness files. "
                "Install: python3 -m pip install -r optimizer/requirements.txt"
            ) from e
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("harness root must be a mapping")
    return data


def _run(cmd: list[str], *, cwd: Path, timeout: int | None = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def evaluate_harness(harness: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    """
    Run the full pipeline for each program in harness["programs"].
    Returns a metrics dict including aggregate score and per-program rows.
    """
    programs: list[str] = harness.get("programs") or []
    if not programs:
        raise ValueError("harness.programs must be a non-empty list")

    baseline_offsets = Path(
        harness.get("baseline_offsets", "intercept/handle_offsets.json")
    )
    if not baseline_offsets.is_absolute():
        baseline_offsets = ROOT / baseline_offsets

    runs_parent = Path(harness.get("runs_dir", "optimizer/runs"))
    if not runs_parent.is_absolute():
        runs_parent = ROOT / runs_parent

    timeout_capture = int(harness.get("timeout_capture_sec", 600))
    timeout_replay = int(harness.get("timeout_replay_sec", 1800))
    verbose = harness.get("replay_verbose", False)

    run_id = harness.get("run_id") or uuid.uuid4().hex[:12]
    run_root = runs_parent / run_id
    run_root.mkdir(parents=True, exist_ok=True)

    met = _load_metrics()

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "run_id": run_id,
            "run_root": str(run_root),
            "programs": programs,
        }

    baseline_json = met.load_handle_offsets(baseline_offsets)
    rows: list[dict[str, Any]] = []
    all_ok = True
    total_score = 0.0

    for prog in programs:
        prog_path = Path(prog)
        if not prog_path.is_absolute():
            prog_path = ROOT / prog_path
        if not prog_path.is_file():
            rows.append(
                {
                    "program": prog,
                    "ok": False,
                    "error": f"missing_program_file:{prog_path}",
                }
            )
            all_ok = False
            continue

        try:
            prog_rel = prog_path.relative_to(ROOT)
        except ValueError:
            rows.append(
                {
                    "program": prog,
                    "ok": False,
                    "error": "program_path_must_be_under_cuda_ioctl_map_root",
                }
            )
            all_ok = False
            continue

        stem = prog_path.stem
        pr_dir = run_root / stem
        pr_dir.mkdir(parents=True, exist_ok=True)
        trace_default = ROOT / "sniffed" / f"{stem}.jsonl"

        captures: list[Path] = []
        for i in range(2):
            r = _run(
                ["bash", "run.sh", "-c", str(prog_rel)],
                cwd=ROOT,
                timeout=timeout_capture,
            )
            err_tail = (r.stderr or "")[-2000:]
            if r.returncode != 0:
                rows.append(
                    {
                        "program": str(prog_rel),
                        "ok": False,
                        "error": f"capture_failed:exit={r.returncode}",
                        "stderr_tail": err_tail,
                    }
                )
                all_ok = False
                captures = []
                break
            if not trace_default.is_file() or trace_default.stat().st_size == 0:
                rows.append(
                    {
                        "program": str(prog_rel),
                        "ok": False,
                        "error": "empty_or_missing_trace_after_capture",
                        "stderr_tail": err_tail,
                    }
                )
                all_ok = False
                captures = []
                break
            dest = pr_dir / f"{stem}_run{i}.jsonl"
            shutil.copy2(trace_default, dest)
            captures.append(dest)

        if len(captures) != 2:
            total_score += -1.0
            continue

        candidate_offsets = pr_dir / "handle_offsets.json"
        inf = _run(
            [
                sys.executable,
                str(ROOT / "tools" / "find_handle_offsets.py"),
                str(captures[0]),
                str(captures[1]),
                str(candidate_offsets),
            ],
            cwd=ROOT,
            timeout=timeout_capture,
        )
        if inf.returncode != 0:
            rows.append(
                {
                    "program": str(prog_rel),
                    "ok": False,
                    "error": f"inference_failed:exit={inf.returncode}",
                    "stderr_tail": (inf.stderr or "")[-2000:],
                }
            )
            all_ok = False
            total_score += -1.0
            continue

        trace_for_replay = captures[1]
        replay_args_base = [
            sys.executable,
            str(ROOT / "replay" / "replay.py"),
        ]
        if verbose:
            replay_args_base.append("-v")
        replay_args_base.append(str(trace_for_replay))

        rb = _run(
            replay_args_base + [str(baseline_offsets)],
            cwd=ROOT,
            timeout=timeout_replay,
        )
        rc = _run(
            replay_args_base + [str(candidate_offsets)],
            cwd=ROOT,
            timeout=timeout_replay,
        )

        b_sum = met.parse_replay_summary(rb.stdout or "")
        c_sum = met.parse_replay_summary(rc.stdout or "")
        cand_json = met.load_handle_offsets(candidate_offsets)
        diff = met.compare_handle_offsets(baseline_json, cand_json)
        gate_ok, gate_reason = met.score_gate(
            candidate_summary=c_sum,
            baseline_summary=b_sum,
            require_zero_failed=True,
            max_skip_regression=int(harness.get("max_skip_regression", 0)),
        )

        asi = met.build_asi(
            program=str(prog_rel),
            replay_stdout=rc.stdout or "",
            replay_stderr=rc.stderr or "",
            baseline_summary=b_sum,
            candidate_summary=c_sum,
            offset_diff=diff,
        )

        # Score: gate first, then agreement (for GEPA)
        if not gate_ok or c_sum is None or (c_sum.failed > 0):
            row_score = -1.0
            ok_row = False
            all_ok = False
        else:
            ok_row = True
            row_score = float(diff.get("handle_offset_agreement_ratio", 0.0))
            if c_sum.skipped > 0:
                row_score *= 0.85

        total_score += row_score
        rows.append(
            {
                "program": str(prog_rel),
                "ok": ok_row and gate_ok,
                "gate": gate_reason,
                "score": row_score,
                "baseline_replay_exit": rb.returncode,
                "candidate_replay_exit": rc.returncode,
                "baseline_summary": b_sum.__dict__ if b_sum else None,
                "candidate_summary": c_sum.__dict__ if c_sum else None,
                "offset_agreement": diff,
                "candidate_offsets": str(candidate_offsets),
                "asi": asi,
            }
        )

    n = len(programs)
    aggregate = total_score / n if n else -1.0
    return {
        "ok": all_ok,
        "run_id": run_id,
        "run_root": str(run_root),
        "aggregate_score": aggregate,
        "programs": rows,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Optimizer live evaluator")
    ap.add_argument(
        "--harness",
        type=Path,
        required=True,
        help="Path to harness.yaml (or .json) or a run directory containing harness.yaml",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate harness file and layout only (no capture/replay)",
    )
    args = ap.parse_args()

    hpath = args.harness.resolve()
    if hpath.is_dir():
        hpath = hpath / "harness.yaml"
        if not hpath.is_file():
            hpath = hpath.parent / "harness.json"

    harness = load_harness_file(hpath)
    # Allow harness to record its path for reproducibility
    harness.setdefault("_harness_path", str(hpath))

    try:
        out = evaluate_harness(harness, dry_run=args.dry_run)
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e), "type": type(e).__name__}))
        sys.exit(1)

    print(json.dumps(out, indent=2))
    sys.exit(0 if out.get("ok") else 1)


if __name__ == "__main__":
    main()
