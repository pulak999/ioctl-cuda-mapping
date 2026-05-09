#!/usr/bin/env python3
"""
Optional GEPA driver: optimizes the harness YAML as a text artifact.

Install dependencies first:
  python3 -m pip install -r optimizer/requirements.txt

Run from cuda-ioctl-map/:
  python3 optimizer/gepa_runner.py --seed optimizer/harness.yaml --max-metric-calls 25
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
from pathlib import Path


def _load_evaluate_module():
    ev_path = Path(__file__).resolve().parent / "evaluate.py"
    mod_name = "ioctl_cuda_optimizer_evaluate"
    spec = importlib.util.spec_from_file_location(mod_name, ev_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load evaluate.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> None:
    ap = argparse.ArgumentParser(description="GEPA optimize_anything driver for harness.yaml")
    ap.add_argument("--seed", type=Path, required=True, help="Initial harness.yaml")
    ap.add_argument("--max-metric-calls", type=int, default=30)
    ap.add_argument(
        "--objective",
        default=(
            "Improve the YAML harness for CUDA ioctl capture/replay evaluation: "
            "choose programs from the ladder that maximize aggregate_score while "
            "keeping lists short and realistic. Output only valid YAML."
        ),
    )
    args = ap.parse_args()

    try:
        from gepa.optimize_anything import (  # type: ignore
            GEPAConfig,
            EngineConfig,
            optimize_anything,
        )
    except ImportError as e:
        print(
            "Missing dependency `gepa`. Install:\n"
            "  python3 -m pip install -r optimizer/requirements.txt",
            file=sys.stderr,
        )
        raise SystemExit(1) from e

    seed_text = Path(args.seed).read_text(encoding="utf-8")
    ev = _load_evaluate_module()

    def evaluator(candidate: str) -> tuple[float, dict]:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".yaml",
            delete=False,
            encoding="utf-8",
        ) as tf:
            tf.write(candidate)
            tmp = Path(tf.name)
        try:
            harness = ev.load_harness_file(tmp)
            metrics = ev.evaluate_harness(harness, dry_run=False)
        except Exception as ex:
            return -1.0, {"error": str(ex), "type": type(ex).__name__}
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass

        score = float(metrics.get("aggregate_score", -1.0))
        if not metrics.get("ok", False):
            score = min(score, -0.5)
        return score, {"metrics": metrics}

    result = optimize_anything(
        seed_candidate=seed_text,
        evaluator=evaluator,
        objective=args.objective,
        config=GEPAConfig(engine=EngineConfig(max_metric_calls=args.max_metric_calls)),
    )

    best = getattr(result, "best_candidate", None)
    if best is None and isinstance(result, dict):
        best = result.get("best_candidate")
    print(json.dumps({"best_candidate": best}, indent=2))


if __name__ == "__main__":
    main()
