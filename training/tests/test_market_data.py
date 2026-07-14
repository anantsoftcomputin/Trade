import unittest

import pandas as pd

from market_data import DataRequest, UpstoxProvider, _share_multiplier, adjust_for_share_actions, validate_ohlcv


def frame(rows=800):
    timestamps = pd.date_range("2020-01-01", periods=rows, freq="B", tz="UTC")
    return pd.DataFrame({
        "timestamp": timestamps,
        "open": [100.0] * rows,
        "high": [102.0] * rows,
        "low": [99.0] * rows,
        "close": [101.0] * rows,
        "volume": [1000] * rows,
    })


class MarketDataTests(unittest.TestCase):
    def test_validates_clean_daily_history(self):
        self.assertEqual(validate_ohlcv(frame(), "1d"), [])

    def test_detects_ohlc_invariant(self):
        broken = frame()
        broken.loc[0, "high"] = 90
        self.assertIn("high_price_invariant", validate_ohlcv(broken, "1d"))

    def test_bonus_adjustment(self):
        action = {"purpose": "Bonus", "ratio": "1:1", "expiry_date": "2022-01-03"}
        adjusted, unresolved = adjust_for_share_actions(frame(), [action])
        self.assertEqual(unresolved, [])
        self.assertEqual(_share_multiplier(action), 2.0)
        self.assertEqual(adjusted.iloc[0].adjusted_close, 50.5)

    def test_split_face_value_adjustment(self):
        action = {"purpose": "Face Value Split (Sub-Division) - From Rs 10 Per Share To Rs 2 Per Share", "expiry_date": "2022-01-03"}
        self.assertEqual(_share_multiplier(action), 5.0)

    def test_intraday_windows_respect_upstox_availability(self):
        windows = UpstoxProvider._windows(DataRequest("RELIANCE", "NSE", 8, "15m"))
        self.assertGreaterEqual(windows[0][0], pd.Timestamp("2022-01-01").date())

    def test_upstox_benchmark_keys_are_supported(self):
        self.assertEqual(("NIFTY 50", "NSE_INDEX|Nifty 50"), UpstoxProvider.benchmark_instrument("NSE"))
        self.assertEqual(("SENSEX", "BSE_INDEX|SENSEX"), UpstoxProvider.benchmark_instrument("BSE"))


if __name__ == "__main__":
    unittest.main()
