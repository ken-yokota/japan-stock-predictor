"""Bridge to the sequence models, which live in a different interpreter.

torch has no Python 3.14 distribution, and this project pinned 3.14 on
purpose. Rather than reverse that decision for one arm, the LSTM and the
Transformer run under ``.venv-neural`` (Python 3.12) as a subprocess, and the
two interpreters exchange a single JSON document. Nothing is imported across
the boundary, so the main application keeps a dependency set it can actually
install.

The arm degrades in three named ways rather than one vague one, because they
call for different responses:

    UNAVAILABLE   the interpreter or torch is not installed -- somebody needs
                  to run the setup, and no morning is at risk
    FAILED        the worker ran and something broke -- a bug to fix
    INSUFFICIENT  the window is too short to cut into sequences -- expected on
                  a thin ticker, and not a defect

A timeout is treated as FAILED and never as a quiet skip. An arm that silently
disappears on slow days is an arm whose absence nobody investigates.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from models.arms import CONDITIONAL, FAILED, UNAVAILABLE, ArmForecast
from models.distribution import ReturnDistribution

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NEURAL_PYTHON = PROJECT_ROOT / ".venv-neural" / "bin" / "python"
WORKER = PROJECT_ROOT / "neural" / "worker.py"

# Long enough that a slow ticker finishes, short enough that a hung worker
# cannot eat the morning. Measured: LSTM about 45s, Transformer about 7s.
DEFAULT_TIMEOUT_SECONDS = 180.0


@dataclass(frozen=True, slots=True)
class SequenceArm:
    """An LSTM or Transformer, fitted in the sibling interpreter."""

    name: str
    label: str
    lookback: int = 20
    epochs: int = 200
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    python: Path = NEURAL_PYTHON
    worker: Path = WORKER

    def available(self) -> bool:
        return self.python.exists() and self.worker.exists()

    def forecast(
        self,
        features: pd.DataFrame,
        target: NDArray[np.float64],
        latest: pd.DataFrame,
        *,
        levels: tuple[float, ...],
        n_splits: int,
    ) -> ArmForecast:
        if not self.available():
            return ArmForecast(
                self.name,
                self.label,
                status=UNAVAILABLE,
                detail=(
                    ".venv-neural（Python 3.12 + torch）が未設置です。"
                    "scripts/setup_neural_env.sh を実行してください。"
                ),
            )
        job = {
            "features": features.apply(pd.to_numeric, errors="coerce")
            .replace([np.inf, -np.inf], np.nan)
            .to_numpy(dtype=float)
            .tolist(),
            "target": [float(v) for v in target],
            "latest": latest.apply(pd.to_numeric, errors="coerce")
            .replace([np.inf, -np.inf], np.nan)
            .to_numpy(dtype=float)
            .tolist(),
            "levels": [float(v) for v in levels],
            "lookback": self.lookback,
            "epochs": self.epochs,
            "model": self.name,
        }
        try:
            completed = subprocess.run(
                [str(self.python), str(self.worker)],
                input=json.dumps(job),
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
                cwd=str(PROJECT_ROOT),
            )
        except subprocess.TimeoutExpired:
            return ArmForecast(
                self.name,
                self.label,
                status=FAILED,
                detail=f"{self.timeout_seconds:.0f}秒で応答なし",
            )
        except OSError as error:
            return ArmForecast(
                self.name, self.label, status=UNAVAILABLE, detail=type(error).__name__
            )

        answer = _last_json(completed.stdout)
        if answer is None:
            # stderr can carry a torch traceback; only the exception class is
            # kept, so a path or a value never reaches a mail.
            tail = completed.stderr.strip().splitlines()
            return ArmForecast(
                self.name,
                self.label,
                status=FAILED,
                detail=tail[-1][:120] if tail else "ワーカーが応答を返しませんでした",
            )
        status = str(answer.get("status", FAILED))
        if status != "OK":
            return ArmForecast(
                self.name,
                self.label,
                status=FAILED if status == "FAILED" else UNAVAILABLE,
                detail=str(answer.get("detail", ""))[:160],
            )
        try:
            curve = ReturnDistribution(
                levels=levels,
                values=tuple(sorted(float(v) for v in answer["quantiles"])),
                alpha=0.0,
                training_sessions=len(target),
                method=f"{self.name}_quantile",
            )
        except (KeyError, TypeError, ValueError) as error:
            return ArmForecast(
                self.name, self.label, status=FAILED, detail=type(error).__name__
            )
        return ArmForecast(
            name=self.name,
            label=self.label,
            predicted_return=curve.median,
            probability_up=curve.probability_above(0.0),
            distribution=curve,
            spread_kind=CONDITIONAL,
            parameters=dict(answer.get("parameters", {})),
        )


def _last_json(text: str) -> dict[str, Any] | None:
    """The worker's answer is its final stdout line; torch may print above it."""

    for line in reversed(text.strip().splitlines()):
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def sequence_arms() -> tuple[SequenceArm, ...]:
    return (
        SequenceArm("lstm", "LSTM"),
        SequenceArm("transformer", "Transformer"),
    )


def neural_environment_status() -> str:
    """One line for a report: whether the sibling interpreter is usable."""

    if not NEURAL_PYTHON.exists():
        return f"未設置（{NEURAL_PYTHON.name} が見つかりません）"
    try:
        completed = subprocess.run(
            [str(NEURAL_PYTHON), "-c", "import torch; print(torch.__version__)"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"確認できません（{type(error).__name__}）"
    if completed.returncode != 0:
        return "torch が読み込めません"
    version = completed.stdout.strip()
    main = f"{sys.version_info.major}.{sys.version_info.minor}"
    return f"利用可能（torch {version} / 本体の Python {main} とは別環境）"
