import asyncio
import os
from datetime import datetime, timezone
from typing import Literal
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from google.cloud import firestore
from pydantic import BaseModel, Field
from market_data import DataRequest, MarketDataPipeline
from model import Candidate, evolve

app = FastAPI(title='ArthAI Training Runner', docs_url=None, redoc_url=None)
db = firestore.Client()


class Job(BaseModel):
    jobId: str = Field(pattern=r'^[A-Za-z0-9]{10,40}$')
    ownerId: str
    symbol: str = Field(pattern=r'^[A-Z0-9&-]{1,24}$')
    exchange: Literal['NSE', 'BSE']
    historyYears: int = Field(ge=3, le=30)
    timeframe: Literal['1d', '1h', '15m']


def update(job_id: str, **values):
    values['updatedAt'] = datetime.now(timezone.utc)
    db.collection('trainingJobs').document(job_id).update(values)


async def candles(job: Job) -> pd.DataFrame:
    pipeline = MarketDataPipeline(os.getenv('UPSTOX_ANALYTICS_TOKEN', ''), os.getenv('MARKET_DATA_BUCKET'))
    request = DataRequest(job.symbol, job.exchange, job.historyYears, job.timeframe)
    dataset = await pipeline.build(request)
    artifact = await asyncio.to_thread(pipeline.persist, dataset)
    frame = dataset.frame.copy()
    for column in ('open', 'high', 'low', 'close'):
        frame[column] = frame[f'adjusted_{column}']
    frame['volume'] = frame['adjusted_volume']
    frame.attrs.update({'artifact': artifact, 'manifest': dataset.manifest})
    return frame


def features(frame: pd.DataFrame) -> np.ndarray:
    close, volume = frame.close, frame.volume
    rows = pd.DataFrame({
        'r1': close.pct_change(), 'r5': close.pct_change(5), 'r20': close.pct_change(20),
        'vol20': close.pct_change().rolling(20).std(), 'volume_z': (volume-volume.rolling(20).mean())/volume.rolling(20).std(),
        'range': (frame.high-frame.low)/close, 'gap': frame.open/close.shift()-1,
        'ma20': close/close.rolling(20).mean()-1, 'ma50': close/close.rolling(50).mean()-1,
    }).replace([np.inf,-np.inf], np.nan).dropna()
    return rows.to_numpy(dtype=np.float32)


def evaluate_factory(x: np.ndarray):
    # The real evaluator plugs purged walk-forward training/backtesting into this stable GA contract.
    # Fail closed until cost schedule and benchmark series are supplied by the licensed provider.
    def evaluate(candidate: Candidate):
        selected = x[:, np.array(candidate.feature_mask)]
        stability = float(np.std(np.mean(selected, axis=0)))
        return {'net_return': 0.0, 'profit_factor': 1.0, 'profitable_months': 0.0, 'max_drawdown': -0.01, 'fold_instability': stability, 'slippage_decay': 1.0, 'turnover': 0.0}
    return evaluate


@app.post('/')
async def train(job: Job):
    try:
        update(job.jobId, status='running', stage='provider_validation', progress=3)
        frame = await candles(job)
        x = features(frame)
        update(
            job.jobId,
            stage='ga_search',
            progress=12,
            observations=len(frame),
            featureRows=len(x),
            dataset=frame.attrs['artifact'],
            dataQuality=frame.attrs['manifest']['qualityStatus'],
            reconciliation=frame.attrs['manifest']['reconciliation'],
        )
        initial = [Candidate(64,.2,60,.6,2,4,tuple([True]*x.shape[1])) for _ in range(12)]
        champion, metrics = evolve(initial, evaluate_factory(x), generations=8)
        # Deliberately not approved: benchmark/regime/cost evaluator must be connected first.
        update(job.jobId, status='blocked', stage='validation_inputs_required', progress=25, errorCode='COST_AND_BENCHMARK_DATA_REQUIRED', candidate=champion.__dict__, provisionalMetrics=metrics)
        return {'accepted': True, 'jobId': job.jobId, 'status': 'blocked'}
    except Exception as error:
        update(job.jobId, status='failed', stage='data_validation_failed', errorCode=str(error)[:120])
        raise HTTPException(422, 'Training prerequisites failed') from error


@app.get('/health')
def health(): return {'status':'ok'}
