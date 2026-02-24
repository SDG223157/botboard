#!/usr/bin/env python3
"""
网格交易自适应模型 v2.0
======================
输入股票代码（A股/ETF），自动生成优化的网格交易方案 + 回测 + 可视化

用法:
    python grid_trader.py 600900        # 长江电力
    python grid_trader.py 513130        # 纳指ETF
    python grid_trader.py 159915        # 创业板ETF
    python grid_trader.py 600900 --capital 200000 --days 250
"""

import argparse
import sys
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

plt.rcParams["font.sans-serif"] = ["Arial Unicode MS", "SimHei", "PingFang SC", "Microsoft YaHei"]
plt.rcParams["axes.unicode_minus"] = False


# ─────────────────────────── 数据获取 ───────────────────────────

def fetch_data(code: str, days: int = 250) -> pd.DataFrame:
    """获取 A 股/ETF 日线数据，自动识别代码格式"""
    pure = code.replace(".SH", "").replace(".SZ", "").replace(".SS", "").strip()

    try:
        import io, contextlib
        with contextlib.redirect_stderr(io.StringIO()):
            import akshare as ak
        if len(pure) == 6 and pure.isdigit():
            try:
                df = ak.fund_etf_hist_em(symbol=pure, period="daily", adjust="qfq")
                if df is not None and len(df) > 50:
                    df = df.rename(columns={"日期": "date", "开盘": "open", "最高": "high",
                                            "最低": "low", "收盘": "close", "成交量": "volume"})
                    df["date"] = pd.to_datetime(df["date"])
                    df = df.sort_values("date").reset_index(drop=True)
                    df = df.tail(days).reset_index(drop=True)
                    for c in ["open", "high", "low", "close", "volume"]:
                        df[c] = pd.to_numeric(df[c], errors="coerce")
                    print(f"  [akshare/ETF] 获取 {pure} 成功, {len(df)} 条记录")
                    return df
            except Exception:
                pass

            try:
                df = ak.stock_zh_a_hist(symbol=pure, period="daily", adjust="qfq")
                if df is not None and len(df) > 50:
                    df = df.rename(columns={"日期": "date", "开盘": "open", "最高": "high",
                                            "最低": "low", "收盘": "close", "成交量": "volume"})
                    df["date"] = pd.to_datetime(df["date"])
                    df = df.sort_values("date").reset_index(drop=True)
                    df = df.tail(days).reset_index(drop=True)
                    for c in ["open", "high", "low", "close", "volume"]:
                        df[c] = pd.to_numeric(df[c], errors="coerce")
                    print(f"  [akshare/股票] 获取 {pure} 成功, {len(df)} 条记录")
                    return df
            except Exception:
                pass
    except Exception:
        print("  [akshare] 不可用，尝试 yfinance...")

    import yfinance as yf
    sh_prefixes = ("6", "5")
    suffix = ".SS" if any(pure.startswith(p) for p in sh_prefixes) else ".SZ"
    ticker = f"{pure}{suffix}"
    end = datetime.now()
    start = end - timedelta(days=int(days * 1.5))
    df = yf.download(ticker, start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"),
                     progress=False, auto_adjust=True)
    if df.empty:
        alt_suffix = ".SZ" if suffix == ".SS" else ".SS"
        alt_ticker = f"{pure}{alt_suffix}"
        print(f"  [yfinance] {ticker} 失败, 尝试 {alt_ticker}...")
        df = yf.download(alt_ticker, start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"),
                         progress=False, auto_adjust=True)
        ticker = alt_ticker
    if df.empty:
        raise ValueError(f"无法获取 {pure} 数据 (已尝试 .SS/.SZ)，请检查代码")
    df = df.reset_index()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df.columns]
    else:
        df.columns = [c.lower() for c in df.columns]
    df = df.tail(days).reset_index(drop=True)
    print(f"  [yfinance] 获取 {ticker} 成功, {len(df)} 条记录")
    return df


# ─────────────────────────── 波动分析 ───────────────────────────

@dataclass
class VolatilityProfile:
    close_now: float
    close_high: float
    close_low: float
    p5: float
    p10: float
    p20: float
    p50: float
    p80: float
    p90: float
    p95: float
    annual_vol: float
    atr14_median_pct: float
    vol_20d_current: float
    vol_20d_min: float
    vol_20d_max: float


def analyze_volatility(df: pd.DataFrame) -> VolatilityProfile:
    c = df["close"].values.astype(float)
    h = df["high"].values.astype(float)
    lo = df["low"].values.astype(float)

    returns = np.diff(np.log(c))
    annual_vol = float(np.std(returns) * np.sqrt(245) * 100)

    tr_list = []
    for i in range(1, len(c)):
        tr = max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1]))
        tr_list.append(tr)
    tr_arr = np.array(tr_list)
    atr14 = pd.Series(tr_arr).rolling(14).mean().dropna().values
    atr14_pct = atr14 / c[14:len(atr14) + 14] * 100
    atr14_median_pct = float(np.median(atr14_pct))

    vol_20d = pd.Series(returns).rolling(20).std().dropna().values * np.sqrt(245) * 100

    return VolatilityProfile(
        close_now=float(c[-1]),
        close_high=float(np.max(c)),
        close_low=float(np.min(c)),
        p5=float(np.percentile(c, 5)),
        p10=float(np.percentile(c, 10)),
        p20=float(np.percentile(c, 20)),
        p50=float(np.percentile(c, 50)),
        p80=float(np.percentile(c, 80)),
        p90=float(np.percentile(c, 90)),
        p95=float(np.percentile(c, 95)),
        annual_vol=annual_vol,
        atr14_median_pct=atr14_median_pct,
        vol_20d_current=float(vol_20d[-1]) if len(vol_20d) > 0 else annual_vol,
        vol_20d_min=float(np.min(vol_20d)) if len(vol_20d) > 0 else annual_vol * 0.5,
        vol_20d_max=float(np.max(vol_20d)) if len(vol_20d) > 0 else annual_vol * 1.5,
    )


# ─────────────────────────── 网格设计 ───────────────────────────

@dataclass
class GridDesign:
    grid_lines: list[float]
    target_positions: list[float]
    zone_labels: list[str]
    step: float
    step_pct: float
    a_zone_upper: float
    a_zone_lower: float
    b_zone_lower: float
    stop_loss: float
    overflow_line: float
    n_grids_a: int
    n_grids_b: int
    capital: float
    max_shares: int
    shares_per_grid: int
    lot_size: int


def design_grid(vp: VolatilityProfile, capital: float = 100000, lot_size: int = 100) -> GridDesign:
    """自适应双区联动网格设计"""
    price = vp.close_now

    step_pct_target = max(1.0, min(2.0, vp.atr14_median_pct * 1.1))
    step_raw = price * step_pct_target / 100

    tick = 0.01 if price < 10 else 0.01
    step = round(round(step_raw / tick) * tick, 2)
    if step < 0.01:
        step = 0.01
    step_pct = step / price * 100

    range_factor = vp.annual_vol / 100 * 0.8
    half_range = price * range_factor / 2
    raw_upper = price + half_range * 1.2
    raw_lower = price - half_range * 0.8

    a_upper = round(min(raw_upper, vp.p80 * 1.02), 2)
    a_lower = round(max(raw_lower, vp.p20 * 0.98), 2)

    if a_upper <= price:
        a_upper = round(price * 1.05, 2)
    if a_lower >= price:
        a_lower = round(price * 0.95, 2)
    if a_upper - a_lower < step * 3:
        a_upper = round(price + step * 3, 2)
        a_lower = round(price - step * 2, 2)

    n_a = max(3, min(10, int(round((a_upper - a_lower) / step))))
    step = round((a_upper - a_lower) / n_a, 2)
    step_pct = step / price * 100

    n_b = 2
    b_lower = round(a_lower - step * n_b, 2)
    stop_loss = round(b_lower - step * 1.5, 2)
    overflow = round(a_upper + step, 2)

    grid_lines = []
    target_pos = []
    zone_labels = []

    grid_lines.append(overflow)
    target_pos.append(0.0)
    zone_labels.append("超涨")

    for i in range(n_a + 1):
        p = round(a_upper - i * step, 2)
        pos = i / n_a
        grid_lines.append(p)
        target_pos.append(pos)
        zone_labels.append("A区")

    for i in range(1, n_b + 1):
        p = round(a_lower - i * step, 2)
        grid_lines.append(p)
        target_pos.append(1.0)
        zone_labels.append("B区")

    grid_lines.append(stop_loss)
    target_pos.append(-1.0)
    zone_labels.append("止损")

    max_shares = int(capital / a_lower)
    max_shares = (max_shares // lot_size) * lot_size
    shares_per_grid = int(max_shares / n_a)
    shares_per_grid = max(lot_size, (shares_per_grid // lot_size) * lot_size)
    max_shares = shares_per_grid * n_a

    return GridDesign(
        grid_lines=grid_lines,
        target_positions=target_pos,
        zone_labels=zone_labels,
        step=step,
        step_pct=step_pct,
        a_zone_upper=a_upper,
        a_zone_lower=a_lower,
        b_zone_lower=b_lower,
        stop_loss=stop_loss,
        overflow_line=overflow,
        n_grids_a=n_a,
        n_grids_b=n_b,
        capital=capital,
        max_shares=max_shares,
        shares_per_grid=shares_per_grid,
        lot_size=lot_size,
    )


# ─────────────────────────── 回测引擎 ───────────────────────────

@dataclass
class BacktestResult:
    dates: list
    equity_grid: list[float]
    equity_bnh: list[float]
    positions: list[float]
    n_trades: int
    total_cost: float
    grid_return: float
    bnh_return: float
    grid_maxdd: float
    bnh_maxdd: float
    trades_log: list[dict] = field(default_factory=list)


def backtest(df: pd.DataFrame, gd: GridDesign,
             buy_cost_pct: float = 0.05, sell_cost_pct: float = 0.10) -> BacktestResult:
    closes = df["close"].values.astype(float)
    dates = df["date"].tolist()

    cash = gd.capital
    shares = 0
    n_trades = 0
    total_cost = 0.0
    trades_log = []

    equity_grid = []
    equity_bnh = []
    positions_pct = []

    bnh_shares = int(gd.capital / closes[0])
    bnh_cash = gd.capital - bnh_shares * closes[0]

    def get_target_pos(price):
        if price >= gd.overflow_line:
            return 0.0
        if price <= gd.stop_loss:
            return -1.0
        for i in range(len(gd.grid_lines) - 1):
            if gd.grid_lines[i] >= price > gd.grid_lines[i + 1]:
                return gd.target_positions[i]
        if price > gd.grid_lines[0]:
            return 0.0
        return 1.0

    for i, price in enumerate(closes):
        target = get_target_pos(price)

        if target < 0:
            if shares > 0:
                revenue = shares * price
                cost = revenue * sell_cost_pct / 100
                cash += revenue - cost
                total_cost += cost
                n_trades += 1
                trades_log.append({"date": dates[i], "action": "止损清仓",
                                   "price": price, "shares": -shares})
                shares = 0
        else:
            target_shares = int(target * gd.max_shares)
            target_shares = (target_shares // gd.lot_size) * gd.lot_size
            diff = target_shares - shares

            if diff > 0 and gd.zone_labels[0] != "B区":
                zone = "B区"
                for j in range(len(gd.grid_lines)):
                    if price >= gd.grid_lines[j]:
                        zone = gd.zone_labels[j]
                        break
                if zone == "B区":
                    diff = 0

            if abs(diff) >= gd.lot_size:
                if diff > 0:
                    cost_amount = diff * price
                    fee = cost_amount * buy_cost_pct / 100
                    if cash >= cost_amount + fee:
                        cash -= cost_amount + fee
                        shares += diff
                        total_cost += fee
                        n_trades += 1
                        trades_log.append({"date": dates[i], "action": "买入",
                                           "price": price, "shares": diff})
                elif diff < 0:
                    sell_n = abs(diff)
                    revenue = sell_n * price
                    fee = revenue * sell_cost_pct / 100
                    cash += revenue - fee
                    shares -= sell_n
                    total_cost += fee
                    n_trades += 1
                    trades_log.append({"date": dates[i], "action": "卖出",
                                       "price": price, "shares": -sell_n})

        eq = cash + shares * price
        equity_grid.append(eq)
        equity_bnh.append(bnh_cash + bnh_shares * price)
        positions_pct.append(shares / gd.max_shares if gd.max_shares > 0 else 0)

    def max_drawdown(eq):
        peak = eq[0]
        dd = 0.0
        for v in eq:
            peak = max(peak, v)
            dd = min(dd, (v - peak) / peak)
        return dd * 100

    return BacktestResult(
        dates=dates,
        equity_grid=equity_grid,
        equity_bnh=equity_bnh,
        positions=positions_pct,
        n_trades=n_trades,
        total_cost=total_cost,
        grid_return=(equity_grid[-1] / gd.capital - 1) * 100,
        bnh_return=(equity_bnh[-1] / gd.capital - 1) * 100,
        grid_maxdd=max_drawdown(equity_grid),
        bnh_maxdd=max_drawdown(equity_bnh),
        trades_log=trades_log,
    )


# ─────────────────────────── 可视化 ───────────────────────────

def plot_results(code: str, df: pd.DataFrame, vp: VolatilityProfile,
                 gd: GridDesign, bt: BacktestResult, output_prefix: str):
    fig, axes = plt.subplots(4, 1, figsize=(14, 18), gridspec_kw={"height_ratios": [3, 2, 2, 2]})
    fig.suptitle(f"{code} 自适应网格交易模型 v2.0", fontsize=16, fontweight="bold", y=0.98)

    ax1 = axes[0]
    ax1.plot(df["date"], df["close"], color="#333", linewidth=1.2, label="收盘价")
    ax1.axhline(gd.overflow_line, color="#FF6B6B", linestyle="--", alpha=0.5, label=f"超涨线 {gd.overflow_line}")
    ax1.axhspan(gd.a_zone_upper, gd.a_zone_lower, alpha=0.12, color="#4ECDC4", label=f"A区 {gd.a_zone_lower}-{gd.a_zone_upper}")
    ax1.axhspan(gd.a_zone_lower, gd.b_zone_lower, alpha=0.12, color="#FFE66D", label=f"B区(防守) {gd.b_zone_lower}-{gd.a_zone_lower}")
    ax1.axhline(gd.stop_loss, color="#FF0000", linestyle="-", alpha=0.7, linewidth=1.5, label=f"止损线 {gd.stop_loss}")

    for i, gl in enumerate(gd.grid_lines):
        if gd.zone_labels[i] == "A区":
            ax1.axhline(gl, color="#4ECDC4", linestyle=":", alpha=0.4, linewidth=0.8)
    ax1.set_title("价格走势与网格区间", fontsize=12)
    ax1.legend(loc="upper right", fontsize=8)
    ax1.set_ylabel("价格 (元)")
    ax1.grid(True, alpha=0.3)

    ax2 = axes[1]
    c = df["close"].values.astype(float)
    returns = np.diff(np.log(c))
    vol_20 = pd.Series(returns).rolling(20).std().dropna().values * np.sqrt(245) * 100
    vol_dates = df["date"].iloc[-len(vol_20):].values
    ax2.fill_between(vol_dates, vol_20, alpha=0.4, color="#9B59B6")
    ax2.plot(vol_dates, vol_20, color="#9B59B6", linewidth=1)
    ax2.axhline(vp.annual_vol, color="#E74C3C", linestyle="--", alpha=0.6,
                label=f"年化波动率 {vp.annual_vol:.1f}%")
    ax2.set_title("20日滚动年化波动率", fontsize=12)
    ax2.set_ylabel("波动率 (%)")
    ax2.legend(fontsize=8)
    ax2.grid(True, alpha=0.3)

    ax3 = axes[2]
    ax3.plot(bt.dates, bt.equity_grid, color="#2ECC71", linewidth=1.5, label=f"网格策略 ({bt.grid_return:+.2f}%)")
    ax3.plot(bt.dates, bt.equity_bnh, color="#E74C3C", linewidth=1.5, label=f"买入持有 ({bt.bnh_return:+.2f}%)")
    ax3.axhline(gd.capital, color="#999", linestyle="--", alpha=0.5)
    ax3.set_title("净值曲线对比", fontsize=12)
    ax3.set_ylabel("资产 (元)")
    ax3.legend(fontsize=9)
    ax3.grid(True, alpha=0.3)

    ax4 = axes[3]
    ax4.fill_between(bt.dates, bt.positions, alpha=0.4, color="#3498DB")
    ax4.plot(bt.dates, bt.positions, color="#3498DB", linewidth=1)
    ax4.set_title("目标仓位变化", fontsize=12)
    ax4.set_ylabel("仓位比例")
    ax4.set_ylim(-0.05, 1.1)
    ax4.grid(True, alpha=0.3)

    plt.tight_layout(rect=[0, 0, 1, 0.96])
    fname = f"{output_prefix}_grid_analysis.png"
    plt.savefig(fname, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"\n  图表已保存: {fname}")


# ─────────────────────────── 报告输出 ───────────────────────────

def print_report(code: str, vp: VolatilityProfile, gd: GridDesign, bt: BacktestResult):
    W = 70
    print("\n" + "=" * W)
    print(f"  {code} 自适应网格交易方案")
    print(f"  生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * W)

    print(f"\n{'─' * W}")
    print("  一、波动特征分析")
    print(f"{'─' * W}")
    print(f"  当前价格:      {vp.close_now:.2f} 元")
    print(f"  区间最高/最低: {vp.close_high:.2f} / {vp.close_low:.2f} 元 (振幅 {(vp.close_high/vp.close_low-1)*100:.1f}%)")
    print(f"  年化波动率:    {vp.annual_vol:.2f}%")
    print(f"  ATR14/价格:    {vp.atr14_median_pct:.2f}% (中位数)")
    print(f"  20日波动率:    当前 {vp.vol_20d_current:.1f}% | 区间 [{vp.vol_20d_min:.1f}%, {vp.vol_20d_max:.1f}%]")
    print(f"  关键分位:      P5={vp.p5:.2f}  P20={vp.p20:.2f}  P50={vp.p50:.2f}  P80={vp.p80:.2f}  P95={vp.p95:.2f}")

    print(f"\n{'─' * W}")
    print("  二、网格方案 (双区联动)")
    print(f"{'─' * W}")
    print(f"  A区(主网格):   {gd.a_zone_lower:.2f} - {gd.a_zone_upper:.2f} 元 ({gd.n_grids_a} 格)")
    print(f"  B区(防守区):   {gd.b_zone_lower:.2f} - {gd.a_zone_lower:.2f} 元 ({gd.n_grids_b} 格, 不加仓)")
    print(f"  步长:          {gd.step:.2f} 元 (~{gd.step_pct:.1f}%)")
    print(f"  超涨清仓线:    {gd.overflow_line:.2f} 元")
    print(f"  止损线:        {gd.stop_loss:.2f} 元")
    print(f"  投入资金:      {gd.capital:,.0f} 元")
    print(f"  满仓股数:      {gd.max_shares} 股 (每格 {gd.shares_per_grid} 股)")

    print(f"\n  {'网格线(元)':>12}  {'目标仓位':>8}  {'区域':>6}  {'操作含义'}")
    print(f"  {'─'*12}  {'─'*8}  {'─'*6}  {'─'*20}")
    for i in range(len(gd.grid_lines)):
        p = gd.grid_lines[i]
        pos = gd.target_positions[i]
        zone = gd.zone_labels[i]
        if pos < 0:
            desc = "强制清仓止损"
            pos_str = "清仓"
        elif pos == 0:
            desc = "清仓观望"
            pos_str = "0%"
        else:
            desc = f"持仓 {int(pos * gd.max_shares)} 股"
            pos_str = f"{pos*100:.0f}%"

        marker = " ◄── 当前价" if i > 0 and i < len(gd.grid_lines) - 1 and \
                 gd.grid_lines[i-1] >= vp.close_now > gd.grid_lines[i] else ""
        if p <= vp.close_now < gd.grid_lines[max(0, i-1)] if i > 0 else False:
            marker = " ◄── 当前价"

        print(f"  {p:>12.2f}  {pos_str:>8}  {zone:>6}  {desc}{marker}")

    print(f"\n{'─' * W}")
    print("  三、回测结果")
    print(f"{'─' * W}")
    print(f"  {'指标':<20} {'网格策略':>12} {'买入持有':>12}")
    print(f"  {'─'*20} {'─'*12} {'─'*12}")
    print(f"  {'区间收益':<20} {bt.grid_return:>+11.2f}% {bt.bnh_return:>+11.2f}%")
    print(f"  {'最大回撤':<20} {bt.grid_maxdd:>+11.2f}% {bt.bnh_maxdd:>+11.2f}%")
    print(f"  {'调仓次数':<20} {bt.n_trades:>12} {'—':>12}")
    print(f"  {'交易成本':<20} {bt.total_cost:>11.0f}元 {'—':>12}")
    improve_ret = bt.grid_return - bt.bnh_return
    improve_dd = bt.bnh_maxdd - bt.grid_maxdd
    print(f"\n  网格改善: 收益 {improve_ret:+.2f}%, 回撤减少 {improve_dd:.2f} 个百分点")

    print(f"\n{'─' * W}")
    print("  四、执行指令 (当前价格)")
    print(f"{'─' * W}")

    target = 0.0
    for i in range(len(gd.grid_lines) - 1):
        if gd.grid_lines[i] >= vp.close_now > gd.grid_lines[i + 1]:
            target = gd.target_positions[i]
            break
    if vp.close_now > gd.grid_lines[0]:
        target = 0.0

    init_shares = int(target * gd.max_shares)
    init_shares = (init_shares // gd.lot_size) * gd.lot_size
    max_affordable = int(gd.capital * 0.99 / vp.close_now)
    max_affordable = (max_affordable // gd.lot_size) * gd.lot_size
    init_shares = min(init_shares, max_affordable)
    init_cost = init_shares * vp.close_now

    print(f"\n  建仓: {vp.close_now:.2f} 元买入 {init_shares} 股 (仓位 {target*100:.0f}%, 金额 {init_cost:,.0f} 元)")
    sell_orders = []
    for i in range(len(gd.grid_lines)):
        if gd.grid_lines[i] > vp.close_now and gd.zone_labels[i] in ("A区", "超涨"):
            tgt = max(0, int(gd.target_positions[i] * gd.max_shares))
            tgt = (tgt // gd.lot_size) * gd.lot_size
            sell_orders.append((gd.grid_lines[i], gd.target_positions[i], tgt))
    sell_orders.sort(key=lambda x: x[0])

    print(f"\n  挂卖单 (上穿减仓):")
    prev_s = init_shares
    for price_line, pos_ratio, tgt in sell_orders:
        diff = prev_s - tgt
        if diff > 0:
            print(f"    {price_line:.2f} 元 → 卖出 {diff} 股 (仓位→{pos_ratio*100:.0f}%)")
            prev_s = tgt

    buy_orders = []
    for i in range(len(gd.grid_lines)):
        if gd.grid_lines[i] < vp.close_now and gd.zone_labels[i] == "A区" and gd.target_positions[i] > target:
            tgt = int(gd.target_positions[i] * gd.max_shares)
            tgt = (tgt // gd.lot_size) * gd.lot_size
            buy_orders.append((gd.grid_lines[i], gd.target_positions[i], tgt))
    buy_orders.sort(key=lambda x: -x[0])

    print(f"\n  挂买单 (下穿加仓):")
    prev_s = init_shares
    for price_line, pos_ratio, tgt in buy_orders:
        diff = tgt - prev_s
        if diff > 0:
            print(f"    {price_line:.2f} 元 → 买入 {diff} 股 (仓位→{pos_ratio*100:.0f}%)")
            prev_s = tgt

    print(f"\n{'─' * W}")
    print("  五、风控规则")
    print(f"{'─' * W}")
    print(f"  1. 防守线: 价格进入 B 区 ({gd.b_zone_lower:.2f}-{gd.a_zone_lower:.2f}), 持仓不动不加仓")
    print(f"  2. 止损线: 收盘价连续2日 < {gd.stop_loss:.2f} 元, 强制清仓")
    print(f"  3. 超涨线: 突破 {gd.overflow_line:.2f} 元, 清仓观望等回落")
    print(f"  4. 滚动更新: 每月重算分位数, 中枢偏移>3%时平移网格")
    print(f"  5. 分红处理: 除权日后按除权价等比调整所有网格线")
    print("=" * W)


# ─────────────────────────── 主程序 ───────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="A股/ETF 自适应网格交易模型",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n  python grid_trader.py 600900\n  python grid_trader.py 513130 --capital 200000\n  python grid_trader.py 159915 --days 120"
    )
    parser.add_argument("code", help="股票/ETF代码 (如 600900, 513130, 159915)")
    parser.add_argument("--capital", type=float, default=100000, help="投入资金 (默认 100000)")
    parser.add_argument("--days", type=int, default=250, help="历史天数 (默认 250)")
    parser.add_argument("--lot", type=int, default=100, help="每手股数 (默认 100)")
    parser.add_argument("--no-plot", action="store_true", help="不生成图表")
    args = parser.parse_args()

    print(f"\n{'='*50}")
    print(f"  网格交易模型 v2.0 — {args.code}")
    print(f"{'='*50}")

    print(f"\n[1/5] 获取数据 ({args.days} 交易日)...")
    df = fetch_data(args.code, args.days)

    print(f"[2/5] 波动特征分析...")
    vp = analyze_volatility(df)

    print(f"[3/5] 自适应网格设计 (资金 {args.capital:,.0f} 元)...")
    gd = design_grid(vp, capital=args.capital, lot_size=args.lot)

    print(f"[4/5] 历史回测...")
    bt = backtest(df, gd)

    print(f"[5/5] 生成报告...")
    print_report(args.code, vp, gd, bt)

    if not args.no_plot:
        plot_results(args.code, df, vp, gd, bt, args.code)

    print(f"\n  完成! 可随时用新代码重新运行。\n")


if __name__ == "__main__":
    main()
