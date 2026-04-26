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

### 关键常量

```python
MAX_POSITIONS = 5
DEFAULT_LEVERAGE = 10
TAKE_PROFIT_PCT = 50          # 止盈：盈利达保证金50%（10x=价格涨5%）
SURGE_MIN_BUY_RATIO = 0.70    # SURGE买入比最低70%
VOL_SURGE_MIN_RATIO = 3.0     # VOL_SURGE突增倍数最低3.0x
SURGE_MIN_DELTA_Q = 500000    # SURGE大单突增最低50万USDT
MIN_24H_VOLUME = 3_000_000    # 24h成交额最低300万USDT

EXCLUDE_SYMBOLS = {
    'BTCUSDT', 'ETHUSDT', 'SOLUSDT',
    'TSLAUSDT', 'NVDAUSDT', 'AMZNUSDT', 'GOOGLUSDT', 'AAPLUSDT',
    'COINUSDT', 'MSTRUSDT', 'METAUSDT', 'TSMUSDT',
    'XAUUSDT', 'XAGUSDT', 'XAUTUSDT', 'NATGASUSDT',
    # 稳定币对
    'USDCUSDT', 'RLUSDUSDT', 'UUSDT', 'XUSDUSDT', 'USD1USDT',
    'FDUSDUSDT', 'TUSDUSDT', 'PAXUSDT', 'BUSDUSDT', 'SUSDUSDT',
}
```

### 买入优先级与仓位分配

```
1. VOL_SURGE → 20U margin (全仓 BASE_MARGIN)
2. SURGE     → 10U margin (半仓)
3. BB_CLIMB  → 5U margin  (1/4仓)
4. BB_CAND   → 禁用
```

每个策略过滤后独立按各自规则排序，然后按优先级尝试开仓。
最多同时持仓 **5** 个币种。

### 可用余额计算

```python
def get_available_balance():
    used_margin = sum(p["margin"] for p in positions)
    return account["balance"] - used_margin
```

**注意**: `account["balance"]` 是总资金池，`open_position()` **不得** 再执行 `balance -= margin`。

### 开仓前置检查

```python
def open_position(symbol, signal_type, entry_price, ...):
    if symbol in EXCLUDE_SYMBOLS: return False      # 排除列表
    if get_position(symbol): return False           # 已在持仓中
    if is_in_cooldown(symbol): return False         # 冷却期内
    if get_positions_count() >= MAX_POSITIONS: return False  # 满仓
    if get_available_balance() < 5: return False    # 可用余额不足5U
    ...
```

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

### 信号获取与过滤（交易脚本端）

```python
def get_vol_surge_signals():
    signals = api_get("/api/vol_surge").get("data", [])
    return [s for s in signals 
            if s.get("ratio", 0) >= VOL_SURGE_MIN_RATIO 
            and time.time() - s.get("start_time", 0) < 300]

def get_surge_signals():
    signals = api_get("/api/surge").get("data", [])
    return [s for s in signals 
            if s.get("avg_buy_ratio", 0) >= SURGE_MIN_BUY_RATIO 
            and time.time() - s.get("last_t", 0) < 300]

def get_bb_climb_signals():
    data = api_get("/api/bollinger_climb")
    # BB爬坡缓存超过5分钟，信号废弃
    if time.time() - data.get("updated_at", 0) > 300:
        return []
    return data.get("data", [])
```

### 收阴过滤（双阴过滤）

```python
def check_recent_1h_candles_bearish(symbol: str) -> bool:
    """检查最近3根1h K线中是否有至少2根收阴（close < open）
    返回 True 表示不应开仓"""
    resp = requests.get("https://api.binance.com/api/v3/klines",
                        params={"symbol": symbol, "interval": "1h", "limit": 3}, timeout=5)
    klines = resp.json()
    bearish_count = sum(1 for k in klines if float(k[4]) < float(k[1]))
    return bearish_count >= 2
```

VOL_SURGE 和 SURGE 策略开仓前都必须通过此过滤。

### 止损冷却期

```python
def set_cooldown(symbol: str, minutes: int = 30):
    """止损平仓后30分钟内不再开仓同一币种"""
    cooldown_symbols[symbol] = time.time() + minutes * 60

def is_in_cooldown(symbol: str) -> bool:
    return time.time() < cooldown_symbols.get(symbol, 0)
```

### 日止盈冷却（5次封顶）

**规则**：同一币种在当天（北京时间8:00为日界）止盈满 **5 次**后，冷却到**次日北京8:00**才恢复开仓。

```python
def record_take_profit_and_check_cooldown(symbol: str):
    date_str = get_beijing_date_str()  # 北京8:00=UTC 00:00
    key = (symbol, date_str)
    daily_take_profit_count[key] = daily_take_profit_count.get(key, 0) + 1
    
    if daily_take_profit_count[key] >= 5:
        # 冷却到次日北京8:00
        tomorrow_utc = (now_utc + timedelta(days=1)).replace(hour=0, minute=0, second=0)
        daily_tp_cooldown_symbols[symbol] = tomorrow_utc.timestamp()
```

**触发时机**：`close_position()` 中 `reason == "TAKE_PROFIT"` 时自动调用。

**检查时机**：`open_position()` 中前置检查，与止损冷却独立并行。

**状态持久化**：`daily_take_profit_count` 和 `daily_tp_cooldown_symbols` 随 `save_state()` 写入 `/tmp/sim_trade_state.json`。

### evaluate_and_open() 完整流程

```python
def evaluate_and_open():
    # 1. VOL_SURGE（第一优先级）
    signals = get_vol_surge_signals()
    # 过滤: EXCLUDE_SYMBOLS、重复持仓、ratio<3.0、24h成交量<300万
    # 按24h成交量降序排列
    # 逐个开仓，每个前检查双阴过滤
    
    # 2. SURGE（第二优先级）
    signals = get_surge_signals()
    # 过滤: EXCLUDE_SYMBOLS、重复持仓、total_delta_q<50万、24h成交量<300万
    # 按24h成交量降序排列
    # 逐个开仓，每个前检查双阴过滤
    
    # 3. BB_CLIMB（第三优先级）
    signals = get_bb_climb_signals()
    # 过滤: EXCLUDE_SYMBOLS、重复持仓、consecutive_hours<阈值、24h成交量<300万
    # 按24h成交量降序排列
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
    # REPLACE_VOL_SURGE 不触发冷却期，也不重置账户
```

### VOL_SURGE 满仓替换机制

```python
def close_weakest_position():
    """满仓且有高倍VOL_SURGE时，替换最弱持仓"""
    # 计算所有持仓未实现盈亏
    pnl_list = [(pos, calculate_pnl(...), current_price) for pos in positions]
    
    profitable = [(p, pnl, price) for p, pnl, price in pnl_list if pnl > 0]
    losing = [(p, pnl, price) for p, pnl, price in pnl_list if pnl <= 0]
    
    if profitable:
        pos_to_close, pnl, close_price = max(profitable, key=lambda x: x[1])  # 盈利最高
    else:
        pos_to_close, pnl, close_price = max(losing, key=lambda x: x[1])      # 亏损最小
    
    close_position(pos_to_close, "REPLACE_VOL_SURGE", close_price, pnl)
```

**触发条件**：
- `get_positions_count() >= MAX_POSITIONS`（满仓5/5）
- 且存在 VOL_SURGE 信号 `ratio >= 5.0`

**替换策略**：
- 有盈利的持仓 → 平盈利最高的（落袋为安）
- 都亏损 → 平亏损最小的（止损代价最低）

**日志**：`[VOL_SURGE替换] 满仓且有N个高倍突增信号(ratio>=5.0)，替换最弱持仓`

### 现货/合约阴阳质检（SPOT_FUTURES_DIVERGENCE）

**目的**: 极端行情下，币安现货与U本位永续合约的1h K线可能出现阴阳不一致。基于现货数据开仓后，若合约实际走势与现货方向相反，需要立即纠错平仓。

**触发时机**: `open_position()` 成功开仓后**立即执行**，一次性质检，查完即结束。

**逻辑**:
```python
def check_spot_futures_divergence_once(symbol: str) -> bool:
    # 1. 同时查询现货和合约各3根1h K线
    spot = requests.get("https://api.binance.com/api/v3/klines", 
                        params={"symbol": symbol, "interval": "1h", "limit": 3})
    futures = requests.get("https://fapi.binance.com/fapi/v1/klines",
                           params={"symbol": symbol, "interval": "1h", "limit": 3})
    
    # 2. 只取前两根（最近两根已完成K线），忽略第三根（进行中）
    for i in range(2):
        spot_bullish = float(spot[i][4]) > float(spot[i][1])      # 阳?
        futures_bullish = float(futures[i][4]) > float(futures[i][1])  # 阳?
        
        # 3. 只看阴阳方向，不看具体数值差异
        if spot_bullish != futures_bullish:
            return True  # 不一致，应平仓
    
    return False
```

**处理**:
- 若返回 `True` → 立即 `close_position(pos, "SPOT_FUTURES_DIVERGENCE", entry_price, 0)`
- `open_position()` 返回 `False`（仓位已被平掉）
- 不触发冷却期（不是止损）

**关键约束**:
- 只查已持仓币种（最多5个），无REST限流压力
- 只看阴阳方向（`close > open` 还是 `close < open`），不看价格数值差距
- 只检查已完成的最近2根K线，忽略进行中K线
- 一次性质检，不保存状态，不轮询

### 联合保证金爆仓

```python
def check_positions():
    total_unrealized = sum(calculate_pnl(p) for p in positions)
    total_equity = account["balance"] + total_unrealized
    
    if total_equity <= 0 and positions:
        # 1. 全部仓位标记为 LIQUIDATED_CROSS，逐仓强平
        # 2. handle_liquidation() 重置账户
        # 3. account["balance"] = INITIAL_CAPITAL (100)
        # 4. positions.clear()
        # 5. trade_log.clear()
        # 6. save_state()
```

**核心规则**：
- **不设单个仓位爆仓价**。`liquidation_price` 字段仅作参考，不用于触发强平
- **唯一爆仓条件**：`total_equity = balance + unrealized_pnl <= 0`
- **全部一起强平**：一旦触发，所有持仓同时平仓，不是逐个清算
- **日志标记**：平仓原因用 `LIQUIDATED_CROSS`，打印 `[联合爆仓强平] {symbol}`
- **账户重置**：`handle_liquidation()` 保存历史记录到 `/tmp/sim_trade_history_*.json`，然后重置为100U

## 追涨追踪器（Momentum Tracker）

- **检测频率**: 每10秒（独立计数器）
- **追踪范围**: 当日涨幅 5% ~ 10%
- **目标**: 估算到达10%涨幅还需多少成交量
- **公式**: `vol_needed = (10 - gain_pct) / gain_pct * vol_24h`
- **最大追踪时长**: 4小时，超时自动移除
- **达标**: 涨幅 ≥ 10% 标记为 "reached"
