# 数据格式与口径

## 币安 Kline API 字段索引

币安 `/api/v3/klines` 返回数组，字段索引：

| 索引 | 字段 | 说明 |
|------|------|------|
| 0 | `t` | 开盘时间 (ms) |
| 1 | `o` | 开盘价 |
| 2 | `h` | 最高价 |
| 3 | `l` | 最低价 |
| 4 | `c` | 收盘价 |
| 5 | `v` | 成交量（标的资产数量） |
| 6 | `T` | 收盘时间 (ms) |
| 7 | `q` | 成交额（QUOTE资产，即USDT） |
| ... | ... | ... |

**关键**: 止损用 `k[3]` (low)，不是 `k[4]` (close)。

## 成交量口径

### 监控面板用：vol_24h_today

- 从 **北京时间08:00** 起累计当日成交量
- 由 WebSocket miniTicker 数据累加
- 用于前端显示和策略过滤

### 交易脚本用：现货24h滚动

- 通过 `api.binance.com/api/v3/ticker/24hr` 获取
- 标准24小时滚动成交量
- `MIN_24H_VOLUME = 3_000_000` 基于此口径

### 注意事项

两者数值不同，不要混用。策略过滤和排序统一用 **现货24h滚动**。

## WebSocket miniTicker 格式

```json
{
  "e": "24hrMiniTicker",
  "E": 1234567890000,
  "s": "BTCUSDT",
  "c": "50000.00",
  "o": "49000.00",
  "h": "51000.00",
  "l": "48000.00",
  "v": "100.5",
  "q": "5000000.0"
}
```

- `c`: 最新价
- `o`: 24h前开盘价（用于计算当日涨幅）
- `q`: 24h成交额（USDT）

## 缓存限制

```python
MAX_MINUTE_KLINES_PER_SYMBOL = 120      # 2小时数据
MAX_HOURLY_KLINES_PER_SYMBOL = 36       # 36小时数据
```

追加后必须截断，防止内存泄漏：

```python
market_data["minute_klines"][symbol].append(kline)
if len(market_data["minute_klines"][symbol]) > MAX_MINUTE_KLINES_PER_SYMBOL:
    market_data["minute_klines"][symbol] = market_data["minute_klines"][symbol][-MAX_MINUTE_KLINES_PER_SYMBOL:]
```

## 状态持久化

交易状态保存到 `/tmp/sim_trade_state.json`：

```json
{
  "account": {"balance": 100.0},
  "positions": [...],
  "cooldowns": {...},
  "trade_history": [...]
}
```

每次开平仓后立即调用 `save_state()`。

## 防缓存策略

全站三重防缓存：
1. Flask 响应头: `Cache-Control: no-cache, no-store, must-revalidate`
2. HTML meta 标签: `<meta http-equiv="Cache-Control" content="no-cache">`
3. API 请求加随机参数: `?_=${Date.now()}`
