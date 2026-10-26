"""Logging shared by every step.

Each module logs to a child of the "bess" logger. What ends up where:

    console                 INFO and above, without tracebacks (Python prints those)
    outputs/bess.log        every `bess` command, appended, with tracebacks
    <run folder>/run.log    everything from one guarded run (`bess grid`, `bess analyse`)
    <run folder>/run_grid.log  the LP grid on its own (run_grid.get_logger)
    data/ingest.log         what ingest downloaded, retried or found revised, appended

Records carry their level, so `grep -E "WARNING|ERROR"` finds every gate that
warned, was overridden or failed. Python warnings (pandas, matplotlib) are
logged too, once capture_warnings() has run. Times are UTC, like run folder
names and publish_log.csv.
"""
import logging
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from . import ROOT

log = logging.getLogger("bess")
log.setLevel(logging.INFO)
WARNINGS = logging.getLogger("py.warnings")      # where logging.captureWarnings sends them
LINE, DATE = "%(asctime)s  %(levelname)-7s  %(message)s", "%Y-%m-%dT%H:%M:%SZ"


def formatter(cls=logging.Formatter):
    """LINE with UTC times."""
    f = cls(LINE, DATE)
    f.converter = time.gmtime
    return f


FORMAT = formatter()


def utf8_streams():
    """Let station names such as Ruakākā print when Windows hands Python a cp1252 pipe.

    A console gets UTF-8 anyway; stdout redirected to a file or `tee` does not.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")


def capture_warnings():
    """Send Python warnings through logging, so they reach run.log and bess.log.

    Off, then on: logging only installs its hook when it has none saved, and a
    test framework that restores warnings.showwarning leaves a stale one.
    """
    logging.captureWarnings(False)
    logging.captureWarnings(True)


class _Brief(logging.Formatter):
    """Console lines without the traceback: Python prints it when the error is re-raised."""

    def format(self, record):
        saved = record.exc_info, record.exc_text
        record.exc_info = record.exc_text = None
        try:
            return super().format(record)
        finally:
            record.exc_info, record.exc_text = saved


class _Console(logging.StreamHandler):
    """Writes to whatever sys.stderr is now, so it survives pytest swapping it per test."""

    @property
    def stream(self):
        return sys.stderr

    @stream.setter
    def stream(self, value):
        pass


def console():
    """Show bess's records on the console, once however often it is called."""
    if not any(isinstance(h, _Console) for h in log.handlers):
        h = _Console()
        h.setFormatter(formatter(_Brief))
        log.addHandler(h)
        WARNINGS.addHandler(h)


@contextmanager
def to_file(path, logger=log):
    """Append logger's records (and its children's) to path for the length of the block.

    The "bess" logger brings Python warnings with it; a step's own logger does not."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    h = logging.FileHandler(path, encoding="utf-8")
    h.setFormatter(FORMAT)
    loggers = [logger, WARNINGS] if logger is log else [logger]
    for lg in loggers:
        lg.addHandler(h)
    try:
        yield
    finally:
        for lg in loggers:
            lg.removeHandler(h)
        h.close()


@contextmanager
def step(name):
    """Log a step's start, end and time; tag an exception with the step it came from."""
    t0 = time.perf_counter()
    log.info(f"{name}: start")
    try:
        yield
    except Exception as e:
        if not hasattr(e, "bess_step"):
            try:
                e.bess_step = name
            except AttributeError:
                pass
        log.error(f"{name}: failed after {time.perf_counter() - t0:,.1f} s: "
                  f"{type(e).__name__}: {e}")
        raise
    log.info(f"{name}: done in {time.perf_counter() - t0:,.1f} s")


def rel(path):
    """path relative to the repository when inside it, so logs don't carry this machine's layout."""
    path = Path(path)
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)
