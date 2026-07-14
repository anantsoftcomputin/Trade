import asyncio
import hashlib
import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Literal

import httpx
import numpy as np
import pandas as pd
import torch
from fastapi import FastAPI, HTTPException
from google.cloud import firestore, storage
from pydantic import BaseModel, Field

from market_data import DataRequest, MarketDataPipeline
from model import Candidate, TemporalAttentionModel, evolve
from research import (
    FEATURES, CostSchedule, approval_gates, backtest, build_feature_frame,
    fetch_yahoo_benchmark, load_artifact, predict, sequence_arrays,
    serialize_artifact, train_network,
)

app = FastAPI(title="ArthAI Research Runner", docs_url=None, redoc_url=None)
db = firestore.Client()
bucket_name = os.getenv("MARKET_DATA_BUCKET", "")
torch.set_num_threads(max(1, int(os.getenv("TORCH_NUM_THREADS", "2"))))
GA_POPULATION = min(24, max(8, int(os.getenv("GA_POPULATION", "8"))))
GA_GENERATIONS = min(20, max(3, int(os.getenv("GA_GENERATIONS", "4"))))
GA_FOLD_EPOCHS = min(10, max(2, int(os.getenv("GA_FOLD_EPOCHS", "2"))))
FINAL_EPOCHS = min(40, max(8, int(os.getenv("FINAL_EPOCHS", "12"))))


class Job(BaseModel):
    jobId: str = Field(pattern=r"^[A-Za-z0-9]{10,40}$")
    ownerId: str
    symbol: str = Field(pattern=r"^[A-Z0-9&-]{1,24}$")
    exchange: Literal["NSE", "BSE"]
    historyYears: int = Field(ge=3, le=30)
    timeframe: Literal["1d", "1h", "15m"]


class SignalRequest(BaseModel):
    ownerId: str
    modelId: str
    capital: float = Field(default=100_000, ge=1_000, le=1_000_000_000)
    riskPct: float = Field(default=1.0, ge=.1, le=5)


def update(job_id: str, **values):
    values["updatedAt"] = datetime.now(timezone.utc)
    db.collection("trainingJobs").document(job_id).update(values)


async def market_frames(symbol: str, exchange: str, years: int) -> tuple[pd.DataFrame, pd.DataFrame, dict, dict]:
    pipeline = MarketDataPipeline(os.getenv("UPSTOX_ANALYTICS_TOKEN", ""), bucket_name)
    dataset = await pipeline.build(DataRequest(symbol, exchange, years, "1d"))
    artifact = await asyncio.to_thread(pipeline.persist, dataset)
    stock = dataset.frame.copy()
    for column in ("open", "high", "low", "close"):
        stock[column] = stock[f"adjusted_{column}"]
    stock["volume"] = stock["adjusted_volume"]
    async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=15), follow_redirects=True) as client:
        benchmark = await fetch_yahoo_benchmark(client, years, "^NSEI" if exchange == "NSE" else "^BSESN")
    return stock, benchmark, dataset.manifest, artifact


def initial_population(feature_count: int, seed: int = 42, size: int = GA_POPULATION) -> list[Candidate]:
    rng = np.random.default_rng(seed)
    result = []
    widths, dropouts, lookbacks = [24, 32, 48], [.1, .16, .22, .3], [30, 45, 60, 90]
    thresholds, stops, targets = [.53, .56, .59, .63], [1.25, 1.75, 2.25, 3.0], [2.0, 3.0, 4.0, 5.5]
    for index in range(size):
        mask = tuple(bool(value) for value in (rng.random(feature_count) > .2))
        result.append(Candidate(
            width=widths[index % len(widths)], dropout=dropouts[index % len(dropouts)],
            lookback=lookbacks[index % len(lookbacks)], threshold=thresholds[index % len(thresholds)],
            stop_atr=stops[index % len(stops)], target_atr=targets[index % len(targets)], feature_mask=mask,
        ))
    return result


def candidate_evaluator(frame: pd.DataFrame, costs: CostSchedule):
    cache: dict[Candidate, dict[str, float]] = {}

    def evaluate(candidate: Candidate) -> dict[str, float]:
        if candidate in cache:
            return cache[candidate]
        x, yd, yr, yv, rows = sequence_arrays(frame, candidate)
        prelock = int(len(x) * .8)
        folds = (
            (int(prelock * .4), int(prelock * .55)),
            (int(prelock * .55), int(prelock * .7)),
            (int(prelock * .7), prelock),
        )
        fold_metrics = []
        stressed_metrics = []
        for fold_number, (train_end, validation_end) in enumerate(folds):
            purged_train_end = max(1, train_end - 5)
            train_x, validation_x, mean, std = _scaled(x[:purged_train_end], x[train_end:validation_end])
            network = train_network(train_x, yd[:purged_train_end], yr[:purged_train_end], yv[:purged_train_end], candidate, epochs=GA_FOLD_EPOCHS, seed=100 + fold_number)
            probability, expected, _ = predict(network, validation_x)
            median = expected[:, 1]
            fold_metrics.append(backtest(frame, rows[train_end:validation_end], probability, candidate, costs, expected_return=median))
            stressed_metrics.append(backtest(frame, rows[train_end:validation_end], probability, candidate, costs, slippage_bps=20, expected_return=median))
        net = np.asarray([item["net_return"] for item in fold_metrics])
        stressed_net = np.asarray([item["net_return"] for item in stressed_metrics])
        result = {
            "net_return": float(net.mean()),
            "profit_factor": float(np.mean([item["profit_factor"] for item in fold_metrics])),
            "profitable_months": float(np.mean([item["profitable_months"] for item in fold_metrics])),
            "max_drawdown": float(np.min([item["max_drawdown"] for item in fold_metrics])),
            "fold_instability": float(net.std()), "slippage_decay": float(np.mean(net - stressed_net)),
            "turnover": float(np.mean([item["turnover"] for item in fold_metrics])),
            "trades": float(np.mean([item["trades"] for item in fold_metrics])),
            "complexity": candidate.width / 100 + candidate.lookback / 500,
        }
        cache[candidate] = result
        return result

    return evaluate


def _scaled(train: np.ndarray, other: np.ndarray):
    mean = train.reshape(-1, train.shape[-1]).mean(axis=0)
    std = train.reshape(-1, train.shape[-1]).std(axis=0)
    std[std < 1e-6] = 1
    return np.clip((train - mean) / std, -10, 10), np.clip((other - mean) / std, -10, 10), mean, std


def persist_model(owner_id: str, model_id: str, payload: bytes, metadata: dict) -> dict[str, str]:
    bucket = storage.Client().bucket(bucket_name)
    prefix = f"model-artifacts/{owner_id}/{model_id}"
    model_path, card_path = f"{prefix}/model.pt", f"{prefix}/model-card.json"
    bucket.blob(model_path).upload_from_string(payload, content_type="application/octet-stream")
    bucket.blob(card_path).upload_from_string(json.dumps(metadata, sort_keys=True, default=str), content_type="application/json")
    return {"model": f"gs://{bucket_name}/{model_path}", "modelCard": f"gs://{bucket_name}/{card_path}"}


def _safe_candidate(candidate: Candidate) -> dict:
    value = asdict(candidate)
    value["feature_mask"] = list(value["feature_mask"])
    return value


def train_research_model(job: Job, frame: pd.DataFrame, benchmark: pd.DataFrame, manifest: dict, dataset_artifact: dict) -> tuple[str, dict]:
    costs = CostSchedule.from_env()
    research = build_feature_frame(frame, benchmark)
    evaluator = candidate_evaluator(research, costs)
    champion, validation = evolve(initial_population(len(FEATURES)), evaluator, generations=GA_GENERATIONS, seed=42)
    x, yd, yr, yv, rows = sequence_arrays(research, champion)
    split = int(len(x) * .8)
    purged_split = split - 5
    x_train, x_test, mean, std = _scaled(x[:purged_split], x[split:])
    model = train_network(x_train, yd[:purged_split], yr[:purged_split], yv[:purged_split], champion, epochs=FINAL_EPOCHS, seed=2026)
    probability, expected, volatility = predict(model, x_test)
    metrics = backtest(research, rows[split:], probability, champion, costs, expected_return=expected[:, 1])
    stressed = backtest(research, rows[split:], probability, champion, costs, slippage_bps=20, expected_return=expected[:, 1])
    first, last = int(rows[split]), int(rows[-1])
    benchmark_return = float(research.benchmark_close.iloc[last] / research.benchmark_close.iloc[first] - 1)
    buy_hold = float(research.close.iloc[last] / research.close.iloc[first] - 1 - costs.round_trip_fraction(float(research.close.iloc[first]), float(research.close.iloc[last])))
    metrics.update({
        "benchmark_return": benchmark_return, "buy_hold_return": buy_hold,
        "excess_return": metrics["net_return"] - buy_hold,
        "slippage_net_return": stressed["net_return"],
        "slippage_decay": metrics["net_return"] - stressed["net_return"],
        "fold_instability": validation["fold_instability"],
        "brier_score": float(np.mean((probability - yd[split:]) ** 2)),
        "testSamples": float(len(x_test)), "trainSamples": float(len(x_train)),
    })
    gates, release = approval_gates(metrics, manifest["qualityStatus"])
    model_ref = db.collection("models").document()
    model_id = model_ref.id
    selected_features = [name for name, enabled in zip(FEATURES, champion.feature_mask) if enabled] or [FEATURES[0]]
    card = {
        "schemaVersion": 1, "modelId": model_id, "ownerId": job.ownerId, "symbol": job.symbol,
        "exchange": job.exchange, "timeframe": job.timeframe, "historyYears": job.historyYears,
        "architecture": "TCN-BiGRU-Attention-MultiHead", "candidate": _safe_candidate(champion),
        "selectedFeatures": selected_features, "metrics": metrics, "gates": gates,
        "releaseStatus": release, "dataQuality": manifest["qualityStatus"], "productionEligible": False,
        "dataset": dataset_artifact, "trainedAt": datetime.now(timezone.utc), "codeVersion": os.getenv("K_REVISION", "local"),
        "costSchedule": asdict(costs), "benchmark": {"symbol": "^NSEI" if job.exchange == "NSE" else "^BSESN", "source": "yahoo_research"},
        "validationWindow": {"from": research.timestamp.iloc[first].isoformat(), "to": research.timestamp.iloc[last].isoformat()},
        "seeds": {"ga": 42, "finalModel": 2026},
        "search": {"population": GA_POPULATION, "generations": GA_GENERATIONS, "folds": 3, "foldEpochs": GA_FOLD_EPOCHS, "finalEpochs": FINAL_EPOCHS, "purgeSessions": 5},
    }
    payload = serialize_artifact(model, champion, mean, std, metrics, selected_features)
    card["artifact"] = persist_model(job.ownerId, model_id, payload, card)
    model_ref.create(card)
    return model_id, card


def _read_gs(uri: str) -> bytes:
    prefix = f"gs://{bucket_name}/"
    if not uri.startswith(prefix):
        raise ValueError("INVALID_MODEL_ARTIFACT")
    return storage.Client().bucket(bucket_name).blob(uri[len(prefix):]).download_as_bytes()


async def create_signal(request: SignalRequest) -> dict:
    model_ref = db.collection("models").document(request.modelId)
    snapshot = model_ref.get()
    if not snapshot.exists:
        raise ValueError("MODEL_NOT_FOUND")
    card = snapshot.to_dict()
    if card.get("ownerId") != request.ownerId:
        raise ValueError("MODEL_NOT_FOUND")
    stock, benchmark, manifest, _ = await market_frames(card["symbol"], card["exchange"], min(int(card["historyYears"]), 8))
    research = build_feature_frame(stock, benchmark, include_targets=False)
    artifact = load_artifact(_read_gs(card["artifact"]["model"]))
    candidate = Candidate(**{**artifact["candidate"], "feature_mask": tuple(artifact["candidate"]["feature_mask"])})
    selected = artifact["feature_names"]
    latest = research[selected].tail(candidate.lookback).to_numpy(np.float32)
    if len(latest) < candidate.lookback:
        raise ValueError("INSUFFICIENT_SIGNAL_HISTORY")
    current = research.iloc[-1]
    entry, atr = float(current.close), float(current.atr)
    approved = card["releaseStatus"] == "paper_approved"
    bullish, median_return = .5, 0.0
    predicted_volatility_value = float(current.atr_pct)
    if not approved:
        action, reason = "ABSTAIN", "Model did not pass every paper-trading approval gate"
    else:
        scaled = ((latest - np.asarray(artifact["mean"])) / np.asarray(artifact["std"]))[None, ...]
        network = TemporalAttentionModel(len(selected), candidate.width, candidate.dropout)
        network.load_state_dict(artifact["state_dict"])
        probability, expected, predicted_volatility = predict(network.eval(), scaled)
        bullish = float(probability[0])
        median_return = float(expected[0, 1])
        predicted_volatility_value = float(predicted_volatility[0])
        if not all(np.isfinite(value) for value in (bullish, median_return, predicted_volatility_value)):
            bullish, median_return, predicted_volatility_value = .5, 0.0, float(current.atr_pct)
            action, reason = "ABSTAIN", "Model inference was numerically unstable"
        elif bullish >= candidate.threshold and median_return > 0:
            action, reason = "BUY", "Direction probability and expected return cleared the model threshold"
        elif bullish <= 1 - candidate.threshold and median_return < 0:
            action, reason = "SELL", "Bearish exit/avoid signal; overnight cash-equity short entry is disabled"
        else:
            action, reason = "HOLD", "The model edge is below its validated entry threshold"
    if action == "SELL":
        stop, target = entry + candidate.stop_atr * atr, max(.01, entry - candidate.target_atr * atr)
    else:
        stop, target = max(.01, entry - candidate.stop_atr * atr), entry + candidate.target_atr * atr
    risk_per_share = abs(entry - stop)
    quantity = int(min(request.capital / entry, request.capital * request.riskPct / 100 / max(risk_per_share, .01))) if action == "BUY" else 0
    confidence = round(max(bullish, 1 - bullish) * 100, 1)
    signal_key = hashlib.sha256(f"{request.ownerId}:{request.modelId}:{current.session}:{request.capital:.2f}:{request.riskPct:.2f}".encode()).hexdigest()[:32]
    signal_ref = db.collection("signals").document(signal_key)
    signal = {
        "ownerId": request.ownerId, "modelId": request.modelId, "symbol": card["symbol"], "exchange": card["exchange"],
        "action": action, "entry": round(entry, 2), "stop": round(stop, 2), "target": round(target, 2),
        "quantity": quantity, "confidence": confidence, "directionProbability": bullish,
        "expectedReturn": median_return, "predictedVolatility": predicted_volatility_value,
        "reason": reason, "dataQuality": manifest["qualityStatus"], "productionEligible": False,
        "asOf": current.timestamp.to_pydatetime(), "createdAt": datetime.now(timezone.utc),
        "evidence": {name: float(current[name]) for name in ("rsi_14", "macd", "relative_strength_20", "volume_z", "regime")},
        "modelVersion": request.modelId, "releaseStatus": card["releaseStatus"],
        "riskAssumptions": {"capital": request.capital, "riskPct": request.riskPct},
    }
    signal_ref.set(signal)
    await asyncio.to_thread(monitor_paper_trades, request.ownerId, card["symbol"], current)
    return {"signalId": signal_ref.id, **signal}


def monitor_paper_trades(owner_id: str, symbol: str, candle: pd.Series):
    trades = db.collection("paperTrades").where("ownerId", "==", owner_id).where("symbol", "==", symbol).where("status", "in", ["planned", "open"]).stream()
    now = datetime.now(timezone.utc)
    for snapshot in trades:
        trade = snapshot.to_dict()
        updates = {"updatedAt": now, "lastPrice": float(candle.close), "lastMarkedAt": now}
        status = trade["status"]
        if status == "planned" and float(candle.high) >= float(trade["entry"]):
            status = "open"
            updates.update({"openedAt": now, "status": status})
        if status == "open":
            if float(candle.low) <= float(trade["stop"]):
                updates.update({"status": "closed", "closedAt": now, "exit": float(trade["stop"]), "outcome": "stop"})
            elif float(candle.high) >= float(trade["target"]):
                updates.update({"status": "closed", "closedAt": now, "exit": float(trade["target"]), "outcome": "target"})
            if updates.get("status") == "closed":
                updates["pnl"] = (updates["exit"] - float(trade["entry"])) * int(trade["quantity"])
        snapshot.reference.update(updates)


@app.post("/")
async def train(job: Job):
    try:
        if job.timeframe != "1d":
            raise ValueError("RESEARCH_TRAINING_SUPPORTS_DAILY_ONLY")
        update(job.jobId, status="running", stage="provider_validation", progress=3)
        frame, benchmark, manifest, dataset = await market_frames(job.symbol, job.exchange, job.historyYears)
        update(job.jobId, stage="feature_engineering", progress=18, observations=len(frame), dataset=dataset, dataQuality=manifest["qualityStatus"], reconciliation=manifest["reconciliation"])
        update(job.jobId, stage="ga_walk_forward", progress=30)
        model_id, card = await asyncio.to_thread(train_research_model, job, frame, benchmark, manifest, dataset)
        update(job.jobId, status="completed", stage="model_registered", progress=100, modelId=model_id, releaseStatus=card["releaseStatus"], metrics=card["metrics"], gates=card["gates"])
        try:
            await create_signal(SignalRequest(ownerId=job.ownerId, modelId=model_id))
        except Exception:
            pass
        return {"accepted": True, "jobId": job.jobId, "status": "completed", "modelId": model_id}
    except Exception as error:
        update(job.jobId, status="failed", stage="training_failed", errorCode=str(error)[:160])
        raise HTTPException(422, "Training failed safely") from error


@app.post("/signal")
async def signal(request: SignalRequest):
    try:
        return await create_signal(request)
    except Exception as error:
        raise HTTPException(422, str(error)[:120]) from error


@app.post("/daily")
async def daily():
    generated = 0
    for snapshot in db.collection("models").where("releaseStatus", "==", "paper_approved").limit(100).stream():
        card = snapshot.to_dict()
        try:
            await create_signal(SignalRequest(ownerId=card["ownerId"], modelId=snapshot.id))
            generated += 1
        except Exception:
            continue
    return {"generated": generated, "asOf": datetime.now(timezone.utc).isoformat()}


@app.get("/health")
def health():
    return {"status": "ok", "engine": "walk-forward-v1"}
