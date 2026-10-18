import numpy as np
import pytest

from bess.credit import capacity_credit, min_top_k, shave_peak, top_k_mean

# Hourly load 15:00-22:00 on one hand-worked evening (MW)
TOY = [6300, 6600, 6800, 6900, 6850, 6700, 6400]
# 2-hour batteries: how far each can hold the peak down, worked by hand
HAND = {100: 100.0, 400: 287.5, 1000: 525.0}


@pytest.mark.parametrize("p, cut", HAND.items())
def test_flat_line_matches_hand_worked_day(p, cut):
    assert shave_peak(TOY, p, 2 * p, dt_h=1.0) == pytest.approx(cut, abs=1e-6)


@pytest.mark.parametrize("p, cut", HAND.items())
def test_lp_with_k1_equals_bisection(p, cut):
    best, _ = min_top_k(TOY, np.ones(len(TOY), bool), p, 2 * p, k=1, dt_h=1.0, eta=1.0)
    assert max(TOY) - best == pytest.approx(shave_peak(TOY, p, 2 * p, dt_h=1.0), abs=1e-4)
    assert max(TOY) - best == pytest.approx(cut, abs=1e-4)


def test_top_k_mean():
    assert top_k_mean(np.array([5, 1, 4, 2, 3.0]), 2) == 4.5


def test_tiny_long_battery_counts_in_full(winter):
    load, win = winter
    assert capacity_credit(load, win, 1, 8) == pytest.approx(1.0, abs=0.01)


def test_credit_never_rises_with_fleet_size(winter):
    load, win = winter
    cc = [capacity_credit(load, win, p, 2) for p in (35, 135, 335, 500, 750, 1000, 1500)]
    assert all(b <= a + 1e-6 for a, b in zip(cc, cc[1:])), cc
    assert 0 < cc[-1] < cc[0] <= 1 + 1e-9


def test_energy_is_conserved(winter):
    load, win = winter
    p, e, eta, dt = 500.0, 1000.0, 0.88, 0.5
    _, b = min_top_k(load, win, p, e, k=200, dt_h=dt, eta=eta)
    out, into = np.clip(b, 0, None), np.clip(-b, 0, None)
    assert b.max() <= p + 1e-6 and b.min() >= -p - 1e-6
    # Discharged energy never exceeds the starting charge plus what charging stored
    assert out.sum() * dt <= e + eta * into.sum() * dt + 1e-6
    # State of charge, replayed from the dispatch, never goes below empty
    soc = e + np.cumsum(dt * (np.sqrt(eta) * into - out / np.sqrt(eta)))
    assert soc.min() >= -1e-6 * e
