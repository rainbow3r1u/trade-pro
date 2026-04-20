#!/usr/bin/env python3
import json
import requests
import sys

# 测试布林爬坡检测
print("=== 测试布林爬坡检测 ===")

# 1. 获取布林爬坡信号
try:
    resp = requests.get("http://localhost:5000/api/bollinger_climb", timeout=10)
    data = resp.json()
    print(f"API返回: count={data.get('count', 0)}, candidate_count={data.get('candidate_count', 0)}")

    if data.get('count', 0) > 0:
        print(f"找到 {data['count']} 个布林爬坡信号:")
        for signal in data['data'][:3]:  # 只显示前3个
            print(f"  {signal['symbol']}: 连续{signal['consecutive_hours']}小时, 上轨={signal['upper']}, 中轨={signal['middle']}")

    if data.get('candidate_count', 0) > 0:
        print(f"找到 {data['candidate_count']} 个候选信号:")
        for candidate in data['candidates'][:3]:
            print(f"  {candidate['symbol']}: 连续{candidate['consecutive_hours']}小时候选")

except Exception as e:
    print(f"API请求失败: {e}")

# 2. 获取调试信息
print("\n=== 调试信息 ===")
try:
    resp = requests.get("http://localhost:5000/api/debug_state", timeout=10)
    debug = resp.json()
    print(f"小时K线缓存币种数: {debug.get('hourly_cache_symbol_count', 0)}")
    print(f"布林回填完成: {debug.get('bb_backfill_done', False)}")

    # 检查样本数据
    sample = debug.get('hourly_cache_sample', {})
    if sample:
        symbol = list(sample.keys())[0]
        print(f"样本币种 {symbol} 有 {sample[symbol]} 根小时K线")

except Exception as e:
    print(f"调试API请求失败: {e}")

# 3. 测试单个币种的小时K线数据
print("\n=== 测试单个币种数据 ===")
try:
    # 获取一个币种的小时K线数据（通过分钟K线聚合）
    resp = requests.get("http://localhost:5000/api/minute_buy_ratio/BTCUSDT", timeout=10)
    btc_data = resp.json()
    print(f"BTCUSDT分钟数据: {btc_data.get('count', 0)} 条")

    # 检查buy_ratio值
    if btc_data.get('data'):
        for kline in btc_data['data'][-3:]:  # 最近3条
            print(f"  时间: {kline['t']}, buy_ratio: {kline.get('buy_ratio', 0.5)}")

except Exception as e:
    print(f"单个币种测试失败: {e}")

# 4. 检查配置
print("\n=== 布林爬坡配置 ===")
config = {
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
    "candidate_enabled": True,
    "candidate_near_hours": 3,
    "candidate_vol_ratio": 0.8
}

print("配置摘要:")
print(f"  布林周期: {config['period']}, 标准差倍数: {config['std_mult']}")
print(f"  上轨容忍: ±{config['upper_tolerance_pct']*100}%")
print(f"  buy_ratio阈值: {config['buy_ratio_threshold']} (跳过默认0.5: {config['buy_ratio_skip_default']})")
print(f"  量能倍数: {config['volume_ratio']}x")
print(f"  HL抬高容忍: {config['hl_tolerance_min']}/{config['hl_tolerance_window']}")
print(f"  ATR周期: {config['atr_period']} (启用: {config['atr_enabled']})")
print(f"  排除币种: {config['exclude_symbols']}")
print(f"  候选机制: {config['candidate_enabled']} ({config['candidate_near_hours']}小时)")