#!/usr/bin/env python3
"""
回溯HIGHUSDT过去2天的布林爬坡策略
"""
import requests
import json
import time
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

print("=== HIGHUSDT布林爬坡策略回溯（2天） ===")

# 布林爬坡配置
BB_CLIMB_CONFIG = {
    "period": 20,
    "std_mult": 2,
    "upper_tolerance_pct": 0.05,
    "buy_ratio_threshold": 0.55,
    "buy_ratio_skip_default": True,
    "volume_ratio": 1.2,
    "hl_tolerance_window": 3,
    "hl_tolerance_min": 2,
    "atr_period": 14,
    "atr_enabled": True,
    "exclude_symbols": {'BTCUSDT', 'ETHUSDT', 'SOLUSDT'},
}

def fetch_hourly_klines(symbol="HIGHUSDT", limit=50):
    """获取小时K线数据（2天约48小时，多取一些）"""
    try:
        url = "https://fapi.binance.com/fapi/v1/klines"
        params = {
            "symbol": symbol,
            "interval": "1h",
            "limit": limit
        }

        print(f"获取{symbol}的{limit}根1小时K线...")
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        klines = []
        for k in data:
            klines.append({
                "t": int(k[0]) // 1000,
                "o": float(k[1]),
                "h": float(k[2]),
                "l": float(k[3]),
                "c": float(k[4]),
                "v": float(k[5]),
                "q": float(k[7]),
                "buy_ratio": 0.5,  # 默认值
            })

        print(f"获取到 {len(klines)} 根K线")
        return klines

    except Exception as e:
        print(f"获取K线数据失败: {e}")
        return []

def calculate_bollinger_bands(closes, period=20, std_mult=2.0):
    """计算布林带"""
    if len(closes) < period:
        return None

    recent_closes = closes[-period:]
    middle = sum(recent_closes) / period
    variance = sum((c - middle) ** 2 for c in recent_closes) / period
    std = variance ** 0.5

    return {
        "upper": middle + std_mult * std,
        "middle": middle,
        "lower": middle - std_mult * std
    }

def calculate_atr(hourly_klines, period=14):
    """计算ATR"""
    if len(hourly_klines) < period + 1:
        return None

    true_ranges = []
    for i in range(len(hourly_klines) - period, len(hourly_klines)):
        k = hourly_klines[i]
        prev_k = hourly_klines[i - 1]
        tr = max(
            k["h"] - k["l"],
            abs(k["h"] - prev_k["c"]),
            abs(k["l"] - prev_k["c"])
        )
        true_ranges.append(tr)

    return sum(true_ranges) / len(true_ranges) if true_ranges else None

def check_hour_climb(k, middle, upper, avg_vol, cfg):
    """检查单根K线条件"""
    if k["c"] <= middle:
        return False
    tolerance = upper * cfg["upper_tolerance_pct"]
    if not (upper - tolerance <= k["c"] <= upper + tolerance):
        return False

    if cfg.get("buy_ratio_skip_default", True) and abs(k.get("buy_ratio", 0.5) - 0.5) < 0.001:
        pass
    elif k["buy_ratio"] <= cfg["buy_ratio_threshold"]:
        return False

    if avg_vol > 0 and k.get("q", 0) < avg_vol * cfg["volume_ratio"]:
        return False

    return True

def check_hl_climb_tolerant(hourly_klines, idx, cfg):
    """检查HL抬高"""
    window = cfg["hl_tolerance_window"]
    min_count = cfg["hl_tolerance_min"]

    climb_count = 0
    check_start = max(0, idx - window + 1)
    for i in range(check_start, idx + 1):
        if i == 0:
            climb_count += 1
            continue
        k = hourly_klines[i]
        prev_k = hourly_klines[i - 1]
        if k["h"] > prev_k["h"] and k["l"] > prev_k["l"]:
            climb_count += 1

    return climb_count >= min_count

def detect_bollinger_climb(symbol, hourly_klines, cfg):
    """检测布林爬坡信号"""
    if symbol in cfg["exclude_symbols"]:
        return None

    if len(hourly_klines) < max(cfg["period"] + 1, cfg["atr_period"] + 1):
        return None

    closes = [k["c"] for k in hourly_klines]
    bb = calculate_bollinger_bands(closes, cfg["period"], cfg["std_mult"])
    if not bb:
        return None

    middle = bb["middle"]
    upper = bb["upper"]

    atr = calculate_atr(hourly_klines, cfg["atr_period"]) if cfg["atr_enabled"] else None

    avg_volumes = [k.get("q", 0) for k in hourly_klines[:-1]]
    avg_vol = sum(avg_volumes) / len(avg_volumes) if avg_volumes else 0

    last_k = hourly_klines[-1]
    if not check_hour_climb(last_k, middle, upper, avg_vol, cfg):
        return None

    if not check_hl_climb_tolerant(hourly_klines, len(hourly_klines) - 1, cfg):
        return None

    if atr is not None and atr > 0:
        current_range = last_k["h"] - last_k["l"]
        if current_range < atr * 0.5:
            return None

    consecutive_count = 1
    for i in range(len(hourly_klines) - 2, -1, -1):
        k = hourly_klines[i]
        if not check_hour_climb(k, middle, upper, avg_vol, cfg):
            break
        if not check_hl_climb_tolerant(hourly_klines, i, cfg):
            break
        consecutive_count += 1

    valid_hours = hourly_klines[-consecutive_count:]

    return {
        "symbol": symbol,
        "upper": round(upper, 6),
        "middle": round(middle, 6),
        "lower": round(bb["lower"], 6),
        "atr": round(atr, 6) if atr else None,
        "consecutive_hours": consecutive_count,
        "avg_volume_24h": round(avg_vol, 2),
        "last_close": round(last_k["c"], 6),
        "last_volume": round(last_k["q"], 2),
        "last_buy_ratio": round(last_k.get("buy_ratio", 0.5), 3),
    }

def sliding_window_detection(klines, cfg):
    """滑动窗口检测过去2天的信号"""
    signals = []

    # 需要至少25根K线进行检测
    if len(klines) < 25:
        print(f"数据不足，需要至少25根，当前只有{len(klines)}根")
        return signals

    print(f"\n=== 滑动窗口检测（{len(klines)}根K线） ===")

    # 从第25根开始滑动检测
    for i in range(24, len(klines)):
        window_klines = klines[i-24:i+1]  # 25根K线窗口
        signal = detect_bollinger_climb("HIGHUSDT", window_klines, cfg)

        if signal:
            signal_time = datetime.fromtimestamp(window_klines[-1]["t"])
            signals.append({
                "time": signal_time,
                "signal": signal
            })

    return signals

def main():
    # 获取HIGHUSDT的小时K线数据（2天约48小时，取50根）
    symbol = "HIGHUSDT"
    klines = fetch_hourly_klines(symbol, limit=50)

    if not klines:
        print("无法获取K线数据")
        return

    # 分析K线数据
    print(f"\n=== HIGHUSDT K线数据分析 ===")
    print(f"时间范围: {len(klines)} 小时")

    df = pd.DataFrame(klines)
    df['datetime'] = pd.to_datetime(df['t'], unit='s')

    print(f"时间范围: {df['datetime'].min()} 到 {df['datetime'].max()}")
    print(f"价格范围: {df['c'].min():.4f} - {df['c'].max():.4f}")
    print(f"平均价格: {df['c'].mean():.4f}")
    print(f"平均成交量: {df['q'].mean():.2f}")

    price_change = ((df['c'].iloc[-1] - df['c'].iloc[0]) / df['c'].iloc[0]) * 100
    print(f"价格变化: {price_change:.2f}%")

    # 显示最近10根K线
    print(f"\n最近10根K线:")
    for i in range(min(10, len(df))):
        row = df.iloc[-(i+1)]
        dt = row['datetime'].strftime('%m-%d %H:%M')
        print(f"  {dt}: 开={row['o']:.4f}, 高={row['h']:.4f}, 低={row['l']:.4f}, 收={row['c']:.4f}, 量={row['q']:.2f}")

    # 滑动窗口检测过去2天的信号
    signals = sliding_window_detection(klines, BB_CLIMB_CONFIG)

    if signals:
        print(f"\n✅ 检测到 {len(signals)} 个布林爬坡信号:")
        for sig in signals:
            s = sig["signal"]
            print(f"\n时间: {sig['time'].strftime('%Y-%m-%d %H:%M')}")
            print(f"  连续小时: {s['consecutive_hours']}")
            print(f"  布林带: 上轨={s['upper']}, 中轨={s['middle']}")
            print(f"  收盘价: {s['last_close']}")
            print(f"  成交量: {s['last_volume']} (平均: {s['avg_volume_24h']})")
            print(f"  ATR: {s['atr']}")
    else:
        print(f"\n❌ 过去2天未检测到布林爬坡信号")

        # 分析最近一次检测失败原因
        print(f"\n=== 最近一次检测分析 ===")
        if len(klines) >= 25:
            test_klines = klines[-25:]

            closes = [k["c"] for k in test_klines]
            bb = calculate_bollinger_bands(closes, BB_CLIMB_CONFIG["period"], BB_CLIMB_CONFIG["std_mult"])

            if bb:
                last_k = test_klines[-1]
                avg_vol = sum(k.get("q", 0) for k in test_klines[:-1]) / len(test_klines[:-1])

                print(f"布林带: 上轨={bb['upper']:.4f}, 中轨={bb['middle']:.4f}")
                print(f"最后收盘价: {last_k['c']:.4f}")

                # 检查各个条件
                price_above_middle = last_k["c"] > bb["middle"]
                tolerance = bb["upper"] * BB_CLIMB_CONFIG["upper_tolerance_pct"]
                price_near_upper = (bb["upper"] - tolerance <= last_k["c"] <= bb["upper"] + tolerance)
                print(f"价格条件: 高于中轨={price_above_middle}, 接近上轨={price_near_upper}")

                buy_ratio = last_k.get("buy_ratio", 0.5)
                buy_ratio_check = buy_ratio > BB_CLIMB_CONFIG["buy_ratio_threshold"]
                skip_default = BB_CLIMB_CONFIG.get("buy_ratio_skip_default", True) and abs(buy_ratio - 0.5) < 0.001
                print(f"buy_ratio条件: buy_ratio={buy_ratio:.3f}, 检查通过={buy_ratio_check}, 跳过默认={skip_default}")

                volume_check = last_k.get("q", 0) >= avg_vol * BB_CLIMB_CONFIG["volume_ratio"]
                print(f"量能条件: 当前量能={last_k.get('q', 0):.2f}, 需要>{avg_vol * BB_CLIMB_CONFIG['volume_ratio']:.2f}, 检查通过={volume_check}")

                hl_check = check_hl_climb_tolerant(test_klines, len(test_klines)-1, BB_CLIMB_CONFIG)
                print(f"HL抬高条件: 检查通过={hl_check}")

                atr = calculate_atr(test_klines, BB_CLIMB_CONFIG["atr_period"])
                if atr:
                    current_range = last_k["h"] - last_k["l"]
                    atr_check = current_range >= atr * 0.5
                    print(f"ATR条件: ATR={atr:.4f}, 当前振幅={current_range:.4f}, 检查通过={atr_check}")

if __name__ == "__main__":
    main()