---
name: crypto-trade-monitor
description: |
  加密货币行情监控与模拟交易系统知识库。在 websocket_new 项目下工作时自动触发。
  覆盖 Flask+SocketIO 实时行情监控、币安现货 WebSocket 数据接入、VOL_SURGE/SURGE/BB_CLIMB 策略检测、
  模拟自动交易（10x杠杆/联合保证金/止盈止损）、追涨追踪器、手机端适配等全部模块。
  当用户请求修改行情监控、交易策略、模拟交易、前端页面、数据口径、性能优化时自动加载。
---

# Crypto Trade Monitor Skill

## 项目速览

- **路径**: `/home/myuser/websocket_new/`
- **端口**: 5003 (Flask + SocketIO)
- **Python**: 3.12
- **核心文件**: `market_monitor_app.py` (监控主服务), `sim_trade.py` (自动交易)
- **前端**: `templates/market_monitor.html`, `templates/momentum_tracker.html`
- **数据**: 币安现货 `stream.binance.com:9443` + `api.binance.com/api/v3/`

## 技术栈

| 组件 | 版本/说明 |
|------|----------|
| Flask-SocketIO | 5.16.1 |
| python-socketio | 5.16.1 |
| websocket-client | 实时数据接入 |
| 数据源 | 币安现货 (非合约) |
| 部署 | 本地运行，无Docker |

## 用户指令（最高优先级约束）

**未经用户明确指令，不得重置交易功能。** 具体包括：
1. 不得重置账户（handle_liquidation、清空 positions、重置 balance 等）
2. 不得修改核心交易逻辑（开仓/平仓/止盈止损/爆仓判断）
3. 不得清空或重置 trade_log / state.json
4. 不得修改已有持仓数据

若后续修改涉及上述任何一项，必须先明确告知影响范围，征得用户确认后方可执行。

## 黄金规则（必须遵守）

### 1. 数据口径 = 现货
所有价格/K线/成交量必须走 `api.binance.com/api/v3/`，不能用 `fapi.binance.com`。WebSocket 也必须用现货流 `stream.binance.com:9443`。

### 2. 余额计算 = 不重复扣除
`account["balance"]` 是总资金池。`open_position()` **不得** 再执行 `balance -= margin`。
可用余额 = `balance - sum(持仓margin)`。平仓时只 `balance += actual_pnl`。

### 3. 止损基于 K线最低价
`k[3]` 是 low（不是 `k[4]` close）。第一优先级 = 前1h K线最低价；若 ≥ 开仓价则回退到前4h K线最低价。

### 4. 信号双通道（重要）
VOL_SURGE 信号分两条线存储，不可混用：
- **`vol_surge_symbols`** → **5分钟**有效期，`sim_trade.py` 交易专用
- **`vol_surge_history`** → **1小时**保留，`/api/vol_surge` 前端展示专用

`/api/vol_surge` 返回1小时数据，带 `tradeable` 布尔标记（`true`=5分钟内可交易）。前端用颜色区分：红色=可交易，灰色=已过期。

SURGE 信号仍只保留5分钟（300秒），BB_CLIMB 无时效限制。

## 策略优先级速查

| 优先级 | 策略 | 仓位 | 过滤条件 |
|--------|------|------|----------|
| 1 | VOL_SURGE | 20U (全仓) | 5分钟内、涨幅>0、非稳定币、均值≥5000、双阴过滤 |
| 2 | SURGE | 10U (半仓) | 5分钟内、双阴过滤(3根中2根收阴)、300万成交量门槛 |
| 3 | BB_CLIMB | 5U (1/4仓) | 无时效、300万成交量门槛 |
| 4 | BB_CAND | 禁用 | — |

## 关键配置常量

```python
INITIAL_CAPITAL = 100
MAX_POSITIONS = 5
DEFAULT_LEVERAGE = 10
BASE_MARGIN = 20
MIN_24H_VOLUME = 3_000_000   # 现货口径
TAKE_PROFIT_PCT = 50         # 盈利达保证金50%止盈（10x=价格涨5%）
VOL_SURGE_THRESHOLD = 3.0    # 15m成交量 > 4h均值3倍
MAX_MINUTE_KLINES = 120      # 每币种保留2小时
MAX_HOURLY_KLINES = 36       # 每币种保留36小时
```

## 联合保证金爆仓

```python
total_equity = balance + unrealized_pnl
if total_equity <= 0 and positions:
    # 强平所有仓位，重置账户为100U
```

**关键原则**：联合保证金模式下**不设单个仓位爆仓价**。只有当所有持仓的总权益（余额+未实现盈亏）<= 0 时，才触发全部强平。日志输出 `[联合爆仓强平] {symbol}` 而非 `[平仓] LIQUIDATED`。

## VOL_SURGE 满仓替换机制

当满仓（5/5）且有 **ratio ≥ 5.0** 的高倍 VOL_SURGE 信号时，主动替换最弱持仓：
- 持仓中有盈利的 → 平仓**盈利最高**的
- 持仓都亏损 → 平仓**亏损最小**的
- 平仓原因：`REPLACE_VOL_SURGE`（不触发冷却期）
- 替换后腾出仓位继续开 VOL_SURGE

## 现货/合约阴阳质检

开仓后立即执行一次性质检：
- 查询该币种现货(`api.binance.com`) + 合约(`fapi.binance.com`)各3根1h K线
- 只比对最近2根**已完成**K线的阴阳方向（`close > open` 还是 `close < open`）
- 任意1根不一致 → 立即以 `SPOT_FUTURES_DIVERGENCE` 平仓
- 不看具体价格数值，不保存状态，不轮询

## 日止盈冷却（5次封顶）

同一币种当天（北京8:00为日界）止盈满 **5 次**后，冷却到**次日北京8:00**：
- `daily_take_profit_count = {(symbol, date_str): count}` — 日止盈计数
- `daily_tp_cooldown_symbols = {symbol: timestamp}` — 冷却截止时间
- 止盈时自动计数，满5次即触发冷却
- 与止损30分钟冷却独立并行

## 文件职责索引

| 文件 | 职责 |
|------|------|
| `market_monitor_app.py` | Flask主服务、WebSocket客户端、策略检测、REST API、全局状态维护 |
| `sim_trade.py` | 轮询5003信号、自动开平仓、止盈止损爆仓、状态持久化 |
| `templates/market_monitor.html` | 主监控面板（行情/大单/突增/布林/交易面板） |
| `templates/momentum_tracker.html` | 追涨追踪器页面（5%~10%涨幅追踪） |

## 详细参考文档

- **[architecture](references/architecture.md)** — 完整文件结构、服务架构、部署方式
- **[trading-logic](references/trading-logic.md)** — 策略算法详细定义、开平仓流程、止盈止损计算
- **[data-format](references/data-format.md)** — K线字段映射、成交量口径、缓存限制、WebSocket消息格式
- **[pitfalls](references/pitfalls.md)** — 已知Bug与陷阱、历史修改记录、常见异常原因
- **[real-trading-transition](references/real-trading-transition.md)** — 模拟交易升级实盘完整方案（二次信号过滤、合约API执行、风控熔断、分阶段上线）
