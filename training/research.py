from __future__ import annotations

import io
import math
import os
import random
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
import numpy as np
import pandas as pd
import torch
from torch import nn
from model import Candidate, TemporalAttentionModel


FEATURES = (
    "return_1", "return_5", "return_20", "volatility_10", "volatility_20",
    "range", "gap", "volume_z", "volume_change", "ma_10", "ma_20", "ma_50",
    "rsi_14", "macd", "macd_signal", "atr_pct", "benchmark_return_1",
    "benchmark_return_20", "relative_strength_20", "benchmark_ma_50", "regime",
)


@dataclass(frozen=True)
class CostSchedule:
    """Configurable Indian cash-equity approximation, expressed in fractions."""

    brokerage_rate: float = 0.025
    brokerage_cap: float = 20.0
    stt_buy: float = 0.001
    stt_sell: float = 0.001
    exchange_rate: float = 0.0000307
    sebi_rate: float = 0.000001
    stamp_buy: float = 0.00015
    gst_rate: float = 0.18
    slippage_bps: float = 5.0
    dp_sell: float = 20.0
    notional: float = 100_000.0

    @classmethod
    def from_env(cls) -> "CostSchedule":
        values: dict[str, float] = {}
        for field in cls.__dataclass_fields__:
            key = f"COST_{field.upper()}"
            if key in os.environ:
                values[field] = float(os.environ[key])
        return cls(**values)

    def round_trip_fraction(self, entry: float, exit_price: float, quantity: int | None = None, slippage_bps: float | None = None) -> float:
        quantity = quantity or max(1, int(self.notional / max(entry, 1e-9)))
        buy, sell = entry * quantity, exit_price * quantity
        brokerage_buy = min(buy * self.brokerage_rate, self.brokerage_cap) if self.brokerage_cap else buy * self.brokerage_rate
        brokerage_sell = min(sell * self.brokerage_rate, self.brokerage_cap) if self.brokerage_cap else sell * self.brokerage_rate
        exchange = (buy + sell) * self.exchange_rate
        sebi = (buy + sell) * self.sebi_rate
        gst = (brokerage_buy + brokerage_sell + exchange + sebi + self.dp_sell) * self.gst_rate
        statutory = brokerage_buy + brokerage_sell + self.dp_sell + buy * self.stt_buy + sell * self.stt_sell + buy * self.stamp_buy + exchange + sebi + gst
        slip = (buy + sell) * ((self.slippage_bps if slippage_bps is None else slippage_bps) / 10_000)
        return (statutory + slip) / max(buy, 1e-9)


async def fetch_yahoo_benchmark(client: httpx.AsyncClient, years: int, ticker: str = "^NSEI") -> pd.DataFrame:
    now = datetime.now(timezone.utc)
    start = now.replace(year=max(1971, now.year - years - 1))
    response = await client.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
        params={"period1": int(start.timestamp()), "period2": int(now.timestamp()), "interval": "1d", "events": "div,splits"},
        headers={"User-Agent": "Mozilla/5.0 ArthAIResearch/1.0"},
    )
    response.raise_for_status()
    result = response.json()["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]
    adjusted = result.get("indicators", {}).get("adjclose", [{}])[0].get("adjclose", quote["close"])
    frame = pd.DataFrame({
        "timestamp": pd.to_datetime(result["timestamp"], unit="s", utc=True),
        "benchmark_close": adjusted,
        "benchmark_volume": quote.get("volume", [0] * len(adjusted)),
    }).dropna(subset=["benchmark_close"]).sort_values("timestamp")
    return frame.drop_duplicates("timestamp", keep="last").reset_index(drop=True)


def _rsi(close: pd.Series, window: int = 14) -> pd.Series:
    change = close.diff()
    gain = change.clip(lower=0).ewm(alpha=1 / window, adjust=False).mean()
    loss = -change.clip(upper=0).ewm(alpha=1 / window, adjust=False).mean()
    return 100 - 100 / (1 + gain / loss.replace(0, np.nan))


def build_feature_frame(stock: pd.DataFrame, benchmark: pd.DataFrame, horizon: int = 5, include_targets: bool = True) -> pd.DataFrame:
    frame = stock.copy().sort_values("timestamp")
    frame["session"] = pd.to_datetime(frame["timestamp"], utc=True).dt.date
    bench = benchmark.copy()
    bench["session"] = pd.to_datetime(bench["timestamp"], utc=True).dt.date
    frame = frame.merge(bench[["session", "benchmark_close"]], on="session", how="left")
    frame["benchmark_close"] = frame["benchmark_close"].ffill()
    close, volume = frame["close"], frame["volume"]
    returns = close.pct_change()
    frame["return_1"] = returns
    frame["return_5"] = close.pct_change(5)
    frame["return_20"] = close.pct_change(20)
    frame["volatility_10"] = returns.rolling(10).std()
    frame["volatility_20"] = returns.rolling(20).std()
    frame["range"] = (frame["high"] - frame["low"]) / close
    frame["gap"] = frame["open"] / close.shift(1) - 1
    frame["volume_z"] = (volume - volume.rolling(20).mean()) / volume.rolling(20).std()
    frame["volume_change"] = volume.pct_change().clip(-5, 5)
    for window in (10, 20, 50):
        frame[f"ma_{window}"] = close / close.rolling(window).mean() - 1
    frame["rsi_14"] = (_rsi(close) - 50) / 50
    ema12, ema26 = close.ewm(span=12, adjust=False).mean(), close.ewm(span=26, adjust=False).mean()
    macd = (ema12 - ema26) / close
    frame["macd"] = macd
    frame["macd_signal"] = macd - macd.ewm(span=9, adjust=False).mean()
    previous = close.shift(1)
    true_range = pd.concat([(frame.high - frame.low), (frame.high - previous).abs(), (frame.low - previous).abs()], axis=1).max(axis=1)
    frame["atr"] = true_range.rolling(14).mean()
    frame["atr_pct"] = frame["atr"] / close
    benchmark_return = frame["benchmark_close"].pct_change()
    frame["benchmark_return_1"] = benchmark_return
    frame["benchmark_return_20"] = frame["benchmark_close"].pct_change(20)
    frame["relative_strength_20"] = frame["return_20"] - frame["benchmark_return_20"]
    frame["benchmark_ma_50"] = frame["benchmark_close"] / frame["benchmark_close"].rolling(50).mean() - 1
    benchmark_ma_200 = frame["benchmark_close"].rolling(200).mean()
    frame["regime"] = np.where(frame["benchmark_close"] > benchmark_ma_200 * 1.02, 1.0, np.where(frame["benchmark_close"] < benchmark_ma_200 * .98, -1.0, 0.0))
    frame["target_return"] = close.shift(-horizon) / close - 1
    frame["target_direction"] = (frame["target_return"] > 0).astype(float)
    frame["target_volatility"] = returns.shift(-1).rolling(horizon).std().shift(-(horizon - 1)).abs()
    required = [*FEATURES, "target_return", "target_volatility"] if include_targets else [*FEATURES]
    frame = frame.replace([np.inf, -np.inf], np.nan).dropna(subset=required).reset_index(drop=True)
    return frame


def sequence_arrays(frame: pd.DataFrame, candidate: Candidate) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    selected = [name for name, enabled in zip(FEATURES, candidate.feature_mask) if enabled]
    if not selected:
        selected = [FEATURES[0]]
    values = frame[selected].to_numpy(np.float32)
    xs, directions, returns, volatility, rows = [], [], [], [], []
    for index in range(candidate.lookback - 1, len(frame)):
        xs.append(values[index - candidate.lookback + 1:index + 1])
        directions.append(frame.target_direction.iloc[index])
        returns.append(frame.target_return.iloc[index])
        volatility.append(frame.target_volatility.iloc[index])
        rows.append(index)
    return np.asarray(xs), np.asarray(directions, np.float32), np.asarray(returns, np.float32), np.asarray(volatility, np.float32), np.asarray(rows)


def _scale(train: np.ndarray, other: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    mean = train.reshape(-1, train.shape[-1]).mean(axis=0)
    std = train.reshape(-1, train.shape[-1]).std(axis=0)
    std[std < 1e-6] = 1
    return (train - mean) / std, (other - mean) / std, mean, std


def _pinball(prediction: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
    quantiles = torch.tensor([.2, .5, .8], device=prediction.device)
    error = target[:, None] - prediction
    return torch.maximum(quantiles * error, (quantiles - 1) * error).mean()


def train_network(x_train: np.ndarray, y_direction: np.ndarray, y_return: np.ndarray, y_volatility: np.ndarray, candidate: Candidate, epochs: int, seed: int) -> TemporalAttentionModel:
    torch.manual_seed(seed)
    np.random.seed(seed)
    random.seed(seed)
    model = TemporalAttentionModel(x_train.shape[-1], candidate.width, candidate.dropout)
    optimizer = torch.optim.AdamW(model.parameters(), lr=8e-4, weight_decay=1e-4)
    safe_x = np.clip(np.nan_to_num(x_train, nan=0.0, posinf=10.0, neginf=-10.0), -10, 10)
    x = torch.from_numpy(safe_x.astype(np.float32))
    yd = torch.from_numpy(y_direction.astype(np.float32))
    yr = torch.from_numpy(np.clip(y_return, -.5, .5).astype(np.float32))
    yv = torch.from_numpy(np.clip(y_volatility, 0, .25).astype(np.float32))
    model.train()
    for _ in range(epochs):
        order = torch.randperm(len(x))
        for start in range(0, len(x), 128):
            batch = order[start:start + 128]
            direction, returns, volatility = model(x[batch])
            loss = nn.functional.binary_cross_entropy_with_logits(direction[:, 0], yd[batch])
            loss = loss + _pinball(returns, yr[batch]) * 8 + nn.functional.smooth_l1_loss(volatility[:, 0], yv[batch]) * 4
            if not torch.isfinite(loss):
                raise ValueError("NON_FINITE_TRAINING_LOSS")
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
    return model.eval()


def predict(model: TemporalAttentionModel, x: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    with torch.inference_mode():
        safe_x = np.clip(np.nan_to_num(x, nan=0.0, posinf=10.0, neginf=-10.0), -10, 10)
        direction, returns, volatility = model(torch.from_numpy(safe_x.astype(np.float32)))
    return torch.sigmoid(direction[:, 0]).numpy(), np.sort(returns.numpy(), axis=1), volatility[:, 0].numpy()


def backtest(frame: pd.DataFrame, rows: np.ndarray, probability: np.ndarray, candidate: Candidate, costs: CostSchedule, horizon: int = 5, slippage_bps: float | None = None, expected_return: np.ndarray | None = None) -> dict[str, float]:
    trades: list[dict[str, Any]] = []
    next_allowed = -1
    expected_values = expected_return if expected_return is not None else np.ones(len(probability), dtype=float)
    for row, prob, edge in zip(rows, probability, expected_values):
        if row < next_allowed or prob < candidate.threshold or edge <= 0 or row + horizon >= len(frame):
            continue
        entry = float(frame.close.iloc[row])
        atr = float(frame.atr.iloc[row])
        stop, target = entry - candidate.stop_atr * atr, entry + candidate.target_atr * atr
        exit_price, exit_row = float(frame.close.iloc[row + horizon]), row + horizon
        for cursor in range(row + 1, row + horizon + 1):
            high, low = float(frame.high.iloc[cursor]), float(frame.low.iloc[cursor])
            if low <= stop:
                exit_price, exit_row = stop, cursor
                break
            if high >= target:
                exit_price, exit_row = target, cursor
                break
        gross = exit_price / entry - 1
        net = gross - costs.round_trip_fraction(entry, exit_price, slippage_bps=slippage_bps)
        trades.append({"return": net, "gross": gross, "month": str(frame.timestamp.iloc[exit_row])[:7], "regime": int(frame.regime.iloc[row])})
        next_allowed = exit_row + 1
    returns = np.asarray([trade["return"] for trade in trades], dtype=float)
    if len(returns) == 0:
        return {"net_return": -1.0, "max_drawdown": -1.0, "profit_factor": 0.0, "profitable_months": 0.0, "trades": 0.0, "win_rate": 0.0, "average_win": 0.0, "average_loss": 0.0, "payoff_ratio": 0.0, "worst_loss_streak": 0.0, "turnover": 0.0}
    equity = np.cumprod(1 + returns)
    drawdown = equity / np.maximum.accumulate(equity) - 1
    wins, losses = returns[returns > 0], returns[returns <= 0]
    month_returns: dict[str, float] = {}
    for trade in trades:
        month_returns[trade["month"]] = (1 + month_returns.get(trade["month"], 0)) * (1 + trade["return"]) - 1
    streak = worst = 0
    for value in returns:
        streak = streak + 1 if value <= 0 else 0
        worst = max(worst, streak)
    result = {
        "net_return": float(equity[-1] - 1), "max_drawdown": float(drawdown.min()),
        "profit_factor": float(wins.sum() / abs(losses.sum())) if losses.sum() else 9.0,
        "profitable_months": float(np.mean(np.asarray(list(month_returns.values())) > 0)), "trades": float(len(returns)),
        "win_rate": float(np.mean(returns > 0)), "average_win": float(wins.mean()) if len(wins) else 0.0,
        "average_loss": float(losses.mean()) if len(losses) else 0.0,
        "payoff_ratio": float(wins.mean() / abs(losses.mean())) if len(wins) and len(losses) else 0.0,
        "worst_loss_streak": float(worst), "turnover": float(len(returns) * 2),
    }
    for label, regime in (("bull", 1), ("sideways", 0), ("bear", -1)):
        subset = [trade["return"] for trade in trades if trade["regime"] == regime]
        result[f"{label}_return"] = float(np.prod(np.asarray(subset) + 1) - 1) if subset else 0.0
        result[f"{label}_trades"] = float(len(subset))
    return result


def approval_gates(metrics: dict[str, float], data_quality: str) -> tuple[dict[str, bool], str]:
    stability_inputs = (
        "net_return", "max_drawdown", "profit_factor", "profitable_months",
        "excess_return", "slippage_net_return", "fold_instability", "brier_score",
    )
    gates = {
        "numericallyStable": all(np.isfinite(float(metrics.get(name, np.nan))) for name in stability_inputs),
        "positiveNetReturn": metrics["net_return"] > 0,
        "drawdownWithinLimit": metrics["max_drawdown"] >= -.25,
        "profitFactor": metrics["profit_factor"] >= 1.15,
        "minimumTrades": metrics["trades"] >= 20,
        "profitableMonths": metrics["profitable_months"] >= .5,
        "beatsBuyAndHold": metrics.get("excess_return", -1) > 0,
        "slippageResilient": metrics.get("slippage_net_return", -1) > 0,
        "foldStability": metrics.get("fold_instability", 1) <= .35,
        "researchOnly": data_quality.startswith("research_only_"),
    }
    quantitative = [value for key, value in gates.items() if key != "researchOnly"]
    return gates, "paper_approved" if all(quantitative) else "rejected"


def serialize_artifact(model: TemporalAttentionModel, candidate: Candidate, mean: np.ndarray, std: np.ndarray, metrics: dict[str, float], feature_names: list[str]) -> bytes:
    buffer = io.BytesIO()
    torch.save({"state_dict": model.state_dict(), "candidate": asdict(candidate), "mean": mean, "std": std, "metrics": metrics, "feature_names": feature_names, "savedAt": datetime.now(timezone.utc).isoformat()}, buffer)
    return buffer.getvalue()


def load_artifact(payload: bytes) -> dict[str, Any]:
    return torch.load(io.BytesIO(payload), map_location="cpu", weights_only=False)
