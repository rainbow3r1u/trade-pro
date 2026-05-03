#!/usr/bin/env python3
import requests
import json

# 检查小时K线数据中的buy_ratio分布
print("=== 检查小时K线数据buy_ratio分布 ===")

# 获取一些币种的分钟数据，查看buy_ratio
symbols_to_check = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'ADAUSDT', 'DOGEUSDT', 'XRPUSDT']

for symbol in symbols_to_check:
    try:
        resp = requests.get(f"http://localhost:5003/api/minute_buy_ratio/{symbol}", timeout=5)
        data = resp.json()
        if data.get('data'):
            klines = data['data']
            buy_ratios = [k.get('buy_ratio', 0.5) for k in klines]
            avg_buy_ratio = sum(buy_ratios) / len(buy_ratios) if buy_ratios else 0.5
            min_br = min(buy_ratios)
            max_br = max(buy_ratios)

            # 统计接近0.5的比例
            near_default = sum(1 for br in buy_ratios if abs(br - 0.5) < 0.01)
            near_default_pct = near_default / len(buy_ratios) * 100

            print(f"{symbol}: {len(klines)}条, buy_ratio平均={avg_buy_ratio:.3f}, 范围=[{min_br:.3f}, {max_br:.3f}], 接近0.5: {near_default_pct:.1f}%")
    except Exception as e:
        print(f"{symbol}: 错误 - {e}")

# 检查是否有突增检测信号（delta_q突增）
print("\n=== 检查delta_q突增信号 ===")
try:
    resp = requests.get("http://localhost:5003/api/surge", timeout=5)
    data = resp.json()
    print(f"突增信号数量: {data.get('count', 0)}")
    if data.get('data'):
        for surge in data['data'][:5]:
            print(f"  {surge['symbol']}: {surge['count']}次突增, 总delta_q={surge['total_delta_q']:,.0f}, 平均buy_ratio={surge['avg_buy_ratio']:.3f}")
except Exception as e:
    print(f"突增API错误: {e}")

# 测试布林候选信号
print("\n=== 分析可能的问题 ===")
print("可能原因:")
print("1. buy_ratio接近0.5，被跳过检查 (buy_ratio_skip_default=True)")
print("2. 量能条件不满足 (需要>1.2倍平均量能)")
print("3. 价格位置不满足 (需要在中轨上方且在上轨±5%内)")
print("4. HL抬高条件不满足 (3根中至少2根HL抬高)")
print("5. ATR过滤不满足 (当前振幅>ATR的50%)")
print("6. 排除币种: BTCUSDT, ETHUSDT, SOLUSDT")

# 建议调整
print("\n=== 建议调整 ===")
print("如果要降低检测门槛，可以:")
print("1. 降低buy_ratio_threshold (如0.52)")
print("2. 设置buy_ratio_skip_default=False")
print("3. 降低volume_ratio (如1.1)")
print("4. 调整hl_tolerance_min (如1/3)")
print("5. 调整upper_tolerance_pct (如0.1)")
print("6. 禁用ATR过滤 (atr_enabled=False)")