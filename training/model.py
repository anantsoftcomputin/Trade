from dataclasses import dataclass
from typing import Iterable
import copy
import numpy as np
import torch
from torch import nn


class TemporalAttentionModel(nn.Module):
    """Compact TCN + bidirectional GRU + attention with return, direction and risk heads."""
    def __init__(self, features: int, width: int = 64, dropout: float = 0.2):
        super().__init__()
        self.tcn = nn.Sequential(
            nn.Conv1d(features, width, 3, padding=2, dilation=1), nn.GELU(), nn.Dropout(dropout),
            nn.Conv1d(width, width, 3, padding=4, dilation=2), nn.GELU(), nn.Dropout(dropout),
        )
        self.gru = nn.GRU(width, width, batch_first=True, bidirectional=True)
        self.attention = nn.MultiheadAttention(width * 2, 4, dropout=dropout, batch_first=True)
        self.norm = nn.LayerNorm(width * 2)
        self.direction = nn.Linear(width * 2, 1)
        self.return_quantiles = nn.Linear(width * 2, 3)
        self.volatility = nn.Sequential(nn.Linear(width * 2, 1), nn.Softplus())

    def forward(self, x: torch.Tensor):
        local = self.tcn(x.transpose(1, 2))[..., :x.shape[1]].transpose(1, 2)
        memory, _ = self.gru(local)
        attended, _ = self.attention(memory, memory, memory, need_weights=False)
        state = self.norm(memory[:, -1] + attended[:, -1])
        return self.direction(state), self.return_quantiles(state), self.volatility(state)


@dataclass(frozen=True)
class Candidate:
    width: int
    dropout: float
    lookback: int
    threshold: float
    stop_atr: float
    target_atr: float
    feature_mask: tuple[bool, ...]


def fitness(metrics: dict[str, float]) -> float:
    """Multi-objective GA score; accuracy is intentionally absent."""
    return (
        metrics['net_return'] * 2.0
        + min(metrics['profit_factor'], 3.0) * 0.35
        + metrics['profitable_months'] * 0.25
        - abs(metrics['max_drawdown']) * 2.5
        - metrics['fold_instability'] * 1.4
        - metrics['slippage_decay'] * 0.8
        - metrics['turnover'] * 0.05
        - metrics.get('complexity', 0.0) * 0.15
    )


def evolve(initial: Iterable[Candidate], evaluate, generations: int = 24, seed: int = 42) -> tuple[Candidate, dict]:
    rng = np.random.default_rng(seed)
    population = list(initial)
    best: tuple[float, Candidate, dict] | None = None
    for _ in range(generations):
        scored = [(fitness(m := evaluate(c)), c, m) for c in population]
        scored.sort(key=lambda row: row[0], reverse=True)
        if best is None or scored[0][0] > best[0]: best = copy.deepcopy(scored[0])
        elites = [row[1] for row in scored[:max(2, len(scored)//4)]]
        children = list(elites)
        while len(children) < len(population):
            a, b = rng.choice(elites, 2, replace=True)
            mask = tuple(x if rng.random() < .5 else y for x, y in zip(a.feature_mask, b.feature_mask))
            child = Candidate(
                width=int(rng.choice([a.width, b.width, 24, 32, 48])),
                dropout=float(np.clip((a.dropout+b.dropout)/2+rng.normal(0,.03), .08, .4)),
                lookback=int(rng.choice([a.lookback, b.lookback, 30, 45, 60, 90])),
                threshold=float(np.clip((a.threshold+b.threshold)/2+rng.normal(0,.01), .52, .75)),
                stop_atr=float(np.clip((a.stop_atr+b.stop_atr)/2+rng.normal(0,.1), 1, 4)),
                target_atr=float(np.clip((a.target_atr+b.target_atr)/2+rng.normal(0,.15), 1.5, 8)),
                feature_mask=mask,
            )
            children.append(child)
        population = children
    assert best is not None
    return best[1], best[2]
