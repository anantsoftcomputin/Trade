import unittest
import numpy as np
import pandas as pd

from model import Candidate, TemporalAttentionModel, evolve
from research import CostSchedule, FEATURES, approval_gates, backtest, build_feature_frame, load_artifact, predict, serialize_artifact, train_network


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

    def test_upstox_midnight_timestamp_joins_same_indian_session(self):
        sessions = pd.date_range("2024-01-01", periods=420, freq="B", tz="Asia/Kolkata")
        stock = self.stock.copy()
        stock["timestamp"] = sessions.tz_convert("UTC")
        benchmark = self.benchmark.copy()
        benchmark["timestamp"] = (sessions + pd.Timedelta(hours=9, minutes=15)).tz_convert("UTC")
        frame = build_feature_frame(stock, benchmark, include_targets=False)
        expected = benchmark.assign(session=sessions.date).set_index("session").benchmark_close
        self.assertEqual(frame.benchmark_close.iloc[-1], expected.loc[frame.session.iloc[-1]])

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

    def test_backtest_requires_positive_expected_return_and_does_not_short_cash_equity(self):
        count = 40
        close = np.linspace(120, 80, count)
        frame = pd.DataFrame({
            "timestamp": pd.date_range("2025-01-01", periods=count, freq="B", tz="UTC"),
            "close": close, "high": close + .4, "low": close - .4,
            "atr": np.ones(count), "regime": np.full(count, -1),
        })
        candidate = Candidate(24, .2, 10, .6, 2.0, 3.0, tuple([True] * len(FEATURES)))
        rows = np.arange(10, 34)
        bearish = backtest(frame, rows, np.full(len(rows), .2), candidate, CostSchedule(), expected_return=np.full(len(rows), -.02))
        mismatched = backtest(frame, rows, np.full(len(rows), .8), candidate, CostSchedule(), expected_return=np.full(len(rows), -.02))
        accepted = backtest(frame, rows, np.full(len(rows), .8), candidate, CostSchedule(), expected_return=np.full(len(rows), .02))
        self.assertEqual(bearish["trades"], 0)
        self.assertEqual(mismatched["trades"], 0)
        self.assertGreater(accepted["trades"], 0)

    def test_ga_mutates_feature_masks(self):
        base = Candidate(24, .2, 30, .55, 1.5, 3.0, tuple([True] * len(FEATURES)))
        seen = set()

        def evaluate(candidate):
            seen.add(candidate.feature_mask)
            return {"net_return": .1, "profit_factor": 1.3, "profitable_months": .6,
                    "max_drawdown": -.1, "fold_instability": .05, "slippage_decay": .01,
                    "turnover": 20, "trades": 25, "complexity": .2}

        evolve([base] * 8, evaluate, generations=4, seed=42)
        self.assertTrue(any(mask != base.feature_mask for mask in seen))

    def test_network_sanitizes_non_finite_training_values(self):
        candidate = Candidate(24, .1, 5, .55, 1.5, 3.0, (True, True, True))
        x = np.zeros((16, 5, 3), dtype=np.float32)
        x[0, 0] = [np.nan, np.inf, -np.inf]
        direction = np.asarray([0, 1] * 8, dtype=np.float32)
        returns = np.linspace(-.1, .1, 16, dtype=np.float32)
        volatility = np.full(16, .02, dtype=np.float32)
        returns[0], volatility[1] = np.nan, np.inf
        model = train_network(x, direction, returns, volatility, candidate, epochs=1, seed=7)
        probability, expected, predicted_volatility = predict(model, x)
        self.assertTrue(np.isfinite(probability).all())
        self.assertTrue(np.isfinite(expected).all())
        self.assertTrue(np.isfinite(predicted_volatility).all())


if __name__ == "__main__":
    unittest.main()
