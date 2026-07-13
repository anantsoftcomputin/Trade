import unittest
import numpy as np
import pandas as pd

from model import Candidate, TemporalAttentionModel
from research import CostSchedule, FEATURES, approval_gates, build_feature_frame, load_artifact, serialize_artifact


class ResearchEngineTests(unittest.TestCase):
    def setUp(self):
        count = 420
        timestamps = pd.date_range("2024-01-01", periods=count, freq="B", tz="UTC")
        close = 100 * np.cumprod(1 + np.sin(np.arange(count) / 17) * .002 + .0004)
        self.stock = pd.DataFrame({
            "timestamp": timestamps, "open": close * .999, "high": close * 1.01,
            "low": close * .99, "close": close, "volume": 1_000_000 + np.arange(count) * 100,
        })
        benchmark = 200 * np.cumprod(1 + np.cos(np.arange(count) / 23) * .001 + .0002)
        self.benchmark = pd.DataFrame({"timestamp": timestamps, "benchmark_close": benchmark})

    def test_feature_frame_is_point_aligned(self):
        frame = build_feature_frame(self.stock, self.benchmark)
        self.assertGreater(len(frame), 150)
        self.assertFalse(frame[list(FEATURES)].isna().any().any())
        self.assertTrue(set(frame.regime.unique()).issubset({-1.0, 0.0, 1.0}))

    def test_inference_keeps_latest_rows(self):
        frame = build_feature_frame(self.stock, self.benchmark, include_targets=False)
        self.assertEqual(frame.timestamp.iloc[-1], self.stock.timestamp.iloc[-1])

    def test_cost_schedule_includes_slippage_and_statutory_costs(self):
        costs = CostSchedule()
        normal = costs.round_trip_fraction(100, 105)
        stressed = costs.round_trip_fraction(100, 105, slippage_bps=25)
        self.assertGreater(normal, 0)
        self.assertGreater(stressed, normal)

    def test_approval_never_enables_live_production(self):
        metrics = {"net_return": .2, "max_drawdown": -.1, "profit_factor": 1.5, "trades": 30,
                   "profitable_months": .6, "excess_return": .1, "slippage_net_return": .1,
                   "fold_instability": .1, "brier_score": .2}
        gates, release = approval_gates(metrics, "research_only_verified")
        self.assertEqual(release, "paper_approved")
        self.assertTrue(gates["researchOnly"])

    def test_non_finite_metrics_fail_closed(self):
        metrics = {"net_return": .2, "max_drawdown": -.1, "profit_factor": 1.5, "trades": 30,
                   "profitable_months": .6, "excess_return": .1, "slippage_net_return": .1,
                   "fold_instability": .1, "brier_score": float("nan")}
        gates, release = approval_gates(metrics, "research_only_verified")
        self.assertFalse(gates["numericallyStable"])
        self.assertEqual(release, "rejected")

    def test_model_artifact_round_trip(self):
        candidate = Candidate(24, .2, 30, .55, 1.5, 3.0, tuple([True] * len(FEATURES)))
        model = TemporalAttentionModel(len(FEATURES), candidate.width, candidate.dropout)
        payload = serialize_artifact(model, candidate, np.zeros(len(FEATURES)), np.ones(len(FEATURES)), {"net_return": .1}, list(FEATURES))
        restored = load_artifact(payload)
        self.assertEqual(restored["candidate"]["lookback"], 30)
        self.assertEqual(restored["feature_names"], list(FEATURES))


if __name__ == "__main__":
    unittest.main()
