"""Download the EMI files and pin each one in data/manifest.csv (gate G1).

A file whose SHA-256 matches the manifest is never downloaded again. A file that
is missing or does not match is downloaded; if the fresh copy still differs from
the manifest, EMI has revised it upstream and the run stops until someone
accepts the new version with --update-manifest.

data/ingest.log (appended, kept next to the manifest) records every download,
retry and re-download, and the old and new SHA-256 of each file accepted as
revised: the manifest only shows the hash that won.
"""
import csv
import datetime as dt
import hashlib
import logging
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from . import DATA, RAW, GateError, load_config, logs

log = logging.getLogger(__name__)

EMI = "https://www.emi.ea.govt.nz/Wholesale/Datasets"
MANIFEST = DATA / "manifest.csv"
INGEST_LOG = DATA / "ingest.log"
FIELDS = ["dataset", "file", "url", "downloaded", "bytes", "sha256"]

# Monthly files: dataset folder in data/raw -> (EMI path, file name pattern)
MONTHLY = {
    "generation_md": ("Generation/Generation_MD", "{ym}_Generation_MD.csv"),
    "hvdc_flows": ("Metered_data/HVDC_Flows", "{ym}_HVDC_flows.csv"),
    "grid_export": ("Metered_data/Grid_export", "{ym}_Grid_export.csv"),
}
# Reference tables are republished under dated names; pin one of each.
REFERENCE = {
    "nsp": ("MappingsAndGeospatial/NetworkSupplyPointsTable",
            "20261016_NetworkSupplyPointsTable.csv"),
    "fleet": ("Generation/GenerationFleet/Existing",
              "20260702_DispatchedGenerationPlant.csv"),
}


def months(start, end):
    return [p.strftime("%Y%m") for p in pd.period_range(start, end, freq="M")]


def targets(cfg):
    """Every (dataset, file, url) the pipeline needs."""
    out = []
    for ym in months(cfg["period"]["start"], cfg["period"]["end"]):
        for ds, (folder, pattern) in MONTHLY.items():
            name = pattern.format(ym=ym)
            out.append((ds, name, f"{EMI}/{folder}/{name}"))
    for ds, (folder, name) in REFERENCE.items():
        out.append((ds, name, f"{EMI}/{folder}/{name}"))
    return out


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_manifest():
    if not MANIFEST.exists():
        return {}
    with open(MANIFEST, newline="", encoding="utf-8") as f:
        return {(r["dataset"], r["file"]): r for r in csv.DictReader(f)}


def write_manifest(rows):
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        for key in sorted(rows):
            w.writerow({k: rows[key][k] for k in FIELDS})
    tmp.replace(MANIFEST)


def download(url, dest, tries=3):
    """Fetch url to dest via a temp file; retry with backoff."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "nz-bess-peak-credit"})
    for attempt in range(1, tries + 1):
        try:
            with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
            tmp.replace(dest)
            return
        except OSError as exc:
            if attempt == tries:
                log.error(f"ingest: {url}: attempt {attempt}/{tries} failed ({exc}); giving up")
                raise
            log.warning(f"ingest: {url}: attempt {attempt}/{tries} failed ({exc}); "
                        f"retrying in {2 ** attempt} s")
            time.sleep(2 ** attempt)


def fetch(target, pinned, update_manifest=False):
    """Make sure one file is on disk and matches its pin. Returns its manifest row."""
    ds, name, url = target
    path = RAW / ds / name
    if path.exists() and pinned and sha256(path) == pinned["sha256"]:
        return pinned, "kept"
    if path.exists() and not pinned:
        digest, status = sha256(path), "pinned"
        log.info(f"ingest: {name}: pinned the copy already on disk, sha256 {digest}")
    else:
        if path.exists():
            log.warning(f"ingest: {name}: the copy on disk does not match its pin; downloading")
        for attempt in (1, 2):
            download(url, path)
            digest = sha256(path)
            if not pinned or update_manifest or digest == pinned["sha256"]:
                break
            log.warning(f"ingest: {name}: download {attempt} has sha256 {digest}, pin is "
                        f"{pinned['sha256']}")
        else:
            raise GateError(f"G1: {name} differs from data/manifest.csv after a fresh "
                            "download; EMI has revised it. Re-run with --update-manifest "
                            "to accept the new version.")
        status = "downloaded"
        if pinned and digest != pinned["sha256"]:
            log.warning(f"ingest: {name}: accepted as revised by EMI (--update-manifest): "
                        f"sha256 {pinned['sha256']} -> {digest}")
        else:
            log.info(f"ingest: {name}: downloaded, sha256 {digest}")
    row = {"dataset": ds, "file": name, "url": url,
           "downloaded": dt.date.today().isoformat(),
           "bytes": path.stat().st_size, "sha256": digest}
    if pinned and pinned["sha256"] == digest:
        row["downloaded"] = pinned["downloaded"]
    return row, status


def run(cfg=None, update_manifest=False, workers=6):
    cfg = cfg or load_config()
    with logs.to_file(INGEST_LOG, log):
        log.info(f"ingest: start{' with --update-manifest' if update_manifest else ''}")
        pins = read_manifest()
        todo = targets(cfg)
        try:
            with ThreadPoolExecutor(workers) as pool:
                results = list(pool.map(
                    lambda t: fetch(t, pins.get(t[:2]), update_manifest), todo))
        except Exception as e:
            log.error(f"ingest: stopped, manifest unchanged: {type(e).__name__}: {e}")
            raise
        for row, _ in results:
            pins[(row["dataset"], row["file"])] = row
        write_manifest(pins)
        counts = pd.Series([s for _, s in results]).value_counts().to_dict()
        log.info(f"ingest: {len(todo)} files {counts}; manifest {logs.rel(MANIFEST)}")
    return counts
