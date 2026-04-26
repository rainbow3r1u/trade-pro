# 已知Bug与陷阱

## 1. 余额重复扣除（已修复）

**问题**: `open_position()` 曾执行 `account["balance"] -= margin`，同时 `get_available_balance()` 计算 `balance - used_margin`，导致双重扣除。

**修复**: 删除 `open_position()` 中的 `balance -= margin`，改为 balance 只增减实际盈亏。

**状态**: ✅ 已修复，绝不可恢复旧逻辑。

## 2. 止损 min/max 混淆（已修复）

**问题**: `calculate_stop_loss_price()` 中误用 `min()` 而非 `max()`，导致止损价远离开仓价，失去保护作用。

**修复**: 改为 `max()` 逻辑（确保止损价不低于某个下限）。

**状态**: ✅ 已修复。

## 3. K线字段错误（已修复）

**问题**: 止损基于 `k[4]` (close) 而非 `k[3]` (low)，导致止损价偏高。

**修复**: 全部改为 `k[3]` (low)。

**状态**: ✅ 已修复。

## 4. 数据口径不统一（已修复）

**问题**: 部分API走合约 `fapi.binance.com`，部分走现货 `api.binance.com`，价格/成交量不一致。

**修复**: 全部统一为现货 `api.binance.com/api/v3/`。

**状态**: ✅ 已修复。新增接口必须使用现货。

## 5. 内存泄漏（已修复）

**问题**: `minute_klines` 保留6天数据，`hourly_kline_cache` 无限增长，内存达2.2GB。

**修复**:
- `minute_klines`: 6天 → 120条（2小时）
- `hourly_kline_cache`: 无限 → 36根
- `trades_aggregator`: 后台线程每60秒 `cleanup_old_data()`

**状态**: ✅ 已修复，当前约315MB。

## 6. 信号时效过长（已修复）

**问题**: VOL_SURGE 信号保留1小时，与交易脚本的实时性不匹配。

**修复**: 缩短为5分钟（300秒），与交易脚本同步。

**状态**: ✅ 已修复。

## 7. 缓存导致前端数据不更新（已修复）

**问题**: 浏览器缓存API响应，导致前端显示旧数据。

**修复**: 全站三重防缓存（HTTP头/HTML meta/随机参数）。

**状态**: ✅ 已修复。

## 常见运行时异常

| 现象 | 原因 | 处理 |
|------|------|------|
| `price=0` in debug | 刚启动或COS加载未完成 | 正常现象，等待数据加载 |
| 无VOL_SURGE信号 | `vol_15m_avg` 需约1小时积累历史；或刚重启history为空 | 冷启动正常，等下一个15分钟区间结束 |
| BB_CLIMB信号少 | 多为小币，300万门槛过滤 | 正常现象，可考虑降门槛 |
| 联合爆仓 | 高杠杆+多币种同时亏损 | 已简化机制，总权益<=0即触发 |

## 修改禁忌

1. **不要** 恢复 `balance -= margin` 到 `open_position()`
2. **不要** 混用合约/现货 API
3. **不要** 让 K线缓存无限增长（必须截断）
4. **不要** 将 VOL_SURGE/SURGE 信号时效改回1小时
5. **不要** 在止损计算中用 `k[4]` (close) 代替 `k[3]` (low)
6. **不要** 让 `vol_surge_history` 和 `vol_surge_symbols` 混用——交易脚本依赖5分钟的symbols，前端展示用1小时的history
