# 架构与部署

## 完整文件结构

```
websocket_new/
├── market_monitor_app.py          # Flask+SocketIO 主服务
├── sim_trade.py                   # 自动交易脚本（独立进程）
├── trades_aggregator.py           # 历史交易聚合（后台线程cleanup）
├── requirements.txt               # Python依赖
├── configs/
│   ├── cos_config.py              # COS/其他配置
│   └── ...
├── core/
│   ├── binance_client.py          # 币安API封装（已统一为现货）
│   ├── kline_manager.py           # K线数据管理
│   └── ...
├── crypto_engine/
│   └── ...
├── data/
│   └── ...
├── models/
│   └── ...
├── scripts/
│   └── ...
├── static/
│   └── ...
├── templates/
│   ├── market_monitor.html        # 主监控面板
│   └── momentum_tracker.html      # 追涨追踪器
└── output/
    └── ...
```

## 服务架构

```
币安现货WebSocket ──► market_monitor_app.py:5003
                           │
                           ├── SocketIO ──► 浏览器前端 (market_monitor.html)
                           ├── REST API   ──► 前端轮询兜底
                           │
                           └── 策略信号 ──► sim_trade.py (轮询 /api/signals)
                                                  │
                                                  └── 开平仓/止盈止损/爆仓
```

## 运行方式

```bash
# 1. 启动监控主服务（必需）
python market_monitor_app.py
# 监听 0.0.0.0:5003

# 2. 启动自动交易（可选，独立进程）
python sim_trade.py
# 轮询 http://localhost:5003/api/signals
```

两个进程通过文件 `/tmp/sim_trade_state.json` 共享持仓状态。

## 端口与路由

| 路由 | 说明 |
|------|------|
| `/` | 主监控面板 |
| `/momentum` | 追涨追踪器页面 |
| `/api/signals` | 交易脚本轮询信号 |
| `/api/account` | 账户状态 |
| `/api/momentum_tracker` | 追涨数据JSON |

## 全局状态 (market_monitor_app.py)

```python
market_data = {
    "symbols": {},              # 实时价格数据
    "large_orders": {},         # 大单记录
    "minute_klines": {},        # 1分钟K线（每币种最多120条）
    "hourly_kline_cache": {},   # 1小时K线（每币种最多36根）
    "vol_surge_symbols": {},    # VOL_SURGE信号（5分钟过期，交易脚本用）
    "vol_surge_history": {},    # VOL_SURGE历史（1小时保留，前端展示用）
    "momentum_tracker": {},     # 追涨追踪器数据
    ...
}
data_lock = threading.Lock()    # 非递归锁
```

## 前端双布局

- **手机 (<768px)**: 纵向卡片列表，表格横向滚动
- **桌面 (>768px)**: 常规表格布局
- 展开状态通过 `Set()` 记忆
- 三重防缓存：HTTP头 + HTML meta + 请求时间戳
