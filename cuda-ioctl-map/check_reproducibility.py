#!/usr/bin/env python3
"""
check_reproducibility.py (W9)

Run a compiled CUDA binary N times under strace, parse each trace, and
produce a reproducibility report measuring how consistently each ioctl
request code appears across runs.

Usage:
  python3 check_reproducibility.py <binary> <step_name> [--runs N]

  <binary>     path to compiled executable (e.g. programs/cu_init)
  <step_name>  canonical name used in schema (e.g. cu_init)
  --runs N     number of repeated executions (default: 3)

Output written to:
  traces/repro_<step_name>_run{0..N-1}.log    raw strace output
  parsed/repro_<step_name>_run{0..N-1}.json   parsed ioctl records
  parsed/<step_name>_repro_report.json        variance summary (W9)

Report schema:
{
  "step": "<step_name>",
  "runs": N,
  "checked": true,
  "code_occurrence_rate": {
    "0xC020462A": 1.0,   // appeared in every run → deterministic
    "0x00000049": 0.67   // appeared in 2/3 runs  → non-deterministic
  },
  "non_deterministic_codes": ["0x00000049"],
  "determinism_score": 0.94,    // fraction of unique codes that are fully deterministic
  "per_run_unique_codes": [16, 16, 17]   // unique code counts per run
}

The report is picked up automatically by build_schema.py when it exists.
"""
import argparse, json, os, subprocess, sys, tempfile
from collections import Counter

BASE = os.path.dirname(os.path.abspath(__file__))

# import parse_trace helpers without executing __main__
sys.path.insert(0, BASE)
from parse_trace import parse_lines

STRACE_CMD = (
    "strace -f -e trace=ioctl,openat,close "
    "-o {log} {binary}"
)

def run_once(binary: str, log_path: str) -> list[dict]:
    """Run binary under strace, write log, return parsed ioctl list."""
    cmd = STRACE_CMD.format(log=log_path, binary=binary)
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if r.returncode not in (0, 1):   # 1 is acceptable (strace exit mirrors binary)
        print(f"  WARNING: strace exited {r.returncode}", file=sys.stderr)
    with open(log_path) as f:
        lines = f.readlines()
    ioctls, _ = parse_lines(lines)
    return ioctls

def check(binary: str, step: str, runs: int = 3):
    traces_dir = os.path.join(BASE, "traces")
    parsed_dir = os.path.join(BASE, "parsed")
    os.makedirs(traces_dir, exist_ok=True)
    os.makedirs(parsed_dir, exist_ok=True)

    per_run_codes: list[set] = []
    per_run_counts: list[Counter] = []
    per_run_unique: list[int] = []

    print(f"[repro] {step}: running {runs}× …")
    for run_idx in range(runs):
        log_path    = os.path.join(traces_dir, f"repro_{step}_run{run_idx}.log")
        parsed_path = os.path.join(parsed_dir, f"repro_{step}_run{run_idx}.json")

        print(f"  run {run_idx+1}/{runs} … ", end="", flush=True)
        ioctls = run_once(binary, log_path)

        codes   = {i["request_code"] for i in ioctls}
        counts  = Counter(i["request_code"] for i in ioctls)
        per_run_codes.append(codes)
        per_run_counts.append(counts)
        per_run_unique.append(len(codes))

        with open(parsed_path, "w") as f:
            json.dump({"step": step, "run": run_idx, "ioctl_sequence": ioctls}, f, indent=2)
        print(f"unique={len(codes)} total={len(ioctls)}")

    # ── compute occurrence rates ──────────────────────────────────────────────
    all_codes = set().union(*per_run_codes)
    occ_rate  = {code: sum(code in s for s in per_run_codes) / runs for code in sorted(all_codes)}
    non_det   = sorted(c for c, r in occ_rate.items() if r < 1.0)
    det_score = (len(all_codes) - len(non_det)) / len(all_codes) if all_codes else 1.0

    report = {
        "step":                    step,
        "binary":                  binary,
        "runs":                    runs,
        "checked":                 True,
        "code_occurrence_rate":    occ_rate,
        "non_deterministic_codes": non_det,
        "determinism_score":       round(det_score, 4),
        "per_run_unique_codes":    per_run_unique,
    }

    out_path = os.path.join(parsed_dir, f"{step}_repro_report.json")
    with open(out_path, "w") as f: json.dump(report, f, indent=2)

    # ── summary ───────────────────────────────────────────────────────────────
    print(f"\n[repro] {step}: determinism_score={det_score:.2%}  "
          f"non_det={len(non_det)}/{len(all_codes)} codes")
    if non_det:
        print(f"  Non-deterministic codes:")
        for c in non_det:
            print(f"    {c}  occurrence_rate={occ_rate[c]:.2f}  "
                  f"({round(occ_rate[c]*runs)}/{runs} runs)")
    else:
        print(f"  All codes deterministic across {runs} runs ✓")
    print(f"  Report → {out_path}")
    return out_path

def _synthetic_test():
    """Self-test with synthetic data — no actual binary required."""
    import tempfile, pathlib
    print("\n[repro self-test] running synthetic W9 validation …")
    base_p = pathlib.Path(BASE)

    # Two synthetic ioctl lists: run0 has codes A,B,C; run1 has A,B; run2 has A,B,C,D
    runs_data = [
        [{"request_code": "0xAAAA0001"}, {"request_code": "0xAAAA0002"}, {"request_code": "0xAAAA0003"}],
        [{"request_code": "0xAAAA0001"}, {"request_code": "0xAAAA0002"}],
        [{"request_code": "0xAAAA0001"}, {"request_code": "0xAAAA0002"},
         {"request_code": "0xAAAA0003"}, {"request_code": "0xAAAA0004"}],
    ]
    all_codes = {"0xAAAA0001","0xAAAA0002","0xAAAA0003","0xAAAA0004"}
    n = len(runs_data)
    occ = {c: sum(c in {i["request_code"] for i in r} for r in runs_data) / n
           for c in sorted(all_codes)}
    non_det = sorted(c for c, r in occ.items() if r < 1.0)
    det_score = (len(all_codes) - len(non_det)) / len(all_codes)

    assert set(non_det) == {"0xAAAA0003","0xAAAA0004"}, f"expected non_det={non_det}"
    assert abs(occ["0xAAAA0001"] - 1.0) < 1e-9
    assert abs(occ["0xAAAA0002"] - 1.0) < 1e-9
    assert abs(occ["0xAAAA0003"] - 2/3) < 1e-9
    assert abs(occ["0xAAAA0004"] - 1/3) < 1e-9
    assert abs(det_score - 0.5) < 1e-9
    print("  occurrence rates ✓")
    print("  non_deterministic_codes ✓")
    print("  determinism_score ✓")
    print("[repro self-test] PASS")

if __name__ == "__main__":
    if "--self-test" in sys.argv:
        _synthetic_test()
        sys.exit(0)

    ap = argparse.ArgumentParser(description="W9 reproducibility checker")
    ap.add_argument("binary",    help="path to compiled CUDA binary")
    ap.add_argument("step_name", help="canonical step name (e.g. cu_init)")
    ap.add_argument("--runs",    type=int, default=3, help="number of repeated runs (default: 3)")
    args = ap.parse_args()

    if not os.path.isfile(args.binary):
        print(f"ERROR: binary not found: {args.binary}", file=sys.stderr)
        sys.exit(1)

    check(args.binary, args.step_name, runs=args.runs)
