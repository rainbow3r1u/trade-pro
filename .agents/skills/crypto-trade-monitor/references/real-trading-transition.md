# 实盘交易替换方案：二次信号过滤模块

## 概述

将 `sim_trade.py` 从"模拟交易"升级为"实盘自动交易"，核心定位是**二次信号过滤模块**：
- **信号源**（market_monitor_app.py, 5003端口）→ 提供原始策略信号
- **二次过滤**（新实盘交易模块）→ 对信号做风控校验、K线过滤、现货合约质检
- **执行层**（币安U本位合约API）→ 真实下单、持仓同步、止盈止损监控

## 核心约束

| 约束 | 说明 |
|------|------|
| 交易标的 | 币安U本位永续合约 (`fapi.binance.com`) |
| 止盈止损 | 本地监控价格触发 → 下市价单平仓（非币安条件单） |
| 资金规模 | <500 USDT，最多5个同时持仓 |
| 杠杆 | 固定10x（开仓前自动设置） |
| 信号源 | 不变，继续轮询5003端口 |
| 只做多 | 当前代码只做多，实盘保持此约束 |

## 架构设计

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              信号生成层（不变）                               │
├─────────────────────────────────────────────────────────────────────────────┤
│  market_monitor_app.py (5003端口)                                           │
│       │                                                                     │
│       ├── /api/vol_surge    → VOL_SURGE信号（5分钟有效）                     │
│       ├── /api/surge        → SURGE信号（5分钟有效）                         │
│       └── /api/bollinger_climb → BB_CLIMB信号                               │
│       │                                                                     │
├───────┼─────────────────────────────────────────────────────────────────────┤
│       │                         二次信号过滤 + 实盘执行层                      │
├───────┼─────────────────────────────────────────────────────────────────────┤
│       ▼                                                                     │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    新实盘交易模块 (替换sim_trade.py)                  │   │
│  ├─────────────────────────────────────────────────────────────────────┤   │
│  │  1. 信号接收层    │ 轮询5003 API，获取原始信号                         │   │
│  │  2. 二次过滤层    │ 收阴过滤/现货合约质检/风控校验/黑名单               │   │
│  │  3. 执行层        │ 币安合约API下单/平仓                               │   │
│  │  4. 持仓同步层    │ REST+WS双通道同步实盘持仓                          │   │
│  │  5. 监控层        │ 本地止盈止损/爆仓检测/价格feed                     │   │
│  │  6. 风控层        │ 单笔亏损上限/日止损线/异常熔断                     │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│       │                                                                     │
│       ▼                                                                     │
│  币安U本位合约 (fapi.binance.com)                                           │
│       │                                                                     │
│       ├── REST: 下单/查持仓/查余额/设杠杆                                   │
│       └── WS: 用户数据流(ACCOUNT_UPDATE/ORDER_TRADE_UPDATE)                 │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 二次过滤层（核心）

在现有过滤基础上，增加实盘专用风控过滤：

```python
def evaluate_signal_for_trading(signal: dict) -> tuple[bool, str]:
    """
    二次信号过滤：对原始信号做实盘风控校验
    返回 (是否通过, 拒绝原因)
    """
    symbol = signal["symbol"]
    
    # 1. 原有过滤（保留）
    if symbol in EXCLUDE_SYMBOLS:
        return False, "EXCLUDED"
    if get_position(symbol):
        return False, "ALREADY_HOLDING"
    if is_in_cooldown(symbol):
        return False, "COOLDOWN"
    
    # 2. 收阴过滤（保留）
    if check_recent_1h_candles_bearish(symbol):
        return False, "BEARISH_CANDLES"
    
    # 3. 现货合约阴阳质检（保留）
    if check_spot_futures_divergence_once(symbol):
        return False, "SPOT_FUTURES_DIVERGENCE"
    
    # 4. 【新增】合约流动性检查
    futures_24h_vol = get_futures_24h_volume(symbol)
    if futures_24h_vol < MIN_24H_VOLUME:
        return False, "LOW_FUTURES_VOLUME"
    
    # 5. 【新增】资金费率检查
    funding_rate = get_funding_rate(symbol)
    if funding_rate > MAX_FUNDING_RATE:  # 如 > 0.1%
        return False, "HIGH_FUNDING_RATE"
    
    # 6. 【新增】合约标记价格 vs 现货价格偏差检查
    mark_price = get_mark_price(symbol)
    spot_price = get_current_price(symbol)
    if abs(mark_price - spot_price) / spot_price > MAX_PRICE_DEVIATION:  # 如 > 2%
        return False, "PRICE_DEVIATION_TOO_LARGE"
    
    # 7. 【新增】日亏损上限检查
    if account["daily_loss"] >= MAX_DAILY_LOSS:
        return False, "DAILY_LOSS_LIMIT"
    
    # 8. 【新增】单笔最大亏损检查
    stop_loss_price, _ = get_stop_loss_price(symbol, signal.get("entry_price", 0))
    entry_price = signal.get("entry_price", 0) or get_current_price(symbol)
    loss_pct = (entry_price - stop_loss_price) / entry_price
    if loss_pct > MAX_STOP_LOSS_PCT:  # 如 > 10%
        return False, "STOP_TOO_FAR"
    
    return True, "PASS"
```

**过滤项速查**:

| 过滤项 | 阈值建议 | 目的 |
|--------|---------|------|
| 合约24h成交量 | ≥300万 USDT | 确保合约端有足够流动性 |
| 资金费率 | ≤0.1% | 避免高资金费率吃掉利润 |
| 标记价格偏差 | ≤2% | 避免合约现货严重脱钩时开仓 |
| 日亏损上限 | ≤20%本金 | 单日最大亏损控制 |
| 止损距离上限 | ≤10% | 止损太远意味着风险过大 |

## 执行层（全新实现）

**API端点**: `fapi.binance.com`（U本位合约）

```python
# 币安U本位合约API配置
BINANCE_FAPI_URL = "https://fapi.binance.com"
BINANCE_FAPI_TESTNET_URL = "https://testnet.binancefuture.com"
USE_TESTNET = os.environ.get('BINANCE_TESTNET', 'true').lower() == 'true'
BASE_URL = BINANCE_FAPI_TESTNET_URL if USE_TESTNET else BINANCE_FAPI_URL
```

### 关键API函数

```python
def futures_request(method: str, path: str, params: dict = None, signed: bool = True) -> dict:
def get_futures_balance() -> float:          # /fapi/v2/balance
def get_futures_positions() -> list:          # /fapi/v2/positionRisk
def set_leverage(symbol: str, leverage: int = 10) -> bool:  # /fapi/v1/leverage
def set_margin_type(symbol: str, margin_type: str = "CROSSED") -> bool:  # /fapi/v1/marginType
def place_futures_order(symbol, side, order_type, quantity, stop_price=None, reduce_only=False) -> dict:  # /fapi/v1/order
def get_mark_price(symbol: str) -> float:     # /fapi/v1/premiumIndex
def get_funding_rate(symbol: str) -> float:   # /fapi/v1/premiumIndex
def get_futures_24h_volume(symbol: str) -> float:  # /fapi/v1/ticker/24hr
```

### 开仓流程

```python
def open_position_real(symbol: str, signal_type: str, margin: float, 
                       stop_loss_price: float, take_profit_price: float) -> bool:
    # 1. 设置杠杆
    set_leverage(symbol, DEFAULT_LEVERAGE)
    
    # 2. 获取标记价格
    mark_price = get_mark_price(symbol)
    if mark_price <= 0:
        return False
    
    # 3. 计算数量（按标记价格）
    position_value = margin * DEFAULT_LEVERAGE
    quantity = position_value / mark_price
    quantity = adjust_quantity_precision(symbol, quantity)
    
    # 4. 查可用余额
    available_balance = get_futures_balance()
    required_margin = position_value / DEFAULT_LEVERAGE
    if available_balance < required_margin * 1.1:
        return False
    
    # 5. 下市价多单
    order = place_futures_order(symbol=symbol, side="BUY", order_type="MARKET", quantity=quantity)
    
    if "error" in order or order.get("status") not in ("FILLED", "PARTIALLY_FILLED"):
        return False
    
    # 6. 获取实际成交价格
    avg_price = float(order.get("avgPrice", 0)) or mark_price
    executed_qty = float(order.get("executedQty", 0))
    
    # 7. 创建本地持仓记录
    pos = {
        "symbol": symbol,
        "signal_type": signal_type,
        "entry_price": avg_price,
        "quantity": executed_qty,
        "margin": margin,
        "leverage": DEFAULT_LEVERAGE,
        "position_value": avg_price * executed_qty,
        "entry_time": time.time(),
        "stop_loss_price": stop_loss_price,
        "take_profit_price": take_profit_price,
        "is_long": True,
        "order_id": order.get("orderId"),
        "commission": float(order.get("cumQuote", 0)) * 0.0004,
    }
    positions.append(pos)
    save_state()
    return True
```

### 平仓流程

```python
def close_position_real(pos: dict, reason: str):
    symbol = pos["symbol"]
    quantity = pos["quantity"]
    
    # 1. 下市价平仓单（reduceOnly=true）
    order = place_futures_order(
        symbol=symbol, side="SELL", order_type="MARKET",
        quantity=quantity, reduce_only=True,
    )
    
    if "error" in order:
        return False
    
    # 2. 获取实际平仓价格
    close_price = float(order.get("avgPrice", 0)) or get_mark_price(symbol)
    
    # 3. 计算实际盈亏
    pnl = (close_price - pos["entry_price"]) * pos["quantity"]
    commission = float(order.get("cumQuote", 0)) * 0.0004
    actual_pnl = pnl - pos.get("commission", 0) - commission
    
    # 4. 更新统计
    account["total_pnl"] += actual_pnl
    account["total_trades"] += 1
    if actual_pnl > 0:
        account["win_trades"] += 1
    else:
        account["loss_trades"] += 1
        account["daily_loss"] += abs(actual_pnl)
    
    # 5. 移除持仓
    positions.remove(pos)
    save_state()
    return True
```

## 持仓同步层

**核心原则**：以币安实盘持仓为准，本地positions为缓存。

```python
def sync_positions_with_exchange():
    """每30秒执行一次：将本地positions与币安实盘持仓同步"""
    futures_positions = get_futures_positions()
    exchange_positions = {
        p["symbol"]: p for p in futures_positions 
        if float(p.get("positionAmt", 0)) != 0
    }
    
    # 1. 币安有但本地没有 → 自动纳入管理（标记 EXTERNAL）
    for symbol, ex_pos in exchange_positions.items():
        if not get_position(symbol):
            qty = abs(float(ex_pos["positionAmt"]))
            entry = float(ex_pos["entryPrice"])
            positions.append({
                "symbol": symbol, "signal_type": "EXTERNAL",
                "entry_price": entry, "quantity": qty,
                "margin": entry * qty / DEFAULT_LEVERAGE,
                "leverage": DEFAULT_LEVERAGE,
                "entry_time": time.time(),
                "stop_loss_price": entry * 0.95,
                "take_profit_price": entry * 1.05,
                "is_long": float(ex_pos["positionAmt"]) > 0,
            })
    
    # 2. 本地有但币安没有 → 强制移除
    for local_pos in list(positions):
        if local_pos["symbol"] not in exchange_positions:
            positions.remove(local_pos)
    
    # 3. 数量不一致 → 以币安为准修正
    for local_pos in positions:
        ex_qty = abs(float(exchange_positions[local_pos["symbol"]]["positionAmt"]))
        if abs(ex_qty - local_pos["quantity"]) > ex_qty * 0.01:
            local_pos["quantity"] = ex_qty
```

## 风控层

```python
# 风控配置
MAX_DAILY_LOSS = 20           # 日最大亏损 20 USDT（20%本金）
MAX_STOP_LOSS_PCT = 0.10      # 止损距离不超过开仓价的10%
MAX_FUNDING_RATE = 0.001      # 资金费率不超过0.1%
MAX_PRICE_DEVIATION = 0.02    # 标记价格与现货价格偏差不超过2%
MAX_SINGLE_POSITION_LOSS = 15 # 单笔最大亏损 15 USDT
CIRCUIT_BREAKER_LOSS = 30     # 熔断：累计亏损达30U停止交易

def check_circuit_breaker() -> bool:
    if account["daily_loss"] >= CIRCUIT_BREAKER_LOSS:
        return True
    return False
```

**异常熔断场景**:
1. **日亏损熔断**: 当日累计亏损 ≥ 30 USDT → 停止开仓
2. **API连续错误熔断**: 连续10次API调用失败 → 停止交易
3. **价格异常熔断**: 标记价格短时间内下跌 > 15% → 暂停开仓5分钟

## 监控层改造

**价格Feed**：
- 主价格源：币安合约标记价格（Mark Price）`/fapi/v1/premiumIndex`
- 备用价格源：币安现货价格（REST fallback）
- 止盈止损检查使用标记价格

**爆仓价计算**（U本位合约简化公式）：
```python
def calculate_liquidation_price(pos: dict) -> float:
    entry = pos["entry_price"]
    leverage = pos["leverage"]
    maintenance_margin_rate = 0.005  # 0.5%
    return entry * (1 - 1/leverage + maintenance_margin_rate)
```

## 主循环设计

```python
def main_real():
    init_trade_db()
    sync_positions_with_exchange()
    
    last_status_time = 0
    last_signal_check = 0
    last_sync_time = 0
    last_daily_reset = datetime.now().day
    
    while True:
        now = time.time()
        
        # 每日重置
        if datetime.now().day != last_daily_reset:
            reset_daily_stats()
            last_daily_reset = datetime.now().day
        
        # 熔断检查
        if check_circuit_breaker():
            time.sleep(60)
            continue
        
        # 1. 持仓同步（每30秒）
        if now - last_sync_time >= 30:
            last_sync_time = now
            sync_positions_with_exchange()
        
        # 2. 检查持仓止盈止损（每秒）
        if positions:
            check_positions_real()
        
        # 3. 评估信号并开仓（每10秒）
        if now - last_signal_check >= 10:
            last_signal_check = now
            evaluate_and_open_real()
        
        # 4. 打印状态（每60秒）
        if now - last_status_time >= 60:
            last_status_time = now
            print_status_real()
            save_state()
        
        time.sleep(1)
```

## 错误处理

| 场景 | 处理策略 |
|------|---------|
| API限流 (429) | 指数退避重试：1s → 2s → 4s → 8s |
| API密钥错误 | 立即退出，打印明确错误 |
| 网络中断 | 重连5次，每次间隔5秒，仍失败则告警并休眠60秒 |
| 下单部分成交 | 记录实际成交数量，继续监控剩余持仓 |
| 币安维护 | 检测到特定错误码，进入休眠模式，每60秒探测一次 |
| 本地positions与实盘不一致 | 每30秒强制同步，以实盘为准 |
| 止盈止损触发但下单失败 | 重试3次，仍失败则人工告警 |

## 部署建议

### 环境变量 (.env)

```bash
BINANCE_API_KEY=your_api_key
BINANCE_API_SECRET=your_api_secret
BINANCE_TESTNET=true   # true=测试网, false=实盘
MAX_DAILY_LOSS=20
MAX_POSITIONS=5
DEFAULT_LEVERAGE=10
MARKET_HOST=http://localhost:5003
```

### 分阶段上线

| 阶段 | 内容 | 验证标准 |
|------|------|---------|
| Phase 1 | 仅运行过滤逻辑，不下单（打印"would open/close"） | 信号过滤正确 |
| Phase 2 | 测试网实盘，真实下单 | 5笔以上交易，盈亏计算正确 |
| Phase 3 | 小资金实盘（50-100U） | 运行1周，无重大异常 |
| Phase 4 | 正常资金实盘 | 持续监控 |

### 启动前检查清单

1. [ ] API Key有U本位合约交易权限
2. [ ] 测试网验证通过（至少完成5笔完整开平仓）
3. [ ] 合约账户有余额（建议 ≥ 100 USDT）
4. [ ] 5003端口监控服务已启动
5. [ ] 数据目录 `data/` 存在且可写

## 回退方案

若实盘出现问题，可立即回退：
1. 设置 `BINANCE_TESTNET=true` → 自动切换到测试网
2. 或直接停掉实盘脚本，启动原 `sim_trade.py`（保留不变）
3. 实盘脚本和模拟脚本可并行存在，通过不同启动命令切换
