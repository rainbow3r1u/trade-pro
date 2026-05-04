#!/usr/bin/env python3
"""
OI验证层回测：VOL_SURGE信号出现时，OI方向能否区分真假信号？

理论（文档2.2.1节）：
  价涨 + OI涨 = 真突破，新资金进场
  价涨 + OI跌 = 空头平仓反弹，容易回调

测试方法：
  1. 拉取历史OI数据(15m) + 15m K线
  2. 运行VS检测
  3. 每个VS信号标注OI方向（信号前15min vs 信号后15min）
  4. 比较OI涨 vs OI跌信号的后续收益
"""

import requests
import time
from datetime import datetime, timezone
from collections import defaultdict


def fetch_oi_history(symbol: str, period: str = "15m", limit: int = 500) -> list:
    """拉取历史OI（15m粒度，约5天数据）"""
    resp = requests.get(
        "https://fapi.binance.com/futures/data/openInterestHist",
        params={"symbol": symbol, "period": period, "limit": limit},
        timeout=15
    )
    if resp.status_code != 200:
        return []
    return resp.json()


def fetch_klines(symbol: str, interval: str = "15m", limit: int = 500) -> list:
    """拉取15m K线"""
    resp = requests.get(
        "https://api.binance.com/api/v3/klines",
        params={"symbol": symbol, "interval": interval, "limit": limit},
        timeout=15
    )
    if resp.status_code != 200:
        return []
    data = resp.json()
    result = []
    for k in data:
        result.append({
            "t": int(k[0]) // 1000,
            "o": float(k[1]), "h": float(k[2]),
            "l": float(k[3]), "c": float(k[4]),
            "v": float(k[5]), "q": float(k[7]),
        })
    return result


def detect_vol_surge(klines: list, min_ratio: float = 1.0, min_gain: float = 2.3) -> list:
    """检测VS信号，返回 [(idx, ratio, gain), ...]"""
    signals = []
    for i in range(16, len(klines)):
        avg_vol = sum(klines[j]["q"] for j in range(i - 16, i)) / 16.0
        if avg_vol <= 0:
            continue
        current_vol = klines[i]["q"]
        ratio = current_vol / avg_vol

        if ratio < min_ratio:
            continue

        gain = (klines[i]["c"] - klines[i]["o"]) / klines[i]["o"] * 100
        if gain < min_gain:
            continue

        signals.append((i, round(ratio, 2), round(gain, 2)))
    return signals


def align_oi_to_kline(oi_data: list, klines: list, kline_idx: int) -> float:
    """找到最接近K线时间戳的OI值"""
    kt = klines[kline_idx]["t"]
    best_oi = None
    best_diff = float("inf")
    for r in oi_data:
        oi_ts = int(r["timestamp"]) // 1000
        diff = abs(oi_ts - kt)
        if diff < best_diff:
            best_diff = diff
            best_oi = float(r["sumOpenInterest"])
    return best_oi


def test_oi_validation(symbol: str):
    """对单个币种测试OI验证层"""
    print(f"\n{'='*60}")
    print(f"测试: {symbol}")
    print(f"{'='*60}")

    # 拉数据
    oi = fetch_oi_history(symbol)
    kls = fetch_klines(symbol)

    if not oi or not kls:
        print(f"  数据拉取失败: OI={len(oi)} Klines={len(kls)}")
        return None

    print(f"  OI数据: {len(oi)}条, K线: {len(kls)}条")

    # 对齐时间范围
    oi_times = {int(r["timestamp"]) // 1000: float(r["sumOpenInterest"]) for r in oi}

    # VS检测
    vs_signals = detect_vol_surge(kls)
    print(f"  VS信号: {len(vs_signals)}个")

    if len(vs_signals) < 3:
        print(f"  信号太少，跳过")
        return None

    results = {"oi_up": [], "oi_down": [], "oi_flat": []}

    for k_idx, ratio, gain in vs_signals:
        kt = kls[k_idx]["t"]
        close = kls[k_idx]["c"]

        # OI: 当前 vs 15min前
        oi_now = oi_times.get(kt)
        oi_prev = oi_times.get(kt - 900)  # 15分钟前

        if oi_now is None or oi_prev is None or oi_prev == 0:
            continue

        oi_chg = (oi_now - oi_prev) / oi_prev * 100

        # 后续收益（30分钟、60分钟）
        fwd_30 = None
        fwd_60 = None
        if k_idx + 2 < len(klines):
            fwd_30 = (kls[k_idx + 2]["c"] - close) / close * 100
        if k_idx + 4 < len(klines):
            fwd_60 = (kls[k_idx + 4]["c"] - close) / close * 100

        entry = {
            "time": kt,
            "ratio": ratio,
            "gain_pct": gain,
            "oi_chg_pct": round(oi_chg, 4),
            "fwd_30m": round(fwd_30, 4) if fwd_30 else None,
            "fwd_60m": round(fwd_60, 4) if fwd_60 else None,
        }

        if oi_chg > 0.1:
            results["oi_up"].append(entry)
        elif oi_chg < -0.1:
            results["oi_down"].append(entry)
        else:
            results["oi_flat"].append(entry)

    return results


def aggregate(results_list: list, label: str):
    """汇总统计"""
    if not results_list:
        return None

    n = len(results_list)
    fwd30s = [r["fwd_30m"] for r in results_list if r["fwd_30m"] is not None]
    fwd60s = [r["fwd_60m"] for r in results_list if r["fwd_60m"] is not None]

    avg_30 = sum(fwd30s) / len(fwd30s) if fwd30s else 0
    avg_60 = sum(fwd60s) / len(fwd60s) if fwd60s else 0

    wins_30 = sum(1 for v in fwd30s if v > 0)
    wins_60 = sum(1 for v in fwd60s if v > 0)

    wr_30 = wins_30 / len(fwd30s) * 100 if fwd30s else 0
    wr_60 = wins_60 / len(fwd60s) * 100 if fwd60s else 0

    return {
        "n": n,
        "avg_fwd_30m": round(avg_30, 3),
        "avg_fwd_60m": round(avg_60, 3),
        "wr_30m": round(wr_30, 1),
        "wr_60m": round(wr_60, 1),
        "avg_oi_chg": round(sum(r["oi_chg_pct"] for r in results_list) / n, 4),
    }


if __name__ == "__main__":
    print("=" * 60)
    print("OI验证层回测：VOL_SURGE + OI方向 → 信号质量")
    print("=" * 60)

    # 选一些流动性好的合约币种
    test_symbols = [
        "DOGEUSDT", "SOLUSDT", "LINKUSDT", "AVAXUSDT",
        "ARBUSDT", "LDOUSDT", "INJUSDT", "APTUSDT",
        "OPUSDT", "SUIUSDT",
    ]

    all_up = []
    all_down = []
    all_flat = []

    for sym in test_symbols:
        r = test_oi_validation(sym)
        if r:
            all_up.extend(r["oi_up"])
            all_down.extend(r["oi_down"])
            all_flat.extend(r["oi_flat"])
        time.sleep(0.3)  # rate limit

    print(f"\n\n{'='*60}")
    print(f"全量汇总")
    print(f"{'='*60}")

    for name, data in [("OI涨(真突破)", all_up), ("OI跌(假反弹)", all_down), ("OI平", all_flat)]:
        stats = aggregate(data, name)
        if stats:
            print(f"\n  📊 {name}: {stats['n']}个信号")
            print(f"     平均30m收益: {stats['avg_fwd_30m']:+.2f}%  |  胜率: {stats['wr_30m']:.0f}%")
            print(f"     平均60m收益: {stats['avg_fwd_60m']:+.2f}%  |  胜率: {stats['wr_60m']:.0f}%")
            print(f"     平均OI变化: {stats['avg_oi_chg']:+.2f}%")

    # 结论
    up_stats = aggregate(all_up, "")
    down_stats = aggregate(all_down, "")
    if up_stats and down_stats:
        print(f"\n  {'='*50}")
        print(f"  🔬 验证结论:")
        diff_30 = up_stats["avg_fwd_30m"] - down_stats["avg_fwd_30m"]
        diff_60 = up_stats["avg_fwd_60m"] - down_stats["avg_fwd_60m"]
        print(f"    OI涨信号 vs OI跌信号:")
        print(f"      30m收益差: {diff_30:+.3f}%")
        print(f"      60m收益差: {diff_60:+.3f}%")
        print(f"      胜率差30m: {up_stats['wr_30m'] - down_stats['wr_30m']:+.0f}%")
        print(f"      胜率差60m: {up_stats['wr_60m'] - down_stats['wr_60m']:+.0f}%")
        if diff_60 > 0 and (up_stats['wr_60m'] - down_stats['wr_60m']) > 0:
            print(f"\n    ✅ 文档理论得到验证: OI涨的VS信号确实优于OI跌的信号")
        else:
            print(f"\n    ⚠️ 当前数据不支持该理论，可能需要更大样本")
