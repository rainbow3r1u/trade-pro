#!/usr/bin/env python3
"""
V7 混合策略实盘模拟系统
- 现货端: 日线布林带爬坡策略 (p=30, std=2.5, h=4, hlw=5, hlm=3)
- 合约端: 15分钟量surge策略，只在有现货持仓的币种上开仓

一切参数根据 v7 2000轮验证最优结果设定
"""
import os
import io
import json
import time
import requests
import threading
import hmac
import hashlib
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent / '.env')

# ========== V7 最优参数配置 ==========

# --- 现货参数 (BB Spot) ---
SPOT_INITIAL_CAPITAL = 100          # 初始资金 USDT
SPOT_PER_TRADE = 5                  # 每单金额 USDT
SPOT_MAX_POSITIONS = 20             # 最大持仓币种数
SPOT_FEE = 0.001                    # 手续费 0.1%
SPOT_TP_MULTIPLIER = 2.0            # 止盈: 2倍买入价
SPOT_GAIN_FILTER_PCT = 15.0         # BB日涨幅限制 (%)
SPOT_VOL_FILTER = 1_000_000         # 24h最低成交量
SPOT_BB_PERIOD = 20                 # 布林带周期(日线)
SPOT_BB_STD_MULT = 2.5              # 标准差倍数
SPOT_MIN_HOURS = 4                  # BB最小连续天数
SPOT_HL_WINDOW = 5                  # 高低点窗口（天）
SPOT_HL_MIN = 3                     # 窗口内最少高低点个数

# --- 合约参数 (VS Futures) ---
FUT_INITIAL_CAPITAL = 100           # 初始资金 USDT
FUT_MARGIN = 20                     # 每单保证金 USDT
FUT_MAX_POSITIONS = 20              # 最大持仓（实际受限于现货）
FUT_LEVERAGE = 10                   # 杠杆倍数
FUT_TP_PCT = 50                     # 止盈比例 (%)
FUT_SL_PCT = 0.02                   # 止损比例 2%
FUT_VOL_FILTER = 1_000_000          # 24h最低成交量
FUT_MIN_RATIO = 1.0                 # 量surge最小倍数
FUT_MIN_GAIN_PCT = 2.3              # 15m涨幅最小阈值%（网站端已过滤，此处为防御）
FUT_MAX_DAILY_TP = 4                # 日最大止盈次数
SPOT_EXHAUSTED_THRESHOLD = 15       # VS止盈N次后标记BB耗尽

# --- 其他配置 ---
HOST = os.environ.get('MARKET_HOST', 'http://localhost:5003')
API_TIMEOUT = 10

EXCLUDE_SYMBOLS = {
    # 大盘/股票/商品
    'BTCUSDT', 'ETHUSDT', 'SOLUSDT',
    'TSLAUSDT', 'NVDAUSDT', 'AMZNUSDT', 'GOOGLUSDT', 'AAPLUSDT',
    'COINUSDT', 'MSTRUSDT', 'METAUSDT', 'TSMUSDT',
    'XAUUSDT', 'XAGUSDT', 'XAUTUSDT', 'NATGASUSDT',
    # 稳定币对
    'USDCUSDT', 'RLUSDUSDT', 'UUSDT', 'XUSDUSDT', 'USD1USDT',
    'FDUSDUSDT', 'TUSDUSDT', 'PAXUSDT', 'BUSDUSDT', 'SUSDUSDT',
    'USDEUSDT', 'USDPUSDT', 'USDSUSDT', 'AEURUSDT', 'EURIUSDT', 'EURUSDT',
    'BFUSDUSDT',
    # 现货专属（期货无此交易对，对齐回测数据）
    'ACMUSDT', 'ADXUSDT', 'ALCXUSDT', 'AMPUSDT', 'ARDRUSDT',
    'ATMUSDT', 'AUDIOUSDT', 'BARUSDT', 'BNSOLUSDT',
    'BTTCUSDT', 'CITYUSDT', 'DCRUSDT', 'DGBUSDT', 'DODOUSDT',
    'FARMUSDT', 'FTTUSDT', 'GLMRUSDT', 'GNOUSDT', 'GNSUSDT',
    'IQUSDT', 'JUVUSDT', 'KGSTUSDT', 'LAZIOUSDT', 'LUNAUSDT',
    'MBLUSDT', 'NEXOUSDT', 'OSMOUSDT', 'PIVXUSDT', 'PONDUSDT',
    'PORTOUSDT', 'PSGUSDT', 'PYRUSDT', 'QIUSDT', 'QKCUSDT',
    'QUICKUSDT', 'RADUSDT', 'REQUSDT', 'SCUSDT', 'STRAXUSDT',
    'TFUELUSDT', 'TKOUSDT', 'WBETHUSDT', 'WBTCUSDT', 'WINUSDT',
    'XNOUSDT',
}

# Spot → Futures symbol映射（合约与现货名称不一致的币种）
SPOT_TO_FUTURES = {
    'RAYUSDT':   'RAYSOLUSDT',
    'BONKUSDT':  '1000BONKUSDT',
    'FLOKIUSDT': '1000FLOKIUSDT',
    'PEPEUSDT':  '1000PEPEUSDT',
    'SHIBUSDT':  '1000SHIBUSDT',
    'LUNCUSDT':  '1000LUNCUSDT',
    'XECUSDT':   '1000XECUSDT',
}
FUTURES_TO_SPOT = {v: k for k, v in SPOT_TO_FUTURES.items()}

def spot_to_futures_symbol(spot_symbol: str) -> str:
    """现货symbol → 合约symbol"""
    return SPOT_TO_FUTURES.get(spot_symbol, spot_symbol)

def futures_to_spot_symbol(futures_symbol: str) -> str:
    """合约symbol → 现货symbol"""
    return FUTURES_TO_SPOT.get(futures_symbol, futures_symbol)

# ========== 全局状态 ==========

# 现货账户
spot_account = {
    "balance": SPOT_INITIAL_CAPITAL,
    "initial_balance": SPOT_INITIAL_CAPITAL,
    "total_pnl": 0,
    "total_trades": 0,
    "win_trades": 0,
    "loss_trades": 0,
    "win_rate": 0,
    "max_drawdown": 0,
    "peak_balance": SPOT_INITIAL_CAPITAL,
}
spot_positions = []  # [{symbol, entry_price, quantity, cost_basis, entry_time, tp_price, sl_price}]

# 合约账户
futures_account = {
    "balance": FUT_INITIAL_CAPITAL,
    "initial_balance": FUT_INITIAL_CAPITAL,
    "total_pnl": 0,
    "total_trades": 0,
    "win_trades": 0,
    "loss_trades": 0,
    "win_rate": 0,
    "max_drawdown": 0,
    "peak_balance": FUT_INITIAL_CAPITAL,
}
futures_positions = []  # [{symbol, entry_price, quantity, margin, leverage, position_value, entry_time, tp_price, sl_price, signal_type, signal_detail}]

# ---- 验证层：资金费率 + 恐惧贪婪指数 ----
FUNDING_CACHE = {}          # {symbol: rate}
FUNDING_LAST_FETCH = 0      # 上次拉取时间戳
FUNDING_FETCH_INTERVAL = 3600  # 每小时拉一次
FUNDING_MAX_RATE = 0.0005   # 0.05% 做多成本上限

FEAR_GREED_VALUE = 50       # 当前值(0-100)，默认50
FEAR_GREED_LAST_FETCH = 0
FEAR_GREED_FETCH_INTERVAL = 86400  # 每天一次

ETF_BTC_FLOW = 0            # 昨日BTC ETF净流入(百万$)，默认0
ETF_FLOW_PATH = "/home/myuser/openclaw-5001-host/config/.openclaw/workspace/etf_data/etf_flow.json"

def check_etf_flow() -> bool:
    """读取ETF数据，返回是否允许VS交易（流入=允许，流出=禁止）"""
    global ETF_BTC_FLOW
    try:
        with open(ETF_FLOW_PATH, "r") as f:
            data = json.load(f)
        btc = data.get("btc", [])
        if btc:
            ETF_BTC_FLOW = btc[-1].get("total_flow", 0)
            if ETF_BTC_FLOW < 0:
                return False  # 流出，禁止VS
    except Exception:
        pass
    return True

def fetch_funding_rates():
    """拉取全部合约资金费率"""
    global FUNDING_CACHE, FUNDING_LAST_FETCH
    now = time.time()
    if now - FUNDING_LAST_FETCH < FUNDING_FETCH_INTERVAL:
        return
    try:
        resp = requests.get("https://fapi.binance.com/fapi/v1/premiumIndex", timeout=10)
        if resp.status_code != 200:
            return
        for item in resp.json():
            sym = item.get("symbol", "")
            if sym.endswith("USDT"):
                rate = float(item.get("lastFundingRate", 0))
                FUNDING_CACHE[sym] = rate
        FUNDING_LAST_FETCH = now
        high = sum(1 for r in FUNDING_CACHE.values() if r > FUNDING_MAX_RATE)
        print(f"[资金费率] 已缓存 {len(FUNDING_CACHE)} 币种 | 费率>0.05%: {high} 个")
    except Exception as e:
        print(f"[资金费率] 拉取失败: {e}")

def check_funding_filter(symbol: str) -> tuple:
    """检查资金费率是否通过。返回 (ok, reason)"""
    futures_sym = SPOT_TO_FUTURES.get(symbol, symbol)
    rate = FUNDING_CACHE.get(futures_sym, 0)
    if abs(rate) < 0.000001:
        # 缓存里没有，先拉一次
        fetch_funding_rates()
        rate = FUNDING_CACHE.get(futures_sym, 0)
    if rate > FUNDING_MAX_RATE:
        return False, f"资金费率{rate*100:.2f}% > 0.05%，做多成本过高"
    return True, f"费率{rate*100:.2f}%"

def fetch_fear_greed():
    """拉取恐惧贪婪指数"""
    global FEAR_GREED_VALUE, FEAR_GREED_LAST_FETCH
    now = time.time()
    if now - FEAR_GREED_LAST_FETCH < FEAR_GREED_FETCH_INTERVAL:
        return
    try:
        resp = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
        if resp.status_code != 200:
            return
        data = resp.json().get("data", [])
        if data:
            FEAR_GREED_VALUE = int(data[0]["value"])
            FEAR_GREED_LAST_FETCH = now
            cls = data[0].get("value_classification", "")
            print(f"[恐惧贪婪] {FEAR_GREED_VALUE} ({cls})")
    except Exception as e:
        print(f"[恐惧贪婪] 拉取失败: {e}")

def get_risk_adjusted_max_positions() -> int:
    """根据恐惧贪婪指数动态调整最大现货持仓数"""
    global FEAR_GREED_VALUE
    fetch_fear_greed()
    v = FEAR_GREED_VALUE
    if v > 85:
        return max(5, SPOT_MAX_POSITIONS // 3)   # 极端贪婪：萎缩到1/3
    elif v > 70:
        return max(8, SPOT_MAX_POSITIONS // 2)   # 贪婪：减半
    elif v < 20:
        return SPOT_MAX_POSITIONS                # 极端恐惧：全额（信号质量高）
    elif v < 35:
        return min(SPOT_MAX_POSITIONS, 25)       # 恐惧：偏积极
    return SPOT_MAX_POSITIONS                     # 中性：不变

# 联动状态
spot_entry_ts = {}  # {symbol: entry_timestamp} 记录每个币种的现货入场时间

# ---- CC爆发策略：合约前置锚 ----
CC_ANCHOR_MARGIN = 5           # 每个CC锚保证金 5 USDT
CC_ANCHOR_TP_PCT = 1.0         # CC锚止盈100%
CC_ANCHOR_SL_PCT = 0.02        # CC锚止损2%
CC_ANCHOR_MAX = 10              # CC锚最大数量
cc_anchors = []  # [{symbol, entry_price, quantity, margin, entry_time, tp_price, sl_price}]
cc_entry_ts = {}  # {symbol: entry_timestamp}

# 过滤/冷却状态
daily_take_profit_count = {}  # {(symbol, date_str): count} 合约日止盈计数
cooldown_symbols = {}  # {symbol: cooldown_end_timestamp} 止损后冷却
tp_per_symbol = {}  # {symbol: count} VS止盈总次数（用于耗尽检测）
exhausted_symbols = set()  # VS止盈>=5次已耗尽的币种

# 交易日志
spot_trade_log = []
futures_trade_log = []
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
    # 先试现货
    try:
        resp = requests.get(
            "https://api.binance.com/api/v3/ticker/price",
            params={"symbol": symbol}, timeout=5
        )
        if resp.status_code == 200:
            return float(resp.json().get("price", 0))
    except:
        pass
    # 现货没有，试合约
    try:
        resp = requests.get(
            "https://fapi.binance.com/fapi/v1/ticker/price",
            params={"symbol": symbol}, timeout=5
        )
        if resp.status_code == 200:
            return float(resp.json().get("price", 0))
    except:
        pass
    return 0

def get_24h_volume(symbol: str) -> float:
    try:
        resp = requests.get(
            "https://api.binance.com/api/v3/ticker/24hr",
            params={"symbol": symbol},
            timeout=5
        )
        if resp.status_code == 200:
            return float(resp.json().get("quoteVolume", 0))
    except:
        pass
    return 0

def get_beijing_date_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def fut_pos_price_symbol(pos: dict) -> str:
    """合约持仓查价用symbol（自动用现货名查spot API）"""
    return pos.get("spot_symbol", pos["symbol"])

def close_futures_position_forced(pos: dict, reason: str) -> float:
    """强制平合约仓位（不检查TP/SL），返回平仓价"""
    global futures_positions, futures_account
    symbol = pos["symbol"]
    spot_sym = fut_pos_price_symbol(pos)
    current_price = get_current_price(spot_sym)
    if current_price <= 0:
        current_price = pos["entry_price"]
    pnl = (current_price - pos["entry_price"]) * pos["quantity"]
    fee = pos["position_value"] * 0.0004 * 2
    net_pnl = pnl - fee
    futures_account["balance"] += net_pnl
    futures_account["total_pnl"] += net_pnl
    futures_account["total_trades"] += 1
    if net_pnl > 0:
        futures_account["win_trades"] += 1
    else:
        futures_account["loss_trades"] += 1
    log = {
        "time": datetime.now().isoformat(),
        "action": "CLOSE",
        "symbol": symbol,
        "signal_type": pos.get("signal_type", ""),
        "entry_price": pos["entry_price"],
        "close_price": current_price,
        "pnl": net_pnl,
        "reason": reason,
    }
    futures_trade_log.append(log)
    futures_positions.remove(pos)
    save_state()
    print(f"[合约平仓] {symbol} @ {format_price(current_price)} | {reason} | PnL: {net_pnl:.4f} USDT")
    return current_price

def is_in_cooldown(symbol: str) -> bool:
    return time.time() < cooldown_symbols.get(symbol, 0)

def set_cooldown(symbol: str, minutes: int = 30):
    cooldown_symbols[symbol] = time.time() + minutes * 60

def check_daily_tp_filter(symbol: str) -> bool:
    date_str = get_beijing_date_str()
    key = (symbol, date_str)
    count = daily_take_profit_count.get(key, 0)
    if count >= FUT_MAX_DAILY_TP:
        return False
    return True

def record_take_profit(symbol: str):
    date_str = get_beijing_date_str()
    key = (symbol, date_str)
    daily_take_profit_count[key] = daily_take_profit_count.get(key, 0) + 1
    count = daily_take_profit_count[key]
    print(f"[合约日止盈] {symbol} 当日止盈 {count}/{FUT_MAX_DAILY_TP} 次")

    # 耗尽检测：VS止盈累计>=5次 → 标记现货耗尽
    tp_per_symbol[symbol] = tp_per_symbol.get(symbol, 0) + 1
    if tp_per_symbol[symbol] >= SPOT_EXHAUSTED_THRESHOLD and symbol not in exhausted_symbols:
        exhausted_symbols.add(symbol)
        print(f"[耗尽] {symbol} VS已止盈{tp_per_symbol[symbol]}次，标记为耗尽，不再开合约")

def format_price(price: float) -> str:
    if price <= 0:
        return "N/A"
    if price < 0.0001:
        return f"{price:.8f}"
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

def check_daily_gain_filter(symbol: str, max_gain_pct: float = SPOT_GAIN_FILTER_PCT) -> tuple[bool, float]:
    """日线涨幅过滤"""
    try:
        resp = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": symbol, "interval": "1d", "limit": 1},
            timeout=5
        )
        if resp.status_code != 200:
            return True, 0
        klines = resp.json()
        if not klines:
            return True, 0
        k = klines[0]
        open_price = float(k[1])
        close_price = float(k[4])
        if open_price <= 0:
            return True, 0
        gain_pct = (close_price - open_price) / open_price * 100
        if gain_pct > max_gain_pct:
            return False, gain_pct
        return True, gain_pct
    except Exception as e:
        return True, 0

def get_daily_klines(symbol: str, limit: int = 40) -> list:
    """获取日线K线"""
    try:
        resp = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": symbol, "interval": "1d", "limit": limit},
            timeout=5
        )
        if resp.status_code == 200:
            return resp.json()
    except:
        pass
    return []

def calculate_bb_lower_band(closes: list, period: int = 30, std_mult: float = 2.5) -> float:
    """计算布林带下轨"""
    if len(closes) < period:
        return None
    recent = closes[-period:]
    sma = sum(recent) / len(recent)
    variance = sum((c - sma) ** 2 for c in recent) / len(recent)
    std = variance ** 0.5
    return sma - std_mult * std

def calculate_pnl(entry_price: float, current_price: float, quantity: float) -> float:
    return (current_price - entry_price) * quantity

# ========== 现货策略 ==========

def get_bb_climb_signals() -> list:
    data = api_get("/api/bollinger_climb_daily")
    signals = data.get("data", [])
    updated_at = data.get("updated_at", 0)
    now = time.time()
    if now - updated_at > 300:
        return []
    return signals

def get_spot_position(symbol: str):
    for p in spot_positions:
        if p["symbol"] == symbol:
            return p
    return None

def get_spot_positions_count():
    return len(spot_positions)

def open_spot_position(symbol: str, entry_price: float, signal_detail: dict = None):
    """开现货仓位"""
    global spot_positions, spot_account
    
    if symbol in EXCLUDE_SYMBOLS:
        return False
    
    if get_spot_position(symbol):
        return False
    
    max_pos = get_risk_adjusted_max_positions()
    if get_spot_positions_count() >= max_pos:
        return False

    if spot_account["balance"] < SPOT_PER_TRADE:
        return False
    
    # 涨幅过滤
    passed, gain_pct = check_daily_gain_filter(symbol)
    if not passed:
        print(f"[现货涨幅过滤] {symbol} 当日涨幅 {gain_pct:.2f}% > {SPOT_GAIN_FILTER_PCT}%，跳过")
        return False
    
    # 计算数量
    quantity = SPOT_PER_TRADE / entry_price
    cost = SPOT_PER_TRADE * (1 + SPOT_FEE)
    
    # 止盈: 2倍买入价
    tp_price = entry_price * SPOT_TP_MULTIPLIER
    
    # 止损: 基于日线布林带下轨（用最近period天数据）
    klines = get_daily_klines(symbol, SPOT_BB_PERIOD + 5)
    sl_price = entry_price * 0.9  # 默认兜底
    if klines and len(klines) >= SPOT_BB_PERIOD:
        closes = [float(k[4]) for k in klines[-SPOT_BB_PERIOD:]]
        bb_lower = calculate_bb_lower_band(closes, SPOT_BB_PERIOD, SPOT_BB_STD_MULT)
        if bb_lower and bb_lower > 0:
            sl_price = bb_lower
    
    pos = {
        "symbol": symbol,
        "entry_price": entry_price,
        "quantity": quantity,
        "cost_basis": cost,
        "entry_time": time.time(),
        "tp_price": tp_price,
        "sl_price": sl_price,
    }
    
    spot_positions.append(pos)
    spot_account["balance"] -= cost
    spot_entry_ts[symbol] = pos["entry_time"]
    
    log = {
        "time": datetime.now().isoformat(),
        "action": "OPEN",
        "symbol": symbol,
        "entry_price": entry_price,
        "quantity": quantity,
        "cost": cost,
        "tp_price": tp_price,
        "sl_price": sl_price,
        "detail": signal_detail or {},
    }
    spot_trade_log.append(log)
    signal_log.append(log)
    
    print(f"[现货开仓] {symbol} @ {format_price(entry_price)} | 数量: {quantity:.4f} | 成本: {cost:.2f} USDT | TP: {format_price(tp_price)} | SL: {format_price(sl_price)}")
    return True

def close_spot_position(pos: dict, reason: str, close_price: float, pnl: float):
    """平现货仓位"""
    global spot_positions, spot_account
    
    symbol = pos["symbol"]
    quantity = pos["quantity"]
    
    # 卖出金额 - 手续费
    sell_value = close_price * quantity * (1 - SPOT_FEE)
    actual_pnl = sell_value - pos["cost_basis"]
    
    spot_account["balance"] += sell_value
    spot_account["total_pnl"] += actual_pnl
    spot_account["total_trades"] += 1
    
    if actual_pnl > 0:
        spot_account["win_trades"] += 1
    else:
        spot_account["loss_trades"] += 1
    
    if spot_account["total_trades"] > 0:
        spot_account["win_rate"] = spot_account["win_trades"] / spot_account["total_trades"] * 100
    
    if spot_account["balance"] > spot_account["peak_balance"]:
        spot_account["peak_balance"] = spot_account["balance"]
    drawdown = (spot_account["peak_balance"] - spot_account["balance"]) / spot_account["peak_balance"] * 100
    if drawdown > spot_account["max_drawdown"]:
        spot_account["max_drawdown"] = drawdown
    
    log = {
        "time": datetime.now().isoformat(),
        "action": "CLOSE",
        "reason": reason,
        "symbol": symbol,
        "entry_price": pos["entry_price"],
        "close_price": close_price,
        "quantity": quantity,
        "pnl": actual_pnl,
        "balance": spot_account["balance"],
    }
    spot_trade_log.append(log)
    
    print(f"[现货平仓] {reason} {symbol} @ {format_price(close_price)} | 盈亏: {actual_pnl:+.2f} USDT | 余额: {spot_account['balance']:.2f}")
    
    spot_positions.remove(pos)
    save_state()

def check_spot_positions():
    """检查所有现货持仓的TP/SL"""
    positions_to_close = []
    
    for pos in spot_positions:
        symbol = pos["symbol"]
        current_price = get_current_price(symbol)
        
        if current_price <= 0:
            continue
        
        entry_price = pos["entry_price"]
        tp_price = pos["tp_price"]
        sl_price = pos["sl_price"]
        quantity = pos["quantity"]
        
        # 止盈检测
        if current_price >= tp_price:
            pnl = calculate_pnl(entry_price, current_price, quantity)
            positions_to_close.append((pos, "TAKE_PROFIT", current_price, pnl))
            continue
        
        # 止损检测
        if current_price <= sl_price:
            pnl = calculate_pnl(entry_price, current_price, quantity)
            positions_to_close.append((pos, "STOP_LOSS", current_price, pnl))
            continue

    # BB信号消失检测（阴线踢出）
    if spot_positions and not positions_to_close:
        bb_signal_symbols = {s.get("symbol") for s in get_bb_climb_signals()}
        for pos in spot_positions:
            if pos["symbol"] not in bb_signal_symbols:
                current_price = get_current_price(pos["symbol"])
                if current_price > 0:
                    pnl = calculate_pnl(pos["entry_price"], current_price, pos["quantity"])
                    positions_to_close.append((pos, "BB_SIGNAL_LOST", current_price, pnl))

    for pos, reason, price, pnl in positions_to_close:
        close_spot_position(pos, reason, price, pnl)

def evaluate_spot_signals():
    """评估BB信号并开现货仓位"""
    signals = get_bb_climb_signals()
    
    for sig in signals:
        symbol = sig.get("symbol", "")

        if symbol in EXCLUDE_SYMBOLS or symbol in exhausted_symbols:
            continue
        if get_spot_position(symbol):
            continue
        adjusted_max = get_risk_adjusted_max_positions()
        if get_spot_positions_count() >= adjusted_max:
            break
        if spot_account["balance"] < SPOT_PER_TRADE:
            break

        consecutive = sig.get("consecutive_hours", 0)
        if consecutive < SPOT_MIN_HOURS:
            continue

        # 成交量过滤
        vol_24h = get_24h_volume(symbol)
        if vol_24h < SPOT_VOL_FILTER:
            continue

        entry_price = get_current_price(symbol)
        if entry_price <= 0:
            continue

        signal_detail = {
            "连续小时": consecutive,
            "24h成交额(亿)": round(vol_24h / 1e8, 2),
        }
        open_spot_position(symbol, entry_price, signal_detail)


# ========== CC爆发策略 ==========

def get_cc_signals() -> list:
    data = api_get("/api/cc_signals")
    return data.get("data", [])


def get_cc_anchor(symbol: str):
    for a in cc_anchors:
        if a["symbol"] == symbol:
            return a
    return None


def get_cc_anchor_count() -> int:
    return len(cc_anchors)


def open_cc_anchor(symbol: str, entry_price: float, signal_detail: dict = None):
    """开CC合约前置锚（无杠杆，等效现货锚）"""
    global cc_anchors

    if get_cc_anchor(symbol):
        return False
    if get_cc_anchor_count() >= CC_ANCHOR_MAX:
        return False

    margin = CC_ANCHOR_MARGIN
    quantity = margin / entry_price  # 无杠杆，保证金=仓位价值

    tp_price = entry_price * (1 + CC_ANCHOR_TP_PCT)
    sl_price = entry_price * (1 - CC_ANCHOR_SL_PCT)

    anchor = {
        "symbol": symbol,
        "entry_price": entry_price,
        "quantity": quantity,
        "margin": margin,
        "entry_time": time.time(),
        "tp_price": tp_price,
        "sl_price": sl_price,
        "strategy": "CC",
    }
    cc_anchors.append(anchor)
    cc_entry_ts[symbol] = time.time()
    save_state()

    print(f"[CC锚开仓] {symbol} @ {format_price(entry_price)} | 保证金: {margin} USDT | TP: {format_price(tp_price)} | SL: {format_price(sl_price)}")
    return True


def close_cc_anchor(anchor: dict, reason: str, close_price: float):
    """平CC锚"""
    global cc_anchors
    pnl = (close_price - anchor["entry_price"]) * anchor["quantity"]
    spot_account["balance"] += pnl
    spot_account["total_pnl"] += pnl
    spot_account["total_trades"] += 1
    if pnl > 0:
        spot_account["win_trades"] += 1
    else:
        spot_account["loss_trades"] += 1
    if spot_account["total_trades"] > 0:
        spot_account["win_rate"] = spot_account["win_trades"] / spot_account["total_trades"] * 100

    cc_anchors.remove(anchor)
    cc_entry_ts.pop(anchor["symbol"], None)
    save_state()
    print(f"[CC锚平仓] {reason} {anchor['symbol']} @ {format_price(close_price)} | PnL: {pnl:+.2f} USDT")


def evaluate_cc_signals():
    """评估CC爆发信号并开合约前置锚"""
    signals = get_cc_signals()

    for sig in signals:
        symbol = sig.get("symbol", "")

        if symbol in EXCLUDE_SYMBOLS:
            continue
        if get_spot_position(symbol) or get_cc_anchor(symbol):
            continue
        if get_cc_anchor_count() >= CC_ANCHOR_MAX:
            break

        entry_price = get_current_price(symbol)
        if entry_price <= 0:
            continue

        # CC不过日涨幅过滤（CC抓的就是暴涨）
        open_cc_anchor(symbol, entry_price, {
            "today_gain": sig.get("today_gain_pct", 0),
            "consecutive": sig.get("consecutive_days", 0),
        })


def check_cc_anchors():
    """检查CC锚止盈止损"""
    for anchor in list(cc_anchors):
        symbol = anchor["symbol"]
        price = get_current_price(symbol)
        if price <= 0:
            continue

        if price >= anchor["tp_price"]:
            close_cc_anchor(anchor, "TAKE_PROFIT", price)
        elif price <= anchor["sl_price"]:
            close_cc_anchor(anchor, "STOP_LOSS", price)


def has_cc_or_spot(symbol: str) -> bool:
    """检查币种是否有BB现货或CC锚"""
    return get_spot_position(symbol) is not None or get_cc_anchor(symbol) is not None


# ========== 合约策略 ==========

def get_vol_surge_signals() -> list:
    data = api_get("/api/vol_surge")
    signals = data.get("data", [])
    now = time.time()
    return [s for s in signals if s.get("ratio", 0) >= FUT_MIN_RATIO and now - s.get("start_time", 0) < 300]

def get_futures_position(symbol: str):
    """查找合约持仓（支持现货symbol名自动映射）"""
    fut_symbol = spot_to_futures_symbol(symbol)
    for p in futures_positions:
        if p["symbol"] == fut_symbol or p["symbol"] == symbol:
            return p
    return None

def get_futures_positions_count():
    return len(futures_positions)

def get_futures_available_balance():
    used = sum(p["margin"] for p in futures_positions)
    return futures_account["balance"] - used

def open_futures_position(symbol: str, signal_type: str, entry_price: float,
                         signal_detail: dict = None):
    """开合约仓位（symbol为现货名，内部自动映射为合约名）"""
    global futures_positions, futures_account

    if symbol in EXCLUDE_SYMBOLS:
        return False

    if get_futures_position(symbol):
        return False

    if is_in_cooldown(symbol):
        return False

    if get_futures_positions_count() >= FUT_MAX_POSITIONS:
        return False

    # 风险层：BTC ETF昨日流出 → 禁止VS
    if not check_etf_flow():
        print(f"[合约跳过] {symbol} BTC ETF昨日净流出 ${abs(ETF_BTC_FLOW):.0f}M，暂停VS交易")
        return False

    # 验证层：资金费率过滤
    rate_ok, rate_reason = check_funding_filter(symbol)
    if not rate_ok:
        print(f"[合约跳过] {symbol} {rate_reason}")
        return False

    available = get_futures_available_balance()
    if available < FUT_MARGIN:
        return False

    # 【V7核心约束】必须在BB现货或CC锚持仓中
    has_spot = get_spot_position(symbol) is not None
    has_cc = get_cc_anchor(symbol) is not None
    if not has_spot and not has_cc:
        return False

    # 时序检查：开仓时间必须在锚之后
    anchor_ts = max(spot_entry_ts.get(symbol, 0), cc_entry_ts.get(symbol, 0))
    if anchor_ts == 0:
        return False

    # 日止盈过滤
    if not check_daily_tp_filter(symbol):
        return False

    # 涨幅过滤
    passed, _ = check_daily_gain_filter(symbol, SPOT_GAIN_FILTER_PCT)
    if not passed:
        return False

    # 量surge过滤: 检查最近3根1h K线是否有2根收阴
    if check_recent_1h_bearish(symbol):
        return False

    # 映射为合约symbol名
    fut_symbol = spot_to_futures_symbol(symbol)

    margin = FUT_MARGIN
    leverage = FUT_LEVERAGE
    position_value = margin * leverage
    quantity = position_value / entry_price

    # 止盈价
    tp_price = entry_price * (1 + FUT_TP_PCT / 100 / leverage)
    # 止损价: 开仓价 * (1 - 2%)
    sl_price = entry_price * (1 - FUT_SL_PCT)

    pos = {
        "symbol": fut_symbol,
        "spot_symbol": symbol,
        "signal_detail": signal_detail or {},
        "entry_price": entry_price,
        "quantity": quantity,
        "margin": margin,
        "leverage": leverage,
        "position_value": position_value,
        "entry_time": time.time(),
        "tp_price": tp_price,
        "sl_price": sl_price,
        "is_long": True,
    }
    
    futures_positions.append(pos)
    save_state()
    
    log = {
        "time": datetime.now().isoformat(),
        "action": "OPEN",
        "symbol": symbol,
        "signal_type": signal_type,
        "entry_price": entry_price,
        "quantity": quantity,
        "margin": margin,
        "leverage": leverage,
        "tp_price": tp_price,
        "sl_price": sl_price,
        "detail": signal_detail or {},
    }
    futures_trade_log.append(log)
    signal_log.append(log)
    
    print(f"[合约开仓] {symbol} @ {format_price(entry_price)} | {signal_type} | 保证金: {margin} USDT | 杠杆: {leverage}x | TP: {format_price(tp_price)} | SL: {format_price(sl_price)}")
    return True

def close_futures_position(pos: dict, reason: str, close_price: float, pnl: float):
    """平合约仓位"""
    global futures_positions, futures_account
    
    margin = pos["margin"]
    leverage = pos["leverage"]
    position_value = pos["position_value"]
    
    # 手续费 0.04% × 2
    total_fee = position_value * 0.0004 * 2
    actual_pnl = pnl - total_fee
    
    futures_account["balance"] += actual_pnl
    futures_account["total_pnl"] += actual_pnl
    futures_account["total_trades"] += 1
    
    if actual_pnl > 0:
        futures_account["win_trades"] += 1
    else:
        futures_account["loss_trades"] += 1
    
    if futures_account["total_trades"] > 0:
        futures_account["win_rate"] = futures_account["win_trades"] / futures_account["total_trades"] * 100
    
    if futures_account["balance"] > futures_account["peak_balance"]:
        futures_account["peak_balance"] = futures_account["balance"]
    drawdown = (futures_account["peak_balance"] - futures_account["balance"]) / futures_account["peak_balance"] * 100
    if drawdown > futures_account["max_drawdown"]:
        futures_account["max_drawdown"] = drawdown
    
    log = {
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
    }
    futures_trade_log.append(log)
    
    print(f"[合约平仓] {reason} {pos['symbol']} @ {format_price(close_price)} | 盈亏: {actual_pnl:+.2f} USDT | 余额: {futures_account['balance']:.2f} | 胜率: {futures_account['win_rate']:.0f}%")
    
    futures_positions.remove(pos)
    save_state()
    
    if reason == "TAKE_PROFIT":
        record_take_profit(pos["symbol"])
    
    if reason == "STOP_LOSS":
        set_cooldown(pos["symbol"], 30)

def check_futures_positions():
    """检查合约持仓的TP/SL"""
    positions_to_close = []
    total_unrealized = 0

    for pos in futures_positions:
        # 用现货symbol查价格（spot API），合约symbol用于记录
        spot_sym = pos.get("spot_symbol", pos["symbol"])
        current_price = get_current_price(spot_sym)
        
        if current_price <= 0:
            continue
        
        entry_price = pos["entry_price"]
        tp_price = pos["tp_price"]
        sl_price = pos["sl_price"]
        quantity = pos["quantity"]
        
        pnl = calculate_pnl(entry_price, current_price, quantity)
        total_unrealized += pnl
        
        if current_price >= tp_price:
            positions_to_close.append((pos, "TAKE_PROFIT", current_price, pnl))
            continue
        
        if current_price <= sl_price:
            positions_to_close.append((pos, "STOP_LOSS", current_price, pnl))
            continue
    
    # 联合爆仓检测
    total_equity = futures_account["balance"] + total_unrealized
    if total_equity <= 0 and futures_positions:
        print(f"\n💥 合约联合爆仓！总权益归零: {total_equity:.2f} USDT")
        for pos in futures_positions:
            symbol = pos["symbol"]
            spot_sym = fut_pos_price_symbol(pos)
            current_price = get_current_price(spot_sym)
            if current_price <= 0:
                current_price = pos["entry_price"] * 0.5
            pnl = calculate_pnl(pos["entry_price"], current_price, pos["quantity"])
            positions_to_close.append((pos, "LIQUIDATED_CROSS", current_price, pnl))
    
    closed_symbols = set()
    for pos, reason, price, pnl in positions_to_close:
        if pos["symbol"] in closed_symbols:
            continue
        close_futures_position(pos, reason, price, pnl)
        closed_symbols.add(pos["symbol"])

def check_recent_1h_bearish(symbol: str) -> bool:
    """检查最近3根1h K线中是否有至少2根收阴"""
    try:
        resp = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": symbol, "interval": "1h", "limit": 3},
            timeout=5
        )
        if resp.status_code != 200:
            return False
        klines = resp.json()
        if len(klines) < 2:
            return False
        bearish_count = 0
        for k in klines:
            if float(k[4]) < float(k[1]):
                bearish_count += 1
        return bearish_count >= 2
    except:
        return False

def evaluate_futures_signals():
    """评估VS信号并开合约仓位"""
    available = get_futures_available_balance()
    if available < FUT_MARGIN:
        return

    signals = get_vol_surge_signals()

    # 按24h成交量降序
    signals_with_vol = []
    has_high_ratio = False  # 是否存在 ratio >= 5.0 的信号（触发满仓替换）
    for sig in signals:
        symbol = sig.get("symbol", "")
        if symbol in EXCLUDE_SYMBOLS or get_futures_position(symbol):
            continue
        if symbol in exhausted_symbols:
            continue
        ratio = sig.get("ratio", 0)
        if ratio < FUT_MIN_RATIO:
            continue
        vol_24h = get_24h_volume(symbol)
        if vol_24h < FUT_VOL_FILTER:
            continue
        sig["_vol_24h"] = vol_24h
        signals_with_vol.append(sig)
        if ratio >= 5.0:
            has_high_ratio = True

    signals_with_vol.sort(key=lambda s: s.get("_vol_24h", 0), reverse=True)

    # 满仓替换：当满仓且有 ratio >= 5.0 的高倍信号时，替换最弱的非VS仓位
    if get_futures_positions_count() >= 5 and has_high_ratio:
        # 找出最弱的非VS仓位（unrealized pnl 最高的，即"最不亏"）
        non_vs_positions = []
        for i, pos in enumerate(futures_positions):
            if not pos.get("signal_type", "").startswith("VOL_SURGE_"):
                entry_price = pos["entry_price"]
                spot_sym = fut_pos_price_symbol(pos)
                current_price = get_current_price(spot_sym)
                if current_price <= 0:
                    continue
                pnl = (current_price - entry_price) * pos["quantity"]
                non_vs_positions.append((i, pnl, pos))

        if non_vs_positions:
            non_vs_positions.sort(key=lambda x: x[1], reverse=True)
            replace_idx, _, replace_pos = non_vs_positions[0]
            # 找最高 ratio 的 VS 信号
            high_sigs = [s for s in signals_with_vol if s.get("ratio", 0) >= 5.0]
            if high_sigs:
                best = max(high_sigs, key=lambda s: s.get("ratio", 0))
                old_symbol = replace_pos["symbol"]
                entry_price = close_futures_position_forced(replace_pos, "REPLACE_VOL_SURGE")
                print(f"[满仓替换] {old_symbol} → {best['symbol']} (ratio={best['ratio']:.1f}x)")
                # 移除被替换的，为新信号腾空间
                signals_with_vol = [s for s in signals_with_vol if s["symbol"] != best["symbol"]]
                signals_with_vol.insert(0, best)

    for sig in signals_with_vol:
        if get_futures_positions_count() >= FUT_MAX_POSITIONS:
            break
        if get_futures_available_balance() < FUT_MARGIN:
            break

        symbol = sig.get("symbol", "")
        ratio = sig.get("ratio", 0)
        vol_24h = sig.get("_vol_24h", 0)

        # VS信号必须在现货入场之后（与回测一致）
        if symbol in spot_entry_ts:
            sig_start = sig.get("start_time", 0)
            if sig_start <= spot_entry_ts[symbol]:
                continue
        else:
            continue

        entry_price = get_current_price(symbol)
        if entry_price <= 0:
            continue

        signal_type = f"VOL_SURGE_{ratio:.1f}x"
        signal_detail = {"突增倍数": round(ratio, 2), "24h成交额(亿)": round(vol_24h / 1e8, 2)}
        open_futures_position(symbol, signal_type, entry_price, signal_detail)

# ========== 状态保存与加载 ==========

SIM_TRADE_STATE_FILE = "/tmp/sim_trade_state.json"

def save_state():
    state = {
        "spot_account": spot_account,
        "spot_positions": spot_positions,
        "futures_account": futures_account,
        "futures_positions": futures_positions,
        "spot_entry_ts": spot_entry_ts,
        "daily_take_profit_count": {f"{k[0]}#{k[1]}": v for k, v in daily_take_profit_count.items()},
        "cooldown_symbols": cooldown_symbols,
        "tp_per_symbol": tp_per_symbol,
        "exhausted_symbols": list(exhausted_symbols),
        "cc_anchors": cc_anchors,
        "cc_entry_ts": cc_entry_ts,
        "spot_trade_log": spot_trade_log[-50:],
        "futures_trade_log": futures_trade_log[-50:],
        "saved_at": datetime.now().isoformat(),
    }
    import tempfile
    import fcntl
    tmp_path = "/tmp/sim_trade_state.json.tmp"
    with open(tmp_path, "w") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        json.dump(state, f, indent=2, default=str)
        f.flush()
        os.fsync(f.fileno())
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    os.replace(tmp_path, SIM_TRADE_STATE_FILE)

def load_state():
    global spot_account, spot_positions, futures_account, futures_positions
    global spot_entry_ts, daily_take_profit_count, cooldown_symbols
    global tp_per_symbol, exhausted_symbols, cc_anchors, cc_entry_ts
    
    if not os.path.exists(SIM_TRADE_STATE_FILE):
        return
    
    try:
        with open(SIM_TRADE_STATE_FILE, "r") as f:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            state = json.load(f)
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        
        spot_account.update(state.get("spot_account", {}))
        spot_positions[:] = state.get("spot_positions", [])
        futures_account.update(state.get("futures_account", {}))
        futures_positions[:] = state.get("futures_positions", [])
        spot_entry_ts.update(state.get("spot_entry_ts", {}))
        
        for k, v in state.get("daily_take_profit_count", {}).items():
            parts = k.split("#")
            if len(parts) == 2:
                daily_take_profit_count[(parts[0], parts[1])] = v
        
        cooldown_symbols.update(state.get("cooldown_symbols", {}))
        tp_per_symbol.update(state.get("tp_per_symbol", {}))
        exhausted_symbols.update(state.get("exhausted_symbols", []))
        if exhausted_symbols:
            print(f"[状态恢复] 已耗尽币种: {len(exhausted_symbols)} 个 - {','.join(list(exhausted_symbols)[:5])}")

        cc_anchors[:] = state.get("cc_anchors", [])
        cc_entry_ts.update(state.get("cc_entry_ts", {}))

        print(f"[状态恢复] 现货持仓: {len(spot_positions)} | CC锚: {len(cc_anchors)} | 合约持仓: {len(futures_positions)}")
    except Exception as e:
        print(f"[状态恢复失败] {e}")

# ========== 状态打印 ==========

def print_status():
    # 现货
    spot_unrealized = 0
    for pos in spot_positions:
        current = get_current_price(pos["symbol"])
        if current > 0:
            spot_unrealized += calculate_pnl(pos["entry_price"], current, pos["quantity"])
    spot_equity = spot_account["balance"] + spot_unrealized
    
    # 合约
    fut_unrealized = 0
    for pos in futures_positions:
        spot_sym = fut_pos_price_symbol(pos)
        current = get_current_price(spot_sym)
        if current > 0:
            fut_unrealized += calculate_pnl(pos["entry_price"], current, pos["quantity"])
    fut_equity = futures_account["balance"] + fut_unrealized
    
    print(f"\n{'='*70}")
    print(f"V7 混合策略 @ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}")
    max_pos = get_risk_adjusted_max_positions()
    fg_str = f" F&G={FEAR_GREED_VALUE}" if FEAR_GREED_VALUE > 70 or FEAR_GREED_VALUE < 35 else ""
    print(f"【BB现货】权益: {spot_equity:.2f} USDT | 余额: {spot_account['balance']:.2f} | 未实现: {spot_unrealized:+.2f} | 持仓: {len(spot_positions)}/{max_pos}{fg_str}")
    if cc_anchors:
        cc_unrealized = sum((get_current_price(a['symbol']) - a['entry_price']) * a['quantity'] for a in cc_anchors)
        print(f"【CC锚】数量: {len(cc_anchors)} | 未实现: {cc_unrealized:+.2f} USDT")
    print(f"【合约VS】权益: {fut_equity:.2f} USDT | 余额: {futures_account['balance']:.2f} | 未实现: {fut_unrealized:+.2f} | 持仓: {len(futures_positions)}/{FUT_MAX_POSITIONS} | 胜率: {futures_account['win_rate']:.0f}%")
    print(f"【综合】权益: {spot_equity + fut_equity:.2f} USDT | 回撤: 现货{spot_account['max_drawdown']:.1f}% 合约{futures_account['max_drawdown']:.1f}%")
    
    if spot_positions:
        print(f"\n现货持仓:")
        for pos in spot_positions:
            current = get_current_price(pos["symbol"])
            pnl_pct = ((current - pos["entry_price"]) / pos["entry_price"] * 100) if current > 0 else 0
            status = "✓止盈" if current >= pos["tp_price"] else ("✗止损" if current <= pos["sl_price"] else "持仓中")
            print(f"  {pos['symbol']:<14} 入:{format_price(pos['entry_price']):<12} 现:{format_price(current):<12} {pnl_pct:>+7.2f}% {status}")
    
    if cc_anchors:
        print(f"\nCC锚持仓:")
        for a in cc_anchors:
            current = get_current_price(a["symbol"])
            pnl_pct = ((current - a["entry_price"]) / a["entry_price"] * 100) if current > 0 else 0
            print(f"  {a['symbol']:<14} 入:{format_price(a['entry_price']):<12} 现:{format_price(current):<12} {pnl_pct:>+7.2f}%")

    if futures_positions:
        print(f"\n合约持仓:")
        for pos in futures_positions:
            spot_sym = fut_pos_price_symbol(pos)
            current = get_current_price(spot_sym)
            pnl_pct = ((current - pos["entry_price"]) / pos["entry_price"] * 100) if current > 0 else 0
            status = "✓止盈" if current >= pos["tp_price"] else ("✗止损" if current <= pos["sl_price"] else "持仓中")
            print(f"  {pos['symbol']:<14} 入:{format_price(pos['entry_price']):<12} 现:{format_price(current):<12} {pnl_pct:>+7.2f}% {status} | {pos['signal_type']}")
    
    print("=" * 70)

# ========== 主循环 ==========

def main():
    load_state()
    
    print("=" * 70)
    print("V7 混合策略实盘模拟系统")
    print(f"现货: 初始{SPOT_INITIAL_CAPITAL} USDT | 每单{SPOT_PER_TRADE} USDT | 最多{SPOT_MAX_POSITIONS}个")
    print(f"合约: 初始{FUT_INITIAL_CAPITAL} USDT | 每单{FUT_MARGIN} USDT | 杠杆{FUT_LEVERAGE}x")
    print(f"核心约束: 合约只在有现货持仓的币种上开仓")
    print("=" * 70)
    
    last_status_time = 0
    last_signal_check = 0
    
    while True:
        try:
            now = time.time()
            
            # 全部现货耗尽 → 全平现货
            if spot_positions and exhausted_symbols and all(
                p["symbol"] in exhausted_symbols for p in spot_positions
            ):
                print("[全部耗尽] 所有现货持仓均已耗尽，全平现货")
                for pos in list(spot_positions):
                    price = get_current_price(pos["symbol"])
                    if price <= 0:
                        price = pos["entry_price"]
                    pnl = (price - pos["entry_price"]) * pos["quantity"]
                    close_spot_position(pos, "ALL_EXHAUSTED", price, pnl)

            # 检查并平仓
            if spot_positions:
                check_spot_positions()
            if cc_anchors:
                check_cc_anchors()
            if futures_positions:
                check_futures_positions()
            
            # 每10秒评估信号
            if now - last_signal_check >= 10:
                last_signal_check = now
                evaluate_spot_signals()
                evaluate_cc_signals()
                evaluate_futures_signals()
            
            # 每60秒：刷新验证层数据 + 打印状态
            if now - last_status_time >= 60:
                fetch_funding_rates()
                fetch_fear_greed()
                last_status_time = now
                print_status()
                save_state()
            
            time.sleep(1)
            
        except KeyboardInterrupt:
            print("\n退出...")
            save_state()
            break
        except Exception as e:
            print(f"[错误] {e}")
            import traceback
            traceback.print_exc()
            time.sleep(5)

if __name__ == "__main__":
    main()
