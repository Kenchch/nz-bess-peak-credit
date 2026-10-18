"""nz-bess-peak-credit: how much of a battery's MW counts at the North Island winter peak."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
RAW = DATA / "raw"
STAGING = DATA / "staging"
SERIES = DATA / "series"
OUTPUTS = ROOT / "outputs"
TZ = "Pacific/Auckland"


class GateError(RuntimeError):
    """A quality gate failed; nothing downstream of it should run."""


def load_config(path=None):
    with open(path or ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)
