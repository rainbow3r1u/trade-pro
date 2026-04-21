#!/usr/bin/env python3
"""
模拟自动交易系统 v2 - 多仓位版本
功能：
- 轮询5000端口策略信号
- 支持最多5个同时持仓
- 信号优先级仓位分配
- 包含止盈、止损、爆仓逻辑
"""
import os
import io
import json
import time
import requests
import threading
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv('/home/ubuntu/crypto-scanner/.env')

# ========== 配置 ==========
HOST = os.environ.get('MARKET_HOST', 'http://localhost:5000')
API_TIMEOUT = 10

# 模拟账户配置
INITIAL_CAPITAL = 100          # 初始资金 USDT
MAX_POSITIONS = 5             # 最大同时持仓数
DEFAULT_LEVERAGE = 10          # 默认杠杆
BASE_MARGIN = INITIAL_CAPITAL / MAX_POSITIONS  # 每仓位基础保证金 = 20 USDT

# 止盈止损配置
TAKE_PROFIT_PCT = 50           # 止盈：盈利达到保证金的50%
STOP_LOSS_WINDOW = 60          # 止损窗口：开仓前N分钟的最低价（1小时）
MIN_STOP_LOSS_PCT = 0.005        # 最小止损距离：止损价至少比开盘价低0.5%
LIQUIDATION_BUFFER = 5         # 爆仓缓冲：比理论爆仓价高5%（更安全）

# 联合保证金爆仓配置
MARGIN_RATIO_LIQUIDATION = 1.0   # 保证金率 < 100% 触发联合爆仓（账户权益 < 维持保证金）
MARGIN_RATIO_WARNING = 1.5       # 保证金率 < 150% 触发预警

# 策略配置
BB_CLIMB_ENABLED = True        # 启用布林爬坡信号（最低优先级）
BB_CANDIDATE_ENABLED = False   # 禁用布林候选蓄力（不做开仓减仓成交）
SURGE_ENABLED = True           # 启用delta_q大单追踪信号（第二优先级，buy_ratio>=70%）
VOL_SURGE_ENABLED = True       # 启用15分钟成交量突增信号（第一优先级）

# 信号阈值
BB_MIN_CONSECUTIVE_HOURS = 2   # 布林爬坡最少连续小时数
BB_CANDIDATE_MIN_HOURS = 2     # 布林候选最少蓄力小时数（已禁用）
SURGE_MIN_DELTA_Q = 500000     # delta_q突增最小值（USDT）
SURGE_MIN_BUY_RATIO = 0.70     # delta_q突增最小买入比（70%）
VOL_SURGE_MIN_RATIO = 1.5      # 15分钟成交量突增最小倍数（与monitor一致）
MIN_24H_VOLUME = 5_000_000    # 24小时成交额最低门槛（500万USDT）

# 排除的币种
EXCLUDE_SYMBOLS = {
    'BTCUSDT', 'ETHUSDT', 'SOLUSDT',
    'TSLAUSDT', 'NVDAUSDT', 'AMZNUSDT', 'GOOGLUSDT', 'AAPLUSDT',
    'COINUSDT', 'MSTRUSDT', 'METAUSDT', 'TSMUSDT',
    'XAUUSDT', 'XAGUSDT', 'XAUTUSDT', 'NATGASUSDT',
}

# ========== 全局状态 ==========
account = {
    "balance": INITIAL_CAPITAL,
    "initial_balance": INITIAL_CAPITAL,
    "total_pnl": 0,
    "total_trades": 0,
    "win_trades": 0,
    "loss_trades": 0,
    "win_rate": 0,
    "max_drawdown": 0,
    "peak_balance": INITIAL_CAPITAL,
}

# 联合保证金维持保证金率（简化）
MAINTENANCE_MARGIN_RATE = 0.005  # 0.5%

positions = []  # 当前持仓列表 [{symbol, entry_price, quantity, margin, ...}]

trade_log = []
signal_log = []

# ========== 工具函数 ==========

def api_get(endpoint: str) -> dict:
    try:
        r = requests.get(f"{HOST}{endpoint}", timeout=API_TIMEOUT)
        return r.json()
    except Exception as e:
        print(f"[API错误] {endpoint}: {e}")
        return {}

def get_current_price(symbol: str) -> float:
    """从币安API获取当前价格"""
    try:
        resp = requests.get(
            "https://fapi.binance.com/fapi/v1/ticker/price",
            params={"symbol": symbol},
            timeout=5
        )
        if resp.status_code == 200:
            data = resp.json()
            return float(data.get("price", 0))
    except:
        pass
    return 0

def get_24h_volume(symbol: str) -> float:
    """获取24小时成交额（USDT）- 直接从币安API获取"""
    try:
        resp = requests.get(
            "https://fapi.binance.com/fapi/v1/ticker/24hr",
            params={"symbol": symbol},
            timeout=5
        )
        if resp.status_code == 200:
            data = resp.json()
            return float(data.get("quoteVolume", 0))
    except:
        pass
    return 0

def get_binance_lowest_price(symbol: str, interval: str = "1m", limit: int = 180) -> float:
    """从币安API获取最近N根K线的最低价
    interval: 1m, 5m, 15m, 1h, 4h, 1d
    limit: K线数量（1小时=60根1m K线）
    """
    try:
        resp = requests.get(
            "https://fapi.binance.com/fapi/v1/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=5
        )
        if resp.status_code == 200:
            klines = resp.json()
            if klines:
                # K线格式: [open_time, open, high, low, close, ...]
                lows = [float(k[4]) for k in klines]  # k[4] = low price
                return min(lows)
    except:
        pass
    return 0

def get_stop_loss_price(symbol: str, entry_price: float) -> float:
    """获取止损价（从币安API获取前一小时最低价）"""
    # 从币安API获取过去24小时1h K线的最低价作为止损参考
    binance_low = get_binance_lowest_price(symbol, "1h", 24)
    if binance_low > 0:
        return calculate_stop_loss_price(entry_price, binance_low)
    
    # Fallback: 使用0.5%作为最小止损
    return entry_price * 0.995

def format_price(price: float) -> str:
    """根据价格大小自动调整显示精度（不使用科学计数法）"""
    if price <= 0:
        return "N/A"
    if price < 0.0001:
        return f"{price:.8f}"  # 小数后8位
    elif price < 0.001:
        return f"{price:.7f}"
    elif price < 0.01:
        return f"{price:.6f}"
    elif price < 0.1:
        return f"{price:.5f}"
    elif price < 1:
        return f"{price:.4f}"
    elif price < 100:
        return f"{price:.4f}"
    else:
        return f"{price:.2f}"

def get_recent_klines(symbol: str, minutes: int = 60) -> list:
    data = api_get(f"/api/minute_buy_ratio/{symbol}")
    klines = data.get("data", [])
    return klines[-minutes:] if len(klines) > minutes else klines

def calculate_margin_by_priority(signal_type: str, available_balance: float) -> float:
    """根据信号优先级计算保证金分配
    
    优先级（从高到低）：
    1. VOL_SURGE_1.5x+ : 20 USDT (全仓) - 15分钟成交量突增（第一优先级）
    2. SURGE_70%+      : 10 USDT (半仓) - 大单追踪 buy_ratio>=70%（第二优先级）
    3. BB_CLIMB_2h+    :  5 USDT (1/4仓) - 布林爬坡预警（第三优先级）
    4. BB_CAND_2h+     :  禁用（不做开仓减仓成交）
    """
    if signal_type.startswith("VOL_SURGE_"):
        return min(BASE_MARGIN, available_balance)  # 全仓 20 USDT
    elif signal_type.startswith("SURGE_"):
        return min(BASE_MARGIN * 0.5, available_balance)  # 半仓 10 USDT
    elif signal_type.startswith("BB_CLIMB_"):
        return min(BASE_MARGIN * 0.25, available_balance)  # 1/4仓 5 USDT
    elif signal_type.startswith("BB_CAND_"):
        return 0  # 禁用，不开仓
    else:
        return min(BASE_MARGIN * 0.5, available_balance)

def calculate_position(entry_price: float, margin: float, leverage: int) -> tuple:
    position_value = margin * leverage
    quantity = position_value / entry_price
    return quantity, position_value

def calculate_liquidation_price(entry_price: float, leverage: int, is_long: bool = True) -> float:
    if is_long:
        return entry_price * (1 - 1 / leverage)
    else:
        return entry_price * (1 + 1 / leverage)

def calculate_stop_loss_price(entry_price: float, klines_min_price: float) -> float:
    """计算止损价：确保至少比开盘价低MIN_STOP_LOSS_PCT"""
    # 理论止损价（K线最低价）
    stop_loss = klines_min_price
    # 最小止损价（开盘价 × (1 - MIN_STOP_LOSS_PCT)）
    min_stop = entry_price * (1 - MIN_STOP_LOSS_PCT)
    # 返回较低者（更保守的止损）
    return min(stop_loss, min_stop)

def calculate_pnl(entry_price: float, current_price: float, quantity: float, is_long: bool = True) -> float:
    if is_long:
        return (current_price - entry_price) * quantity
    else:
        return (entry_price - current_price) * quantity

# ========== 策略信号获取 ==========

def get_bb_climb_signals() -> list:
    if not BB_CLIMB_ENABLED:
        return []
    data = api_get("/api/bollinger_climb")
    return data.get("data", [])

def get_bb_candidate_signals() -> list:
    if not BB_CANDIDATE_ENABLED:
        return []
    data = api_get("/api/bollinger_climb")
    return data.get("candidates", [])

def get_surge_signals() -> list:
    if not SURGE_ENABLED:
        return []
    data = api_get("/api/surge")
    signals = data.get("data", [])
    # 过滤：buy_ratio >= 70%
    return [s for s in signals if s.get("avg_buy_ratio", 0) >= SURGE_MIN_BUY_RATIO]

def get_vol_surge_signals() -> list:
    if not VOL_SURGE_ENABLED:
        return []
    data = api_get("/api/vol_surge")
    signals = data.get("data", [])
    # 过滤：突增倍数 >= 最小倍数
    return [s for s in signals if s.get("ratio", 0) >= VOL_SURGE_MIN_RATIO]

# ========== 持仓管理 ==========

def get_position(symbol: str):
    """获取指定币种的持仓"""
    for p in positions:
        if p["symbol"] == symbol:
            return p
    return None

def get_positions_count():
    return len(positions)

def get_available_balance():
    """获取可用余额（减去已用保证金）"""
    used_margin = sum(p["margin"] for p in positions)
    return account["balance"] - used_margin

# ========== 交易操作 ==========

def open_position(symbol: str, signal_type: str, entry_price: float, 
                 stop_loss_price: float, leverage: int = DEFAULT_LEVERAGE) -> bool:
    """开仓"""
    global positions, account
    
    if symbol in EXCLUDE_SYMBOLS:
        return False
    
    if get_position(symbol):
        return False  # 已在持仓中
    
    if get_positions_count() >= MAX_POSITIONS:
        return False  # 已达最大持仓数
    
    available = get_available_balance()
    margin = calculate_margin_by_priority(signal_type, available)
    
    if margin < 5:
        return False  # 保证金不足
    
    quantity, position_value = calculate_position(entry_price, margin, leverage)
    
    # 止盈价
    take_profit_price = entry_price * (1 + TAKE_PROFIT_PCT / 100 / leverage)
    
    # 爆仓价（5%缓冲）
    theoretical_liq = calculate_liquidation_price(entry_price, leverage, is_long=True)
    liquidation_price = theoretical_liq * (1 + LIQUIDATION_BUFFER / 100)
    
    pos = {
        "symbol": symbol,
        "signal_type": signal_type,
        "entry_price": entry_price,
        "quantity": quantity,
        "margin": margin,
        "leverage": leverage,
        "position_value": position_value,
        "entry_time": time.time(),
        "stop_loss_price": stop_loss_price,
        "take_profit_price": take_profit_price,
        "liquidation_price": liquidation_price,
        "is_long": True,
    }
    
    positions.append(pos)
    
    # 扣除保证金
    account["balance"] -= margin
    
    log_entry = {
        "time": datetime.now().isoformat(),
        "action": "OPEN",
        "symbol": symbol,
        "signal_type": signal_type,
        "entry_price": entry_price,
        "quantity": quantity,
        "margin": margin,
        "leverage": leverage,
        "position_value": position_value,
        "stop_loss_price": stop_loss_price,
        "take_profit_price": take_profit_price,
        "liquidation_price": liquidation_price,
    }
    trade_log.append(log_entry)
    signal_log.append(log_entry)
    
    print(f"[开仓] {symbol} @ {format_price(entry_price)} | {signal_type}")
    print(f"       保证金: {margin:.1f} USDT ({margin/BASE_MARGIN*100:.0f}%仓) | 止损: {format_price(stop_loss_price)} | 止盈: {format_price(take_profit_price)}")
    
    return True

def close_position(pos: dict, reason: str, close_price: float, pnl: float):
    """平仓"""
    global positions, account
    
    margin = pos["margin"]
    leverage = pos["leverage"]
    
    # 手续费 0.04% × 2
    total_fee = margin * 0.0004 * 2
    actual_pnl = pnl - total_fee
    
    # 更新账户
    account["balance"] += margin + actual_pnl
    account["total_pnl"] += actual_pnl
    account["total_trades"] += 1
    
    if actual_pnl > 0:
        account["win_trades"] += 1
    else:
        account["loss_trades"] += 1
    
    if account["total_trades"] > 0:
        account["win_rate"] = account["win_trades"] / account["total_trades"] * 100
    
    if account["balance"] > account["peak_balance"]:
        account["peak_balance"] = account["balance"]
    drawdown = (account["peak_balance"] - account["balance"]) / account["peak_balance"] * 100
    if drawdown > account["max_drawdown"]:
        account["max_drawdown"] = drawdown
    
    log_entry = {
        "time": datetime.now().isoformat(),
        "action": "CLOSE",
        "reason": reason,
        "symbol": pos["symbol"],
        "entry_price": pos["entry_price"],
        "close_price": close_price,
        "quantity": pos["quantity"],
        "pnl": actual_pnl,
        "fee": total_fee,
        "margin": margin,
        "duration_secs": time.time() - pos["entry_time"],
    }
    trade_log.append(log_entry)
    
    print(f"[平仓] {reason} {pos['symbol']} @ {format_price(close_price)}")
    print(f"       盈亏: {actual_pnl:.2f} USDT | 余额: {account['balance']:.2f} USDT | 胜率: {account['win_rate']:.0f}%")
    
    positions.remove(pos)
    
    # 检测爆仓：记录并重置
    if reason in ("LIQUIDATED", "LIQUIDATED_CROSS"):
        handle_liquidation()

def handle_liquidation():
    """爆仓处理：保存记录并重置账户"""
    global account, positions, trade_log, signal_log
    
    print("\n" + "="*60)
    print("💥 爆仓！保存记录并重置账户")
    print("="*60)
    
    # 保存完整交易记录
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"/tmp/sim_trade_history_{timestamp}.json"
    
    summary = {
        "reset_time": datetime.now().isoformat(),
        "final_balance": account["balance"],
        "total_pnl": account["total_pnl"],
        "total_trades": account["total_trades"],
        "win_trades": account["win_trades"],
        "loss_trades": account["loss_trades"],
        "win_rate": account["win_rate"],
        "max_drawdown": account["max_drawdown"],
        "trade_log": trade_log,
    }
    
    with open(filename, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    
    print(f"交易记录已保存: {filename}")
    print(f"最终余额: {account['balance']:.2f} USDT")
    print(f"累计交易: {account['total_trades']}次, 胜率: {account['win_rate']:.1f}%")
    
    # 重置账户和持仓
    account["balance"] = INITIAL_CAPITAL
    account["total_pnl"] = 0
    account["total_trades"] = 0
    account["win_trades"] = 0
    account["loss_trades"] = 0
    account["win_rate"] = 0
    account["max_drawdown"] = 0
    account["peak_balance"] = INITIAL_CAPITAL
    
    positions = []
    trade_log = []
    signal_log = []
    
    print(f"\n账户已重置，初始资金: {INITIAL_CAPITAL} USDT")
    print("="*60 + "\n")

def check_positions():
    """检查所有持仓状态（联合保证金模式）
    
    检测顺序（优先级从高到低）：
    1. 单个仓位止盈
    2. 单个仓位止损
    3. 单个仓位爆仓（触及 liquidation_price）
    4. 联合保证金爆仓（margin_ratio < MARGIN_RATIO_LIQUIDATION）
    """
    positions_to_close = []
    
    # 计算联合保证金状态
    total_unrealized_pnl = 0
    total_maintenance_required = 0
    
    for pos in positions:
        symbol = pos["symbol"]
        current_price = get_current_price(symbol)
        
        if current_price <= 0:
            continue
        
        entry_price = pos["entry_price"]
        stop_loss = pos["stop_loss_price"]
        take_profit = pos["take_profit_price"]
        liquidation_price = pos["liquidation_price"]
        
        pnl = calculate_pnl(entry_price, current_price, pos["quantity"], is_long=True)
        total_unrealized_pnl += pnl
        
        # 维持保证金 = 仓位价值 × 0.5%
        maintenance = pos["position_value"] * MAINTENANCE_MARGIN_RATE
        total_maintenance_required += maintenance
        
        # 检测止盈（最高优先级）
        if current_price >= take_profit:
            positions_to_close.append((pos, "TAKE_PROFIT", current_price, pnl))
            continue
        
        # 检测止损
        if current_price <= stop_loss:
            positions_to_close.append((pos, "STOP_LOSS", current_price, pnl))
            continue
        
        # 检测单个仓位爆仓（触及 liquidation_price）
        if current_price <= liquidation_price:
            positions_to_close.append((pos, "LIQUIDATED", current_price, pnl))
            continue
    
    # 联合保证金检测
    # 账户总权益 = 剩余余额 + 已用保证金 + 未实现盈亏
    #            = 初始资金 + 累计已实现盈亏 + 未实现盈亏
    used_margin = sum(p["margin"] for p in positions)
    total_equity = account["balance"] + used_margin + total_unrealized_pnl
    
    margin_ratio = total_equity / total_maintenance_required if total_maintenance_required > 0 else float('inf')
    
    if margin_ratio < MARGIN_RATIO_LIQUIDATION and positions:
        # 触发联合爆仓，强平所有仓位
        print(f"\n⚠️ 联合保证金不足！保证金率: {margin_ratio:.2f}x (< {MARGIN_RATIO_LIQUIDATION}x)")
        print(f"   账户权益: {total_equity:.2f} USDT | 维持保证金: {total_maintenance_required:.2f} USDT")
        for pos in positions:
            symbol = pos["symbol"]
            current_price = get_current_price(symbol)
            if current_price <= 0:
                current_price = pos["entry_price"] * 0.5  # 估一个
            pnl = calculate_pnl(pos["entry_price"], current_price, pos["quantity"], is_long=True)
            positions_to_close.append((pos, "LIQUIDATED_CROSS", current_price, pnl))
    
    # 去重：避免同一仓位被多次平仓
    closed_symbols = set()
    for pos, reason, price, pnl in positions_to_close:
        if pos["symbol"] in closed_symbols:
            continue  # 跳过重复平仓
        close_position(pos, reason, price, pnl)
        closed_symbols.add(pos["symbol"])

# ========== 信号评估 ==========

def evaluate_and_open():
    """评估信号并开仓
    
    信号优先级（从高到低）：
    1. 15分钟成交量突增 VOL_SURGE — 全仓，按成交量倍数降序，尽量多开
    2. 大单追踪 SURGE — 半仓，按 buy_ratio 降序，buy_ratio>=70% 才成交
    3. 布林爬坡预警 BB_CLIMB — 1/4仓，最低优先级
    4. 布林候选蓄力 BB_CAND — 禁用（不做开仓减仓成交）
    """
    available = get_available_balance()
    if available < 5:
        return  # 可用余额不足
    
    if get_positions_count() >= MAX_POSITIONS:
        return  # 已达最大持仓数
    
    # 1. 15分钟成交量突增（第一优先级）
    # 成交量倍数越高越优先，不限制 opened，尽量多开
    if VOL_SURGE_ENABLED:
        signals = get_vol_surge_signals()
        # 按成交量倍数降序排序（越高越优先成交）
        signals.sort(key=lambda s: s.get("ratio", 0), reverse=True)
        for sig in signals:
            if get_positions_count() >= MAX_POSITIONS:
                break
            if get_available_balance() < 5:
                break
            
            symbol = sig.get("symbol", "")
            if symbol in EXCLUDE_SYMBOLS or get_position(symbol):
                continue
            
            ratio = sig.get("ratio", 0)
            if ratio < VOL_SURGE_MIN_RATIO:
                continue
            
            # 过滤24h成交额低于5000万的币
            vol_24h = get_24h_volume(symbol)
            if vol_24h < MIN_24H_VOLUME:
                continue
            
            entry_price = get_current_price(symbol)
            if entry_price <= 0:
                continue
            
            stop_loss_price = get_stop_loss_price(symbol, entry_price)
            signal_type = f"VOL_SURGE_{ratio:.1f}x"
            open_position(symbol, signal_type, entry_price, stop_loss_price)
    
    # 2. 大单追踪（第二优先级）
    # buy_ratio 越高越优先，buy_ratio>=70% 才成交
    if SURGE_ENABLED:
        signals = get_surge_signals()
        # 按 buy_ratio 降序排序（越高越优先成交）
        signals.sort(key=lambda s: s.get("avg_buy_ratio", 0), reverse=True)
        for sig in signals:
            if get_positions_count() >= MAX_POSITIONS:
                break
            if get_available_balance() < 5:
                break
            
            symbol = sig.get("symbol", "")
            if symbol in EXCLUDE_SYMBOLS or get_position(symbol):
                continue
            
            total_delta_q = sig.get("total_delta_q", 0)
            if total_delta_q < SURGE_MIN_DELTA_Q:
                continue
            
            # 过滤24h成交额低于5000万的币
            vol_24h = get_24h_volume(symbol)
            if vol_24h < MIN_24H_VOLUME:
                continue
            
            entry_price = get_current_price(symbol)
            if entry_price <= 0:
                continue
            
            stop_loss_price = get_stop_loss_price(symbol, entry_price)
            signal_type = f"SURGE_{sig.get('count', 0)}x"
            open_position(symbol, signal_type, entry_price, stop_loss_price)
    
    # 3. 布林爬坡预警（第三优先级，最低）
    if BB_CLIMB_ENABLED:
        signals = get_bb_climb_signals()
        for sig in signals:
            if get_positions_count() >= MAX_POSITIONS:
                break
            if get_available_balance() < 5:
                break
            
            symbol = sig.get("symbol", "")
            if symbol in EXCLUDE_SYMBOLS or get_position(symbol):
                continue
            
            # 过滤24h成交额低于5000万的币
            vol_24h = get_24h_volume(symbol)
            if vol_24h < MIN_24H_VOLUME:
                continue
            
            entry_price = get_current_price(symbol)
            if entry_price <= 0:
                continue
            
            stop_loss_price = get_stop_loss_price(symbol, entry_price)
            consecutive = sig.get("consecutive_hours", 0)
            
            if consecutive >= BB_MIN_CONSECUTIVE_HOURS:
                signal_type = f"BB_CLIMB_{consecutive}h"
                open_position(symbol, signal_type, entry_price, stop_loss_price)
    
    # 4. 布林候选蓄力 — 禁用，不做开仓减仓成交
    # if BB_CANDIDATE_ENABLED: ... (已禁用)

# ========== 主循环 ==========

def print_status():
    # 计算联合保证金状态
    total_unrealized_pnl = 0
    total_maintenance_required = 0
    
    for pos in positions:
        symbol = pos["symbol"]
        current = get_current_price(symbol)
        if current <= 0:
            continue
        pnl = calculate_pnl(pos["entry_price"], current, pos["quantity"], is_long=True)
        total_unrealized_pnl += pnl
        maintenance = pos["position_value"] * MAINTENANCE_MARGIN_RATE
        total_maintenance_required += maintenance
    
    # 修正：账户总权益 = 剩余余额 + 已用保证金 + 未实现盈亏
    used_margin = sum(p["margin"] for p in positions)
    total_equity = account["balance"] + used_margin + total_unrealized_pnl
    margin_ratio = total_equity / total_maintenance_required if total_maintenance_required > 0 else float('inf')
    
    print(f"\n{'='*60}")
    print(f"模拟交易 @ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [联合保证金模式]")
    print(f"{'='*60}")
    print(f"余额: {account['balance']:.2f} USDT | 累计盈亏: {account['total_pnl']:.2f} USDT")
    print(f"交易: {account['total_trades']}次 | 胜率: {account['win_rate']:.0f}% | 最大回撤: {account['max_drawdown']:.1f}%")
    print(f"账户权益: {total_equity:.2f} USDT | 维持保证金: {total_maintenance_required:.2f} USDT | 保证金率: {margin_ratio:.2f}x")
    print(f"持仓: {get_positions_count()}/{MAX_POSITIONS} | 可用: {get_available_balance():.2f} USDT")
    
    if positions:
        print(f"\n{'Symbol':<14} {'Entry':<10} {'Current':<10} {'Margin':<7} {'PnL%':<8} {'Liq%':<8} {'状态'}")
        print("-" * 75)
        for pos in positions:
            symbol = pos["symbol"]
            current = get_current_price(symbol)
            entry = pos["entry_price"]
            pnl_pct = (current - entry) / entry * 100
            margin = pos["margin"]
            liq_pct = (current - pos['liquidation_price']) / entry * 100 if entry > 0 else 0
            
            # 判断状态
            if current >= pos["take_profit_price"]:
                status = "✓止盈"
            elif current <= pos["liquidation_price"]:
                status = "💥爆仓"
            elif current <= pos["stop_loss_price"]:
                status = "✗止损"
            else:
                status = "持仓中"
            
            print(f"{symbol:<14} {format_price(entry):<12} {format_price(current):<12} {margin:<7.1f} {pnl_pct:>+7.2f}% {liq_pct:>+7.2f}% {status}")
        
        # 联合保证金警告
        if margin_ratio < MARGIN_RATIO_LIQUIDATION:
            print(f"\n🚨 联合保证金率 {margin_ratio:.2f}x < {MARGIN_RATIO_LIQUIDATION}x，即将爆仓！")
        elif margin_ratio < MARGIN_RATIO_WARNING:
            print(f"\n⚠️ 联合保证金预警，保证金率 {margin_ratio:.2f}x (< {MARGIN_RATIO_WARNING}x)")
    else:
        print("\n无持仓")
    print("=" * 60)

def save_state():
    state = {
        "account": account,
        "positions": positions,
        "trade_log": trade_log[-100:],
    }
    # 原子写入 + 文件锁，防止与 web 服务同时读写导致 JSON 损坏
    import tempfile
    import fcntl
    tmp_path = "/tmp/sim_trade_state.json.tmp"
    with open(tmp_path, "w") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        json.dump(state, f, indent=2, default=str)
        f.flush()
        os.fsync(f.fileno())
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    os.replace(tmp_path, "/tmp/sim_trade_state.json")

def main():
    print("=" * 60)
    print("模拟自动交易系统 v2")
    print(f"初始资金: {INITIAL_CAPITAL} USDT | 最大持仓: {MAX_POSITIONS}个")
    print(f"基础保证金: {BASE_MARGIN} USDT/仓 | 杠杆: {DEFAULT_LEVERAGE}x")
    print("=" * 60)
    
    last_status_time = 0
    last_signal_check = 0
    
    while True:
        try:
            now = time.time()
            
            # 检查持仓
            if positions:
                check_positions()
            
            # 每10秒评估信号
            if now - last_signal_check >= 10:
                last_signal_check = now
                if get_positions_count() < MAX_POSITIONS:
                    evaluate_and_open()
            
            # 每60秒打印状态
            if now - last_status_time >= 60:
                last_status_time = now
                print_status()
                save_state()
            
            time.sleep(1)
            
        except KeyboardInterrupt:
            print("\n退出...")
            if positions:
                print(f"注意：还有 {len(positions)} 个持仓未平仓")
            break
        except Exception as e:
            print(f"[错误] {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
