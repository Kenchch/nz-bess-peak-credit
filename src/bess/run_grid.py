"""Day 3: the LP grid (architecture 3, 4.2, 4.3; gate G5).

Loop winter x basis x window x P x d (plus the usable70 and solar3x
sensitivities at d = 2 h) through credit.capacity_credit, check that CC never
rises with P and that discharge <= starting energy + charge x eta, then
interpolate p* where CC crosses SOSA's 0.6 / 0.8. Writes credit_table.csv,
dispatch_d2.parquet and crossings.csv. Not built yet.
"""


def run(*args, **kwargs):
    raise NotImplementedError("run_grid is Day 3 work")
