#!/usr/bin/env python3
"""
详细分析布林爬坡信号触发条件
"""
print("=== 布林爬坡信号触发条件详细分析 ===")

# 配置参数
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
}

print("\n1. 核心问题：是满足一小时就信号还是需要连续多小时？")
print("   答案：需要满足当前小时的条件，但可以只有1小时")

print("\n2. 信号触发逻辑分析：")
print("   📊 检测函数: _detect_bollinger_climb()")
print("   📊 步骤分解:")

print("\n   步骤1: 检查最后一根K线（当前小时）")
print("     - 价格位置: 收盘价 > 中轨 且 在上轨±5%范围内")
print("     - buy_ratio: > 0.55（或跳过默认0.5）")
print("     - 量能: > 1.2倍24小时平均量能")
print("     ✅ 当前小时必须满足这些条件")

print("\n   步骤2: 检查HL抬高条件（容忍机制）")
print(f"     - 检查窗口: 最近{BB_CLIMB_CONFIG['hl_tolerance_window']}根K线")
print(f"     - 最少要求: {BB_CLIMB_CONFIG['hl_tolerance_min']}根HL抬高")
print("     - HL抬高定义: 最高价 > 前一根最高价 且 最低价 > 前一根最低价")
print("     ✅ 当前K线位置需要满足HL抬高条件")

print("\n   步骤3: ATR趋势过滤")
print("     - 当前K线振幅（最高-最低）> ATR的50%")
print("     ✅ 确保趋势明确，波动足够")

print("\n   步骤4: 计算连续小时数（consecutive_count）")
print("     - 从当前小时往前回溯")
print("     - 检查每根K线是否满足步骤1-3的条件")
print("     - 直到遇到不满足条件的K线为止")
print("     ✅ 连续小时数 = 连续满足条件的小时数")

print("\n3. 关键理解：")
print("   ❓ 问题：需要连续多少小时才能触发信号？")
print("   ✅ 答案：只需要当前小时满足条件即可触发信号")
print("   📊 连续小时数（consecutive_hours）表示：")
print("      - 信号已经持续了多少小时")
print("      - 不是触发信号所需的最小小时数")

print("\n4. 实际案例验证（HIGHUSDT回溯结果）：")
print("   信号1: 05:00检测到，连续1小时")
print("      → 05:00小时满足所有条件，往前回溯没有其他满足条件的小时")
print("      → consecutive_hours = 1")
print("")
print("   信号2: 06:00检测到，连续2小时")
print("      → 06:00小时满足所有条件")
print("      → 05:00小时也满足所有条件（与信号1相同）")
print("      → consecutive_hours = 2")
print("")
print("   信号3: 16:00检测到，连续3小时")
print("      → 16:00小时满足所有条件")
print("      → 15:00、14:00小时也满足所有条件")
print("      → consecutive_hours = 3")

print("\n5. 信号触发的最小条件：")
print("   ✅ 只需要当前小时满足：")
print("      1. 价格位置条件")
print("      2. buy_ratio条件（或跳过）")
print("      3. 量能条件")
print("      4. HL抬高条件（容忍机制）")
print("      5. ATR条件")
print("   ✅ 不需要之前的小时也满足条件")

print("\n6. 连续小时数的意义：")
print("   📈 连续小时数 = 信号强度指标")
print("   - 1小时: 刚开始突破")
print("   - 2-3小时: 趋势确认")
print("   - 4+小时: 强势趋势")
print("   💡 连续小时数越大，信号越强")

print("\n7. 前端显示逻辑：")
print("   📊 页面显示: '连续X小时'")
print("   - X = consecutive_hours")
print("   - 表示信号已经持续了X小时")
print("   - 不是'需要X小时才能触发'")

print("\n8. 与'需要连续3小时才能触发'的区别：")
print("   ❌ 误解: 需要连续3小时满足条件才能触发信号")
print("   ✅ 实际: 当前小时满足条件即可触发，连续小时数显示信号持续时间")
print("")
print("   举例说明:")
print("   - 情况A: 第1小时满足条件 → 触发信号，显示'连续1小时'")
print("   - 情况B: 第1、2小时满足条件 → 第2小时触发，显示'连续2小时'")
print("   - 情况C: 第1、2、3小时满足条件 → 第3小时触发，显示'连续3小时'")

print("\n9. 面板标题的误导性：")
print("   📝 前端面板标题: '贴着布林上轨连续3小时buy_ratio>55%'")
print("   ❗ 这可能让人误解为'需要连续3小时'")
print("   ✅ 实际检测逻辑: 当前小时满足即可，连续小时数是结果不是前提")

print("\n=== 总结 ===")
print("布林爬坡信号的触发条件是：")
print("1. ✅ 当前小时满足所有条件即可触发信号")
print("2. ✅ 不需要之前的小时也满足条件")
print("3. 📊 '连续X小时'表示信号已经持续了X小时")
print("4. 💪 连续小时数越大，信号强度越高")
print("5. 🎯 策略设计: 捕捉早期突破，跟踪趋势持续")

print("\n所以回答您的问题：")
print("   ❓ '是满足一小时就信号还是？'")
print("   ✅ '满足一小时就可以触发信号，连续小时数显示信号持续时间'")