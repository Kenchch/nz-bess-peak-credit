import numpy as np
import pytest

from bess.credit import (capacity_credit, kth_highest, lifted_peaks, min_top_k,
                         min_top_k_no_new_peaks, shave_peak, top_k_mean)

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


def test_tidy_pass_keeps_h100_and_stops_idle_cycling(winter):
    load, win = winter
    load, win = load[: 48 * 60], win[: 48 * 60]
    raw_h, raw_b, raw = min_top_k(load, win, 500.0, 1000.0, k=200, detail=True)
    tidy_h, tidy_b, tidy = min_top_k(load, win, 500.0, 1000.0, k=200, detail=True, tidy=True)
    assert tidy_h == raw_h                                  # H100 is the first-stage value
    assert top_k_mean((load - tidy_b)[win], 200) <= raw_h + 1e-3 + 1e-6
    assert (tidy["discharge"] + tidy["charge"]).sum() <= (raw["discharge"] + raw["charge"]).sum() + 1e-6
    assert tidy["discharge"][~win].max() < 1e-6            # never discharges where H100 can't see it


def test_plain_lp_can_charge_into_a_new_peak_and_the_rule_stops_it(winter):
    load, win = winter
    load, win = load[: 48 * 60], win[: 48 * 60]
    p, e, k = 1000.0, 2000.0, 200
    plain_h, plain_b, plain = min_top_k(load, win, p, e, k, detail=True)
    line = kth_highest((load - plain_b)[win], k)
    assert lifted_peaks(load, win, plain["charge"], plain_b, line) > 0    # the flaw is real here

    h, b, det, info = min_top_k_no_new_peaks(load, win, p, e, k)
    assert info["passes"] > 1 and info["h100_uncapped"] == pytest.approx(plain_h)
    assert lifted_peaks(load, win, det["charge"], b, kth_highest((load - b)[win], k)) == 0
    assert h >= plain_h - 1e-6                     # a rule can only cost H100, never help it
    assert top_k_mean((load - b)[win], k) == pytest.approx(h, abs=1e-6)


def test_rule_changes_nothing_when_every_half_hour_counts():
    load = np.array([6300, 6600, 6800, 6900, 6850, 6700, 6400.0])
    win = np.ones(len(load), bool)
    h, _, _, info = min_top_k_no_new_peaks(load, win, 400, 800, k=1, dt_h=1.0, eta=1.0)
    assert info["converged"] and max(load) - h == pytest.approx(287.5, abs=1e-4)


def test_rule_never_makes_a_longer_battery_worse(winter):
    # The old rule froze the cap at the lowest line seen and could rank 4 h below 2 h
    load, win = winter
    load, win = load[: 48 * 60], win[: 48 * 60]
    am = win & (np.tile(np.arange(48), 60) < 24)          # 07:00-12:00 only
    p, k = 1500.0, 50
    h2, *_ = min_top_k_no_new_peaks(load, am, p, 2 * p, k)
    h4, b4, det, _ = min_top_k_no_new_peaks(load, am, p, 4 * p, k)
    assert h4 <= h2 + 1e-6
    assert lifted_peaks(load, am, det["charge"], b4, kth_highest((load - b4)[am], k)) == 0


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
