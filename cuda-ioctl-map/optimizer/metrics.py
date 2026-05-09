"""
Parse replay stdout and compare handle_offsets.json candidates to a baseline.
Used by optimizer/evaluate.py and unit tests.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Example: DONE — 781/781 succeeded, 0 failed, 0 skipped
_DONE_RE = re.compile(
    r"DONE\s+—\s+(\d+)/(\d+)\s+succeeded,\s+(\d+)\s+failed,\s+(\d+)\s+skipped",
    re.MULTILINE,
)


@dataclass
class ReplaySummary:
    ok: int
    total: int
    failed: int
    skipped: int

    @property
    def success_ratio(self) -> float:
        if self.total <= 0:
            return 0.0
        return self.ok / self.total


def parse_replay_summary(stdout: str) -> ReplaySummary | None:
    m = _DONE_RE.search(stdout)
    if not m:
        return None
    ok, total, failed, skipped = map(int, m.groups())
    return ReplaySummary(ok=ok, total=total, failed=failed, skipped=skipped)


def load_handle_offsets(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _sorted_offsets(entry: dict[str, Any]) -> list[int]:
    return sorted(int(x) for x in entry.get("handle_offsets", []) if x is not None)


def compare_handle_offsets(
    baseline: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, Any]:
    """
    Per-request agreement on handle_offsets list (order-insensitive).
    Returns summary suitable for metrics / ASI.
    """
    all_reqs = sorted(set(baseline.keys()) | set(candidate.keys()), key=lambda s: int(s, 16))
    per_req: list[dict[str, Any]] = []
    exact = 0
    both = 0
    for req in all_reqs:
        b = baseline.get(req, {})
        c = candidate.get(req, {})
        bo = _sorted_offsets(b) if isinstance(b, dict) else []
        co = _sorted_offsets(c) if isinstance(c, dict) else []
        if req in baseline and req in candidate:
            both += 1
            if bo == co:
                exact += 1
            else:
                per_req.append(
                    {
                        "req": req,
                        "baseline": bo,
                        "candidate": co,
                        "only_baseline": sorted(set(bo) - set(co)),
                        "only_candidate": sorted(set(co) - set(bo)),
                    }
                )
        elif req in baseline and req not in candidate:
            per_req.append(
                {
                    "req": req,
                    "baseline": bo,
                    "candidate": [],
                    "only_baseline": bo,
                    "only_candidate": [],
                }
            )
        elif req in candidate and req not in baseline:
            per_req.append(
                {
                    "req": req,
                    "baseline": [],
                    "candidate": co,
                    "only_baseline": [],
                    "only_candidate": co,
                }
            )

    agreement_ratio = exact / both if both else 1.0
    return {
        "requests_compared": both,
        "handle_offset_lists_exact_match": exact,
        "handle_offset_agreement_ratio": agreement_ratio,
        "mismatches": per_req[:50],
        "mismatch_count": len(per_req),
    }


def build_asi(
    *,
    program: str,
    replay_stdout: str,
    replay_stderr: str,
    baseline_summary: ReplaySummary | None,
    candidate_summary: ReplaySummary | None,
    offset_diff: dict[str, Any],
) -> dict[str, Any]:
    return {
        "program": program,
        "replay_tail_stdout": replay_stdout[-8000:] if replay_stdout else "",
        "replay_tail_stderr": replay_stderr[-4000:] if replay_stderr else "",
        "baseline_replay_summary": (
            baseline_summary.__dict__ if baseline_summary else None
        ),
        "candidate_replay_summary": (
            candidate_summary.__dict__ if candidate_summary else None
        ),
        "offset_diff": offset_diff,
    }


def score_gate(
    *,
    candidate_summary: ReplaySummary | None,
    baseline_summary: ReplaySummary | None,
    require_zero_failed: bool = True,
    max_skip_regression: int = 0,
) -> tuple[bool, str]:
    if candidate_summary is None:
        return False, "could_not_parse_candidate_replay_summary"
    if require_zero_failed and candidate_summary.failed > 0:
        return False, "candidate_replay_has_failures"
    if baseline_summary is not None and candidate_summary is not None:
        extra_skips = candidate_summary.skipped - baseline_summary.skipped
        if extra_skips > max_skip_regression:
            return False, f"skip_regression:baseline_skips={baseline_summary.skipped},candidate_skips={candidate_summary.skipped}"
    return True, "ok"
