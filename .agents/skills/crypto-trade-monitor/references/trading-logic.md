# 交易策略详细逻辑

## 策略检测（market_monitor_app.py）

### VOL_SURGE（成交量突增）

- **触发条件**: 当前15分钟成交量 > 过去4小时均值 × 3.0
- **额外过滤**:
  - 排除稳定币对（USDCUSDT, RLUSDUSDT 等）
  - 要求涨幅 > 0%（`gain_pct > 0`）
  - 4小时均值 ≥ 5000 USDT（防止极端ratio）
- **信号双通道存储**:
  - `vol_surge_symbols` → **5分钟**有效期，`cleanup_surge_records()` 清理 >300s，供 **交易脚本** 使用
  - `vol_surge_history` → **1小时**保留，`cleanup_vol_surge_history()` 清理 >3600s，供 **前端展示** 使用
  - 检测通过时两者同时写入，`vol_surge_history` 会更新为最新（同币种去重）
- **API 返回**: `/api/vol_surge` 返回1小时数据，带 `tradeable` 布尔标记（`true`=5分钟内可交易）
- **前端展示**: 可交易信号红色高亮+绿色剩余时间；已过期信号灰色低透明度
- **排序**: 按 `ratio`（当前/均值）降序

### SURGE（大单突增）

- **触发条件**: 大单成交量突增（具体阈值见代码）
- **额外过滤**:
  - 信号5分钟内有效
  - 双阴过滤：最近3根1h K线中有≥2根收阴（`close < open`）则跳过
  - 24h成交量 ≥ 300万 USDT
- **排序**: 按24h成交量降序

### BB_CLIMB（布林爬坡）

- **触发条件**: 价格沿布林带上轨爬升
- **额外过滤**:
  - 无信号时效限制
  - 24h成交量 ≥ 300万 USDT
- **排序**: 按24h成交量降序

### BB_CAND

- **状态**: 已禁用，代码保留但不开仓

## 自动交易（sim_trade.py）

### 买入优先级

```
1. VOL_SURGE → 20U margin (全仓 BASE_MARGIN)
2. SURGE     → 10U margin (半仓)
3. BB_CLIMB  → 5U margin  (1/4仓)
```

每个策略过滤后独立按各自规则排序，然后按优先级尝试开仓。
最多同时持仓 **5** 个币种。

### 开仓流程

```python
def open_position(symbol, margin, leverage, signal_type):
    # 1. 检查可用余额 >= margin
    # 2. 检查无重复持仓
    # 3. 计算 position_value = margin * leverage
    # 4. 获取当前价格作为 entry_price
    # 5. 计算止损价（见下方）
    # 6. 创建持仓字典，append 到 positions
    # 7. 立即 save_state()
    # ⚠️ 绝不执行 account["balance"] -= margin
```

### 止损计算

```python
def calculate_stop_loss_price(symbol, entry_price, leverage):
    # 第一优先级：前1h K线最低价
    klines_1h = get_1h_klines(symbol, limit=1)
    low_1h = float(klines_1h[0][3])  # k[3] = low
    
    if low_1h < entry_price:
        return low_1h
    
    # 回退：前4h K线最低价
    klines_4h = get_1h_klines(symbol, limit=4)
    low_4h = min(float(k[3]) for k in klines_4h)
    return min(low_4h, entry_price)
    # 若4h_low也 >= entry_price，止损设在 entry_price
```

### 止盈

- 盈利达到 **保证金的50%** 时触发
- 10x杠杆下 = 价格上涨 **5%**
- 公式: `tp_price = entry_price * (1 + TAKE_PROFIT_PCT/100 / leverage)`

### 平仓

```python
def close_position(pos, reason, close_price, pnl):
    # 手续费按 position_value 计算（开仓+平仓双向）
    # fee = position_value * 0.0004 * 2
    actual_pnl = pnl - position_value * 0.0004 * 2
    account["balance"] += actual_pnl
    
    if reason == "STOP_LOSS":
        set_cooldown(symbol, 30)  # 30分钟内不再开仓
```

### 联合保证金爆仓

```python
def check_positions():
    total_unrealized = sum(calculate_pnl(p) for p in positions)
    total_equity = account["balance"] + total_unrealized
    
    if total_equity <= 0 and positions:
        # 1. 逐仓强平所有 position
        # 2. account["balance"] = INITIAL_CAPITAL (100)
        # 3. positions.clear()
        # 4. save_state()
```

## 追涨追踪器（Momentum Tracker）

- **检测频率**: 每10秒（独立计数器）
- **追踪范围**: 当日涨幅 5% ~ 10%
- **目标**: 估算到达10%涨幅还需多少成交量
- **公式**: `vol_needed = (10 - gain_pct) / gain_pct * vol_24h`
- **最大追踪时长**: 4小时，超时自动移除
- **达标**: 涨幅 ≥ 10% 标记为 "reached"
