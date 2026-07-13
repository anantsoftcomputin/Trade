"""Licensed-first market-data ingestion and research-only reconciliation.

Upstox is the authenticated primary source. NSE public UDiFF bhavcopies and
Yahoo Finance are optional verification sources; neither is allowed to silently
replace missing primary history.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import re
import zipfile
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


@dataclass(frozen=True)
class Reconciliation:
    source: str
    status: Literal["passed", "failed", "unavailable", "unsupported"]
    overlap: int = 0
    max_close_difference_bps: float | None = None
    mismatches: int = 0
    detail: str | None = None


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

    async def resolve(self, request: DataRequest) -> Instrument:
        response = await self.client.get(
            f"{self.base_url}/v2/instruments/search",
            params={"query": request.symbol, "exchanges": request.exchange, "segments": "EQ", "records": 30},
            headers=self.headers,
        )
        response.raise_for_status()
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
            response = await self.client.get(url, headers=self.headers)
            response.raise_for_status()
            checksums.append(_sha256(response.content))
            for candle in response.json().get("data", {}).get("candles", []):
                if len(candle) >= 6:
                    rows.append(candle[:6])
        return SourceResult("upstox", _canonical_frame(rows), checksums, {"instrumentKey": instrument.instrument_key})

    async def corporate_actions(self, instrument: Instrument) -> tuple[list[dict[str, Any]], str]:
        response = await self.client.get(
            f"{self.base_url}/v2/fundamentals/{quote(instrument.isin, safe='')}/corporate-actions",
            headers=self.headers,
        )
        response.raise_for_status()
        return response.json().get("data", []), _sha256(response.content)


class NSEBhavcopyProvider:
    """Recent NSE verification only; public archives are not a bulk API."""

    base_url = "https://nsearchives.nseindia.com/content/cm"

    def __init__(self, client: httpx.AsyncClient, sessions: int = 5):
        self.client = client
        self.sessions = max(1, min(sessions, 20))
        self.headers = {
            "Accept": "application/zip,text/csv,*/*",
            "User-Agent": "Mozilla/5.0 (compatible; ArthAIResearch/1.0; +https://trade-56777.web.app)",
        }

    async def _day(self, day: date, symbol: str) -> tuple[list[Any] | None, str | None]:
        filename = f"BhavCopy_NSE_CM_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"
        try:
            response = await self.client.get(f"{self.base_url}/{filename}", headers=self.headers, timeout=8)
            if response.status_code in (403, 404):
                return None, None
            response.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                csv_name = next(name for name in archive.namelist() if name.lower().endswith(".csv"))
                frame = pd.read_csv(archive.open(csv_name))
            row = frame[(frame["TckrSymb"].astype(str).str.upper() == symbol) & (frame["SctySrs"].astype(str) == "EQ")]
            if len(row) != 1:
                return None, _sha256(response.content)
            item = row.iloc[0]
            return [item["TradDt"], item["OpnPric"], item["HghPric"], item["LwPric"], item["ClsPric"], item["TtlTradgVol"]], _sha256(response.content)
        except (httpx.HTTPError, zipfile.BadZipFile, KeyError, StopIteration, ValueError):
            return None, None

    async def candles(self, request: DataRequest) -> SourceResult:
        if request.exchange != "NSE" or request.timeframe != "1d":
            return SourceResult("nse_bhavcopy", pd.DataFrame(columns=CANONICAL_COLUMNS), metadata={"unsupported": True})
        candidates: list[date] = []
        cursor = datetime.now(timezone.utc).date() - timedelta(days=1)
        while len(candidates) < self.sessions * 2:
            if cursor.weekday() < 5:
                candidates.append(cursor)
            cursor -= timedelta(days=1)
        results = await asyncio.gather(*(self._day(day, request.symbol) for day in candidates))
        rows, checksums = [], []
        for row, checksum in results:
            if row is not None:
                rows.append(row)
            if checksum:
                checksums.append(checksum)
        rows = rows[: self.sessions]
        return SourceResult("nse_bhavcopy", _canonical_frame(rows), checksums, {"requestedSessions": self.sessions})


class YahooReconciliationProvider:
    """Unofficial, research-only validator. Disabled unless explicitly enabled."""

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def candles(self, request: DataRequest) -> SourceResult:
        if request.timeframe != "1d":
            return SourceResult("yahoo_research", pd.DataFrame(columns=CANONICAL_COLUMNS), metadata={"unsupported": True})
        ticker = f"{request.symbol}.{'NS' if request.exchange == 'NSE' else 'BO'}"
        end = int(datetime.now(timezone.utc).timestamp())
        start = int((datetime.now(timezone.utc) - timedelta(days=int(request.history_years * 365.25) + 10)).timestamp())
        response = await self.client.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(ticker, safe='')}",
            params={"period1": start, "period2": end, "interval": "1d", "events": "div,splits"},
            headers={"User-Agent": "Mozilla/5.0 (compatible; ArthAIResearch/1.0)"},
            timeout=20,
        )
        response.raise_for_status()
        result = response.json()["chart"]["result"][0]
        quote_data = result["indicators"]["quote"][0]
        rows = [
            [datetime.fromtimestamp(ts, timezone.utc), opn, high, low, close, volume]
            for ts, opn, high, low, close, volume in zip(
                result["timestamp"], quote_data["open"], quote_data["high"], quote_data["low"], quote_data["close"], quote_data["volume"]
            )
        ]
        return SourceResult("yahoo_research", _canonical_frame(rows), [_sha256(response.content)], {"ticker": ticker, "researchOnly": True})


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


def reconcile(primary: pd.DataFrame, secondary: SourceResult, tolerance_bps: float = 50.0) -> Reconciliation:
    if secondary.metadata.get("unsupported"):
        return Reconciliation(secondary.source, "unsupported", detail="timeframe_or_exchange_not_supported")
    if secondary.frame.empty:
        return Reconciliation(secondary.source, "unavailable", detail="no_records_received")
    left = primary.assign(day=primary["timestamp"].dt.date)[["day", "close"]]
    right = secondary.frame.assign(day=secondary.frame["timestamp"].dt.date)[["day", "close"]]
    overlap = left.merge(right, on="day", suffixes=("_primary", "_secondary"))
    if overlap.empty:
        return Reconciliation(secondary.source, "unavailable", detail="no_overlapping_sessions")
    differences = ((overlap.close_primary / overlap.close_secondary - 1).abs() * 10_000).replace([np.inf], np.nan).dropna()
    mismatches = int((differences > tolerance_bps).sum())
    status: Literal["passed", "failed"] = "passed" if mismatches == 0 else "failed"
    return Reconciliation(secondary.source, status, len(overlap), float(differences.max()), mismatches)


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
            adjusted, unresolved = adjust_for_share_actions(primary.frame, actions)
            if unresolved:
                raise MarketDataError("UNRESOLVED_SPLIT_OR_BONUS_ACTION")

            verification_sources: list[SourceResult] = []
            if os.getenv("ENABLE_NSE_RECONCILIATION", "true").lower() == "true":
                verification_sources.append(await NSEBhavcopyProvider(client, int(os.getenv("NSE_VERIFY_SESSIONS", "5"))).candles(request))
            if os.getenv("ENABLE_YAHOO_RECONCILIATION", "false").lower() == "true":
                try:
                    verification_sources.append(await YahooReconciliationProvider(client).candles(request))
                except (httpx.HTTPError, KeyError, TypeError, ValueError):
                    verification_sources.append(SourceResult("yahoo_research", pd.DataFrame(columns=CANONICAL_COLUMNS)))

        reconciliations = [reconcile(primary.frame, source) for source in verification_sources]
        if any(item.status == "failed" for item in reconciliations):
            raise MarketDataError("CROSS_SOURCE_PRICE_MISMATCH")
        quality = "verified" if any(item.status == "passed" for item in reconciliations) else "research_only_unverified"
        generated = datetime.now(timezone.utc)
        manifest = {
            "schemaVersion": 1,
            "generatedAt": generated.isoformat(),
            "request": asdict(request),
            "instrument": asdict(instrument),
            "primarySource": "upstox",
            "sourceChecksums": {"upstox": primary.checksums, "corporateActions": [action_checksum], **{s.source: s.checksums for s in verification_sources}},
            "reconciliation": [asdict(item) for item in reconciliations],
            "qualityStatus": quality,
            "adjustments": {
                "corporateActionsReceived": len(actions),
                "hasShareAdjustments": bool((adjusted["adjustment_factor"] != 1.0).any()),
                "dividendsApplied": False,
            },
            "rows": len(adjusted),
            "firstTimestamp": adjusted.timestamp.min().isoformat(),
            "lastTimestamp": adjusted.timestamp.max().isoformat(),
        }
        return Dataset(adjusted, instrument, manifest)

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
