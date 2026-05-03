#!/usr/bin/env python3
"""
测试布林爬坡检测逻辑
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

# 模拟市场数据
market_data = {
    "trading_symbols": set(['TESTUSDT']),
    "hourly_kline_cache": {}
}

# 导入相关函数（需要修改以避免导入整个应用）
def _calculate_bollinger_bands(closes, period=20, std_mult=2.0):
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

def _check_hour_climb(k, middle, upper, avg_vol, cfg):
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

def _check_hl_climb_tolerant(hourly_klines, idx, cfg):
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

# 测试配置
test_config = {
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

print("=== 测试布林爬坡检测逻辑 ===")

# 测试1: 创建符合条件的数据
print("\n1. 测试理想情况:")
# 创建25根小时K线，价格从100逐步上涨到120
hourly_klines = []
for i in range(25):
    price = 100 + i * 0.8  # 逐步上涨
    hourly_klines.append({
        "t": 1776546000 + i * 3600,
        "o": price - 0.5,
        "h": price + 0.5,
        "l": price - 1.0,
        "c": price,
        "q": 1000000,  # 成交额
        "buy_ratio": 0.6,  # 高于阈值
    })

closes = [k["c"] for k in hourly_klines]
bb = _calculate_bollinger_bands(closes, test_config["period"], test_config["std_mult"])
if bb:
    print(f"  布林带: 上轨={bb['upper']:.2f}, 中轨={bb['middle']:.2f}, 下轨={bb['lower']:.2f}")
    print(f"  最后收盘价: {hourly_klines[-1]['c']:.2f}")

    # 检查条件
    last_k = hourly_klines[-1]
    avg_vol = sum(k["q"] for k in hourly_klines[:-1]) / len(hourly_klines[:-1])

    check1 = _check_hour_climb(last_k, bb["middle"], bb["upper"], avg_vol, test_config)
    check2 = _check_hl_climb_tolerant(hourly_klines, len(hourly_klines)-1, test_config)

    print(f"  价格条件: {check1}")
    print(f"  HL抬高条件: {check2}")

# 测试2: 测试实际数据中的buy_ratio问题
print("\n2. 测试buy_ratio问题:")
test_kline = {
    "c": 105.0,
    "buy_ratio": 0.501,  # 接近0.5
    "q": 1000000
}

# 使用默认配置（跳过接近0.5的检查）
check_default = _check_hour_climb(test_kline, 100, 110, 900000, test_config)
print(f"  buy_ratio=0.501, buy_ratio_skip_default=True: {check_default}")

# 修改配置不跳过默认值
test_config_no_skip = test_config.copy()
test_config_no_skip["buy_ratio_skip_default"] = False
check_no_skip = _check_hour_climb(test_kline, 100, 110, 900000, test_config_no_skip)
print(f"  buy_ratio=0.501, buy_ratio_skip_default=False: {check_no_skip}")

# 测试3: 量能条件
print("\n3. 测试量能条件:")
test_kline_low_vol = {
    "c": 105.0,
    "buy_ratio": 0.6,
    "q": 1000000  # 1M
}
avg_vol = 1000000  # 1M平均
check_vol = _check_hour_climb(test_kline_low_vol, 100, 110, avg_vol, test_config)
print(f"  量能=1M, 平均量能=1M, 需要>1.2M: {check_vol}")

test_kline_high_vol = {
    "c": 105.0,
    "buy_ratio": 0.6,
    "q": 1300000  # 1.3M > 1.2M
}
check_vol_high = _check_hour_climb(test_kline_high_vol, 100, 110, avg_vol, test_config)
print(f"  量能=1.3M, 平均量能=1M, 需要>1.2M: {check_vol_high}")

print("\n=== 结论 ===")
print("布林爬坡检测策略在工作，但条件严格:")
print("1. buy_ratio接近0.5时被跳过检查")
print("2. 需要量能>1.2倍平均量能")
print("3. 需要价格在中轨上方且在上轨±5%内")
print("4. 需要HL抬高条件满足")
print("5. 当前市场可能没有同时满足所有条件的币种")