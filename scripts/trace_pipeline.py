"""Run every module in pipeline order and log how the data changes shape.

    uv run python -I scripts/trace_pipeline.py

Everything is written under outputs/trace/<run_id>/ (staging, series and the
analysis outputs, including the full LP grid), so data/ and outputs/runs/ are
never touched. At the end each trace copy is compared with the latest pipeline
run that wrote the same file, so the log doubles as a reproducibility check.
The grid makes this take about 7 minutes.

    trace.log              full log: per step, input and output shape, columns,
                           dtypes, nulls, ranges, sample rows, what changed
    analysis/run_grid.log  every LP of the grid, with its result and checks
    data_lineage.md        one table, one row per step
"""
import datetime as dt
import logging
import re
import sys
import textwrap
import time
from pathlib import Path

import numpy as np
import pandas as pd

from bess import OUTPUTS, RAW, SERIES, STAGING, load_config
from bess.cli import git_tag
from bess import (calibrate, credit, ingest, logs, peak_shape, reconcile, run_grid, series,
                  stage, top200)

SAMPLE_MONTH = "202507"
ACCEPT_GAP = "trace run: the solar 2024 gap accepted in RESULTS.md"
TP_COL = re.compile(r"^TP\d+$")

log = logging.getLogger("trace")
lineage = []


# ---------------------------------------------------------------- formatting

def setup(out):
    logs.utf8_streams()          # a cp1252 pipe cannot print the Chinese log or Ruakākā
    logs.console()               # the steps' own lines, as `bess` shows them
    out.mkdir(parents=True)
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S")
    for h in (logging.FileHandler(out / "trace.log", encoding="utf-8"),
              logging.StreamHandler(sys.stdout)):
        h.setFormatter(fmt)
        log.addHandler(h)


def header(n, total, name, module, what):
    log.info("")
    log.info("=" * 78)
    log.info(f"[{n}/{total}] {name}   ({module})   {what}")
    log.info("=" * 78)


def frame(label, df, sample=3, max_cols=10):
    """Shape, memory, per-column dtype / nulls / range, and a few rows."""
    mem = df.memory_usage(deep=True).sum() / 2**20
    log.info(f"  {label}: {len(df):,} 行 x {df.shape[1]} 列, 内存 {mem:,.1f} MB")
    tps = [c for c in df.columns if TP_COL.match(str(c))]
    if tps:
        vals = df[tps].apply(pd.to_numeric, errors="coerce")
        log.info(f"      {tps[0]}..{tps[-1]:<15} {len(tps)} 列 kWh, "
                 f"空值 {int(vals.isna().sum().sum()):,}, 范围 "
                 f"[{np.nanmin(vals.to_numpy()):,.4g}, {np.nanmax(vals.to_numpy()):,.4g}]")
    for c in df.columns:
        if c in tps:
            continue
        col = df[c]
        info = f"{str(col.dtype):<22} 空值 {int(col.isna().sum()):>8,}"
        if pd.api.types.is_bool_dtype(col):
            info += f"  True {int(col.fillna(False).sum()):,}"
        elif pd.api.types.is_numeric_dtype(col) or pd.api.types.is_datetime64_any_dtype(col):
            if col.notna().any():
                lo, hi = col.min(), col.max()
                fmt = (lambda v: f"{v:,.4g}") if pd.api.types.is_numeric_dtype(col) else str
                info += f"  范围 [{fmt(lo)}, {fmt(hi)}]"
        else:
            info += f"  {col.nunique():,} 种取值"
        log.info(f"      {str(c):<24} {info}")
    shown = [c for c in df.columns if c not in tps][:max_cols]
    extra = f"  (另有 {len(tps)} 个 TP 列未显示)" if tps else ""
    log.info(f"      前 {sample} 行{extra}:")
    log.info(textwrap.indent(df[shown].head(sample).to_string(max_colwidth=22), "      | "))


def array(label, a):
    a = np.asarray(a)
    finite = a[np.isfinite(a)] if a.dtype.kind == "f" else a
    rng = f", 范围 [{finite.min():,.4g}, {finite.max():,.4g}]" if finite.size else ""
    nan = f", NaN {int(np.isnan(a).sum()):,}" if a.dtype.kind == "f" else ""
    log.info(f"  {label}: ndarray {a.shape} {a.dtype}{nan}{rng}")


def change(text):
    log.info(f"  -> 变化: {text}")


def record(step, module, inp, out, rows, what):
    lineage.append({"step": step, "module": module, "input": inp, "output": out,
                    "rows": rows, "change": what})


def timed(fn, *args, **kw):
    t0 = time.perf_counter()
    result = fn(*args, **kw)
    log.info(f"  耗时 {time.perf_counter() - t0:,.1f} s")
    return result


# ---------------------------------------------------------------- steps

def step_config(cfg):
    header(0, 9, "config", "config.yaml", "所有模块共用的参数")
    for k, v in cfg.items():
        log.info(f"  {k}: {v}")


def step_ingest(cfg):
    header(1, 9, "ingest", "ingest.py", "G1 输入固定: 文件存在且 SHA-256 与清单一致")
    pins = ingest.read_manifest()
    todo = ingest.targets(cfg)
    by_ds = {}
    for ds, name, _ in todo:
        path = RAW / ds / name
        pin = pins.get((ds, name))
        ok = path.exists() and pin is not None and ingest.sha256(path) == pin["sha256"]
        d = by_ds.setdefault(ds, {"files": 0, "ok": 0, "bytes": 0})
        d["files"] += 1
        d["ok"] += ok
        d["bytes"] += path.stat().st_size if path.exists() else 0
    for ds, d in by_ds.items():
        log.info(f"  {ds:<14} {d['files']:>3} 个文件, 哈希一致 {d['ok']:>3}, "
                 f"{d['bytes'] / 2**20:,.1f} MB")
    bad = sum(d["files"] - d["ok"] for d in by_ds.values())
    log.info(f"  G1: {'通过' if not bad else f'{bad} 个文件不一致'} (只校验, 不下载)")

    log.info("")
    log.info(f"  原始文件长什么样 (以 {SAMPLE_MONTH} 为例):")
    samples = {
        "generation_md": RAW / "generation_md" / f"{SAMPLE_MONTH}_Generation_MD.csv",
        "hvdc_flows": RAW / "hvdc_flows" / f"{SAMPLE_MONTH}_HVDC_flows.csv",
        "grid_export": RAW / "grid_export" / f"{SAMPLE_MONTH}_Grid_export.csv",
        "nsp": RAW / "nsp" / ingest.REFERENCE["nsp"][1],
        "fleet": RAW / "fleet" / ingest.REFERENCE["fleet"][1],
    }
    for ds, path in samples.items():
        frame(f"{ds} ({path.name})", pd.read_csv(path, dtype=str), sample=2, max_cols=8)
    change("无。ingest 只负责把文件放到 data/raw/ 并在 manifest.csv 里记录 URL、日期、"
           "字节数、SHA-256。原始格式是宽表: 一行 = 一个机组(或计量点)一天, TP1..TP50 = "
           "每半小时 kWh")
    total = sum(d["files"] for d in by_ds.values())
    record("ingest", "ingest.py", "EMI 网站", f"data/raw/ {total} 个 CSV",
           f"{total} 文件", "下载 + 哈希固定, 数据本身不变")


def step_stage(cfg, out):
    header(2, 9, "stage", "stage.py", "G2 宽表转长表, 6 条规则, 隔离不删除")
    islands = timed(stage.island_map, RAW / "nsp" / ingest.REFERENCE["nsp"][1])
    log.info(f"  island_map: {len(islands):,} 个 POC/站点代码 -> 岛 "
             f"({pd.Series(islands).value_counts().to_dict()})")

    log.info("")
    log.info(f"  单月展开示例 ({SAMPLE_MONTH}, Generation_MD):")
    path = RAW / "generation_md" / f"{SAMPLE_MONTH}_Generation_MD.csv"
    long, rule, wide = stage.stage_generation(path, islands)
    frame("输入 宽表 (加了 island, fuel 两列)", wide, sample=2, max_cols=10)
    log.info(f"  规则检查: {dict(zip(*np.unique(rule, return_counts=True)))} "
             f"(0 = 通过; 1..6 = {stage.RULES})")
    frame("输出 长表", long, sample=3)
    days = pd.to_datetime(wide.Trading_Date).nunique()
    change(f"{len(wide):,} 行 x 50 个 TP 列 -> {len(long):,} 行 x {long.shape[1]} 列。"
           f"一行从\"一个机组一天\"变成\"一个机组一个半小时\"; 每行展开的条数 = 当天真实"
           f"半小时数(46/48/50), TP49/TP50 只在夏令时结束日保留。{days} 天, 平均每行 "
           f"{len(long) / len(wide):.2f} 条")

    log.info("")
    log.info("  全部 66 个月 x 2 个来源:")
    report = timed(stage.run, cfg, raw=RAW, out=out)
    tot = report[report.file == "ALL"]
    frame("quality_report.csv 合计行", tot, sample=2, max_cols=12)
    gen = pd.read_parquet(out / "generation.parquet")
    hvdc = pd.read_parquet(out / "hvdc.parquet")
    q = pd.read_parquet(out / "quarantine.parquet")
    frame("generation.parquet", gen)
    frame("hvdc.parquet", hvdc)
    log.info(f"  quarantine.parquet: {len(q):,} 行")
    for r in tot.itertuples():
        log.info(f"  核对 {r.source}: rows_in {r.rows_in:,} = rows_ok {r.rows_ok:,} + "
                 f"rejected {r.rejected:,} -> {'一致' if r.rows_in == r.rows_ok + r.rejected else '不一致'}")
    change("字符串列转 category (poc 等只有几百种取值), kWh 成为单一 float 列; "
           "Trading_Date 从字符串变成 datetime64")
    g, h = tot.set_index("source").loc["generation"], tot.set_index("source").loc["hvdc"]
    record("stage", "stage.py", f"宽表 {g.rows_in:,} + {h.rows_in:,} 行",
           f"generation {len(gen):,} 行, hvdc {len(hvdc):,} 行, 隔离 {len(q):,} 行",
           f"x{len(gen) / g.rows_in:.1f} (宽 -> 长)",
           "TP1..TP50 展开成行; 6 条规则; 字符串 -> category")
    return gen, hvdc


def step_series(cfg, staging, out):
    header(3, 9, "series", "series.py", "拼北岛半小时序列 (数据契约 4.1)")
    cal = timed(series.calendar, cfg["period"]["start"], cfg["period"]["end"])
    frame("calendar() 时间骨架", cal)
    per_day = cal.groupby("trading_date").size()
    log.info(f"  每天半小时数: {per_day.value_counts().sort_index().to_dict()} "
             "(46 = 夏令时开始, 50 = 夏令时结束)")
    lab = series.label(cal, cfg)
    frame("label() 加上冬季 / 时段 / NI-WCM 窗口", lab)
    log.info(f"  period 分布: {lab.period.value_counts().to_dict()}")

    gen = pd.read_parquet(staging / "generation.parquet",
                          columns=["trading_date", "tp", "island", "fuel", "kwh"])
    hvdc = pd.read_parquet(staging / "hvdc.parquet")
    key = ["trading_date", "tp"]
    ni = gen[gen.island == "NI"]
    log.info(f"  只取北岛: {len(gen):,} -> {len(ni):,} 行 (去掉南岛 {len(gen) - len(ni):,})")
    by_fuel = (ni.groupby(key + ["fuel"], observed=True).kwh.sum()
               .unstack("fuel", fill_value=0.0) / series.KWH_PER_MW)
    by_fuel.columns = by_fuel.columns.astype(str)
    frame("按 (日期, TP) x 燃料 透视, kWh / 500 = MW", by_fuel.reset_index())
    hay = hvdc[hvdc.poc == series.HVDC_NI_POC]
    flows = hay.groupby(key + ["flow_direction"], observed=True).kwh.sum().unstack(fill_value=0.0)
    flows.columns = flows.columns.astype(str)
    frame("HAY2201 按 I / X 透视 (kWh)", flows.reset_index())

    s = timed(series.run, cfg, staging=staging, out=out)
    frame("ni_halfhourly.parquet", s)
    log.info(f"  公式核对: l_gross = gen_ni + hvdc_north 最大误差 "
             f"{(s.l_gross_mw - s.gen_ni_mw - s.hvdc_north_mw).abs().max():.2e}; "
             f"l_net = l_gross - wind - solar 最大误差 "
             f"{(s.l_net_mw - s.l_gross_mw + s.wind_mw + s.solar_mw).abs().max():.2e}")
    w = s[s.winter.notna()]
    log.info(f"  冬季行数: {w.groupby('winter').size().to_dict()}")
    change(f"约 {len(gen):,} 行 (机组 x 半小时) 汇总成 {len(s):,} 行 (每个半小时一行); "
           f"燃料从行变成列 (gen_ni / wind / solar / bess); 新增时间标签列和两条需求序列")
    record("series", "series.py", f"generation {len(gen):,} + hvdc {len(hvdc):,} 行",
           f"ni_halfhourly {len(s):,} 行 x {s.shape[1]} 列",
           f"/{len(gen) / len(s):.0f} (汇总)",
           "机组 -> 北岛合计; 燃料行 -> 列; kWh -> MW; 加冬季/时段标签")
    return s


def step_reconcile(cfg, series_path, out):
    header(4, 9, "reconcile", "reconcile.py", "G3 对账 (只警告): 北岛供电 vs GXP 用电")
    path = RAW / "grid_export" / f"{SAMPLE_MONTH}_Grid_export.csv"
    frame(f"输入 Grid_export 宽表 ({path.name})", pd.read_csv(path, dtype=str), sample=2)
    rec = timed(reconcile.run, cfg, series_path=series_path, raw=RAW, out=out)
    frame("reconcile.csv", rec)
    change(f"96,432 个半小时按月汇总成 {len(rec)} 行 (GWh); 每月 Grid_export 也压成一个数, "
           "两者相减得到损耗+嵌入式发电的占比, 再加一列\"HVDC 符号反过来会怎样\"作对照")
    record("reconcile", "reconcile.py", "series + 66 个 Grid_export 宽表",
           f"reconcile.csv {len(rec)} 行", "半小时 -> 月", "月度能量平衡; 符号检查")


def step_calibrate(cfg, staging, series_path, out):
    header(5, 9, "calibrate", "calibrate.py", "G4 校准: 复现 SOSA 风光峰值系数")
    fleet = calibrate.load_fleet(RAW / "fleet" / ingest.REFERENCE["fleet"][1],
                                 stage.island_map(RAW / "nsp" / ingest.REFERENCE["nsp"][1]))
    frame("load_fleet() 风/光/电池装机及生效区间", fleet)
    s, _, left_out = timed(calibrate.renewables, cfg, staging=staging, series_path=series_path)
    base = set(series.COLUMNS)
    added = [c for c in s.columns if c not in base]
    frame(f"renewables() 在序列上新增 {len(added)} 列", s[["ts_local"] + added])
    log.info(f"  未计入的电厂: {len(left_out)} 个")
    for fuel, what, why in left_out:
        log.info(f"      {fuel}: {what}: {why}")
    cal = timed(calibrate.run, cfg, out=out, accept_gap=ACCEPT_GAP,
                staging=staging, series_path=series_path)
    frame("calibration.csv (每种口径一行)", cal)
    change(f"序列加宽: {len(series.COLUMNS)} -> {s.shape[1]} 列 (全国发电 + 每种燃料/范围的出力和"
           f"装机); 再按 年 x 需求口径 x 选取方式 x 范围 x 技术 展开成 {len(cal)} 行结果表")
    record("calibrate", "calibrate.py", "series + generation + fleet",
           f"calibration.csv {len(cal)} 行 (+calibration.md)",
           f"列 {len(series.COLUMNS)} -> {s.shape[1]}", "加装机和出力列; 48 种口径对比 SOSA")


def step_peak_shape(cfg, series_path, out):
    header(6, 9, "peak_shape", "peak_shape.py", "峰有多宽: 深度-时长曲线")
    s = pd.read_parquet(series_path)
    w = s[s.winter.notna()]
    for window in peak_shape.WINDOWS:
        load, days = peak_shape.day_matrix(w[w.period == window], "l_gross_mw")
        array(f"day_matrix({window}) 天 x 半小时", load)
        duration, battery = peak_shape.curves(load)
        array(f"curves({window}) battery_h 天 x 深度({len(peak_shape.DELTAS)} 档)", battery)
    table = timed(peak_shape.run, cfg, series_path=series_path, out=out)
    frame("peak_shape.csv", table)
    change("长序列 -> 每个窗口一张 (天 x 半小时) 矩阵 -> 对 16 档深度做三维广播 "
           f"(天 x 深度 x 半小时) -> 按天取 P50/P90 -> {len(table):,} 行汇总表")
    record("peak_shape", "peak_shape.py", "series (冬季)", f"peak_shape.csv {len(table):,} 行 + png",
           "长 -> 矩阵 -> 汇总", "天 x 半小时矩阵; 16 档深度; P50/P90")


def step_top200(cfg, staging, series_path, out):
    header(7, 9, "top200", "top200.py", "总需求 vs 净需求的 top-200 时段")
    table = timed(top200.run, cfg, out=out, staging=staging, series_path=series_path)
    frame("top200_summary.csv", table, max_cols=12)
    change(f"每个冬季在窗口内各取 200 个最高半小时 (两种口径), 求交集和均值 -> "
           f"{len(table)} 行 (冬季 x 口径)")
    record("top200", "top200.py", "series + 装机列", f"top200_summary.csv {len(table)} 行",
           "~6,400/冬季 -> 200 -> 1", "两种口径各取 top-200, 比重叠、早晚、风光系数")


def step_grid(cfg, series_path, out, winter=2025, p_mw=335, hours=2):
    header(8, 9, "run_grid", "run_grid.py + credit.py",
           "G5 LP 网格: 每组参数一个 LP, 再汇总成查表和平衡点 p*")
    s = pd.read_parquet(series_path)
    w = s[s.winter.eq(winter).fillna(False)].reset_index(drop=True)
    log.info(f"  一个 LP 的内部结构 ({winter} 冬季, gross, window all, P = {p_mw} MW, {hours} h):")
    load = run_grid.winter_load(w, "gross", "base", cfg)
    in_window = run_grid.window_mask(w, "all")
    array(f"load = l_gross_mw + bess_mw (加回现有电池)", load)
    log.info(f"  加回电池后需求最多升高 {w.bess_mw.max():.1f} MW, 冬季平均 {w.bess_mw.mean():.2f} MW")
    array("in_window (07:00-22:00)", in_window)

    captured = {}
    real_linprog = credit.linprog

    def spy(c, A_ub, b_ub, A_eq, b_eq, bounds, method):
        captured.update(nv=len(c), ub=A_ub.shape, ub_nnz=A_ub.nnz, eq=A_eq.shape, eq_nnz=A_eq.nnz)
        t0 = time.perf_counter()
        res = real_linprog(c, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=b_eq,
                           bounds=bounds, method=method)
        captured.update(solve_s=time.perf_counter() - t0, status=res.message)
        return res

    k = cfg["k_top"]
    e_rated, e_stored = run_grid.stored_energy(p_mw, hours, 1.0, cfg)
    log.info(f"  电量口径 {cfg['credit'].get('rated_energy')}: 额定 {e_rated:,.0f} MWh 送上电网, "
             f"储能需装 {e_stored:,.1f} MWh (= 额定 / sqrt(eta))")
    before = credit.top_k_mean(load[in_window], k)
    credit.linprog = spy
    try:
        after, battery = credit.min_top_k(load, in_window, p_mw, e_stored, k,
                                          eta=cfg["eta_roundtrip"])
    finally:
        credit.linprog = real_linprog
    log.info(f"  LP 变量 {captured['nv']:,} 个 = 3 x {len(load):,} (放电/充电/电量) "
             f"+ {int(in_window.sum()):,} (超出量 u) + 1 (横线 tau)")
    log.info(f"  不等式约束 A_ub {captured['ub']}, 非零 {captured['ub_nnz']:,}; "
             f"等式约束 A_eq {captured['eq']}, 非零 {captured['eq_nnz']:,}")
    log.info(f"  HiGHS: {captured['status']}, {captured['solve_s']:.2f} s")
    array("battery (放电为正, 充电为负) MW", battery)
    cc = (before - after) / p_mw
    log.info(f"  第 1 轮 (不限制充电): H100 前 {before:,.1f} MW -> 后 {after:,.1f} MW; CC = {cc:.1%}")
    line = credit.kth_highest((load - battery)[in_window], k)
    charge = np.clip(-battery, 0, None)
    lifted = credit.lifted_peaks(load, in_window, charge, battery, line)
    log.info(f"  H100 线 (第 {k} 高) {line:,.1f} MW; 窗口外被充电推到线以上的半小时: {lifted}")
    after2, battery2, _, info = credit.min_top_k_no_new_peaks(
        load, in_window, p_mw, e_stored, k, eta=cfg["eta_roundtrip"])
    log.info(f"  min_top_k_no_new_peaks: {info['passes']} 轮 (分组收敛 {info['converged']}), "
             f"CC {cc:.1%} -> "
             f"{(before - after2) / p_mw:.1%}; 窗口外新峰 "
             f"{credit.lifted_peaks(load, in_window, np.clip(-battery2, 0, None), battery2, info['line_mw'])}")
    change(f"{len(load):,} 个负荷值 -> {captured['nv']:,} 个变量的稀疏 LP (必要时加充电上限再解) -> "
           f"一条 {len(battery):,} 长的充放电曲线 -> 一个标量 CC")

    log.info("")
    jobs = run_grid.plan(cfg, sorted(int(x) for x in s.winter.dropna().unique()))
    log.info(f"  完整网格: {len(jobs):,} 个 LP, 每个是 (冬季, 口径, 窗口, 敏感性, P, d) 的一个组合; "
             f"每次的输入、结果和 G5 检查记在 {out / 'run_grid.log'}")
    table, cross = timed(run_grid.run, cfg, series_path=series_path, out=out)
    frame("credit_table.csv (一行 = 一个 LP)", table, max_cols=12)
    frame("crossings.csv (一行 = 一条曲线一种统计)", cross, max_cols=11)
    disp = pd.read_parquet(out / "dispatch_d2.parquet")
    frame("dispatch_d2.parquet (一行 = 一个半小时 x 一个 P x 一个口径)", disp)
    change(f"{len(jobs):,} 组参数 x 每组约 1 万个半小时 -> {len(table):,} 行结果 (每个 LP 一行, 序列"
           f"本身被压成 H100 前后两个数) -> 按曲线汇总、插值成 {len(cross)} 行 p*; d = 2 h 的充放电"
           f"曲线另存 {len(disp):,} 行供 Power BI 画图")
    record("run_grid", "run_grid.py + credit.py", f"series 6 个冬季, {len(jobs):,} 组参数",
           f"credit_table {len(table):,} 行, crossings {len(cross)} 行, dispatch {len(disp):,} 行",
           "~1 万/LP -> 1 行 -> p*", f"{captured['nv']:,} 变量/LP; G5 全部检查")


def step_not_built():
    header(9, 9, "publish / observed / figures", "Day 4", "尚未实现")
    for mod in ("publish", "observed", "figures"):
        log.info(f"  {mod}.py: 只有说明和 NotImplementedError, 本次不运行")
    record("publish 等", "Day 4", "-", "-", "-", "尚未实现")


# ---------------------------------------------------------------- checks

def compare_with_committed(out):
    log.info("")
    log.info("=" * 78)
    log.info("复现检查: trace 输出 vs 已有的流水线输出")
    log.info("=" * 78)
    pairs = [(out / "staging" / f, STAGING / f) for f in ("generation.parquet", "hvdc.parquet")]
    pairs.append((out / "series" / "ni_halfhourly.parquet", SERIES / "ni_halfhourly.parquet"))
    root = OUTPUTS / "runs"
    # Only runs that finished (DONE.txt, written by cli.guarded) count as results
    runs = sorted(p for p in root.iterdir() if (p / "DONE.txt").exists()) if root.is_dir() else []
    log.info(f"  可比对的已完成运行: {[r.name for r in runs][-3:] or '无'}")
    for f in ("reconcile.csv", "calibration.csv", "peak_shape.csv", "top200_summary.csv",
              "credit_table.csv", "crossings.csv", "dispatch_d2.parquet"):
        have = [r / f for r in runs if (r / f).exists()]       # latest run that wrote this file
        pairs.append((out / "analysis" / f, have[-1] if have else out / "missing" / f))
    for new, old in pairs:
        if not old.exists():
            log.info(f"  {old.name}: 没有可比对的旧文件")
            continue
        read = pd.read_parquet if new.suffix == ".parquet" else pd.read_csv
        a, b = read(new), read(old)
        # wall-clock time differs from run to run
        a, b = a.drop(columns="solve_s", errors="ignore"), b.drop(columns="solve_s", errors="ignore")
        if a.equals(b):         # fast path; assert_frame_equal takes minutes on 8M rows
            log.info(f"  {old.relative_to(old.parents[2])}: 完全相同 ({len(a):,} 行)")
            continue
        try:
            pd.testing.assert_frame_equal(a, b, check_exact=False, rtol=1e-9, check_categorical=False)
            log.info(f"  {old.relative_to(old.parents[2])}: 一致 ({len(a):,} 行)")
        except AssertionError as e:
            log.info(f"  {old.relative_to(old.parents[2])}: 不一致: {str(e).splitlines()[0]}")


def write_lineage(out):
    lines = ["# Data lineage (trace run)", "",
             "| 步骤 | 模块 | 输入 | 输出 | 行数变化 | 结构上发生了什么 |",
             "|---|---|---|---|---|---|"]
    lines += [f"| {r['step']} | `{r['module']}` | {r['input']} | {r['output']} | {r['rows']} | "
              f"{r['change']} |" for r in lineage]
    (out / "data_lineage.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + git_tag()
    out = OUTPUTS / "trace" / run_id
    setup(out)
    staging, series_dir, analysis = out / "staging", out / "series", out / "analysis"
    analysis.mkdir()
    series_path = series_dir / "ni_halfhourly.parquet"
    log.info(f"trace run {run_id}; 所有输出写在 {out}")

    t0 = time.perf_counter()
    cfg = load_config()
    step_config(cfg)
    step_ingest(cfg)
    step_stage(cfg, staging)
    step_series(cfg, staging, series_dir)
    step_reconcile(cfg, series_path, analysis)
    step_calibrate(cfg, staging, series_path, analysis)
    step_peak_shape(cfg, series_path, analysis)
    step_top200(cfg, staging, series_path, analysis)
    step_grid(cfg, series_path, analysis)
    step_not_built()
    write_lineage(out)
    compare_with_committed(out)
    log.info("")
    log.info(f"完成, 共 {time.perf_counter() - t0:,.0f} s; 日志 {out / 'trace.log'}")


if __name__ == "__main__":
    main()
