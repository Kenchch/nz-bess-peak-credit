import numpy as np
import pytest


def synthetic_winter(days=214, seed=1):
    """Invented North-Island-like half-hourly load. Shapes only, not real data."""
    rng = np.random.default_rng(seed)
    h = (np.arange(48) + 0.5) / 2
    out = []
    for _ in range(days):
        cold = rng.normal()
        day = (4600 + 250 * cold
               + (700 + 250 * cold) * np.exp(-((h - 8.0) / 1.3) ** 2)
               + (1100 + 300 * cold) * np.exp(-((h - 18.5) / 1.8) ** 2)
               - 900 * np.exp(-((h - 3.5) / 2.5) ** 2)
               + rng.normal(0, 40, 48))
        out.append(day)
    load = np.concatenate(out)
    tp = np.tile(np.arange(48), days)
    in_window = (tp >= 14) & (tp < 44)  # 07:00-22:00, as in NI-WCM
    return load, in_window


@pytest.fixture(scope="session")
def winter():
    return synthetic_winter()
