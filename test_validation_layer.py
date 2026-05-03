#!/usr/bin/env python3
"""
测试文档《全职交易数据基础设施》中的验证层理论。

测试项：
  1. OI(持仓量) 验证 — 价量关系辨别真假突破
  2. 资金费率 过滤 — 做多成本过高时跳过
  3. 多空比 极端值 — 拥挤交易反转预警

数据源：币安合约REST API (免费，无需认证)
"""

import requests
import time
import json
from collections import defaultdict

# ============================================================
# 1. OI 验证层测试
# ============================================================
def test_oi_validation():
    """
    理论（文档2.2.1节）：
      - 价涨 + OI涨 = 真突破，新资金进场做多
      - 价涨 + OI跌 = 空头平仓反弹，容易回调
      - 价跌 + OI涨 = 空头增仓，别抄底
      - 价跌 + OI跌 = 多头平仓，下跌趋势确认
    """
    print("=" * 60)
    print("测试1: OI持仓量验证 — 价量关系矩阵")
    print("=" * 60)

    # 取一些热门合约币种
    test_symbols = [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT",
        "LINKUSDT", "AVAXUSDT", "ADAUSDT", "INJUSDT",
        "ARBUSDT", "LDOUSDT", "OPUSDT", "APTUSDT",
    ]

    results = []
    sess = requests.Session()

    for sym in test_symbols:
        try:
            # 合约24h ticker (价格+成交量)
            tk = sess.get("https://fapi.binance.com/fapi/v1/ticker/24hr",
                         params={"symbol": sym}, timeout=10).json()

            # OI 数据
            oi = sess.get("https://fapi.binance.com/fapi/v1/openInterest",
                         params={"symbol": sym}, timeout=10).json()

            price_change = float(tk.get("priceChangePercent", 0))
            price = float(tk.get("lastPrice", 0))
            vol_24h = float(tk.get("quoteVolume", 0))
            oi_val = float(oi.get("openInterest", 0))

            results.append({
                "symbol": sym,
                "price": price,
                "price_chg_pct": price_change,
                "vol_24h": vol_24h,
                "oi": oi_val,
            })
        except Exception as e:
            print(f"  {sym}: API错误 - {e}")

    # 排序展示
    results.sort(key=lambda x: -abs(x["price_chg_pct"]))

    print(f"\n{'币种':<12} {'价格':>10} {'24h涨跌':>8} {'24h量(M)':>10} {'OI(万张)':>10} {'OI象限':>12}")
    print("-" * 70)
    for r in results:
        if r["price_chg_pct"] > 1 and r["oi"] > 0:
            quadrant = "🟢 真突破?"
        elif r["price_chg_pct"] > 1:
            quadrant = "🟡 空头平仓?"
        elif r["price_chg_pct"] < -1 and r["oi"] > 0:
            quadrant = "🔴 空头增仓!"
        elif r["price_chg_pct"] < -1:
            quadrant = "🔴 多头平仓!"
        else:
            quadrant = "⚪ 正常"

        print(f"{r['symbol']:<12} {r['price']:>10.4f} {r['price_chg_pct']:>+7.2f}% "
              f"{r['vol_24h']/1e6:>10.1f} {r['oi']/1e4:>10.1f} {quadrant:>12}")

    # 关键发现：如果VOL_SURGE触发在"空头平仓"象限，信号不可信
    up_coins = [r for r in results if r["price_chg_pct"] > 0.5]
    if up_coins:
        print(f"\n📊 上涨币种共{len(up_coins)}个")
        print("  其中OI也涨的(真突破):", end=" ")
        # Need historical OI to determine OI direction, not just level
        print("需要60秒后第二次采样才能判断OI方向 → 测试2")


# ============================================================
# 2. OI 方向检测 (需要两次采样)
# ============================================================
def test_oi_direction():
    """
    两次采样间隔60秒，检测OI变化方向。
    模拟真实场景：VOL_SURGE触发时查OI是涨是跌。
    """
    print("\n" + "=" * 60)
    print("测试2: OI方向检测 — 60秒间隔双采样")
    print("=" * 60)

    sess = requests.Session()
    test_symbols = ["BTCUSDT", "ETHUSDT", "DOGEUSDT", "SOLUSDT", "LINKUSDT"]

    # 第一次采样
    snap1 = {}
    for sym in test_symbols:
        try:
            oi = sess.get("https://fapi.binance.com/fapi/v1/openInterest",
                         params={"symbol": sym}, timeout=10).json()
            tk = sess.get("https://fapi.binance.com/fapi/v1/ticker/24hr",
                         params={"symbol": sym}, timeout=10).json()
            snap1[sym] = {
                "oi": float(oi.get("openInterest", 0)),
                "price": float(tk.get("lastPrice", 0)),
            }
        except Exception as e:
            print(f"  {sym}: 第一次采样失败 - {e}")

    print(f"  第1次采样: {len(snap1)} 币种")
    print("  等待60秒进行第2次采样...")
    time.sleep(60)

    # 第二次采样
    print(f"\n{'币种':<12} {'价格变化':>10} {'OI变化':>12} {'OI方向':>10} {'验证结论':>20}")
    print("-" * 75)
    for sym in test_symbols:
        try:
            oi2 = sess.get("https://fapi.binance.com/fapi/v1/openInterest",
                          params={"symbol": sym}, timeout=10).json()
            tk2 = sess.get("https://fapi.binance.com/fapi/v1/ticker/24hr",
                          params={"symbol": sym}, timeout=10).json()

            oi_new = float(oi2.get("openInterest", 0))
            price_new = float(tk2.get("lastPrice", 0))

            if sym not in snap1:
                continue

            oi_old = snap1[sym]["oi"]
            price_old = snap1[sym]["price"]

            oi_chg = (oi_new - oi_old) / oi_old * 100 if oi_old > 0 else 0
            price_chg = (price_new - price_old) / price_old * 100

            if price_chg > 0 and oi_chg > 0.1:
                verdict = "✅ 真突破(跟进)"
            elif price_chg > 0 and oi_chg < -0.1:
                verdict = "⚠️ 空头平仓(不跟)"
            elif price_chg < 0 and oi_chg > 0.1:
                verdict = "❌ 空头增仓(回避)"
            elif price_chg < 0 and oi_chg < -0.1:
                verdict = "❌ 多头平仓(回避)"
            else:
                verdict = "⚪ OI平稳"

            print(f"{sym:<12} {price_chg:>+9.4f}% {oi_chg:>+11.4f}% "
                  f"{'涨' if oi_chg>0.05 else '跌' if oi_chg<-0.05 else '平':>10} {verdict:>20}")

            snap1[sym] = {"oi": oi_new, "price": price_new}

        except Exception as e:
            print(f"  {sym}: 第二次采样失败 - {e}")

    print("\n💡 这就是文档里说的: 价涨+OI涨才是真突破。价涨+OI跌是空头平仓反弹，不要追。")


# ============================================================
# 3. 资金费率过滤
# ============================================================
def test_funding_rate_filter():
    """
    理论（文档2.2.1节）：
      - 资金费率 > 0.05% 且持续上升：做多成本高，不要追多
      - 资金费率 < -0.05%：空头拥挤，做多更安全
      - 资金费率从正变负：趋势可能转向
    """
    print("\n" + "=" * 60)
    print("测试3: 资金费率过滤 — 做多成本检测")
    print("=" * 60)

    sess = requests.Session()

    # 取所有合约的premium index
    try:
        resp = sess.get("https://fapi.binance.com/fapi/v1/premiumIndex", timeout=10)
        all_funding = resp.json()
    except Exception as e:
        print(f"  获取资金费率失败: {e}")
        return

    # 筛选USDT交易对，按费率排序
    usdt_funding = []
    for item in all_funding:
        sym = item.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        rate = float(item.get("lastFundingRate", 0))
        usdt_funding.append((sym, rate))

    usdt_funding.sort(key=lambda x: -x[1])

    # 极端正值（做多成本高）
    print(f"\n  总合约数: {len(usdt_funding)}")
    print(f"\n  🔴 资金费率最高 (做多成本高，应跳过):")
    for sym, rate in usdt_funding[:10]:
        bar = "█" * min(int(abs(rate) * 10000), 20)
        print(f"    {sym:<14} {rate:>+8.4%}  {bar}")

    print(f"\n  🟢 资金费率最低 (空头拥挤，做多反而安全):")
    for sym, rate in usdt_funding[-10:]:
        bar = "█" * min(int(abs(rate) * 10000), 20)
        print(f"    {sym:<14} {rate:>+8.4%}  {bar}")

    high_funding = [s for s, r in usdt_funding if r > 0.0005]  # > 0.05%
    low_funding = [s for s, r in usdt_funding if r < -0.0005]  # < -0.05%

    print(f"\n  📊 统计:")
    print(f"    费率 > 0.05% (做多贵): {len(high_funding)} 个币种 — VOL_SURGE在这些币种上应跳过")
    print(f"    费率 < -0.05% (做多便宜): {len(low_funding)} 个币种 — 做多更安全")
    print(f"    费率正常范围: {len(usdt_funding) - len(high_funding) - len(low_funding)} 个币种")

    if high_funding:
        print(f"\n  ⚠️ 如果当前有BB/VOL_SURGE信号在以下币种上，考虑跳过:")
        print(f"     {', '.join(high_funding[:5])}")


# ============================================================
# 4. 多空比极端值检测
# ============================================================
def test_long_short_ratio():
    """
    理论（文档2.2.1节）：
      - 多空比 > 3.0：市场极度看多，小心反转
      - 多空比 < 0.5：市场极度看空，可能是底部
      - 多空比 0.8-1.5：正常，策略正常执行
    """
    print("\n" + "=" * 60)
    print("测试4: 多空比极端值 — 拥挤交易预警")
    print("=" * 60)

    sess = requests.Session()

    # 尝试获取BTC和ETH的多空比
    test_symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT"]
    periods = ["5m", "15m", "1h"]

    for sym in test_symbols:
        try:
            ls = sess.get(
                "https://fapi.binance.com/futures/data/globalLongShortAccountRatio",
                params={"symbol": sym, "period": "15m", "limit": 5},
                timeout=10
            ).json()

            if not ls:
                print(f"  {sym}: 无数据")
                continue

            latest = ls[-1]
            ls_ratio = float(latest.get("longShortRatio", 0))
            long_pct = float(latest.get("longAccount", 0)) * 100

            if ls_ratio > 3.0:
                flag = "🔴 极度看多! 小心反转"
            elif ls_ratio > 2.0:
                flag = "🟡 偏多"
            elif ls_ratio < 0.5:
                flag = "🟢 极度看空! 可能底部"
            elif ls_ratio < 0.8:
                flag = "🟢 偏空"
            else:
                flag = "⚪ 正常"

            print(f"  {sym:<12} 多空比={ls_ratio:.2f}  多头={long_pct:.0f}%  {flag}")

        except Exception as e:
            print(f"  {sym}: API错误 - {e}")

    print("\n  💡 如果某币的多空比 > 3.0，该币的VOL_SURGE信号应降级处理")


# ============================================================
# 5. Fear & Greed Index
# ============================================================
def test_fear_greed():
    """
    理论（文档2.3.3节）：
      - 极端恐惧(<20)：信号可信度高，可以适当加仓
      - 极端贪婪(>80)：警惕回调，信号慎开
    """
    print("\n" + "=" * 60)
    print("测试5: Fear & Greed 指数")
    print("=" * 60)

    try:
        resp = requests.get("https://api.alternative.me/fng/?limit=7", timeout=10)
        data = resp.json()
        entries = data.get("data", [])

        for i, e in enumerate(entries[:7]):
            val = int(e["value"])
            cls = e["value_classification"]
            ts = int(e["timestamp"])
            date = time.strftime("%m-%d", time.gmtime(ts))

            if val <= 20:
                emoji = "🟢"
                advice = "极端恐惧 → 信号可信度高"
            elif val <= 40:
                emoji = "🟢"
                advice = "恐惧 → 偏安全"
            elif val <= 60:
                emoji = "⚪"
                advice = "中性 → 正常交易"
            elif val <= 80:
                emoji = "🟡"
                advice = "贪婪 → 谨慎"
            else:
                emoji = "🔴"
                advice = "极端贪婪 → 信号慎开!"

            prefix = "→" if i == 0 else "  "
            print(f"  {prefix} {date}  {emoji} {val}  {cls:<20} {advice}")

        current = int(entries[0]["value"])
        print(f"\n  📊 当前: {current}")
        if current > 80:
            print("  ⚠️ 建议: 减少新开仓，收紧止损")
        elif current < 20:
            print("  ✅ 建议: 可以适当加仓，信号质量通常更高")

    except Exception as e:
        print(f"  获取失败: {e}")


# ============================================================
# 综合验证层模拟
# ============================================================
def test_signal_validation_simulation():
    """
    模拟文档 3.3 节的决策流程：
    当VOL_SURGE触发时，走一遍完整的验证层逻辑。
    """
    print("\n" + "=" * 60)
    print("测试6: 综合验证层模拟 — 决策流程走一遍")
    print("=" * 60)

    sess = requests.Session()

    # 模拟：假设DOGEUSDT触发了VOL_SURGE信号
    test_cases = [
        {"symbol": "DOGEUSDT", "signal": "VOL_SURGE", "ratio": 4.5, "price": 0.0},
        {"symbol": "LINKUSDT", "signal": "VOL_SURGE", "ratio": 3.2, "price": 0.0},
    ]

    for case in test_cases:
        sym = case["symbol"]
        print(f"\n  ── 模拟: {sym} 触发 {case['signal']} (ratio={case['ratio']}) ──")

        decisions = []

        # Step 1: 基础检查
        decisions.append(("基础", "✅", "余额充足"))

        # Step 2: 获取OI
        try:
            oi = sess.get("https://fapi.binance.com/fapi/v1/openInterest",
                         params={"symbol": sym}, timeout=10).json()
            oi_val = float(oi.get("openInterest", 0))
            decisions.append(("OI检查", "✅", f"OI={oi_val/1e4:.1f}万张 (需要60s后再采样判断方向)"))
        except:
            decisions.append(("OI检查", "⚠️", "API失败，跳过"))

        # Step 3: 获取资金费率
        try:
            prem = sess.get("https://fapi.binance.com/fapi/v1/premiumIndex",
                          params={"symbol": sym}, timeout=10).json()
            rate = float(prem.get("lastFundingRate", 0))
            if rate > 0.0005:
                decisions.append(("资金费率", "❌ 跳过", f"费率={rate:.4%} > 0.05%，做多成本过高"))
            elif rate < -0.0005:
                decisions.append(("资金费率", "✅ 加分", f"费率={rate:.4%}，空头拥挤做多安全"))
            else:
                decisions.append(("资金费率", "✅", f"费率={rate:.4%}，正常"))
        except:
            decisions.append(("资金费率", "⚠️", "API失败，跳过"))

        # Step 4: 多空比
        try:
            ls = sess.get(
                "https://fapi.binance.com/futures/data/globalLongShortAccountRatio",
                params={"symbol": sym, "period": "15m", "limit": 1},
                timeout=10
            ).json()
            if ls:
                ratio = float(ls[0].get("longShortRatio", 1))
                if ratio > 3.0:
                    decisions.append(("多空比", "⚠️ 降级", f"多空比={ratio:.1f}，极度看多可能反转"))
                elif ratio < 0.5:
                    decisions.append(("多空比", "✅ 加分", f"多空比={ratio:.2f}，极度看空可能是底"))
                else:
                    decisions.append(("多空比", "✅", f"多空比={ratio:.2f}，正常"))
        except:
            decisions.append(("多空比", "⚠️", "API失败，跳过"))

        # Step 5: 价格波动
        try:
            tk = sess.get("https://fapi.binance.com/fapi/v1/ticker/24hr",
                         params={"symbol": sym}, timeout=10).json()
            chg = float(tk.get("priceChangePercent", 0))
            if abs(chg) > 15:
                decisions.append(("日涨幅", "❌ 跳过", f"24h涨跌={chg:+.1f}%，波动过大"))
            else:
                decisions.append(("日涨幅", "✅", f"24h涨跌={chg:+.1f}%"))
        except:
            decisions.append(("日涨幅", "⚠️", "API失败"))

        # 汇总
        print(f"  {'检查项':<12} {'结果':<10} {'详情'}")
        print(f"  {'-'*50}")
        skip = False
        for step, result, detail in decisions:
            print(f"  {step:<12} {result:<10} {detail}")
            if "跳过" in result:
                skip = True

        if skip:
            print(f"  ==> 最终: ❌ 跳过 {sym} — 验证层未通过")
        else:
            score = sum(1 for _, r, _ in decisions if "加分" in r)
            print(f"  ==> 最终: {'✅ 开仓' if score >= 0 else '⚪ 可开仓'} — 验证层通过 (加分项:{score})")


if __name__ == "__main__":
    print("╔══════════════════════════════════════════════════════╗")
    print("║   《全职交易数据基础设施》验证层理论测试              ║")
    print("║   测试时间:", time.strftime("%Y-%m-%d %H:%M UTC"), "               ║")
    print("╚══════════════════════════════════════════════════════╝")

    test_oi_validation()
    test_funding_rate_filter()
    test_long_short_ratio()
    test_fear_greed()

    print("\n\n⏳ 等待60秒后进行OI方向双采样测试...")
    test_oi_direction()
    test_signal_validation_simulation()

    print("\n" + "=" * 60)
    print("测试完成。")
    print("=" * 60)
    print("""
总结 — 当前可实现的理论：

  ✅ 可直接实现:
     1. OI验证层 — 60秒轮询，VOL_SURGE触发时查OI方向 (需历史回测验证效果)
     2. 资金费率过滤 — 1小时拉一次，费率>0.05%的币种跳过做多
     3. 多空比预警 — 15分钟拉一次，极端值触发降级
     4. Fear & Greed — 每天一次，极端值调整整体仓位

  ⚠️ 需要更多工作:
     5. 深度验证 — 需要WebSocket depth20接入
     6. BTC ETF流量 — 需要网页抓取/手动

  ❌ 暂不可行:
     7. 宏观日历自动解析 — 手动更适合
     8. 全自动交易日志 — 需新建系统
""")
