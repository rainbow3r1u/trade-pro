# V7 混合策略系统架构文档

> 本文档描述币安全量行情监控 + V7双账户模拟交易系统的完整逻辑。
> 基于 `market_monitor_app.py`（清理后 2833 行）和 `sim_trade.py`（871 行）。

---

## 1. 系统概述

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          Docker 容器 (Port 5003)                         │
│  ┌─────────────────────────────┐    ┌─────────────────────────────┐    │
│  │   market_monitor_app.py      │    │      sim_trade.py            │    │
│  │   (Flask + WebSocket 服务)   │◄──►│   (V7 双账户模拟交易)        │    │
│  │                              │    │                              │    │
│  │  • 实时行情采集 (WebSocket)   │    │  • 现货策略: 日线BB爬坡      │    │
│  │  • 分钟K线聚合               │    │  • 合约策略: 15m量surge      │    │
│  │  • 15m成交量突增检测         │    │  • TP/SL/日止盈/冷却管理     │    │
│  │  • 日线BB信号检测 (UTC 00:05)│    │  • 状态持久化                │    │
│  │  • REST API / WS 广播        │    │                              │    │
│  └─────────────────────────────┘    └─────────────────────────────┘    │
│                              ▲                                          │
│                              │                                          │
│                    ┌─────────┴──────────┐                               │
│                    │   前端 (HTML/JS)    │                               │
│                    │   行情面板 + 参数页  │                               │
│                    └────────────────────┘                               │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         外部数据源                                       │
│  • 币安 WebSocket: wss://stream.binance.com:9443/ws/!miniTicker@arr     │
│  • 币安 REST API: https://api.binance.com/api/v3/klines (日线)          │
│  • 币安 REST API: /api/v3/ticker/24hr, /ticker/price                    │
│  • COS (腾讯云对象存储): 历史K线归档、1h缓存、成交量历史                 │
└─────────────────────────────────────────────────────────────────────────┘
```

### 双账户设计

| 账户 | 初始资金 | 信号源 | 持仓标的 | 杠杆 |
|------|---------|--------|---------|------|
| **现货 (Spot)** | 100 USDT | 日线BB爬坡 | USDT现货 | 1x |
| **合约 (Futures)** | 100 USDT | 15m成交量突增 | 永续合约 | 10x |

**核心约束**: 合约只在**有现货持仓的币种**上开仓，形成"现货选币 + 合约增强"的混合策略。

---

## 2. 清理总结 (market_monitor_app.py)

清理前: **3780 行** → 清理后: **2833 行** (-947 行, -25%)

### 删除的死代码

| 类别 | 删除内容 | 说明 |
|------|---------|------|
| **Hyperliquid** | `fetch_hyperliquid_klines`, `init_market_data_from_hyperliquid`, `update_prices_from_hyperliquid`, `fetch_hyperliquid_all_mids`, `fetch_hyperliquid_meta`, `HYPERLIQUID_API`, `hyperliquid_ws_loop`, `hyperliquid_backfill_loop` | `USE_HYPERLIQUID=False` 从未启用，释放2个无效后台线程 |
| **旧Binance初始化** | `_init_from_binance_api` | 从未调用 |
| **API回填系统** | `backfill_hourly_klines`, `_backfill_worker`, `_fetch_single_hourly_klines` | V7已改用本地缓存 |
| **北京8点旧逻辑** | `calculate_beijing_8am_data`, `get_beijing_midnight_open_price` | 从未调用 |
| **COS加载函数** | `load_symbols_snapshot_from_cos`, `load_vol_24h_today_from_cos`, `load_today_open_prices_from_cos` | 定义但从未调用 |
| **旧聚合函数** | `_aggregate_minutes_to_hours`, `_aggregate_hours_to_days` | 父函数删除后变为死代码 |
| **旧BB函数** | `_calculate_bollinger_bands`, `_calculate_buy_ratio`, `_detect_bollinger_candidate` | 未被当前检测逻辑使用 |
| **今日开盘价** | `fetch_today_open_prices` | 从未被调用 |
| **小时BB后台** | `bollinger_climb_background_loop`, `_refresh_bollinger_climb_cache` | V7已改用日线，每10秒运行浪费CPU |
| **缓存变量** | `_bb_climb_cache`, `_bb_climb_lock` | 旧小时BB缓存 |

### 兼容性修改

- **`/api/bollinger_climb`**（前端仍在调用）→ 改为**直接返回日线缓存数据**（`_bb_daily_cache`），不再维护独立的小时BB缓存
- **`run_web.py`** → 同步移除了已删除函数的导入和线程启动
- **Python语法验证通过**

---

## 3. 网站服务逻辑 (market_monitor_app.py)

### 3.1 启动流程

```
[Docker入口: docker_entrypoint.py]
    │
    ├─► [run_web.py] 启动 Flask + SocketIO (Port 5003)
    │       │
    │       ├─► init_market_data()              ← 数据初始化
    │       │       ├─► 优先从COS加载1h K线缓存
    │       │       ├─► 优先从本地 data/hourly_backfill.json 加载
    │       │       └─► 回退: 币安API加载24h快照 + 北京8点开盘价
    │       │
    │       ├─► 启动后台线程 (清理后共7个)
    │       │       ├─► ws_update_loop          ← WebSocket实时行情
    │       │       ├─► write_loop              ← 15分钟写COS快照
    │       │       ├─► minute_aggregator_loop  ← 分钟K线聚合 + 15m成交量检测
    │       │       ├─► daily_open_price_update_loop  ← 每天UTC 00:00捕获开盘价
    │       │       ├─► sim_trade_broadcast_loop      ← 广播交易状态
    │       │       ├─► bb_daily_background_loop      ← 日线BB缓存刷新 (UTC 00:05)
    │       │       └─► _refresh_snapshot_cache       ← 刷新前端快照缓存
    │       │
    │       └─► socketio.run(app, host='0.0.0.0', port=5003)
    │
    └─► [sim_trade.py] 启动交易脚本
            ├─► load_state()  ← 恢复持仓/账户状态
            └─► main() 循环
```

### 3.2 实时数据采集 (ws_update_loop)

**数据源**: 币安 WebSocket `!miniTicker@arr`（全币种 miniTicker 聚合流）

```
[币安WebSocket每1-3秒推送一批ticker数据]
    │
    ▼
[on_message 处理每帧数据]
    │
    ├─► 分钟切换检测
    │       └─► 如果进入新分钟 → 调用 _aggregate_minute_kline()
    │               ├─► 聚合上一分钟的秒级数据 → 生成分钟K线
    │               ├─► 更新 minute_klines 缓存 (保留最近120条 = 2小时)
    │               └─► 清理过期 second_deltas
    │
    ├─► 对每个币种:
    │       ├─► 更新实时价格 / 24h成交额 / 24h最高最低
    │       ├─► 计算 delta_q = 当前q - 上一秒q (秒级成交额增量)
    │       ├─► 存入 second_deltas[current_second][symbol] = delta_q
    │       ├─► 更新 minute_state (开高低收 + 累计成交量)
    │       ├─► 估算 buy_ratio (基于价格变动方向)
    │       └─► delta_q 突增检测 (>50万USDT + buy_ratio>=0.8 → 记录到 surge_cache)
    │
    └─► 通过 SocketIO 广播 ws_update → 前端实时更新
```

### 3.3 15分钟成交量检测 (minute_aggregator_loop)

```
[每15分钟触发一次]
    │
    ├─► 计算当前15分钟区间的累计成交额
    │
    ├─► 与"前4小时的15分钟均值"比较
    │       └─► 如果 ratio >= 4.0 → 标记为 vol_surge
    │
    ├─► vol_surge_symbols   (5分钟有效期，给交易脚本用)
    ├─► vol_surge_history   (1小时历史，给前端展示用)
    │
    └─► 保存当前15分钟成交量到COS (异步线程)
```

### 3.4 日线BB信号检测 (bb_daily_background_loop)

**刷新频率**: 每天 **UTC 00:05** 执行一次（清理前为每小时，浪费大量API请求）

```
[UTC 00:05 触发]
    │
    ├─► _load_all_daily_klines()
    │       ├─► 并发20线程，从币安API拉取592个币种的日线K线
    │       ├─► 每个币种拉取40根日K线 (约40天)
    │       └─► 存入 _daily_kline_cache
    │
    └─► _refresh_bb_daily_cache()
            ├─► 遍历所有币种的日线K线
            ├─► 对每个币种调用 _detect_bollinger_climb()
            │       ├─► 参数: period=30, std_mult=2.5
            │       ├─► 条件1: 收盘价在中轨附近且在上轨±8%范围内
            │       ├─► 条件2: 最近5天中至少3天HL抬高
            │       ├─► 条件3: 量能 > 1.2倍均量
            │       ├─► 条件4: ATR趋势过滤
            │       └─► 返回: {symbol, upper, middle, consecutive_hours, ...}
            └─► 结果存入 _bb_daily_cache (最多保留50个信号)
```

### 3.5 今日开盘价更新 (daily_open_price_update_loop)

```
[每天 UTC 00:00 触发]
    │
    ├─► capture_today_open_from_ws()
    │       └─► 从WebSocket缓存中读取各币种当前价格作为今日开盘价
    │
    ├─► save_today_open_prices_to_cos()
    ├─► 重置 vol_24h_today (今日累计成交额归零)
    └─► save_vol_24h_today_to_cos() (写入空数据)
```

### 3.6 提供的API端点

| 端点 | 用途 | 消费者 |
|------|------|--------|
| `GET /api/bollinger_climb` | 日线BB爬坡信号（兼容前端旧接口） | 前端 (每30秒) |
| `GET /api/bollinger_climb_daily` | 日线BB爬坡信号（V7策略用） | sim_trade.py |
| `GET /api/vol_surge` | 15分钟成交量突增列表 | sim_trade.py + 前端 |
| `GET /api/snapshot` | 全量行情快照 | 前端初始化 |
| `GET /api/surge` | delta_q大单突增（买>=80%） | 前端 |
| `GET /api/momentum_tracker` | 追涨追踪器 | 前端 |
| `GET /api/minute_buy_ratio/<symbol>` | 分钟级主动买卖比 | 前端 |
| `GET /api/sim_trade` | 双账户状态 | 前端 (WebSocket广播) |
| `POST /api/backtest/...` | 回测API族 | 前端参数面板 |

---

## 4. 交易脚本逻辑 (sim_trade.py)

### 4.1 主循环

```
[main() 每秒循环]
    │
    ├─► 检查持仓平仓 (TP/SL)
    │       ├─► check_spot_positions()   ← 每1秒检查
    │       └─► check_futures_positions() ← 每1秒检查
    │
    ├─► 每10秒评估信号
    │       ├─► evaluate_spot_signals()   ← 日线BB信号 → 开现货
    │       └─► evaluate_futures_signals() ← 15m量surge → 开合约
    │
    └─► 每60秒打印状态 + save_state()
```

### 4.2 现货策略 (BB Spot)

```
[evaluate_spot_signals] 每10秒执行
    │
    ├─► 调用 /api/bollinger_climb_daily 获取信号列表
    │
    ├─► 对每个信号:
    │       ├─► 排除 EXCLUDE_SYMBOLS (BTC/ETH/稳定币等)
    │       ├─► 检查是否已有持仓
    │       ├─► 检查持仓数 < SPOT_MAX_POSITIONS (20)
    │       ├─► 检查余额 >= SPOT_PER_TRADE (5 USDT)
    │       ├─► 检查 consecutive_hours >= SPOT_MIN_HOURS (4)
    │       ├─► 检查 24h成交额 >= SPOT_VOL_FILTER (100万USDT)
    │       ├─► 检查日涨幅 <= SPOT_GAIN_FILTER_PCT (10%)
    │       └─► 全部通过 → open_spot_position()
    │
    └─► [open_spot_position]
            ├─► 数量 = SPOT_PER_TRADE / entry_price
            ├─► 成本 = SPOT_PER_TRADE * (1 + 0.1%手续费)
            ├─► 止盈价 = entry_price * 2.0 (翻倍出)
            ├─► 止损价 = 布林带下轨 (基于最近30天数据)
            │       └─►  fallback: entry_price * 0.9
            ├─► 记录 spot_entry_ts[symbol] = 当前时间
            └─► 扣除余额，加入 spot_positions

[check_spot_positions] 每1秒检查
    │
    ├─► 遍历所有现货持仓
    ├─► 获取当前价格
    ├─► 如果 current_price >= tp_price → TAKE_PROFIT 平仓
    ├─► 如果 current_price <= sl_price → STOP_LOSS 平仓
    └─► 计算实际盈亏，更新账户统计
```

### 4.3 合约策略 (VS Futures)

```
[evaluate_futures_signals] 每10秒执行
    │
    ├─► 调用 /api/vol_surge 获取15分钟成交量突增信号
    │       └─► 过滤: ratio >= FUT_MIN_RATIO (4.0) 且 5分钟内可交易
    │
    ├─► 对每个信号:
    │       ├─► 排除 EXCLUDE_SYMBOLS
    │       ├─► 检查是否已有合约持仓
    │       ├─► 检查持仓数 < FUT_MAX_POSITIONS (20)
    │       ├─► 检查可用余额 >= FUT_MARGIN (20 USDT)
    │       ├─► 【核心约束】检查是否有现货持仓!
    │       ├─► 检查现货入场时间 <= 信号时间
    │       ├─► 检查日止盈次数 < FUT_MAX_DAILY_TP (4)
    │       ├─► 检查日涨幅 <= 10%
    │       ├─► 检查最近3根1h K线没有2根收阴
    │       └─► 全部通过 → open_futures_position()
    │
    └─► [open_futures_position]
            ├─► 保证金 = FUT_MARGIN (20 USDT)
            ├─► 杠杆 = FUT_LEVERAGE (10x)
            ├─► 名义价值 = 20 * 10 = 200 USDT
            ├─► 止盈价 = entry_price * (1 + 50% / 10) = entry_price * 1.05
            ├─► 止损价 = entry_price * (1 - 2%) = entry_price * 0.98
            └─► 扣除保证金，加入 futures_positions

[check_futures_positions] 每1秒检查
    │
    ├─► 遍历所有合约持仓
    ├─► 获取当前价格
    ├─► 如果 current_price >= tp_price → TAKE_PROFIT 平仓
    ├─► 如果 current_price <= sl_price → STOP_LOSS 平仓 → 设置30分钟冷却
    ├─► 联合爆仓检测: 如果总权益 <= 0 → LIQUIDATED_CROSS 全部平仓
    └─► 计算实际盈亏（扣除0.04%*2手续费），更新账户统计
```

### 4.4 状态持久化

```
[save_state] 每60秒执行
    │
    └─► 写入 /tmp/sim_trade_state.json
            ├─► spot_account / spot_positions
            ├─► futures_account / futures_positions
            ├─► spot_entry_ts
            ├─► daily_take_profit_count
            ├─► cooldown_symbols
            └─► 最近50条交易日志

[load_state] 启动时执行
    └─► 从 /tmp/sim_trade_state.json 恢复所有状态
```

---

## 5. V7 最优参数

基于2000轮回测验证的最优组合:

```python
# 日线BB参数
period = 30           # 布林带周期
std_mult = 2.5        # 标准差倍数
min_hours = 4         # 最小连续小时数
hl_window = 5         # 高低点窗口（天）
hl_min = 3            # 窗口内最少抬高次数
gain_filter = 10%     # 日涨幅上限

# 现货交易
per_trade = 5 USDT
max_positions = 20
tp_multiplier = 2.0   # 翻倍止盈

# 合约交易
margin = 20 USDT
leverage = 10x
tp_pct = 50%          # 名义50% → 实际5倍
sl_pct = 2%           # 实际2%止损
max_daily_tp = 4      # 日最大止盈次数
```

**回测结果**: +75.3% 综合收益，100% 正收益周期

---

## 6. 关键设计决策

### 6.1 为什么日线BB比小时BB更稳定？

| 维度 | 旧版 (小时K线) | 新版 (日线K线) |
|------|---------------|---------------|
| 数据量 | 内存仅1.5小时 | API拉取40天 |
| 噪音 | 高 (小时级波动) | 低 (日级趋势) |
| 未来函数 | 曾用当天收盘价算涨幅 | 用前一天收盘价 |
| 参数稳定性 | hlm=2时58%亏损率 | hlm=3时100%正收益 |

### 6.2 为什么合约必须绑定现货持仓？

这是V7策略的核心风控设计:
- 现货BB爬坡信号已经过日线级别的趋势确认
- 合约只在"已确认趋势"的币种上开仓，避免在垃圾币/假突破上浪费保证金
- 形成"现货选股 + 合约择时"的分层架构

### 6.3 数据源分工

| 数据源 | 用途 | 更新频率 |
|--------|------|---------|
| 币安WebSocket | 实时价格、分钟K线、delta_q | 实时 (~1-3秒) |
| 币安API日线 | BB信号检测 | 每天UTC 00:05 |
| 币安API 1h/15m | 合约过滤条件、回测 | 按需 |
| COS归档 | 历史K线、1h缓存、成交量历史 | 15分钟/每天 |

---

## 7. 文件清单

| 文件 | 行数 | 用途 |
|------|------|------|
| `market_monitor_app.py` | 2833 | 核心Web服务 (Flask + WebSocket) |
| `sim_trade.py` | 871 | V7双账户模拟交易 |
| `run_web.py` | 48 | Docker内启动Web服务 |
| `docker_entrypoint.py` | 64 | Docker入口 (启动Web + 交易脚本) |
| `templates/market_monitor.html` | ~1200 | 前端行情面板 |
| `templates/params.html` | ~300 | 参数回测面板 |
| `templates/momentum_tracker.html` | ~200 | 追涨追踪器页面 |

---

*文档生成时间: 2026-04-29*
*对应代码版本: market_monitor_app.py (清理后), sim_trade.py (V7)*
