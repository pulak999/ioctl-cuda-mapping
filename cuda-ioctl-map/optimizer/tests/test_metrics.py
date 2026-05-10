"""Unit tests for optimizer/metrics.py (no GPU)."""

from __future__ import annotations

import unittest
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "optimizer"))

import metrics  # type: ignore  # noqa: E402


class TestParseReplaySummary(unittest.TestCase):
    def test_parses_done_line(self) -> None:
        out = "[0000] OK\n\nDONE — 10/10 succeeded, 0 failed, 0 skipped\n"
        s = metrics.parse_replay_summary(out)
        self.assertIsNotNone(s)
        assert s is not None
        self.assertEqual(s.ok, 10)
        self.assertEqual(s.total, 10)
        self.assertEqual(s.failed, 0)
        self.assertEqual(s.skipped, 0)

    def test_missing_returns_none(self) -> None:
        self.assertIsNone(metrics.parse_replay_summary("no summary here"))


class TestCompareHandleOffsets(unittest.TestCase):
    def test_exact_match(self) -> None:
        b = {"0xC0000001": {"handle_offsets": [4, 0]}}
        c = {"0xC0000001": {"handle_offsets": [0, 4]}}
        r = metrics.compare_handle_offsets(b, c)
        self.assertEqual(r["handle_offset_lists_exact_match"], 1)
        self.assertEqual(r["requests_compared"], 1)
        self.assertEqual(r["mismatch_count"], 0)

    def test_mismatch(self) -> None:
        b = {"0xC0000001": {"handle_offsets": [0, 4]}}
        c = {"0xC0000001": {"handle_offsets": [8]}}
        r = metrics.compare_handle_offsets(b, c)
        self.assertEqual(r["handle_offset_lists_exact_match"], 0)
        self.assertGreater(r["mismatch_count"], 0)


class TestScoreGate(unittest.TestCase):
    def test_fails_on_failed_ioctl(self) -> None:
        c = metrics.ReplaySummary(ok=9, total=10, failed=1, skipped=0)
        b = metrics.ReplaySummary(ok=10, total=10, failed=0, skipped=0)
        ok, _reason = metrics.score_gate(
            candidate_summary=c, baseline_summary=b, max_skip_regression=0
        )
        self.assertFalse(ok)

    def test_fails_on_skip_regression(self) -> None:
        c = metrics.ReplaySummary(ok=10, total=10, failed=0, skipped=2)
        b = metrics.ReplaySummary(ok=10, total=10, failed=0, skipped=0)
        ok, _reason = metrics.score_gate(
            candidate_summary=c, baseline_summary=b, max_skip_regression=0
        )
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
