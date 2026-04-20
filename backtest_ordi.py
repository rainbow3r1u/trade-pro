#!/usr/bin/env python3
"""
回溯ORDIUSDT的布林爬坡预警策略
"""
import requests
import json
import time
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

print("=== ORDIUSDT布林爬坡策略回溯 ===")

# 布林爬坡配置（使用当前应用的配置）
BB_CLIMB_CONFIG = {
    "period": 20,                    # 布林周期
    "std_mult": 2,                   # 标准差倍数
    "upper_tolerance_pct": 0.05,    # 收盘价在上轨±5%范围内
    "buy_ratio_threshold": 0.55,    # buy_ratio阈值
    "buy_ratio_skip_default": True, # API默认0.5的buy_ratio跳过检查
    "volume_ratio": 1.2,            # 量能倍数
    "hl_tolerance_window": 3,       # HL抬高容忍窗口
    "hl_tolerance_min": 2,          # 窗口内最少HL抬高次数
    "atr_period": 14,               # ATR周期
    "atr_enabled": True,            # 是否启用ATR趋势过滤
    "exclude_symbols": {'BTCUSDT', 'ETHUSDT', 'SOLUSDT'},
}

# 从币安API获取ORDIUSDT的小时K线数据
def fetch_hourly_klines(symbol="ORDIUSDT", limit=24):
    """从币安API获取小时K线数据"""
    try:
        url = "https://fapi.binance.com/fapi/v1/klines"
        params = {
            "symbol": symbol,
            "interval": "1h",
            "limit": limit
        }

        print(f"从币安API获取{symbol}的{limit}根1小时K线...")
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        klines = []
        for k in data:
            # 币安K线格式: [时间戳, 开盘价, 最高价, 最低价, 收盘价, 成交量, 结束时间, 成交额, 交易数, 主动买入成交量, 主动买入成交额, 忽略]
            klines.append({
                "t": int(k[0]) // 1000,  # 秒时间戳
                "o": float(k[1]),
                "h": float(k[2]),
                "l": float(k[3]),
                "c": float(k[4]),
                "v": float(k[5]),  # 成交量
                "q": float(k[7]),  # 成交额
                "buy_ratio": 0.5,  # 币安API不提供buy_ratio，使用默认值
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
    """计算ATR（Average True Range）"""
    if len(hourly_klines) < period + 1:
        return None

    true_ranges = []
    for i in range(len(hourly_klines) - period, len(hourly_klines)):
        k = hourly_klines[i]
        prev_k = hourly_klines[i - 1]
        tr = max(
            k["h"] - k["l"],              # 当日振幅
            abs(k["h"] - prev_k["c"]),    # 当日最高与昨收的差
            abs(k["l"] - prev_k["c"])     # 当日最低与昨收的差
        )
        true_ranges.append(tr)

    return sum(true_ranges) / len(true_ranges) if true_ranges else None

def check_hour_climb(k, middle, upper, avg_vol, cfg):
    """检查单根K线的独立条件"""
    # 1. 收盘价 > 中轨 且 在上轨±5%范围内
    if k["c"] <= middle:
        return False
    tolerance = upper * cfg["upper_tolerance_pct"]
    if not (upper - tolerance <= k["c"] <= upper + tolerance):
        return False

    # 2. buy_ratio > 0.55（仅对真实数据检查，默认0.5跳过）
    if cfg.get("buy_ratio_skip_default", True) and abs(k.get("buy_ratio", 0.5) - 0.5) < 0.001:
        pass  # 默认0.5，跳过买比检查
    elif k["buy_ratio"] <= cfg["buy_ratio_threshold"]:
        return False

    # 3. 量能 > 1.2倍均量
    if avg_vol > 0 and k.get("q", 0) < avg_vol * cfg["volume_ratio"]:
        return False

    return True

def check_hl_climb_tolerant(hourly_klines, idx, cfg):
    """检查HL抬高条件（带容忍机制）"""
    window = cfg["hl_tolerance_window"]
    min_count = cfg["hl_tolerance_min"]

    # 收集窗口内的HL抬高次数
    climb_count = 0
    check_start = max(0, idx - window + 1)
    for i in range(check_start, idx + 1):
        if i == 0:
            climb_count += 1  # 第一根默认算
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

    # 计算布林带
    closes = [k["c"] for k in hourly_klines]
    bb = calculate_bollinger_bands(closes, cfg["period"], cfg["std_mult"])
    if not bb:
        return None

    middle = bb["middle"]
    upper = bb["upper"]

    # 计算ATR
    atr = calculate_atr(hourly_klines, cfg["atr_period"]) if cfg["atr_enabled"] else None

    # 计算24小时平均成交量（不含最后一根）
    avg_volumes = [k.get("q", 0) for k in hourly_klines[:-1]]
    avg_vol = sum(avg_volumes) / len(avg_volumes) if avg_volumes else 0

    # 检查最后一根K线的独立条件
    last_k = hourly_klines[-1]
    if not check_hour_climb(last_k, middle, upper, avg_vol, cfg):
        return None

    # 检查HL容忍抬高
    if not check_hl_climb_tolerant(hourly_klines, len(hourly_klines) - 1, cfg):
        return None

    # ATR趋势过滤
    if atr is not None and atr > 0:
        current_range = last_k["h"] - last_k["l"]
        if current_range < atr * 0.5:
            return None

    # 往前计算持续了几小时
    consecutive_count = 1
    for i in range(len(hourly_klines) - 2, -1, -1):
        k = hourly_klines[i]
        if not check_hour_climb(k, middle, upper, avg_vol, cfg):
            break
        if not check_hl_climb_tolerant(hourly_klines, i, cfg):
            break
        consecutive_count += 1

    # 只取最后consecutive_count根K线
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
        "valid_hours": [{
            "t": h["t"],
            "o": round(h["o"], 6),
            "h": round(h["h"], 6),
            "l": round(h["l"], 6),
            "c": round(h["c"], 6),
            "v": round(h.get("v", 0), 2),
            "buy_ratio": round(h["buy_ratio"], 3)
        } for h in valid_hours]
    }

def analyze_klines(klines):
    """分析K线数据"""
    if not klines:
        print("没有K线数据")
        return

    print(f"\n=== ORDIUSDT K线数据分析 ===")
    print(f"时间范围: {len(klines)} 小时")

    # 转换为DataFrame便于分析
    df = pd.DataFrame(klines)
    df['datetime'] = pd.to_datetime(df['t'], unit='s')

    print(f"时间范围: {df['datetime'].min()} 到 {df['datetime'].max()}")
    print(f"价格范围: {df['c'].min():.4f} - {df['c'].max():.4f}")
    print(f"平均价格: {df['c'].mean():.4f}")
    print(f"平均成交量: {df['q'].mean():.2f}")
    print(f"总成交量: {df['q'].sum():.2f}")

    # 计算价格变化
    price_change = ((df['c'].iloc[-1] - df['c'].iloc[0]) / df['c'].iloc[0]) * 100
    print(f"价格变化: {price_change:.2f}%")

    # 显示最近5根K线
    print(f"\n最近5根K线:")
    for i in range(min(5, len(df))):
        row = df.iloc[-(i+1)]
        print(f"  {row['datetime']}: 开={row['o']:.4f}, 高={row['h']:.4f}, 低={row['l']:.4f}, 收={row['c']:.4f}, 量={row['q']:.2f}")

def main():
    # 获取ORDIUSDT的小时K线数据
    symbol = "ORDIUSDT"
    klines = fetch_hourly_klines(symbol, limit=30)  # 获取30根，确保有足够数据

    if not klines:
        print("无法获取K线数据")
        return

    # 分析K线数据
    analyze_klines(klines)

    # 应用布林爬坡检测
    print(f"\n=== 应用布林爬坡检测 ===")

    # 需要至少25根K线（20周期布林+额外数据）
    if len(klines) >= 25:
        # 使用最后25根K线进行检测
        test_klines = klines[-25:]

        print(f"使用最后 {len(test_klines)} 根K线进行检测")

        # 检测信号
        signal = detect_bollinger_climb(symbol, test_klines, BB_CLIMB_CONFIG)

        if signal:
            print(f"✅ 检测到布林爬坡信号!")
            print(f"   币种: {signal['symbol']}")
            print(f"   连续小时: {signal['consecutive_hours']}")
            print(f"   布林带: 上轨={signal['upper']}, 中轨={signal['middle']}, 下轨={signal['lower']}")
            print(f"   最后收盘价: {signal['last_close']}")
            print(f"   最后成交量: {signal['last_volume']}")
            print(f"   平均成交量: {signal['avg_volume_24h']}")
            print(f"   ATR: {signal['atr']}")
            print(f"   buy_ratio: {signal['last_buy_ratio']}")

            print(f"\n有效K线 ({signal['consecutive_hours']}小时):")
            for i, h in enumerate(signal['valid_hours'][-5:]):  # 显示最后5根
                dt = datetime.fromtimestamp(h['t']).strftime('%Y-%m-%d %H:%M')
                print(f"   {dt}: 收={h['c']}, 买比={h['buy_ratio']}, 量={h['v']}")
        else:
            print(f"❌ 未检测到布林爬坡信号")

            # 分析失败原因
            print(f"\n=== 失败原因分析 ===")

            # 计算布林带
            closes = [k["c"] for k in test_klines]
            bb = calculate_bollinger_bands(closes, BB_CLIMB_CONFIG["period"], BB_CLIMB_CONFIG["std_mult"])

            if bb:
                last_k = test_klines[-1]
                avg_vol = sum(k.get("q", 0) for k in test_klines[:-1]) / len(test_klines[:-1])

                print(f"布林带: 上轨={bb['upper']:.4f}, 中轨={bb['middle']:.4f}")
                print(f"最后收盘价: {last_k['c']:.4f}")

                # 检查各个条件
                # 1. 价格位置
                price_above_middle = last_k["c"] > bb["middle"]
                tolerance = bb["upper"] * BB_CLIMB_CONFIG["upper_tolerance_pct"]
                price_near_upper = (bb["upper"] - tolerance <= last_k["c"] <= bb["upper"] + tolerance)
                print(f"价格条件: 高于中轨={price_above_middle}, 接近上轨={price_near_upper}")

                # 2. buy_ratio条件
                buy_ratio = last_k.get("buy_ratio", 0.5)
                buy_ratio_check = buy_ratio > BB_CLIMB_CONFIG["buy_ratio_threshold"]
                skip_default = BB_CLIMB_CONFIG.get("buy_ratio_skip_default", True) and abs(buy_ratio - 0.5) < 0.001
                print(f"buy_ratio条件: buy_ratio={buy_ratio:.3f}, 阈值={BB_CLIMB_CONFIG['buy_ratio_threshold']}, 检查通过={buy_ratio_check}, 跳过默认={skip_default}")

                # 3. 量能条件
                volume_check = last_k.get("q", 0) >= avg_vol * BB_CLIMB_CONFIG["volume_ratio"]
                print(f"量能条件: 当前量能={last_k.get('q', 0):.2f}, 平均量能={avg_vol:.2f}, 需要>{avg_vol * BB_CLIMB_CONFIG['volume_ratio']:.2f}, 检查通过={volume_check}")

                # 4. HL抬高条件
                hl_check = check_hl_climb_tolerant(test_klines, len(test_klines)-1, BB_CLIMB_CONFIG)
                print(f"HL抬高条件: 检查通过={hl_check}")

                # 5. ATR条件
                atr = calculate_atr(test_klines, BB_CLIMB_CONFIG["atr_period"])
                if atr:
                    current_range = last_k["h"] - last_k["l"]
                    atr_check = current_range >= atr * 0.5
                    print(f"ATR条件: ATR={atr:.4f}, 当前振幅={current_range:.4f}, 需要>{atr*0.5:.4f}, 检查通过={atr_check}")
    else:
        print(f"K线数据不足，需要至少25根，当前只有{len(klines)}根")

if __name__ == "__main__":
    main()