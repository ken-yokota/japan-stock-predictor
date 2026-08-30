#!/usr/bin/env python3
"""Sequence models, run in their own interpreter because torch needs one.

This file is executed by ``.venv-neural`` (Python 3.12), never imported by the
main application (Python 3.14), which has no torch distribution available. It
reads one JSON job on stdin and writes one JSON answer on stdout, so the only
coupling between the two interpreters is a document neither can corrupt for the
other.

It fits an LSTM or a small Transformer encoder against **pinball loss at every
requested quantile at once**, which is what makes the output a distribution
rather than a point with a band bolted on. One forward pass returns the whole
curve, and the curve is sorted before it is returned because independently
optimised quantile heads can cross.

What the shape of the data means here, stated plainly because it governs how
much any of this is worth: a sequence model needs a sequence, so each training
example is the last ``lookback`` sessions rather than one row. The window is
120 sessions, so a 20-session lookback leaves about 100 examples, each of them
overlapping its neighbours by 19/20. That is very little independent
information for a model of this class, and the honest expectation is that it
underperforms the linear arms. It is built so that expectation can be measured
instead of asserted.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import numpy as np
import torch
from torch import nn

SEED = 42


def _sequences(
    matrix: np.ndarray, target: np.ndarray, lookback: int
) -> tuple[np.ndarray, np.ndarray]:
    """Turn rows into overlapping windows, oldest first.

    Window ``i`` ends at row ``i`` and is used to predict ``target[i]``, so no
    example ever contains the session it is asked about.
    """

    windows, answers = [], []
    for end in range(lookback - 1, len(target)):
        windows.append(matrix[end - lookback + 1 : end + 1])
        answers.append(target[end])
    if not windows:
        return np.empty((0, lookback, matrix.shape[1])), np.empty((0,))
    return np.asarray(windows, dtype=np.float32), np.asarray(
        answers, dtype=np.float32
    )


class LSTMHead(nn.Module):
    """One recurrent layer, then one linear head per quantile."""

    def __init__(self, features: int, hidden: int, quantiles: int) -> None:
        super().__init__()
        self.lstm = nn.LSTM(features, hidden, batch_first=True)
        self.head = nn.Linear(hidden, quantiles)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        output, _ = self.lstm(x)
        return self.head(output[:, -1, :])


class TransformerHead(nn.Module):
    """A single small encoder block over the lookback window.

    One block, two heads, and a deliberately narrow feed-forward width. On a
    hundred overlapping examples anything larger memorises the window, and a
    memorised window produces confident nonsense rather than an obvious
    failure, which is the worse outcome.
    """

    def __init__(self, features: int, model_dim: int, quantiles: int) -> None:
        super().__init__()
        self.project = nn.Linear(features, model_dim)
        self.position = nn.Parameter(torch.zeros(1, 512, model_dim))
        layer = nn.TransformerEncoderLayer(
            d_model=model_dim,
            nhead=2,
            dim_feedforward=model_dim * 2,
            dropout=0.1,
            batch_first=True,
            norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(layer, num_layers=1)
        self.head = nn.Linear(model_dim, quantiles)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        projected = self.project(x)
        projected = projected + self.position[:, : projected.shape[1], :]
        encoded = self.encoder(projected)
        return self.head(encoded[:, -1, :])


def pinball(
    predicted: torch.Tensor, actual: torch.Tensor, levels: torch.Tensor
) -> torch.Tensor:
    """Loss for every quantile at once; the mean over levels and examples."""

    error = actual.unsqueeze(1) - predicted
    return torch.maximum(levels * error, (levels - 1.0) * error).mean()


def _standardise(
    windows: np.ndarray, current: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Centre and scale on the training windows only.

    The statistics come from the training tensor and are then applied to the
    row being predicted. Computing them over both would let the session being
    forecast influence its own normalisation.
    """

    mean = windows.mean(axis=(0, 1), keepdims=True)
    scale = windows.std(axis=(0, 1), keepdims=True)
    scale[scale == 0.0] = 1.0
    return (windows - mean) / scale, (current - mean) / scale


def run(job: dict[str, Any]) -> dict[str, Any]:
    torch.manual_seed(SEED)
    np.random.seed(SEED)
    torch.set_num_threads(1)

    levels = [float(v) for v in job["levels"]]
    lookback = int(job.get("lookback", 20))
    epochs = int(job.get("epochs", 200))
    kind = str(job.get("model", "lstm"))

    matrix = np.asarray(job["features"], dtype=np.float64)
    target = np.asarray(job["target"], dtype=np.float64)
    latest = np.asarray(job["latest"], dtype=np.float64)
    # The imputation the sklearn arms get from their pipeline. Column medians
    # from the training window only.
    medians = np.nanmedian(matrix, axis=0)
    medians = np.where(np.isfinite(medians), medians, 0.0)
    matrix = np.where(np.isfinite(matrix), matrix, medians)
    latest = np.where(np.isfinite(latest), latest, medians)

    if len(target) < lookback + 10:
        return {
            "status": "INSUFFICIENT",
            "detail": f"{len(target)}行では{lookback}期の系列を作れません",
        }

    windows, answers = _sequences(matrix, target, lookback)
    # The window that ends at the most recent training row, with the row being
    # predicted appended: this is the sequence leading into today.
    tail = np.vstack([matrix[-(lookback - 1) :], latest])[None, :, :].astype(
        np.float32
    )
    windows, tail = _standardise(windows.astype(np.float64), tail.astype(np.float64))

    x = torch.tensor(windows, dtype=torch.float32)
    y = torch.tensor(answers, dtype=torch.float32)
    x_tail = torch.tensor(tail, dtype=torch.float32)
    level_tensor = torch.tensor(levels, dtype=torch.float32).unsqueeze(0)

    features = x.shape[2]
    model: nn.Module = (
        LSTMHead(features, hidden=16, quantiles=len(levels))
        if kind == "lstm"
        else TransformerHead(features, model_dim=16, quantiles=len(levels))
    )
    optimiser = torch.optim.Adam(model.parameters(), lr=0.01, weight_decay=1e-3)

    model.train()
    for _ in range(epochs):
        optimiser.zero_grad()
        loss = pinball(model(x), y, level_tensor)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimiser.step()

    model.eval()
    with torch.no_grad():
        predicted = model(x_tail).squeeze(0).tolist()
        final_loss = float(pinball(model(x), y, level_tensor))

    return {
        "status": "OK",
        # Sorted: independently optimised heads cross, and a crossed pair is
        # not a distribution.
        "quantiles": sorted(float(v) for v in predicted),
        "parameters": {
            "lookback": lookback,
            "epochs": epochs,
            "examples": len(answers),
            "train_pinball": final_loss,
        },
    }


def main() -> int:
    try:
        job = json.loads(sys.stdin.read())
    except Exception as error:
        print(json.dumps({"status": "FAILED", "detail": type(error).__name__}))
        return 1
    try:
        print(json.dumps(run(job)))
    except Exception as error:
        print(json.dumps({"status": "FAILED", "detail": type(error).__name__}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
