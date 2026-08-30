"""Feed-forward neural pipeline, under the same preprocessing contract.

This is a multi-layer perceptron, not an LSTM and not a Transformer. Those
need a deep-learning backend, and on this project's interpreter (Python 3.14)
no distribution of torch, TensorFlow or jaxlib exists yet -- verified, not
assumed. The operator chose to run a neural arm now under its real name and
revisit the recurrent and attention models separately.

The name matters. Reporting an MLP in a column headed "LSTM" would be the kind
of small lie that survives into a decision, so every surface calls this "MLP".

Two things worth saying about the size of it. The window is 120 sessions
against roughly 40-70 features, so the hidden layer is deliberately small and
the L2 penalty deliberately strong: anything wider memorises the window. And
scikit-learn's MLP optimises squared error only, so this arm estimates a
conditional *mean* and takes its spread from out-of-fold residuals, exactly as
the linear arms do -- it cannot produce a conditional distribution the way the
quantile and boosting arms can.
"""

from __future__ import annotations

from sklearn.impute import SimpleImputer  # type: ignore[import-untyped]
from sklearn.neural_network import MLPRegressor  # type: ignore[import-untyped]
from sklearn.pipeline import Pipeline  # type: ignore[import-untyped]
from sklearn.preprocessing import StandardScaler  # type: ignore[import-untyped]

from models.base import DEFAULT_RANDOM_STATE


def build_mlp_pipeline(
    alpha: float,
    hidden: int = 16,
    *,
    random_state: int = DEFAULT_RANDOM_STATE,
    max_iter: int = 2000,
) -> Pipeline:
    """One hidden layer, strongly penalised, fitted only inside its own fold."""

    if alpha <= 0.0:
        raise ValueError("alpha must be positive")
    if hidden < 1:
        raise ValueError("hidden must be positive")
    return Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scaler", StandardScaler()),
            (
                "model",
                MLPRegressor(
                    hidden_layer_sizes=(hidden,),
                    activation="tanh",
                    solver="lbfgs",
                    alpha=alpha,
                    max_iter=max_iter,
                    random_state=random_state,
                ),
            ),
        ]
    )
