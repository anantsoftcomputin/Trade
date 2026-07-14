"""Authenticated Upstox-only market-data ingestion."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal
from urllib.parse import quote

import httpx
import numpy as np
import pandas as pd
from google.cloud import storage


Timeframe = Literal["1d", "1h", "15m"]
Exchange = Literal["NSE", "BSE"]
CANONICAL_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


class MarketDataError(RuntimeError):
    """A stable, user-safe data pipeline failure."""


@dataclass(frozen=True)
class DataRequest:
    symbol: str
    exchange: Exchange
    history_years: int
    timeframe: Timeframe


@dataclass(frozen=True)
class Instrument:
    symbol: str
    exchange: Exchange
    isin: str
    instrument_key: str


@dataclass
class SourceResult:
    source: str
    frame: pd.DataFrame
    checksums: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Dataset:
    frame: pd.DataFrame
    instrument: Instrument
    manifest: dict[str, Any]


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_frame(rows: list[list[Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=CANONICAL_COLUMNS)
    if frame.empty:
        return frame
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    for column in CANONICAL_COLUMNS[1:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna(subset=CANONICAL_COLUMNS).sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)


def validate_ohlcv(frame: pd.DataFrame, timeframe: Timeframe) -> list[str]:
    errors: list[str] = []
    missing = set(CANONICAL_COLUMNS) - set(frame.columns)
    if missing:
        return [f"missing_columns:{','.join(sorted(missing))}"]
    minimum = 756 if timeframe == "1d" else 2_000
    if len(frame) < minimum:
        errors.append(f"insufficient_rows:{len(frame)}<{minimum}")
    if frame["timestamp"].duplicated().any():
        errors.append("duplicate_timestamps")
    numeric = frame[CANONICAL_COLUMNS[1:]]
    if numeric.isnull().any().any() or not np.isfinite(numeric.to_numpy()).all():
        errors.append("invalid_numeric_values")
    if (frame[["open", "high", "low", "close"]] <= 0).any().any() or (frame["volume"] < 0).any():
        errors.append("non_positive_price_or_negative_volume")
    if (frame["high"] < frame[["open", "close", "low"]].max(axis=1)).any():
        errors.append("high_price_invariant")
    if (frame["low"] > frame[["open", "close", "high"]].min(axis=1)).any():
        errors.append("low_price_invariant")
    return errors


class UpstoxProvider:
    base_url = "https://api.upstox.com"

    def __init__(self, token: str, client: httpx.AsyncClient):
        if not token:
            raise MarketDataError("UPSTOX_ANALYTICS_TOKEN_NOT_CONFIGURED")
        self.client = client
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    async def _get(self, url: str, **kwargs) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                response = await self.client.get(url, headers=self.headers, **kwargs)
                if response.status_code not in (408, 429, 500, 502, 503, 504):
                    response.raise_for_status()
                    return response
                last_error = httpx.HTTPStatusError("Transient Upstox response", request=response.request, response=response)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as error:
                last_error = error
            if attempt < 3:
                await asyncio.sleep(.5 * (2 ** attempt))
        raise MarketDataError("UPSTOX_TEMPORARILY_UNAVAILABLE") from last_error

    async def resolve(self, request: DataRequest) -> Instrument:
        response = await self._get(
            f"{self.base_url}/v2/instruments/search",
            params={"query": request.symbol, "exchanges": request.exchange, "segments": "EQ", "records": 30},
        )
        candidates = [
            item for item in response.json().get("data", [])
            if item.get("exchange") == request.exchange
            and item.get("trading_symbol", "").upper() == request.symbol
            and item.get("isin")
        ]
        if len(candidates) != 1:
            raise MarketDataError("UPSTOX_INSTRUMENT_NOT_UNIQUE_OR_MISSING")
        item = candidates[0]
        return Instrument(request.symbol, request.exchange, item["isin"], item["instrument_key"])

    @staticmethod
    def _windows(request: DataRequest) -> list[tuple[date, date]]:
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=int(request.history_years * 365.25) + 10)
        availability_start = date(2000, 1, 1) if request.timeframe == "1d" else date(2022, 1, 1)
        start = max(start, availability_start)
        days = 3650 if request.timeframe == "1d" else (90 if request.timeframe == "1h" else 30)
        windows: list[tuple[date, date]] = []
        cursor = start
        while cursor <= end:
            window_end = min(end, cursor + timedelta(days=days - 1))
            windows.append((cursor, window_end))
            cursor = window_end + timedelta(days=1)
        return windows

    async def candles(self, request: DataRequest, instrument: Instrument) -> SourceResult:
        unit, interval = {"1d": ("days", 1), "1h": ("hours", 1), "15m": ("minutes", 15)}[request.timeframe]
        rows: list[list[Any]] = []
        checksums: list[str] = []
        key = quote(instrument.instrument_key, safe="")
        for start, end in self._windows(request):
            url = f"{self.base_url}/v3/historical-candle/{key}/{unit}/{interval}/{end.isoformat()}/{start.isoformat()}"
            response = await self._get(url)
            checksums.append(_sha256(response.content))
            for candle in response.json().get("data", {}).get("candles", []):
                if len(candle) >= 6:
                    rows.append(candle[:6])
        return SourceResult("upstox", _canonical_frame(rows), checksums, {"instrumentKey": instrument.instrument_key})

    async def corporate_actions(self, instrument: Instrument) -> tuple[list[dict[str, Any]], str]:
        response = await self._get(f"{self.base_url}/v2/fundamentals/{quote(instrument.isin, safe='')}/corporate-actions")
        return response.json().get("data", []), _sha256(response.content)

    @staticmethod
    def benchmark_instrument(exchange: Exchange) -> tuple[str, str]:
        return ("NIFTY 50", "NSE_INDEX|Nifty 50") if exchange == "NSE" else ("SENSEX", "BSE_INDEX|SENSEX")

    async def benchmark(self, exchange: Exchange, years: int) -> tuple[pd.DataFrame, dict[str, Any]]:
        symbol, key = self.benchmark_instrument(exchange)
        request = DataRequest(symbol.replace(" ", ""), exchange, years, "1d")
        result = await self.candles(request, Instrument(symbol, exchange, f"{exchange}-INDEX", key))
        errors = validate_ohlcv(result.frame, "1d")
        if errors:
            raise MarketDataError("UPSTOX_BENCHMARK_VALIDATION_FAILED:" + "|".join(errors))
        frame = result.frame[["timestamp", "close", "volume"]].rename(columns={"close": "benchmark_close", "volume": "benchmark_volume"})
        return frame, {"source": "upstox", "symbol": symbol, "instrumentKey": key, "checksums": result.checksums}


def _action_text(action: dict[str, Any]) -> str:
    details = " ".join(str(item.get("value", "")) for item in action.get("event_details", []) if isinstance(item, dict))
    return " ".join(str(action.get(key, "")) for key in ("type", "event_type", "purpose", "description")) + " " + details


def _share_multiplier(action: dict[str, Any]) -> float | None:
    text = _action_text(action).lower()
    if not any(word in text for word in ("split", "sub-division", "subdivision", "bonus", "consolidation")):
        return None
    if "bonus" in text:
        match = re.search(r"(\d+(?:\.\d+)?)\s*[:/]\s*(\d+(?:\.\d+)?)", str(action.get("ratio", "")) + " " + text)
        return 1 + float(match.group(1)) / float(match.group(2)) if match else None
    face = re.search(r"from\s+(?:rs\.?\s*)?(\d+(?:\.\d+)?).*?to\s+(?:rs\.?\s*)?(\d+(?:\.\d+)?)", text)
    if face and float(face.group(2)) > 0:
        return float(face.group(1)) / float(face.group(2))
    match = re.search(r"(\d+(?:\.\d+)?)\s*[:/]\s*(\d+(?:\.\d+)?)", str(action.get("ratio", "")))
    if match and float(match.group(1)) > 0:
        return float(match.group(2)) / float(match.group(1))
    return None


def adjust_for_share_actions(frame: pd.DataFrame, actions: list[dict[str, Any]]) -> tuple[pd.DataFrame, list[str]]:
    adjusted = frame.copy()
    adjusted["adjustment_factor"] = 1.0
    unresolved: list[str] = []
    for action in actions:
        text = _action_text(action).lower()
        if not any(word in text for word in ("split", "sub-division", "subdivision", "bonus", "consolidation")):
            continue
        raw_date = action.get("expiry_date") or action.get("ex_date") or action.get("exDate")
        ex_date = pd.to_datetime(raw_date, utc=True, errors="coerce")
        multiplier = _share_multiplier(action)
        if pd.isna(ex_date) or multiplier is None or multiplier <= 0:
            unresolved.append(json.dumps(action, sort_keys=True, default=str)[:500])
            continue
        mask = adjusted["timestamp"] < ex_date
        price_factor = 1.0 / multiplier
        adjusted.loc[mask, "adjustment_factor"] *= price_factor
    for column in ("open", "high", "low", "close"):
        adjusted[f"adjusted_{column}"] = adjusted[column] * adjusted["adjustment_factor"]
    adjusted["adjusted_volume"] = adjusted["volume"] / adjusted["adjustment_factor"]
    return adjusted, unresolved


class MarketDataPipeline:
    def __init__(self, upstox_token: str, bucket_name: str | None = None):
        self.upstox_token = upstox_token
        self.bucket_name = bucket_name

    async def build(self, request: DataRequest) -> Dataset:
        timeout = httpx.Timeout(60, connect=15)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            upstox = UpstoxProvider(self.upstox_token, client)
            instrument = await upstox.resolve(request)
            primary = await upstox.candles(request, instrument)
            errors = validate_ohlcv(primary.frame, request.timeframe)
            if errors:
                raise MarketDataError("PRIMARY_DATA_VALIDATION_FAILED:" + "|".join(errors))
            actions, action_checksum = await upstox.corporate_actions(instrument)
            action_checksums = [action_checksum]
            adjusted, unresolved = adjust_for_share_actions(primary.frame, actions)
            if unresolved:
                raise MarketDataError("UNRESOLVED_SPLIT_OR_BONUS_ACTION")
        generated = datetime.now(timezone.utc)
        manifest = {
            "schemaVersion": 1,
            "generatedAt": generated.isoformat(),
            "request": asdict(request),
            "instrument": asdict(instrument),
            "primarySource": "upstox",
            "sourceChecksums": {"upstox": primary.checksums, "corporateActions": action_checksums},
            "reconciliation": [],
            "qualityStatus": "upstox_validated",
            "adjustments": {
                "corporateActionsReceived": len(actions),
                "hasShareAdjustments": bool((adjusted["adjustment_factor"] != 1.0).any()),
                "dividendsApplied": False,
            },
            "productionEligible": False,
            "rows": len(adjusted),
            "firstTimestamp": adjusted.timestamp.min().isoformat(),
            "lastTimestamp": adjusted.timestamp.max().isoformat(),
        }
        return Dataset(adjusted, instrument, manifest)

    async def benchmark(self, exchange: Exchange, years: int) -> tuple[pd.DataFrame, dict[str, Any]]:
        timeout = httpx.Timeout(60, connect=15)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            return await UpstoxProvider(self.upstox_token, client).benchmark(exchange, years)

    def persist(self, dataset: Dataset) -> dict[str, str]:
        bucket_name = self.bucket_name or os.getenv("MARKET_DATA_BUCKET")
        if not bucket_name:
            project = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCLOUD_PROJECT")
            bucket_name = f"{project}.firebasestorage.app" if project else None
        if not bucket_name:
            raise MarketDataError("MARKET_DATA_BUCKET_NOT_CONFIGURED")
        parquet = io.BytesIO()
        dataset.frame.to_parquet(parquet, index=False, compression="zstd")
        payload = parquet.getvalue()
        dataset_id = _sha256(payload)
        prefix = f"market-data/{dataset.instrument.exchange}/{dataset.instrument.isin}/snapshots/{dataset_id}"
        manifest = {**dataset.manifest, "datasetId": dataset_id, "parquetSha256": dataset_id}
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        bucket.blob(f"{prefix}/candles.parquet").upload_from_string(payload, content_type="application/vnd.apache.parquet")
        bucket.blob(f"{prefix}/manifest.json").upload_from_string(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")), content_type="application/json"
        )
        return {"datasetId": dataset_id, "parquet": f"gs://{bucket_name}/{prefix}/candles.parquet", "manifest": f"gs://{bucket_name}/{prefix}/manifest.json"}
