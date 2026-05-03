# 回测系统 Skill 文档

> **最后更新：2026-04-28**
> **系统状态：全部运行中**

---

## 一、系统架构

```
/home/myuser/
├── backtester-rs/           # Rust 回测引擎（主力）
│   ├── src/
│   │   ├── main.rs          # CLI入口（4个子命令）
│   │   ├── types.rs         # 数据结构（Kline, Params, Position, SharedData）
│   │   ├── data_loader.rs   # JSON缓存加载器
│   │   ├── simulator.rs     # VS+BB合约模拟器（Arc共享数据）
│   │   ├── spot.rs          # BB现货模拟器（新增）
│   │   ├── hybrid.rs        # 混合模式引擎（BB现货 + VS合约联动）
│   │   ├── search.rs        # 参数搜索（随机/混合，支持并行）
│   │   ├── strategies/
│   │   │   ├── vol_surge.rs # VOL_SURGE 15m成交量突增检测
│   │   │   └── bb_climb.rs  # BB_CLIMB 布林爬坡检测（返回上中下轨）
│   │   └── evaluator.rs     # 占位（指标在simulator内计算）
│   ├── data_cache/          # 本地K线缓存（symlink到Python项目）
│   ├── config/              # 参数JSON文件
│   └── results/             # 所有搜索结果
│
├── backtester/              # Python 回测引擎（初版，保留备用）
│   ├── engine.py
│   ├── simulator.py
│   ├── search.py
│   ├── data_loader.py
│   ├── strategies/
│   ├── cos_service/         # COS数据管道
│   │   ├── fetch_to_cos.py  # 币安→COS拉取
│   │   └── cos_client.py    # COS客户端封装
│   └── cache_warm.py        # 本地缓存预热脚本
│
└── websocket_new/           # 实盘交易系统
    ├── sim_trade.py          # 实盘自动交易脚本
    └── market_monitor_app.py # 市场监控+信号检测
```

---

## 二、数据管道

### 数据源
- **市场**：币安合约（fapi.binance.com）
- **币种**：524个USDT永续合约（排除BTC/ETH/SOL/USDE/稳定币）
- **粒度**：15m / 1h / 1d
- **历史**：60天（2026-02-26 ~ 2026-04-28）

### COS存储
- Bucket: `lhsj-1h-1314017643`
- 路径: `klines/cos_kline/{interval}/{symbol}/{year}/{month}/{day}.json`
- 总量：305万根K线

### 本地缓存
- 路径：`/home/myuser/backtester/data_cache/`
- 大小：400MB（15m: 246MB, 1h: 80MB, 1d: 3.4MB）
- 格式：JSON `{klines: {symbol: [kline_dicts]}}`
- Rust通过symlink共享：`backtester-rs/data_cache/`

---

## 三、CLI 命令

### 合约回测（VS + BB_CLIMB）
```bash
cd /home/myuser/backtester-rs
source ~/.cargo/env

# 单次回测
./target/release/backtester-rs run --symbols 200 --config config/default_params.json

# 参数搜索（并行）
./target/release/backtester-rs search --trials 2000 --symbols 524 --output results/search_xxx.json
```

### 混合模式（BB现货 + VS合约联动）
```bash
# 混合搜索（串行，防内存爆炸）
./target/release/backtester-rs hybrid --trials 100 --symbols 100 --output results/hybrid_xxx.json
```

---

## 四、可调整参数

### 合约参数（search/search_params）

| 参数 | 默认值 | 搜索范围 | 说明 |
|------|--------|---------|------|
| `VOL_SURGE_MIN_RATIO` | 3.0 | 2.5, 3.0, 3.5, 4.0 | 15分钟放量倍数门槛 |
| `VOL_SURGE_MIN_AVG_VOL` | 5000 | 3000, 5000, 8000 | 前16根均量下限(U) |
| `VOL_SURGE_MARGIN` | 20 | 10, 15, 20, 25, 30 | VS开仓保证金(U) |
| `TAKE_PROFIT_PCT` | 50 | 30, 40, 50, 60 | 保证金止盈百分比 |
| `MIN_STOP_LOSS_PCT` | 2% | 1%, 2%, 3%, 5% | 开仓价止损百分比 |
| `BB_CLIMB_MARGIN` | 5 | 5, 10 | BB开仓保证金(U) |
| `BB_CLIMB_MIN_HOURS` | 2 | 2, 3 | 布林爬坡最少连续小时 |
| `VOLUME_24H_FILTER` | 300万 | 100万~500万 | 24h成交额门槛(U) |

### 混合模式BB现货参数（hybrid_search）

| 参数 | 搜索范围 | 说明 |
|------|---------|------|
| `BB_PERIOD` | 20, 30 | 布林通道周期 |
| `BB_STD_MULT` | 2.0, 2.5 | 标准差倍数 |
| `BB_MIN_HOURS` | 4, 6, 8 | 爬坡最少连续小时 |
| `BB_HL_WINDOW` | 3, 5 | HL抬高检查窗口 |
| `BB_HL_MIN` | 2, 3 | 窗口内最少满足数 |

### 固定参数（不在搜索范围）

| 参数 | 值 | 说明 |
|------|-----|------|
| `LEVERAGE` | 10x | 合约杠杆 |
| `MAX_POSITIONS` | 5 | 合约最大持仓 |
| `INITIAL_CAPITAL` | 100U | 合约初始资金 |
| `STOP_DAILY_GAIN_PCT` | 20% | 日涨幅过滤 |
| `MAX_DAILY_TP_PER_SYMBOL` | 2 | 日止盈两次过滤 |
| `FEES_PCT` | 0.04% | 合约手续费 |
| 现货手续费 | 0.1% | BB买入/卖出 |
| BB初始资金 | 100U | 混合模式 |
| BB最大持仓 | 20个 | 混合模式 |
| BB单仓 | 5U | 混合模式 |
| BB止盈 | 100% | 价格翻倍 |
| BB止损 | 跌破布林下轨 | 1h布林 |

---

## 五、风控规则（全部已实现）

| 规则 | 位置 | 说明 |
|------|------|------|
| 日止盈两次过滤 | `simulator.rs:209` / `hybrid.rs` | VS当日止盈≥2次后不再开 |
| 双阴过滤 | `simulator.rs` / `hybrid.rs:chk_double_yin` | 3根1h K线≥2根收阴跳过 |
| 日涨幅过滤 | `simulator.rs:chk_gain` | 日涨幅>20%跳过 |
| 止损冷却30分钟 | `simulator.rs` / `hybrid.rs:fut_cd` | 止损后2根15m K线不重复开 |
| 满仓替换 | `simulator.rs` / `hybrid.rs` | VS ratio≥5.0时替换最弱持仓 |
| 联合爆仓 | `simulator.rs` / `hybrid.rs` | 总权益≤0全部强平 |
| VS不可被替换 | `simulator.rs` | VS持仓不被替换 |

---

## 六、全部回测结果

### 搜索1：200币种，200轮（Python初版）
**文件：** `/home/myuser/backtester/results/test_search.json`

| # | Score | 收益 | 回撤 | 交易 | 胜率 | 关键参数 |
|---|-------|------|------|------|------|----------|
| 1 | 0.30 | +35.3% | 60.8% | 215 | 58.1% | ratio=3.5, vs_m=10, tp=40, sl=5%, vol=1M |

### 搜索2：200币种，2000轮（Rust，优化前）
**文件：** `/home/myuser/backtester-rs/results/search_20260428_114250.json`

| # | Score | 收益 | 回撤 | 交易 | 胜率 | 关键参数 |
|---|-------|------|------|------|------|----------|
| 1 | 136.5 | +519.7% | 94.7% | 360 | 58.1% | ratio=3.5, vs_m=30, tp=60, sl=5%, vol=1M, bb_h=3 |

### 搜索3：200币种，2000轮（Rust，Arc优化）
**文件：** `/home/myuser/backtester-rs/results/search_rust_opt.json`

| # | Score | 收益 | 回撤 | 交易 | 胜率 | 关键参数 |
|---|-------|------|------|------|------|----------|
| 1 | 99.1 | +379.1% | 70.3% | 777 | 40.9% | ratio=4.0, vs_m=20, tp=50, sl=1%, vol=1M, bb_h=3 |

### 搜索4：524币种全量，2000轮（Rust，Arc优化）
**文件：** `/home/myuser/backtester-rs/results/search_524_2k.json`

| # | Score | 收益 | 回撤 | 交易 | 胜率 | 关键参数 |
|---|-------|------|------|------|------|----------|
| 1 | 81.4 | +336.5% | 88.7% | 958 | 47.0% | ratio=4.0, vs_m=30, tp=50, sl=2%, vol=1M, bb_h=2 |

### 搜索5：100币种，100轮（混合模式）⭐最新
**文件：** `/home/myuser/backtester-rs/results/hybrid_xxx.json`（未保存到文件）

| 指标 | 值 |
|------|-----|
| 综合收益 | +19.5% |
| BB现货 | -13.7% |
| VS合约 | +52.6% |
| 回撤 | 73.3% |
| VS交易 | 116笔，胜率31.9% |
| BB参数 | period=30, std=2.0, h=8, hlw=5, hlm=3 |

---

## 七、当前实盘部署参数

**文件：** `/home/myuser/websocket_new/sim_trade.py`

| 参数 | 值 | 说明 |
|------|-----|------|
| VOL_SURGE_MIN_RATIO | 4.0 | 放量4倍门槛 |
| MIN_STOP_LOSS_PCT | 1% | 开仓价止损 |
| MIN_24H_VOLUME | 100万 | 成交量门槛 |
| BB_MIN_CONSECUTIVE_HOURS | 3 | 布林爬坡连续3小时 |
| SURGE_ENABLED | False | 大单策略已禁用 |
| MAX_DAILY_TP | 2 | 日止盈两次过滤 |
| 阴阳质检 | 开仓前 | 不一致不开仓 |
| USDEUSDT | 已排除 | 新增过滤 |

---

## 八、性能数据

| 场景 | 币种 | 单轮耗时 | 搜索方式 | 2000轮耗时 |
|------|------|---------|---------|-----------|
| Python | 100 | ~10s | 串行 | ~5.5h |
| Rust v1 | 200 | ~6s | 3.5核并行 | ~50min |
| Rust Arc | 200 | ~2s | 3.5核并行 | ~28min |
| Rust Arc | 524 | ~4s | 3.5核并行 | ~65min |
| 混合 | 100 | ~3s | 串行 | ~55min(100轮) |

---

## 九、常用操作

### 查看搜索进度
```bash
tail -5 /tmp/search_524.log
```

### 查看TOP结果
```bash
grep "#1 score" /tmp/search_524.log
```

### 查看是否跑完
```bash
grep "DONE" /tmp/search_524.log
```

### 重启交易脚本
```bash
screen -S trade -X quit
cd /home/myuser/websocket_new && screen -dmS trade bash -c "python3 -u sim_trade.py > /tmp/trade.log 2>&1"
```

### 重新编译Rust
```bash
source ~/.cargo/env && cd /home/myuser/backtester-rs && cargo build --release
```

### 更新COS数据
```bash
cd /home/myuser/backtester/cos_service && screen -dmS cos bash -c "python3 -u fetch_to_cos.py --init --days 60 > /tmp/cos_fetch.log 2>&1"
```

---

## 十、关键文件索引

| 用途 | 路径 |
|------|------|
| Rust引擎源码 | `/home/myuser/backtester-rs/src/` |
| Rust二进制 | `/home/myuser/backtester-rs/target/release/backtester-rs` |
| 搜索1结果 | `/home/myuser/backtester/results/test_search.json` |
| 搜索2结果 | `/home/myuser/backtester-rs/results/search_20260428_114250.json` |
| 搜索3结果 | `/home/myuser/backtester-rs/results/search_rust_opt.json` |
| 搜索4结果 | `/home/myuser/backtester-rs/results/search_524_2k.json` |
| 策略文档 | `/home/myuser/backtester-rs/results/STRATEGY_CURRENT.md` |
| 系统文档 | `/home/myuser/backtester-rs/results/SYSTEM_SKILL.md` |
| 实盘交易 | `/home/myuser/websocket_new/sim_trade.py` |
| 市场监控 | `/home/myuser/websocket_new/market_monitor_app.py` |
| COS管道 | `/home/myuser/backtester/cos_service/fetch_to_cos.py` |
| 本地缓存 | `/home/myuser/backtester/data_cache/` |
