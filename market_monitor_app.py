#!/usr/bin/env python3
"""
币安全量行情监控 - 独立Flask服务
端口: 5000
功能: REST API拉全量 + WebSocket实时更新 + 分钟K线聚合
新增: 主动买卖估算(delta_q + buy_ratio + 买卖成交额)
"""
import os
import io
import json
from dotenv import load_dotenv

# 加载 .env 文件（与5002端口共享COS凭证）
load_dotenv('/home/ubuntu/crypto-scanner/.env')
import time
import threading
from datetime import datetime, timedelta, timezone
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO
import requests
import pandas as pd

# ========== 配置 ==========
PORT = 5000
BINANCE_API = "https://fapi.binance.com"
HYPERLIQUID_API = "https://api.hyperliquid.xyz/info"
SNAPSHOT_FILE = "/var/www/market_snapshot.json"
WRITE_INTERVAL_SECONDS = 900  # 15分钟写一次快照
USE_HYPERLIQUID = False  # 是否使用 Hyperliquid 数据源（暂时禁用，另有用途）

# COS 配置（独立于5002端口）
COS_KEY = "klines/minute_klines.parquet"  # 分钟K线（完全独立）
COS_HOURLY_KEY = "klines/hourly_klines_5000.parquet"  # 1h K线缓存（独立于5002）
COS_REGION = os.environ.get('COS_REGION', 'ap-seoul')
COS_ENDPOINT = os.environ.get('COS_ENDPOINT', 'cos.ap-seoul.myqcloud.com')
COS_SECRET_ID = os.environ.get('COS_SECRET_ID', '')
COS_SECRET_KEY = os.environ.get('COS_SECRET_KEY', '')
COS_BUCKET = os.environ.get('COS_BUCKET', '')

# 本地文件备份（当COS不可用时使用）
LOCAL_KLINE_FILE = "/tmp/market_minute_klines.parquet"

# K线配置
MAX_MINUTE_KLINES = 6 * 24 * 60  # 6天 = 8640分钟

# delta_q 突增检测配置
DELTA_Q_SURGE_THRESHOLD = 500_000  # 50万USDT以上的delta_q视为突增
SURGE_CACHE_MAX_MINUTES = 15  # 突增记录保留最近15分钟
SURGE_EXCLUDE_SYMBOLS = {'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XAUUSDT', 'XAGUSDT'}  # 排除的币种

# ========== 布林爬坡检测配置 ==========
BB_CLIMB_CONFIG = {
    "period": 20,                    # 布林周期
    "std_mult": 2,                   # 标准差倍数
    "upper_tolerance_pct": 0.08,    # 收盘价在上轨±8%范围内
    "buy_ratio_threshold": 0.55,    # buy_ratio阈值（仅对真实数据检查）
    "buy_ratio_skip_default": True,
    "volume_ratio": 1.2,
    "hl_tolerance_window": 3,
    "hl_tolerance_min": 2,
    "atr_period": 14,
    "atr_enabled": True,
    "exclude_symbols": {
        'BTCUSDT', 'ETHUSDT', 'SOLUSDT',
        'TSLAUSDT', 'NVDAUSDT', 'AMZNUSDT', 'GOOGLUSDT', 'AAPLUSDT',
        'COINUSDT', 'MSTRUSDT', 'METAUSDT', 'TSMUSDT',
        'XAUUSDT', 'XAGUSDT', 'XAUTUSDT', 'NATGASUSDT',
    },
    "candidate_enabled": True,
    "candidate_near_hours": 2,
    "candidate_vol_ratio": 0.5,
}

# ========== 布林爬坡缓存 ==========
_bb_climb_cache = {
    "results": [],
    "candidates": [],
    "updated_at": 0,
}
_bb_climb_lock = threading.Lock()

# ========== 全局数据 ==========
market_data = {
    "updated_at": None,
    "symbols": {},                    # {symbol: {q, price, o, h, l, v, updated_at}}
    "last_q": {},                     # {symbol: 上一次q值}
    "second_deltas": {},               # {秒时间戳: {symbol: delta_q}}
    "minute_klines": {},               # {symbol: [kline1, kline2, ...]}
    "current_minute": None,            # 当前分钟开始时间戳
    "minute_state": {},                # {symbol: {open, high, low, close, vol, q, first_price, buy_q, sell_q, last_price}}
    "surge_cache": {},                 # {symbol: [{t, delta_q, buy_ratio, price}, ...]}
    "hourly_kline_cache": {},          # {symbol: [hourly_kline, ...]} 从API拉取+WebSocket聚合的1h K线
    "bb_backfill_done": False,           # API回填是否完成
    "today_open_prices": {},            # {symbol: 北京0点开盘价}
    "today_open_updated": 0,             # 上次更新时间
    # 15分钟成交量统计
    "vol_15m_current": {},              # {symbol: current_15m_volume} 当前15分钟累计
    "vol_15m_last": {},                 # {symbol: last_15m_volume} 上一个15分钟成交额
    "vol_15m_slot": None,               # 当前15分钟区间时间戳
    "vol_15m_history": {},              # {symbol: {slot_ts: vol}} 最近16个15分钟成交额历史(4小时)，从COS加载
    "vol_15m_avg_4h": {},               # {symbol: avg_vol} 前4小时的15分钟均值
    "vol_24h_today": {},                # {symbol: today_volume} 今日累计成交额（北京时间8点起）
    "vol_surge_symbols": {},            # {symbol: {start_time, ratio, vol}} 突增币种记录
}
data_lock = threading.Lock()

# ========== Flask ==========
app = Flask(__name__)
app.config["SECRET_KEY"] = "market-monitor-2024"
app.template_folder = os.path.join(os.path.dirname(__file__), "templates")
socketio = SocketIO(app, cors_allowed_origins="*", ping_timeout=60, ping_interval=25)


# ========== 全局 HTTP Session ==========
_requests_session = requests.Session()

# ========== COS 客户端 ==========
_cos_client = None

def get_cos_client():
    global _cos_client
    # 凭证变化时重建客户端
    current_sid = os.environ.get('COS_SECRET_ID', '')
    if _cos_client is None or (COS_SECRET_ID != current_sid and current_sid):
        from qcloud_cos import CosConfig, CosS3Client
        # 优先用环境变量（dotenv加载后可能更新）
        sid = current_sid or COS_SECRET_ID
        skey = os.environ.get('COS_SECRET_KEY', '') or COS_SECRET_KEY
        region = os.environ.get('COS_REGION', COS_REGION)
        endpoint = os.environ.get('COS_ENDPOINT', COS_ENDPOINT)
        if not sid or not skey:
            return None
        cos_config = CosConfig(Region=region, SecretId=sid, SecretKey=skey, Endpoint=endpoint)
        _cos_client = CosS3Client(cos_config)
    return _cos_client

# ========== 工具函数 ==========
def fetch_24h_snapshot() -> list:
    """REST API拉取全量24h数据"""
    try:
        resp = _requests_session.get(f"{BINANCE_API}/fapi/v1/ticker/24hr", timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"REST API拉取失败: {e}")
        return []

def fetch_hyperliquid_all_mids() -> dict:
    """从 Hyperliquid 获取所有币种价格"""
    try:
        resp = _requests_session.post(HYPERLIQUID_API, json={"type": "allMids"}, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"Hyperliquid allMids 获取失败: {e}")
        return {}

def fetch_hyperliquid_klines(coin: str, interval: str = "1h", start_time: int = None) -> list:
    """从 Hyperliquid 获取 K线数据"""
    try:
        req = {"coin": coin, "interval": interval}
        if start_time:
            req["startTime"] = start_time
        resp = _requests_session.post(HYPERLIQUID_API, json={"type": "candleSnapshot", "req": req}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"Hyperliquid K线获取失败 ({coin}): {e}")
        return []

def fetch_hyperliquid_meta() -> dict:
    """从 Hyperliquid 获取元数据（交易对列表）"""
    try:
        resp = _requests_session.post(HYPERLIQUID_API, json={"type": "meta"}, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"Hyperliquid meta 获取失败: {e}")
        return {}

def init_market_data_from_hyperliquid():
    """完全使用 Hyperliquid 数据初始化（北京时间8点起算24h数据）"""
    global market_data
    print("正在从 Hyperliquid 拉取全量数据...")
    
    # 1. 获取所有币种当前价格
    mids = fetch_hyperliquid_all_mids()
    if not mids:
        print("Hyperliquid 价格获取失败，回退到 Binance")
        init_market_data()
        return
    
    print(f"Hyperliquid 获取到 {len(mids)} 个币种价格")
    
    # 2. 获取交易对列表
    meta = fetch_hyperliquid_meta()
    trading_coins = set()
    if meta:
        universe = meta.get("universe", [])
        trading_coins = {asset["name"] for asset in universe}
        print(f"Hyperliquid 交易对: {len(trading_coins)} 个")
    
    # 3. 计算北京时间8点的时间戳（UTC 0点）
    now_utc = datetime.now(tz=timezone.utc)
    today_utc0 = datetime(now_utc.year, now_utc.month, now_utc.day, tzinfo=timezone.utc)
    today_start_ms = int(today_utc0.timestamp() * 1000)
    
    # 4. 批量获取K线数据（用于计算开盘价和成交额）
    print("正在获取K线数据...")
    new_symbols = {}
    today_open_prices = {}
    
    # 顺序获取K线数据，避免触发429限流
    # 不再限制数量，改为分批处理
    coins_to_fetch = [c for c in mids.keys() if c in trading_coins]
    total_coins = len(coins_to_fetch)
    
    for i, coin in enumerate(coins_to_fetch):
        time.sleep(0.5)  # 每个请求间隔500ms，减少429错误
        
        klines = fetch_hyperliquid_klines(coin, "1h", start_time=today_start_ms)
        
        if i > 0 and i % 20 == 0:
            print(f"  已获取 {i}/{total_coins} 个币种K线...")
        
        if not klines:
            continue
        
        symbol = f"{coin}USDT"
        current_price = float(mids.get(coin, 0))
        
        if current_price <= 0:
            continue
        
        # 计算开盘价（第一根K线的开盘价）
        open_price = float(klines[-1].get("o", 0)) if klines else current_price
        
        # 计算成交额（累加所有K线的 volume * close）
        total_volume = 0
        total_quote_volume = 0
        high_price = 0
        low_price = float('inf')
        
        for kl in klines:
            vol = float(kl.get("v", 0))
            close = float(kl.get("c", 0))
            h = float(kl.get("h", 0))
            l = float(kl.get("l", 0))
            total_volume += vol
            total_quote_volume += vol * close  # 近似成交额
            high_price = max(high_price, h)
            low_price = min(low_price, l) if l > 0 else low_price
        
        if low_price == float('inf'):
            low_price = current_price
        
        # 涨跌幅
        gain_pct = (current_price - open_price) / open_price * 100 if open_price > 0 else 0
        
        new_symbols[symbol] = {
            "q": total_quote_volume,  # 成交额
            "v": total_volume,         # 成交量
            "price": current_price,
            "o": open_price,           # 今日开盘价
            "h": high_price,
            "l": low_price,
            "priceChangePercent": gain_pct,
            "updated_at": time.time()
        }
        today_open_prices[symbol] = open_price
    
    with data_lock:
        market_data["symbols"] = new_symbols
        market_data["trading_symbols"] = {f"{c}USDT" for c in trading_coins}
        market_data["today_open_prices"] = today_open_prices
        market_data["today_open_updated"] = time.time()
        market_data["updated_at"] = time.time()
        market_data["current_minute"] = get_current_minute_ts()
    
    print(f"Hyperliquid 初始化完成: {len(new_symbols)} 个币种")
    print(f"今日开盘价已设置: {len(today_open_prices)} 个币")

def update_prices_from_hyperliquid():
    """用 Hyperliquid 数据更新实时价格"""
    if not USE_HYPERLIQUID:
        return
    
    mids = fetch_hyperliquid_all_mids()
    if not mids:
        return
    
    updated = 0
    with data_lock:
        symbols = market_data.get("symbols", {})
        for coin, price in mids.items():
            symbol = f"{coin}USDT"
            if symbol in symbols:
                old_price = symbols[symbol].get("price", 0)
                new_price = float(price)
                symbols[symbol]["price"] = new_price
                symbols[symbol]["updated_at"] = time.time()
                # 更新涨跌幅
                open_price = symbols[symbol].get("o", 0)
                if open_price > 0:
                    symbols[symbol]["priceChangePercent"] = (new_price - open_price) / open_price * 100
                updated += 1
        market_data["symbols"] = symbols
    
    print(f"Hyperliquid 价格更新: {updated} 个币种")

def init_market_data():
    """启动时初始化全量数据 - 币安API加载全部币种 + COS数据补充北京8点开盘价"""
    global market_data
    
    # 优先使用 Hyperliquid 数据源
    if USE_HYPERLIQUID:
        init_market_data_from_hyperliquid()
        return
    
    # ========== 优先从COS加载全量快照 ==========
    cos_symbols = load_symbols_snapshot_from_cos()
    cos_vol_24h = load_vol_24h_today_from_cos()
    cos_opens = load_today_open_prices_from_cos()
    
    if cos_symbols:
        print(f"[INIT] 从COS加载了 {len(cos_symbols)} 个币种的快照，优先使用COS数据")
        with data_lock:
            market_data["symbols"] = cos_symbols
            if cos_vol_24h:
                market_data["vol_24h_today"] = cos_vol_24h
            if cos_opens:
                market_data["today_open_prices"] = cos_opens
            market_data["updated_at"] = time.time()
            market_data["current_minute"] = get_current_minute_ts()
            market_data["last_q"] = {s: info["q"] for s, info in cos_symbols.items() if info.get("q", 0) > 0}
        
        # 如果COS中没有今日开盘价，立即从当前价格捕获（避免涨跌幅计算错误）
        if not cos_opens:
            print("[INIT] COS中无今日开盘价，立即从当前价格捕获...")
            capture_today_open_from_ws()
            save_today_open_prices_to_cos()
        
        # 即使从COS加载了，仍需要获取trading_symbols白名单
        trading_symbols = set()
        try:
            resp = _requests_session.get(f"{BINANCE_API}/fapi/v1/exchangeInfo", timeout=30)
            resp.raise_for_status()
            for s in resp.json().get("symbols", []):
                if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING":
                    trading_symbols.add(s["symbol"])
            with data_lock:
                market_data["trading_symbols"] = trading_symbols
            print(f"从exchangeInfo获取 {len(trading_symbols)} 个TRADING币种")
        except Exception as e:
            print(f"exchangeInfo获取失败: {e}")
        
        print(f"[INIT] COS数据加载完成，共 {len(cos_symbols)} 个币种")
        
        # 加载分钟K线
        try:
            historical_klines = load_minute_klines_from_cos()
            with data_lock:
                if historical_klines:
                    market_data["minute_klines"] = historical_klines
                    print(f"从COS加载了 {len(historical_klines)} 个币种的分钟K线")
        except Exception as e:
            print(f"加载分钟K线失败: {e}")
        return
    
    # ========== COS无数据，回退到币安API加载 ==========
    print("正在从币安API加载全量数据...")
    
    # 从exchangeInfo获取TRADING状态的币种白名单
    trading_symbols = set()
    try:
        resp = _requests_session.get(f"{BINANCE_API}/fapi/v1/exchangeInfo", timeout=30)
        resp.raise_for_status()
        for s in resp.json().get("symbols", []):
            if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING":
                trading_symbols.add(s["symbol"])
        print(f"从exchangeInfo获取 {len(trading_symbols)} 个TRADING币种")
    except Exception as e:
        print(f"exchangeInfo获取失败: {e}")
    
    with data_lock:
        market_data["trading_symbols"] = trading_symbols
    
    # 1. 从币安API加载全部币种（500+个）
    api_symbols = _fetch_all_from_binance_api(trading_symbols)
    print(f"币安API加载: {len(api_symbols)} 个币种")
    
    # 2. 从COS加载已有数据（北京8点开盘价）
    hourly_cache = _load_hourly_cache_from_cos()
    today_open_prices = {}
    cos_symbols = set()
    
    # 计算北京8点时间戳
    now_utc = datetime.now(tz=timezone.utc)
    today_utc0 = datetime(now_utc.year, now_utc.month, now_utc.day, tzinfo=timezone.utc)
    beijing_8am_ts = int(today_utc0.timestamp())
    
    if hourly_cache:
        print(f"从COS加载了 {len(hourly_cache)} 个币种的历史数据")
        # 提取北京8点开盘价
        for symbol, hklines in hourly_cache.items():
            for h in hklines:
                if h['t'] == beijing_8am_ts:
                    today_open_prices[symbol] = h['o']
                    cos_symbols.add(symbol)
                    # 更新该币种的开盘价和涨跌幅
                    if symbol in api_symbols:
                        api_symbols[symbol]['o'] = h['o']
                        price = api_symbols[symbol]['price']
                        api_symbols[symbol]['priceChangePercent'] = (price - h['o']) / h['o'] * 100 if h['o'] > 0 else 0
                    break
        print(f"从COS更新了 {len(today_open_prices)} 个币种的北京8点开盘价")
    
    # 3. 为剩余币种（没有COS数据的）从币安API获取北京8点K线
    remaining_symbols = [s for s in api_symbols.keys() if s not in cos_symbols]
    print(f"需要为 {len(remaining_symbols)} 个币种获取北京8点开盘价...")
    
    # 批量获取北京8点K线（限制频率避免429）
    beijing_8am_opens = _fetch_beijing_8am_klines(remaining_symbols, beijing_8am_ts)
    
    for symbol, open_price in beijing_8am_opens.items():
        today_open_prices[symbol] = open_price
        if symbol in api_symbols:
            api_symbols[symbol]['o'] = open_price
            price = api_symbols[symbol]['price']
            api_symbols[symbol]['priceChangePercent'] = (price - open_price) / open_price * 100 if open_price > 0 else 0
    
    print(f"总计 {len(today_open_prices)} 个币种有北京8点开盘价")
    
    with data_lock:
        market_data["symbols"] = api_symbols
        market_data["today_open_prices"] = today_open_prices
        market_data["hourly_kline_cache"] = hourly_cache
        market_data["updated_at"] = time.time()
        market_data["current_minute"] = get_current_minute_ts()
        # 初始化 last_q，避免 WebSocket 第一条消息 delta_q=0
        market_data["last_q"] = {s: info["q"] for s, info in api_symbols.items()}
    
    print(f"总计加载: {len(api_symbols)} 个币种")
    
    # 首次启动后，立即把数据写入COS
    save_symbols_snapshot_to_cos()
    save_vol_24h_today_to_cos()
    save_today_open_prices_to_cos()
    
    # 加载分钟K线（用于15分钟统计）
    try:
        historical_klines = load_minute_klines_from_cos()
        with data_lock:
            if historical_klines:
                market_data["minute_klines"] = historical_klines
                print(f"从COS加载了 {len(historical_klines)} 个币种的分钟K线")
    except Exception as e:
        print(f"加载分钟K线失败: {e}")

def _fetch_all_from_binance_api(trading_symbols: set) -> dict:
    """从币安API获取全部币种数据（24h滚动数据）"""
    raw = fetch_24h_snapshot()
    
    new_symbols = {}
    for item in raw:
        symbol = item.get("symbol", "")
        if not symbol.endswith("USDT"):
            continue
        if trading_symbols and symbol not in trading_symbols:
            continue
        
        price_change_pct = float(item.get("priceChangePercent", 0))
        if price_change_pct == 0:
            continue
        
        q = float(item.get("quoteVolume", 0))
        price = float(item.get("lastPrice", 0))
        
        new_symbols[symbol] = {
            "q": q,
            "v": float(item.get("volume", 0)),
            "price": price,
            "o": float(item.get("openPrice", 0)),
            "h": float(item.get("highPrice", 0)),
            "l": float(item.get("lowPrice", 0)),
            "priceChangePercent": price_change_pct,
            "updated_at": time.time()
        }
    
    return new_symbols

def _fetch_beijing_8am_klines(symbols: list, beijing_8am_ts: int) -> dict:
    """批量获取币种的北京8点K线（1h周期）"""
    result = {}
    session = requests.Session()
    
    # 计算北京8点的时间戳对应的ms
    # 减1ms确保获取到正确的K线（避免边界时间戳问题）
    beijing_8am_ms = beijing_8am_ts * 1000 - 1
    
    for i, symbol in enumerate(symbols):
        try:
            # 获取北京8点的那根1h K线
            params = {
                "symbol": symbol,
                "interval": "1h",
                "startTime": beijing_8am_ms,
                "limit": 1
            }
            resp = _requests_session.get(f"{BINANCE_API}/fapi/v1/klines", params=params, timeout=5)
            
            if resp.status_code == 200:
                klines = resp.json()
                if klines and len(klines) > 0:
                    open_price = float(klines[0][1])  # 开盘价
                    result[symbol] = open_price
            elif resp.status_code == 429:
                # 限流了，等待更长时间
                retry_after = int(resp.headers.get("Retry-After", 60))
                print(f"[北京8点K线] {symbol} 触发429限流，等待{retry_after}秒")
                time.sleep(retry_after)
                continue
            elif resp.status_code == 418:
                # IP被熔断
                retry_after = int(resp.headers.get("Retry-After", 300))
                print(f"[北京8点K线] {symbol} 触发418熔断，等待{retry_after}秒")
                time.sleep(retry_after)
                continue
                
        except Exception as e:
            print(f"[北京8点K线] {symbol} 获取失败: {e}")
        
        # 每10个币种暂停一下，避免频率限制
        if (i + 1) % 10 == 0:
            print(f"[北京8点K线] 已处理 {i + 1}/{len(symbols)} 个币种")
            time.sleep(1)
    
    return result

def _init_from_binance_api():
    """从币安API初始化（回退方案）"""
    raw = fetch_24h_snapshot()
    
    new_symbols = {}
    new_last_q = {}
    trading_symbols = market_data.get("trading_symbols", set())
    
    for item in raw:
        symbol = item.get("symbol", "")
        if not symbol.endswith("USDT"):
            continue
        if trading_symbols and symbol not in trading_symbols:
            continue
        
        price_change_pct = float(item.get("priceChangePercent", 0))
        if price_change_pct == 0:
            continue
        
        q = float(item.get("quoteVolume", 0))
        price = float(item.get("lastPrice", 0))
        
        new_symbols[symbol] = {
            "q": q,
            "v": float(item.get("volume", 0)),
            "price": price,
            "o": float(item.get("openPrice", 0)),
            "h": float(item.get("highPrice", 0)),
            "l": float(item.get("lowPrice", 0)),
            "priceChangePercent": price_change_pct,
            "updated_at": time.time()
        }
        new_last_q[symbol] = q
    
    with data_lock:
        market_data["symbols"] = new_symbols
        market_data["last_q"] = new_last_q
        market_data["updated_at"] = time.time()
        market_data["current_minute"] = get_current_minute_ts()
    
    print(f"已从币安API加载: {len(new_symbols)} 个币种")

def get_current_minute_ts():
    """获取当前分钟的开始时间戳（秒）"""
    return int(time.time()) // 60 * 60

def get_current_15m_slot():
    """获取当前15分钟区间的时间戳"""
    return int(time.time()) // 900 * 900  # 900秒 = 15分钟

def check_volume_surge(symbol: str, current_15m_vol: float, avg_4h_vol: float):
    """检测15分钟成交量突增
    current_15m_vol: 当前完成的15分钟成交额
    avg_4h_vol: 前4小时的15分钟平均成交额（滑动窗口均值）
    """
    if avg_4h_vol > 0 and current_15m_vol > avg_4h_vol * 3.0:
        surge_info = {
            "start_time": time.time(),
            "ratio": current_15m_vol / avg_4h_vol,
            "vol": current_15m_vol,          # 当前15分钟成交额
            "last_vol": avg_4h_vol,           # 前4小时均值（上期）
            "avg_4h_vol": avg_4h_vol         # 前4小时均值
        }
        market_data["vol_surge_symbols"][symbol] = surge_info
        print(f"[VOL_SURGE] {symbol} 15分钟成交量突增: 当前{current_15m_vol/1e3:.1f}K vs 前4h均值{avg_4h_vol/1e3:.1f}K ({surge_info['ratio']:.2f}x)")
        return True
    return False

def cleanup_surge_records():
    """清理超过1小时的突增记录"""
    now = time.time()
    expired = []
    for symbol, info in market_data.get("vol_surge_symbols", {}).items():
        if now - info.get("start_time", 0) > 3600:  # 1小时
            expired.append(symbol)
    for symbol in expired:
        del market_data["vol_surge_symbols"][symbol]
        print(f"[VOL_SURGE] {symbol} 突增记录已过期，移除")

def fetch_today_open_prices():
    """从币安API获取今日北京时间0点的开盘价（只取成交额前100的币）"""
    print("DEBUG: fetch_today_open_prices started")
    with data_lock:
        symbols = market_data.get("symbols", {})
        if not symbols:
            print("DEBUG: no symbols available yet")
            return
    print(f"DEBUG: have {len(symbols)} symbols")
    
    # 北京时区今日08:00 = UTC 00:00
    beijing_offset = timedelta(hours=8)
    now_utc = datetime.now(tz=timezone.utc)
    now_beijing = now_utc + beijing_offset
    beijing_date = now_beijing.date()
    # 北京时间08:00 = UTC 00:00 (用timezone.utc明确指定)
    today_beijing_8am_utc = datetime(beijing_date.year, beijing_date.month, beijing_date.day, 0, 0, 0, tzinfo=timezone.utc)
    today_start_ms = int(today_beijing_8am_utc.timestamp()) * 1000
    
    # 按成交额排序，获取前100名（覆盖主要交易量，2秒间隔避免限流）
    sorted_symbols = sorted(symbols.items(), key=lambda x: x[1].get("q", 0), reverse=True)[:100]
    
    new_today_open = {}
    for symbol, info in sorted_symbols:
        try:
            params = {
                "symbol": symbol,
                "interval": "1h",
                "startTime": today_start_ms,
                "limit": 1
            }
            resp = _requests_session.get(f"{BINANCE_API}/fapi/v1/klines", params=params, timeout=5)
            if resp.status_code == 200:
                klines = resp.json()
                if klines:
                    new_today_open[symbol] = float(klines[0][1])
            elif resp.status_code == 429:
                # 限流了，等待更长时间
                time.sleep(10)
                continue
        except:
            pass
        time.sleep(2)  # 避免触发限流
    
    with data_lock:
        # 合并新数据与现有数据
        existing = market_data.get("today_open_prices", {})
        existing.update(new_today_open)
        market_data["today_open_prices"] = existing
        market_data["today_open_updated"] = time.time()
    
    print(f"今日开盘价已更新: {len(new_today_open)} 个币 (累计 {len(existing)} 个)")

def save_snapshot():
    """写入快照文件"""
    with data_lock:
        data = {
            "updated_at": datetime.now().isoformat(),
            "symbols": market_data["symbols"]
        }
    try:
        with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"快照已写入: {SNAPSHOT_FILE}")
    except Exception as e:
        print(f"写入失败: {e}")

def load_minute_klines_from_cos():
    """从COS加载历史分钟K线，失败则用本地文件"""
    # 先尝试COS
    try:
        if COS_SECRET_ID and COS_SECRET_KEY and COS_BUCKET:
            client = get_cos_client()
            if client is None:
                print("COS加载跳过: 凭证无效")
            else:
                resp = client.get_object(Bucket=COS_BUCKET, Key=COS_KEY)
                data = resp['Body'].get_raw_stream().read()
                df = pd.read_parquet(io.BytesIO(data))
                print(f"从COS加载了 {len(df)} 条分钟K线")

                klines_dict = {}
                for symbol in df['symbol'].unique():
                    symbol_df = df[df['symbol'] == symbol].sort_values('timestamp')
                    records = symbol_df.to_dict('records')
                    for r in records:
                        r['t'] = r.pop('timestamp')
                        r['o'] = r.pop('open')
                        r['h'] = r.pop('high')
                        r['l'] = r.pop('low')
                        r['c'] = r.pop('close')
                        r['v'] = r.pop('volume')
                        r['q'] = r.pop('quote_volume')
                    klines_dict[symbol] = records

                return klines_dict
    except Exception as e:
        print(f"COS加载失败: {e}")
    
    # 回退到本地文件
    try:
        if os.path.exists(LOCAL_KLINE_FILE):
            df = pd.read_parquet(LOCAL_KLINE_FILE)
            print(f"从本地文件加载了 {len(df)} 条分钟K线")
            
            klines_dict = {}
            for symbol in df['symbol'].unique():
                symbol_df = df[df['symbol'] == symbol].sort_values('timestamp')
                records = symbol_df.to_dict('records')
                # Rename columns to match in-memory format
                for r in records:
                    r['t'] = r.pop('timestamp')
                    r['o'] = r.pop('open')
                    r['h'] = r.pop('high')
                    r['l'] = r.pop('low')
                    r['c'] = r.pop('close')
                    r['v'] = r.pop('volume')
                    r['q'] = r.pop('quote_volume')
                klines_dict[symbol] = records
            
            return klines_dict
    except Exception as e:
        print(f"本地文件加载失败: {e}")
    
    print("无历史分钟K线数据（新启动或文件丢失）")
    return {}

def save_minute_klines_to_cos(klines_dict):
    """保存分钟K线到COS"""
    try:
        # 转换为 DataFrame
        all_rows = []
        for symbol, klines in klines_dict.items():
            for kline in klines:
                all_rows.append({
                    'symbol': symbol,
                    'timestamp': kline['t'],
                    'open': kline['o'],
                    'high': kline['h'],
                    'low': kline['l'],
                    'close': kline['c'],
                    'volume': kline['v'],
                    'quote_volume': kline['q']
                })
        
        if not all_rows:
            return
        
        df = pd.DataFrame(all_rows)
        
        # 滚动清理超过6天的数据
        cutoff = time.time() - (MAX_MINUTE_KLINES * 60)
        df = df[df['timestamp'] >= cutoff]
        
        # 保存到本地文件（始终）
        try:
            df.to_parquet(LOCAL_KLINE_FILE, index=False)
            print(f"分钟K线已保存本地: {len(df)} 条")
        except Exception as e:
            print(f"本地文件保存失败: {e}")
        
        # 上传到COS（如果配置了）
        if COS_SECRET_ID and COS_SECRET_KEY and COS_BUCKET:
            try:
                client = get_cos_client()
                if client is None:
                    print("COS上传跳过: 凭证无效")
                else:
                    buffer = io.BytesIO()
                    df.to_parquet(buffer, index=False)
                    buffer.seek(0)

                    client.put_object(
                        Bucket=COS_BUCKET,
                        Key=COS_KEY,
                        Body=buffer.read()
                    )
                    print(f"分钟K线已上传COS: {len(df)} 条")
            except Exception as e:
                print(f"COS上传失败: {e}")
    except Exception as e:
        print(f"分钟K线保存失败: {e}")

# ========== 15分钟成交量 COS 持久化 ==========
VOL_15M_PREFIX = "vol_15m/slots_"

def _get_vol_15m_key(slot_ts: int) -> str:
    """根据slot_ts生成COS key（按月份分文件）"""
    dt = datetime.fromtimestamp(slot_ts, tz=timezone.utc)
    return f"{VOL_15M_PREFIX}{dt.year}{dt.month:02d}.parquet"

def save_vol_15m_to_cos(slot_ts: int, vol_data: dict):
    """将15分钟成交量写入COS（按月份分文件）
    vol_data: {symbol: vol}
    """
    try:
        if not COS_SECRET_ID or not COS_SECRET_KEY or not COS_BUCKET:
            return
        
        client = get_cos_client()
        if client is None:
            return
        
        key = _get_vol_15m_key(slot_ts)
        
        # 构建新数据行
        new_rows = [{'slot_ts': slot_ts, 'symbol': s, 'vol': float(v)} for s, v in vol_data.items()]
        new_df = pd.DataFrame(new_rows)
        
        # 尝试读取旧文件
        try:
            resp = client.get_object(Bucket=COS_BUCKET, Key=key)
            old_data = resp['Body'].get_raw_stream().read()
            old_df = pd.read_parquet(io.BytesIO(old_data))
            # 去重：同一slot_ts+symbol只保留新数据
            old_df = old_df[~((old_df['slot_ts'] == slot_ts) & (old_df['symbol'].isin(vol_data.keys())))]
            merged_df = pd.concat([old_df, new_df], ignore_index=True)
        except Exception:
            merged_df = new_df
        
        # 滚动清理：只保留最近7天（672个15分钟区间）
        cutoff = time.time() - 7 * 24 * 3600
        merged_df = merged_df[merged_df['slot_ts'] >= cutoff]
        
        buffer = io.BytesIO()
        merged_df.to_parquet(buffer, index=False)
        buffer.seek(0)
        
        client.put_object(Bucket=COS_BUCKET, Key=key, Body=buffer.read())
        print(f"[VOL_15M_COS] 已上传 {len(new_df)} 条到 {key}")
    except Exception as e:
        print(f"[VOL_15M_COS] 上传失败: {e}")

def load_vol_15m_from_cos(end_slot: int, slots_count: int = 16) -> dict:
    """从COS加载指定时间范围内的15分钟成交量
    返回: {symbol: {slot_ts: vol}}
    """
    result = {}
    try:
        if not COS_SECRET_ID or not COS_SECRET_KEY or not COS_BUCKET:
            return result
        
        client = get_cos_client()
        if client is None:
            return result
        
        # 计算需要加载的月份范围
        start_slot = end_slot - (slots_count - 1) * 900
        start_dt = datetime.fromtimestamp(start_slot, tz=timezone.utc)
        end_dt = datetime.fromtimestamp(end_slot, tz=timezone.utc)
        
        # 生成需要查询的所有月份key
        keys_to_fetch = set()
        current_dt = datetime(start_dt.year, start_dt.month, 1, tzinfo=timezone.utc)
        end_month_dt = datetime(end_dt.year, end_dt.month, 1, tzinfo=timezone.utc)
        while current_dt <= end_month_dt:
            keys_to_fetch.add(f"{VOL_15M_PREFIX}{current_dt.year}{current_dt.month:02d}.parquet")
            # 下一个月
            if current_dt.month == 12:
                current_dt = datetime(current_dt.year + 1, 1, 1, tzinfo=timezone.utc)
            else:
                current_dt = datetime(current_dt.year, current_dt.month + 1, 1, tzinfo=timezone.utc)
        
        all_dfs = []
        for key in keys_to_fetch:
            try:
                resp = client.get_object(Bucket=COS_BUCKET, Key=key)
                data = resp['Body'].get_raw_stream().read()
                df = pd.read_parquet(io.BytesIO(data))
                all_dfs.append(df)
            except Exception:
                pass  # 文件不存在是正常的
        
        if not all_dfs:
            return result
        
        merged_df = pd.concat(all_dfs, ignore_index=True)
        merged_df = merged_df[(merged_df['slot_ts'] >= start_slot) & (merged_df['slot_ts'] <= end_slot)]
        
        for _, row in merged_df.iterrows():
            symbol = row['symbol']
            slot_ts = int(row['slot_ts'])
            vol = float(row['vol'])
            if symbol not in result:
                result[symbol] = {}
            result[symbol][slot_ts] = vol
        
        print(f"[VOL_15M_COS] 从COS加载了 {len(merged_df)} 条15分钟成交量")
    except Exception as e:
        print(f"[VOL_15M_COS] 加载失败: {e}")
    
    return result

def calc_vol_15m_avg_strict(symbol: str, current_slot: int, history: dict) -> float:
    """严格4小时时间窗口计算15分钟均值（分母恒为16，缺失补0）"""
    total = 0.0
    symbol_hist = history.get(symbol, {})
    for i in range(1, 17):
        ts = current_slot - i * 900
        total += symbol_hist.get(ts, 0.0)
    return total / 16.0



# ========== 市场数据快照 COS 持久化 ==========
COS_SNAPSHOT_PREFIX = "market_monitor/"

def save_symbols_snapshot_to_cos():
    """将当前所有币种快照写入COS（价格、成交额、涨跌幅等）"""
    try:
        if not COS_SECRET_ID or not COS_SECRET_KEY or not COS_BUCKET:
            return
        client = get_cos_client()
        if client is None:
            return
        
        with data_lock:
            symbols = dict(market_data.get("symbols", {}))
        
        if not symbols:
            return
        
        rows = []
        for symbol, info in symbols.items():
            rows.append({
                'symbol': symbol,
                'price': float(info.get('price', 0)),
                'q': float(info.get('q', 0)),
                'v': float(info.get('v', 0)),
                'o': float(info.get('o', 0)),
                'h': float(info.get('h', 0)),
                'l': float(info.get('l', 0)),
                'priceChangePercent': float(info.get('priceChangePercent', 0)),
                'updated_at': float(info.get('updated_at', 0)),
            })
        
        df = pd.DataFrame(rows)
        buffer = io.BytesIO()
        df.to_parquet(buffer, index=False)
        buffer.seek(0)
        
        key = f"{COS_SNAPSHOT_PREFIX}symbols_snapshot.parquet"
        client.put_object(Bucket=COS_BUCKET, Key=key, Body=buffer.read())
        print(f"[SNAPSHOT_COS] symbols快照已上传: {len(rows)} 个币种")
    except Exception as e:
        print(f"[SNAPSHOT_COS] symbols快照上传失败: {e}")

def load_symbols_snapshot_from_cos() -> dict:
    """从COS加载最新币种快照"""
    result = {}
    try:
        if not COS_SECRET_ID or not COS_SECRET_KEY or not COS_BUCKET:
            return result
        client = get_cos_client()
        if client is None:
            return result
        
        key = f"{COS_SNAPSHOT_PREFIX}symbols_snapshot.parquet"
        resp = client.get_object(Bucket=COS_BUCKET, Key=key)
        data = resp['Body'].get_raw_stream().read()
        df = pd.read_parquet(io.BytesIO(data))
        
        for _, row in df.iterrows():
            symbol = row['symbol']
            result[symbol] = {
                'price': float(row['price']),
                'q': float(row['q']),
                'v': float(row['v']),
                'o': float(row['o']),
                'h': float(row['h']),
                'l': float(row['l']),
                'priceChangePercent': float(row['priceChangePercent']),
                'updated_at': float(row['updated_at']),
            }
        print(f"[SNAPSHOT_COS] 从COS加载了 {len(result)} 个币种的快照")
    except Exception as e:
        print(f"[SNAPSHOT_COS] symbols快照加载失败: {e}")
    return result

def save_vol_24h_today_to_cos():
    """将今日累计成交额写入COS"""
    try:
        if not COS_SECRET_ID or not COS_SECRET_KEY or not COS_BUCKET:
            return
        client = get_cos_client()
        if client is None:
            return
        
        with data_lock:
            vol_24h = dict(market_data.get("vol_24h_today", {}))
        
        if not vol_24h:
            return
        
        rows = [{'symbol': s, 'vol': float(v)} for s, v in vol_24h.items()]
        df = pd.DataFrame(rows)
        buffer = io.BytesIO()
        df.to_parquet(buffer, index=False)
        buffer.seek(0)
        
        key = f"{COS_SNAPSHOT_PREFIX}vol_24h_today.parquet"
        client.put_object(Bucket=COS_BUCKET, Key=key, Body=buffer.read())
        print(f"[SNAPSHOT_COS] vol_24h_today已上传: {len(rows)} 条")
    except Exception as e:
        print(f"[SNAPSHOT_COS] vol_24h_today上传失败: {e}")

def load_vol_24h_today_from_cos() -> dict:
    """从COS加载今日累计成交额"""
    result = {}
    try:
        if not COS_SECRET_ID or not COS_SECRET_KEY or not COS_BUCKET:
            return result
        client = get_cos_client()
        if client is None:
            return result
        
        key = f"{COS_SNAPSHOT_PREFIX}vol_24h_today.parquet"
        resp = client.get_object(Bucket=COS_BUCKET, Key=key)
        data = resp['Body'].get_raw_stream().read()
        df = pd.read_parquet(io.BytesIO(data))
        
        for _, row in df.iterrows():
            result[row['symbol']] = float(row['vol'])
        print(f"[SNAPSHOT_COS] 从COS加载了 {len(result)} 个币种的vol_24h_today")
    except Exception as e:
        print(f"[SNAPSHOT_COS] vol_24h_today加载失败: {e}")
    return result

def save_today_open_prices_to_cos():
    """将今日开盘价写入COS"""
    try:
        if not COS_SECRET_ID or not COS_SECRET_KEY or not COS_BUCKET:
            return
        client = get_cos_client()
        if client is None:
            return
        
        with data_lock:
            opens = dict(market_data.get("today_open_prices", {}))
        
        if not opens:
            return
        
        rows = [{'symbol': s, 'open_price': float(v)} for s, v in opens.items()]
        df = pd.DataFrame(rows)
        buffer = io.BytesIO()
        df.to_parquet(buffer, index=False)
        buffer.seek(0)
        
        key = f"{COS_SNAPSHOT_PREFIX}today_open_prices.parquet"
        client.put_object(Bucket=COS_BUCKET, Key=key, Body=buffer.read())
        print(f"[SNAPSHOT_COS] today_open_prices已上传: {len(rows)} 条")
    except Exception as e:
        print(f"[SNAPSHOT_COS] today_open_prices上传失败: {e}")

def load_today_open_prices_from_cos() -> dict:
    """从COS加载今日开盘价"""
    result = {}
    try:
        if not COS_SECRET_ID or not COS_SECRET_KEY or not COS_BUCKET:
            return result
        client = get_cos_client()
        if client is None:
            return result
        
        key = f"{COS_SNAPSHOT_PREFIX}today_open_prices.parquet"
        resp = client.get_object(Bucket=COS_BUCKET, Key=key)
        data = resp['Body'].get_raw_stream().read()
        df = pd.read_parquet(io.BytesIO(data))
        
        for _, row in df.iterrows():
            result[row['symbol']] = float(row['open_price'])
        print(f"[SNAPSHOT_COS] 从COS加载了 {len(result)} 个币种的today_open_prices")
    except Exception as e:
        print(f"[SNAPSHOT_COS] today_open_prices加载失败: {e}")
    return result


def calculate_beijing_8am_data(hourly_cache: dict):
    """计算北京时间8点起的累计数据（涨跌幅和成交额）"""
    now_utc = datetime.now(tz=timezone.utc)
    today_utc0 = datetime(now_utc.year, now_utc.month, now_utc.day, tzinfo=timezone.utc)
    beijing_8am_ts = int(today_utc0.timestamp())  # 北京8点 = UTC 0点
    
    new_symbols = {}
    today_open_prices = {}
    
    for symbol, hklines in hourly_cache.items():
        if not hklines:
            continue
        
        # 找到北京8点的那根K线
        beijing_8am_kline = None
        for h in hklines:
            if h['t'] == beijing_8am_ts:
                beijing_8am_kline = h
                break
        
        if beijing_8am_kline:
            open_price = beijing_8am_kline['o']
            today_open_prices[symbol] = open_price
            
            # 累计北京8点起的成交额
            today_volume = sum(h.get('q', 0) for h in hklines if h['t'] >= beijing_8am_ts)
            
            # 当前价格（最后一根K线的收盘价）
            current_price = hklines[-1]['c'] if hklines else open_price
            
            # 计算涨跌幅（基于北京8点开盘价）
            gain_pct = (current_price - open_price) / open_price * 100 if open_price > 0 else 0
            
            # 24h最高价和最低价（从北京8点起）
            today_high = max(h['h'] for h in hklines if h['t'] >= beijing_8am_ts) if hklines else current_price
            today_low = min(h['l'] for h in hklines if h['t'] >= beijing_8am_ts) if hklines else current_price
            
            new_symbols[symbol] = {
                "q": today_volume,  # 北京8点起的累计成交额
                "v": sum(h.get('v', 0) for h in hklines if h['t'] >= beijing_8am_ts),  # 成交量
                "price": current_price,
                "o": open_price,  # 北京8点开盘价
                "h": today_high,
                "l": today_low,
                "priceChangePercent": gain_pct,  # 北京8点起的涨跌幅
                "updated_at": time.time()
            }
    
    return new_symbols, today_open_prices

def _update_today_open_from_hourly_cache(hourly_cache: dict):
    """从1h K线缓存中提取北京08:00的开盘价，更新today_open_prices"""
    now_utc = datetime.now(tz=timezone.utc)
    # 最近一个已过去的北京08:00 = 今天UTC 00:00
    today_utc0 = datetime(now_utc.year, now_utc.month, now_utc.day, tzinfo=timezone.utc)
    today_start_ts = int(today_utc0.timestamp())

    updated = 0
    with data_lock:
        today_open = market_data.get("today_open_prices", {})
        for symbol, hklines in hourly_cache.items():
            for h in hklines:
                if h['t'] == today_start_ts:
                    today_open[symbol] = h['o']
                    updated += 1
                    break
        market_data["today_open_prices"] = today_open
    print(f"[开盘价] 从1h K线更新了 {updated} 个币的今日开盘价 (北京08:00)")

def get_beijing_midnight_open_price(symbol: str, info: dict) -> float:
    """获取北京时区当日0点的开盘价（优先从缓存，其次从K线计算，最后fallback）"""
    # 优先用缓存的今日开盘价
    with data_lock:
        today_open = market_data.get("today_open_prices", {}).get(symbol)
    if today_open and today_open > 0:
        return today_open
    
    # fallback: 直接返回info中的open价格，不再用24h ticker反推
    # 因为24h ticker的openPrice是24小时前，不是北京8点
    return info.get("o", 0)

# ========== 币种分类映射 ==========
CATEGORY_MAP = {
    "XAUUSDT": ("黄金", "贵金属"),
    "XAUTUSDT": ("黄金", "贵金属"),
    "XAGUSDT": ("白银", "贵金属"),
    "TSLAUSDT": ("特斯拉", "美股"),
    "NVDAUSDT": ("英伟达", "美股"),
    "AMZNUSDT": ("亚马逊", "美股"),
    "GOOGLUSDT": ("谷歌", "美股"),
    "AAPLUSDT": ("苹果", "美股"),
}

def get_table_data():
    """获取表格数据（排序后）"""
    with data_lock:
        # 深拷贝避免遍历期间被其他线程修改
        symbols = dict(market_data["symbols"])
        vol_15m_last = dict(market_data.get("vol_15m_last", {}))
        vol_24h_today = dict(market_data.get("vol_24h_today", {}))
        vol_15m_avg_4h = dict(market_data.get("vol_15m_avg_4h", {}))
    
    current_minute = int(time.time()) // 60 * 60

    rows = []
    for symbol, info in symbols.items():
        price = info["price"]
        o = info["o"]
        open_price = get_beijing_midnight_open_price(symbol, info)
        if open_price > 0:
            o = open_price
        gain_pct = (price - o) / o * 100 if o > 0 else 0

        cat_name, cat_type = CATEGORY_MAP.get(symbol, (None, None))
        
        # 使用今日累计成交额
        vol_24h = vol_24h_today.get(symbol, info.get("q", 0))
        
        # 获取前4小时的15分钟均值
        vol_15m_avg = vol_15m_avg_4h.get(symbol, 0)
        
        # 获取上一个完整15分钟成交额；如果缺失，回退到当前正在累加的15分钟值
        current_15m = vol_15m_last.get(symbol, 0)
        if current_15m == 0:
            current_15m = market_data.get("vol_15m_current", {}).get(symbol, 0)
        
        # 检查是否突增：当前15分钟 > 前4小时均值 × 3.0
        is_surge = False
        surge_ratio = 0
        if vol_15m_avg > 0 and current_15m > vol_15m_avg * 3.0:
            is_surge = True
            surge_ratio = current_15m / vol_15m_avg

        rows.append({
            "symbol": symbol,
            "price": price,
            "q": vol_24h,  # 今日累计成交额
            "v": info["v"],
            "o": o,
            "gain_pct": round(gain_pct, 2),
            "cat_name": cat_name,
            "cat_type": cat_type,
            "vol_15m": vol_15m_avg,  # 前4小时的15分钟均值
            "vol_15m_current": current_15m,  # 当前15分钟成交额
            "is_surge": is_surge,
            "surge_ratio": round(surge_ratio, 2) if surge_ratio else 0
        })

    # 排序：突增币种排第一，其他按涨跌幅排序
    surge_rows = [r for r in rows if r["is_surge"]]
    normal_rows = [r for r in rows if not r["is_surge"]]
    
    # 突增币种按突增倍数降序
    surge_rows.sort(key=lambda x: -x["surge_ratio"])
    # 普通币种按涨跌幅降序
    normal_rows.sort(key=lambda x: -x["gain_pct"])
    
    rows = surge_rows + normal_rows

    for i, row in enumerate(rows, 1):
        row["rank"] = i

    return rows

# ========== 布林爬坡 - API回填历史1h K线 ==========

def _fetch_single_hourly_klines(symbol: str) -> list | None:
    """拉取单个币种的25根1h K线，遇到429自动重试"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            resp = _requests_session.get(
                f"{BINANCE_API}/fapi/v1/klines",
                params={"symbol": symbol, "interval": "1h", "limit": 25},
                timeout=10
            )
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 60))
                print(f"[BACKFILL] {symbol} 触发429限流，等待{retry_after}秒 (尝试{attempt+1}/{max_retries})")
                time.sleep(retry_after)
                continue  # 重试
            if resp.status_code == 418:
                retry_after = int(resp.headers.get("Retry-After", 300))
                print(f"[BACKFILL] {symbol} 触发418熔断，等待{retry_after}秒 (尝试{attempt+1}/{max_retries})")
                time.sleep(retry_after)
                continue  # 重试
            resp.raise_for_status()
            data = resp.json()
            if not data:
                return None

            result = []
            for k in data:
                q = float(k[7])  # quoteAssetVolume
                result.append({
                    "t": int(k[0]) // 1000,  # ms → s
                    "o": float(k[1]),
                    "h": float(k[2]),
                    "l": float(k[3]),
                    "c": float(k[4]),
                    "v": float(k[5]),
                    "q": q,
                    "buy_q": q * 0.5,
                    "sell_q": q * 0.5,
                    "buy_ratio": 0.5,
                })
            return result
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"[BACKFILL] {symbol} 拉取失败: {e}")
                return None
            time.sleep(5)
    return None


def _backfill_worker(symbols: list[str], result_dict: dict, delay: float = 0.5):
    """工作线程：逐个拉取1h K线"""
    for symbol in symbols:
        klines = _fetch_single_hourly_klines(symbol)
        if klines:
            result_dict[symbol] = klines
        time.sleep(delay)


def backfill_hourly_klines():
    """启动时构建1h K线缓存：优先从分钟K线聚合，不够的从币安API补"""
    # 第一步：从已有的分钟K线聚合1h K线
    with data_lock:
        minute_klines = dict(market_data.get("minute_klines", {}))
        symbols = list(market_data.get("symbols", {}).keys())

    agg_result = {}
    agg_count = 0
    for symbol, mklines in minute_klines.items():
        if len(mklines) >= 60:  # 至少1小时的数据才能聚合
            hourly = _aggregate_minutes_to_hours(symbol, mklines)
            if len(hourly) >= 20:  # 至少20小时才能算布林带
                agg_result[symbol] = hourly
                agg_count += 1

    print(f"[BACKFILL] 从分钟K线聚合了 {agg_count} 个币种的1h K线")

    # 写入全局缓存
    with data_lock:
        market_data["hourly_kline_cache"] = agg_result
        market_data["bb_backfill_done"] = len(agg_result) >= len(symbols) * 0.9  # 90%以上就算完成

    if agg_count >= len(symbols) * 0.9:
        print(f"[BACKFILL] 分钟K线聚合已覆盖 {agg_count}/{len(symbols)} 个币种，无需API回填")
        with data_lock:
            market_data["bb_backfill_done"] = True
        return

    # 第二步：对不够的币种从API补
    need_api = [s for s in symbols if s not in agg_result or len(agg_result.get(s, [])) < 20]
    if not need_api:
        with data_lock:
            market_data["bb_backfill_done"] = True
        return

    print(f"[BACKFILL] 需从API补拉 {len(need_api)} 个币种的1h K线...")
    start_time = time.time()

    result_dict = {}
    _backfill_worker(need_api, result_dict, delay=0.5)

    # 合并API数据到已有缓存
    with data_lock:
        cache = market_data.get("hourly_kline_cache", {})
        for symbol, hklines in result_dict.items():
            if symbol in cache:
                # 合并，按时间戳去重
                existing = {h["t"]: h for h in cache[symbol]}
                for h in hklines:
                    existing[h["t"]] = h  # API数据补充
                cache[symbol] = [existing[t] for t in sorted(existing.keys())]
            else:
                cache[symbol] = hklines
        market_data["hourly_kline_cache"] = cache
        market_data["bb_backfill_done"] = True

    elapsed = time.time() - start_time
    total = len(cache)
    print(f"[BACKFILL] API补拉完成: {len(result_dict)} 个币种, 耗时{elapsed:.1f}秒, 总计{total}个币种")

    # 持久化1h K线缓存到COS，下次启动直接加载
    _save_hourly_cache_to_cos()


def _save_hourly_cache_to_cos():
    """将1h K线缓存保存到COS"""
    try:
        client = get_cos_client()
        if client is None:
            print("[HOURLY-COS] 上传跳过: 凭证无效")
            return
        with data_lock:
            hourly_cache = dict(market_data.get("hourly_kline_cache", {}))

        if not hourly_cache:
            print("[HOURLY-COS] 上传跳过: hourly_cache为空")
            return

        print(f"[HOURLY-COS] 开始上传: {len(hourly_cache)} 个币种")

        all_rows = []
        for symbol, hklines in hourly_cache.items():
            for h in hklines:
                all_rows.append({
                    'symbol': symbol,
                    'timestamp': h['t'],
                    'open': h['o'],
                    'high': h['h'],
                    'low': h['l'],
                    'close': h['c'],
                    'volume': h.get('v', 0),
                    'quote_volume': h.get('q', 0),
                    'buy_ratio': h.get('buy_ratio', 0.5),
                })

        if not all_rows:
            return

        df = pd.DataFrame(all_rows)
        buffer = io.BytesIO()
        df.to_parquet(buffer, index=False)
        buffer.seek(0)

        client.put_object(
            Bucket=COS_BUCKET,
            Key=COS_HOURLY_KEY,
            Body=buffer.read()
        )
        print(f"[HOURLY-COS] 1h K线缓存已上传: {len(all_rows)} 条 ({len(hourly_cache)} 个币种)")
    except Exception as e:
        print(f"[HOURLY-COS] 上传失败: {e}")


def _load_hourly_cache_from_cos():
    """从COS加载1h K线缓存，返回 {symbol: [hourly_kline, ...]}"""
    try:
        client = get_cos_client()
        if client is None:
            return {}
        resp = client.get_object(Bucket=COS_BUCKET, Key=COS_HOURLY_KEY)
        data = resp['Body'].get_raw_stream().read()
        df = pd.read_parquet(io.BytesIO(data))
        print(f"[HOURLY-COS] 从COS加载了 {len(df)} 条1h K线")

        cache = {}
        for symbol in df['symbol'].unique():
            symbol_df = df[df['symbol'] == symbol].sort_values('timestamp')
            hklines = []
            for _, r in symbol_df.iterrows():
                hklines.append({
                    't': int(r['timestamp']),
                    'o': float(r['open']),
                    'h': float(r['high']),
                    'l': float(r['low']),
                    'c': float(r['close']),
                    'v': float(r.get('volume', 0)),
                    'q': float(r.get('quote_volume', 0)),
                    'buy_ratio': float(r.get('buy_ratio', 0.5)),
                })
            cache[symbol] = hklines
        return cache
    except Exception as e:
        print(f"[HOURLY-COS] 加载失败: {e}")
        return {}


# ========== 布林爬坡检测 ==========

def _aggregate_minutes_to_hours(symbol: str, minute_klines: list) -> list:
    """将分钟K线聚合为小时K线，返回最近若干小时的数据"""
    if not minute_klines:
        return []
    
    # 按小时分组
    hourly_data = {}
    for k in minute_klines:
        hour_ts = (k["t"] // 3600) * 3600  # 向下取整到整小时
        if hour_ts not in hourly_data:
            hourly_data[hour_ts] = {"klines": [], "buy_q": 0, "sell_q": 0}
        hourly_data[hour_ts]["klines"].append(k)
        hourly_data[hour_ts]["buy_q"] += k.get("buy_q", 0)
        hourly_data[hour_ts]["sell_q"] += k.get("sell_q", 0)
    
    # 构建小时K线
    result = []
    for hour_ts in sorted(hourly_data.keys()):
        klines = hourly_data[hour_ts]["klines"]
        buy_q = hourly_data[hour_ts]["buy_q"]
        sell_q = hourly_data[hour_ts]["sell_q"]
        total_q = buy_q + sell_q
        
        result.append({
            "t": hour_ts,
            "o": klines[0]["o"],
            "h": max(k["h"] for k in klines),
            "l": min(k["l"] for k in klines),
            "c": klines[-1]["c"],
            "v": sum(k.get("v", 0) for k in klines),
            "q": sum(k.get("q", 0) for k in klines),
            "buy_q": buy_q,
            "sell_q": sell_q,
            "buy_ratio": buy_q / total_q if total_q > 0 else 0.5
        })
    
    return result


def _calculate_bollinger_bands(closes: list, period: int = 20, std_mult: float = 2.0) -> dict | None:
    """计算布林带（中轨=均线，上下轨=中轨±2倍标准差）"""
    if len(closes) < period:
        return None
    
    recent_closes = closes[-period:]
    middle = sum(recent_closes) / period
    variance = sum((c - middle) ** 2 for c in recent_closes) / period
    std = variance ** 0.5
    
    return {
        "upper": middle + std_mult * std,
        "middle": middle,
        "lower": middle - std_mult * std
    }


def _calculate_atr(hourly_klines: list, period: int = 14) -> float | None:
    """计算ATR（Average True Range）"""
    if len(hourly_klines) < period + 1:
        return None
    
    true_ranges = []
    for i in range(len(hourly_klines) - period, len(hourly_klines)):
        k = hourly_klines[i]
        prev_k = hourly_klines[i - 1]
        tr = max(
            k["h"] - k["l"],              # 当日振幅
            abs(k["h"] - prev_k["c"]),    # 当日最高与昨收的差
            abs(k["l"] - prev_k["c"])     # 当日最低与昨收的差
        )
        true_ranges.append(tr)
    
    return sum(true_ranges) / len(true_ranges) if true_ranges else None


def _check_hl_climb_tolerant(hourly_klines: list, idx: int, cfg: dict) -> bool:
    """检查HL抬高条件（带容忍机制）：最近N根中至少M根HL抬高"""
    window = cfg["hl_tolerance_window"]
    min_count = cfg["hl_tolerance_min"]
    
    # 收集窗口内的HL抬高次数（需要和前一根比较，所以从idx-1开始）
    climb_count = 0
    check_start = max(1, idx - window + 2)  # 确保有前一根可以比较
    for i in range(check_start, idx + 1):
        k = hourly_klines[i]
        prev_k = hourly_klines[i - 1]
        if k["h"] > prev_k["h"] and k["l"] > prev_k["l"]:
            climb_count += 1
    
    return climb_count >= min_count


def _check_hour_climb(k: dict, middle: float, upper: float, avg_vol: float, cfg: dict) -> bool:
    """检查单根K线的独立条件（不含HL，HL需用容忍机制单独判断）"""
    # 1. 收盘价 > 中轨 且 在上轨±5%范围内
    if k["c"] <= middle:
        return False
    tolerance = upper * cfg["upper_tolerance_pct"]
    if not (upper - tolerance <= k["c"] <= upper + tolerance):
        return False
    
    # 2. buy_ratio > 0.55（仅对真实数据检查，默认0.5跳过）
    if not (cfg.get("buy_ratio_skip_default", True) and abs(k.get("buy_ratio", 0.5) - 0.5) < 0.001):
        # 非默认buy_ratio才检查阈值
        if k.get("buy_ratio", 0.5) <= cfg["buy_ratio_threshold"]:
            return False
    
    # 3. 量能 > 1.2倍均量
    if avg_vol > 0 and k.get("q", 0) < avg_vol * cfg["volume_ratio"]:
        return False
    
    return True


def _detect_bollinger_climb(symbol: str, hourly_klines: list) -> dict | None:
    """检测布林爬坡信号：收盘价在中轨附近+HL容忍抬高+量能放大+buy_ratio高+ATR趋势"""
    cfg = BB_CLIMB_CONFIG
    
    if symbol in cfg["exclude_symbols"]:
        return None
    
    # 过滤非TRADING状态的币种
    trading_symbols = market_data.get("trading_symbols", set())
    if trading_symbols and symbol not in trading_symbols:
        return None
    
    if len(hourly_klines) < max(cfg["period"] + 1, cfg["atr_period"] + 1):
        return None
    
    # 计算布林带
    closes = [k["c"] for k in hourly_klines]
    bb = _calculate_bollinger_bands(closes, cfg["period"], cfg["std_mult"])
    if not bb:
        return None
    
    middle = bb["middle"]
    upper = bb["upper"]
    
    # 计算ATR
    atr = _calculate_atr(hourly_klines, cfg["atr_period"]) if cfg["atr_enabled"] else None
    
    # 计算24小时平均成交量（不含最后一根）
    avg_volumes = [k.get("q", 0) for k in hourly_klines[:-1]]
    avg_vol = sum(avg_volumes) / len(avg_volumes) if avg_volumes else 0
    
    # 检查最后一根K线的独立条件（价格中轨附近+买比+量能）
    last_k = hourly_klines[-1]
    if not _check_hour_climb(last_k, middle, upper, avg_vol, cfg):
        return None
    
    # 检查HL容忍抬高（基于最后一根的位置）
    if not _check_hl_climb_tolerant(hourly_klines, len(hourly_klines) - 1, cfg):
        return None
    
    # ATR趋势过滤：当前K线振幅应大于ATR的50%（表明趋势明确）
    if atr is not None and atr > 0:
        current_range = last_k["h"] - last_k["l"]
        if current_range < atr * 0.5:
            return None
    
    # 往前计算持续了几小时（容忍版）
    consecutive_count = 1
    for i in range(len(hourly_klines) - 2, -1, -1):
        k = hourly_klines[i]
        if not _check_hour_climb(k, middle, upper, avg_vol, cfg):
            break
        if not _check_hl_climb_tolerant(hourly_klines, i, cfg):
            break
        consecutive_count += 1
    
    # 只取最后consecutive_count根K线
    valid_hours = hourly_klines[-consecutive_count:]
    
    return {
        "symbol": symbol,
        "upper": round(upper, 6),
        "middle": round(middle, 6),
        "atr": round(atr, 6) if atr else None,
        "consecutive_hours": consecutive_count,
        "avg_volume_24h": round(avg_vol, 2),
        "valid_hours": [{
            "t": h["t"],
            "o": round(h["o"], 6),
            "h": round(h["h"], 6),
            "l": round(h["l"], 6),
            "c": round(h["c"], 6),
            "v": round(h.get("v", 0), 2),
            "buy_ratio": round(h["buy_ratio"], 3)
        } for h in valid_hours]
    }


def _detect_bollinger_candidate(symbol: str, hourly_klines: list) -> dict | None:
    """检测布林候选蓄力信号：爬坡信号断后，连续N小时在上轨附近蓄力
    
    条件：
    1. 最近有爬坡信号断开（不满足HL/买比等条件，但价格仍在上轨附近）
    2. 连续candidate_near_hours小时满足：收盘价>中轨 且 在上轨±5%范围内
    3. 最后一根K线量能 > 均量 × candidate_vol_ratio
    """
    cfg = BB_CLIMB_CONFIG
    
    if not cfg.get("candidate_enabled", True):
        return None
    
    if symbol in cfg["exclude_symbols"]:
        return None
    
    # 过滤非TRADING状态的币种
    trading_symbols = market_data.get("trading_symbols", set())
    if trading_symbols and symbol not in trading_symbols:
        return None
    
    if len(hourly_klines) < max(cfg["period"] + 1, cfg["atr_period"] + 1):
        return None
    
    # 计算布林带
    closes = [k["c"] for k in hourly_klines]
    bb = _calculate_bollinger_bands(closes, cfg["period"], cfg["std_mult"])
    if not bb:
        return None
    
    middle = bb["middle"]
    upper = bb["upper"]
    
    # 计算ATR
    atr = _calculate_atr(hourly_klines, cfg["atr_period"]) if cfg["atr_enabled"] else None
    
    # 计算均量（不含最后一根）
    avg_volumes = [k.get("q", 0) for k in hourly_klines[:-1]]
    avg_vol = sum(avg_volumes) / len(avg_volumes) if avg_volumes else 0
    
    # 最后一根K线不能是爬坡信号（候选是信号断后的补充）
    last_k = hourly_klines[-1]
    if _check_hour_climb(last_k, middle, upper, avg_vol, cfg) and _check_hl_climb_tolerant(hourly_klines, len(hourly_klines) - 1, cfg):
        return None  # 已经是爬坡信号，不需要候选
    
    # 从最后一根往前，检查连续多少小时满足"在上轨附近"条件
    near_hours = cfg["candidate_near_hours"]  # 3
    near_count = 0
    
    for i in range(len(hourly_klines) - 1, -1, -1):
        k = hourly_klines[i]
        # 条件1: 收盘价 > 中轨
        if k["c"] <= middle:
            break
        # 条件2: 收盘价在上轨±5%范围内
        tolerance = upper * cfg["upper_tolerance_pct"]
        if not (upper - tolerance <= k["c"] <= upper + tolerance):
            break
        near_count += 1
    
    if near_count < near_hours:
        return None
    
    # 候选量能条件：最后一根K线量能 > 均量 × 0.8
    if avg_vol > 0 and last_k.get("q", 0) < avg_vol * cfg["candidate_vol_ratio"]:
        return None
    
    # 收集候选K线
    candidate_klines = hourly_klines[-near_count:]
    
    # 检查候选期内是否有爬坡信号K线（说明是从爬坡断开进入蓄力的）
    # 至少有一根K线曾经接近满足爬坡条件（HL抬高过）
    has_hl_climb = False
    for i in range(len(hourly_klines) - near_count, len(hourly_klines)):
        if i == 0:
            has_hl_climb = True
            break
        k = hourly_klines[i]
        prev_k = hourly_klines[i - 1]
        if k["h"] > prev_k["h"] and k["l"] > prev_k["l"]:
            has_hl_climb = True
            break
    
    if not has_hl_climb:
        return None
    
    return {
        "symbol": symbol,
        "upper": round(upper, 6),
        "middle": round(middle, 6),
        "atr": round(atr, 6) if atr else None,
        "consecutive_hours": near_count,
        "avg_volume_24h": round(avg_vol, 2),
        "candidate_hours": [{
            "t": h["t"],
            "o": round(h["o"], 6),
            "h": round(h["h"], 6),
            "l": round(h["l"], 6),
            "c": round(h["c"], 6),
            "v": round(h.get("v", 0), 2),
            "buy_ratio": round(h["buy_ratio"], 3)
        } for h in candidate_klines]
    }


def _refresh_bollinger_climb_cache():
    """后台定时刷新布林爬坡缓存"""
    global _bb_climb_cache
    with data_lock:
        minute_klines = dict(market_data.get("minute_klines", {}))
        hourly_kline_cache = dict(market_data.get("hourly_kline_cache", {}))

    results = []
    candidates = []
    # 合并所有币种（API回填 + WebSocket积累）
    all_symbols = set(minute_klines.keys()) | set(hourly_kline_cache.keys())

    for symbol in all_symbols:
        api_hourly = hourly_kline_cache.get(symbol, [])
        ws_klines = minute_klines.get(symbol, [])
        ws_hourly = _aggregate_minutes_to_hours(symbol, ws_klines) if len(ws_klines) >= 60 else []

        ws_hourly_by_t = {h["t"]: h for h in ws_hourly}
        api_hourly_by_t = {h["t"]: h for h in api_hourly}
        merged_dict = api_hourly_by_t.copy()
        merged_dict.update(ws_hourly_by_t)

        hourly_klines = [merged_dict[t] for t in sorted(merged_dict.keys())]

        if len(hourly_klines) < 25:
            continue

        signal = _detect_bollinger_climb(symbol, hourly_klines)
        if signal:
            results.append(signal)
        else:
            candidate = _detect_bollinger_candidate(symbol, hourly_klines)
            if candidate:
                candidates.append(candidate)

    results.sort(key=lambda x: -x["consecutive_hours"])
    candidates.sort(key=lambda x: -x["consecutive_hours"])

    with _bb_climb_lock:
        _bb_climb_cache = {
            "results": results[:50],
            "candidates": candidates[:30],
            "updated_at": time.time(),
        }


def bollinger_climb_background_loop():
    """每10秒刷新一次布林爬坡缓存"""
    while True:
        try:
            time.sleep(10)
            _refresh_bollinger_climb_cache()
        except Exception as e:
            print(f"[BB_CACHE] 刷新失败: {e}")


@app.route("/api/bollinger_climb")
def api_bollinger_climb():
    """返回布林爬坡预警信号（读缓存）"""
    with _bb_climb_lock:
        cache = dict(_bb_climb_cache)

    return jsonify({
        "code": 0,
        "count": len(cache.get("results", [])),
        "data": cache.get("results", []),
        "candidate_count": len(cache.get("candidates", [])),
        "candidates": cache.get("candidates", []),
    })


def _calculate_buy_ratio(price_delta: float, last_price: float) -> float:
    """
    根据价格变化估算主动买入比例
    price_delta: 这一秒的价格变化
    last_price: 上一秒的价格
    返回: buy_ratio (0.1 ~ 0.9)
    """
    if last_price <= 0:
        return 0.5
    
    # 价格变化率（相对于上一秒价格，更敏感）
    change_rate = price_delta / last_price
    
    # 用符号函数压缩到 [-1, 1]，再映射到 [0.1, 0.9]
    # change_rate * 100 的系数：0.01价格变化 → 完全偏某一边
    if change_rate > 0:
        buy_ratio = 0.5 + min(0.4, change_rate * 100)
    elif change_rate < 0:
        buy_ratio = 0.5 - min(0.4, abs(change_rate) * 100)
    else:
        buy_ratio = 0.5
    
    return max(0.1, min(0.9, buy_ratio))

# ========== 页面 ==========
@app.route("/")
def index():
    return render_template("market_monitor.html")

@app.route("/api/snapshot")
def api_snapshot():
    """返回全量数据"""
    rows = get_table_data()
    return jsonify({
        "code": 0,
        "count": len(rows),
        "data": rows
    })

@app.route("/api/debug_state")
def api_debug_state():
    """返回内部状态（调试用）"""
    with data_lock:
        current_minute = market_data.get("current_minute")
        second_keys = list(market_data.get("second_deltas", {}).keys())
        minute_klines_counts = {s: len(v) for s, v in market_data.get("minute_klines", {}).items()}
        hourly_cache_counts = {s: len(v) for s, v in market_data.get("hourly_kline_cache", {}).items()}
        bb_backfill_done = market_data.get("bb_backfill_done", False)
        trading_symbols = list(market_data.get("trading_symbols", set()))
        vol_15m_slot = market_data.get("vol_15m_slot")
        vol_15m_current = market_data.get("vol_15m_current", {})
        vol_15m_last = market_data.get("vol_15m_last", {})
        vol_24h_today = market_data.get("vol_24h_today", {})
        vol_surge_symbols = market_data.get("vol_surge_symbols", {})
    
    # Hyperliquid 聚合器统计
    hl_stats = {}
    try:
        from utils.trades_aggregator import get_aggregator
        agg = get_aggregator()
        hl_stats = agg.get_stats()
    except:
        pass
    
    return jsonify({
        "current_minute": current_minute,
        "second_deltas_key_count": len(second_keys),
        "first_5_second_keys": second_keys[:5],
        "minute_klines_counts": dict(list(minute_klines_counts.items())[:10]),
        "hourly_cache_symbol_count": len(hourly_cache_counts),
        "hourly_cache_sample": dict(list(hourly_cache_counts.items())[:5]),
        "bb_backfill_done": bb_backfill_done,
        "server_time": time.time(),
        "server_minute": get_current_minute_ts(),
        "trading_symbols": trading_symbols[:50],
        "hyperliquid_stats": hl_stats,
        "vol_15m_slot": vol_15m_slot,
        "vol_15m_current_count": len(vol_15m_current),
        "vol_15m_current_sample": dict(list(vol_15m_current.items())[:5]),
        "vol_15m_last_count": len(vol_15m_last),
        "vol_24h_today_count": len(vol_24h_today),
        "vol_surge_symbols_count": len(vol_surge_symbols),
        "vol_surge_symbols": vol_surge_symbols
    })

@app.route("/api/minute_buy_ratio/<symbol>")
def api_minute_buy_ratio(symbol: str):
    """返回指定币的分钟级主动买卖比"""
    symbol = symbol.upper()
    with data_lock:
        klines = market_data["minute_klines"].get(symbol, [])
    
    result = [{
        "t": k["t"],
        "buy_ratio": round(k.get("buy_ratio", 0.5), 3),
        "buy_q": round(k.get("buy_q", 0), 2),
        "sell_q": round(k.get("sell_q", 0), 2),
        "q": round(k.get("q", 0), 2),
        "o": k.get("o", 0),
        "c": k.get("c", 0),
        "l": k.get("l", 0),  # 最低价
        "h": k.get("h", 0),  # 最高价
        "gain": round((k.get("c", 0) - k.get("o", 0)) / k.get("o", 1) * 100, 2) if k.get("o", 0) > 0 else 0
    } for k in klines[-60:]]  # 最近60分钟
    
    return jsonify({"code": 0, "symbol": symbol, "count": len(result), "data": result})

@app.route("/api/surge")
def api_surge():
    """返回delta_q突增的币种列表（仅买>=70%）"""
    with data_lock:
        surge_cache = market_data.get("surge_cache", {})
    
    result = []
    now = time.time()
    for symbol, records in surge_cache.items():
        # 过滤30分钟内的记录
        recent = [r for r in records if now - r["t"] < SURGE_CACHE_MAX_MINUTES * 60]
        if recent:
            total_delta_q = sum(r["delta_q"] for r in recent)
            avg_buy_ratio = sum(r["buy_ratio"] for r in recent) / len(recent)
            # 大单追踪：只显示买>=70%（买方主导才显示）
            if avg_buy_ratio >= 0.7:
                result.append({
                    "symbol": symbol,
                    "count": len(recent),
                    "total_delta_q": round(total_delta_q, 2),
                    "avg_buy_ratio": round(avg_buy_ratio, 3),
                    "last_t": recent[-1]["t"],
                    "last_price": recent[-1]["price"]
                })
    
    result.sort(key=lambda x: -x["total_delta_q"])
    return jsonify({"code": 0, "count": len(result), "data": result[:50]})

@app.route("/api/vol_surge")
def api_vol_surge():
    """返回15分钟成交量突增的币种列表"""
    with data_lock:
        vol_surge_symbols = market_data.get("vol_surge_symbols", {})
        symbols = market_data.get("symbols", {})
        vol_15m_last = market_data.get("vol_15m_last", {})
    
    result = []
    now = time.time()
    for symbol, info in vol_surge_symbols.items():
        # 检查是否还在1小时内
        elapsed = now - info.get("start_time", 0)
        if elapsed > 3600:
            continue
        
        symbol_info = symbols.get(symbol, {})
        result.append({
            "symbol": symbol,
            "ratio": round(info.get("ratio", 0), 2),
            "vol_15m": info.get("vol", 0),
            "last_vol": info.get("last_vol", 0),
            "price": symbol_info.get("price", 0),
            "gain_pct": symbol_info.get("priceChangePercent", 0),
            "remaining_seconds": int(3600 - elapsed),
            "start_time": info.get("start_time", 0)
        })
    
    # 按突增倍数降序
    result.sort(key=lambda x: -x["ratio"])
    return jsonify({"code": 0, "count": len(result), "data": result})

# ========== WebSocket ==========
@socketio.on("connect", namespace="/")
def on_connect():
    import sys
    print("浏览器已连接", flush=True)
    sys.stdout.flush()
    data = get_table_data()
    socketio.emit("init_data", {"data": data, "count": len(data)})
    print(f"已发送init_data ({len(data)} 条)", flush=True)
    return True

@socketio.on("disconnect")
def on_disconnect():
    print("浏览器断开连接")

def ws_update_loop():
    """WebSocket接收并广播更新，同时记录秒级数据"""
    import websocket

    def on_message(ws, message):
        try:
            frame = json.loads(message)
            if not isinstance(frame, list):
                return

            current_minute = get_current_minute_ts()
            current_second = int(time.time())
            updates = []

            with data_lock:
                # 检查是否进入新的一分钟
                if market_data["current_minute"] is None:
                    market_data["current_minute"] = current_minute
                
                if current_minute > market_data["current_minute"]:
                    # 进入新分钟，聚合上一分钟
                    _aggregate_minute_kline()
                    market_data["current_minute"] = current_minute
                    # 重置分钟状态
                    market_data["minute_state"] = {}

                for item in frame:
                    symbol = item.get("s", "")
                    if not symbol.endswith("USDT"):
                        continue
                    # 过滤非TRADING状态的币种
                    ts = market_data.get("trading_symbols", set())
                    if ts and symbol not in ts:
                        continue

                    q = float(item.get("q", 0))
                    price = float(item.get("c", 0))

                    # 如果币种不存在，自动添加
                    if symbol not in market_data["symbols"]:
                        # 注意：此处已经在 data_lock 保护范围内，不需要再次加锁
                        # 获取北京8点开盘价（如果有）
                        open_price = market_data.get("today_open_prices", {}).get(symbol, price)
                        
                        # 计算北京8点起的涨跌幅
                        if open_price > 0:
                            gain_pct = (price - open_price) / open_price * 100
                        else:
                            gain_pct = 0
                        
                        market_data["symbols"][symbol] = {
                            "price": price,
                            "q": q,
                            "v": float(item.get("v", 0)),
                            "o": open_price,  # 北京8点开盘价
                            "h": float(item.get("h", price)),
                            "l": float(item.get("l", price)),
                            "priceChangePercent": gain_pct,  # 北京8点起的涨跌幅
                            "updated_at": time.time()
                        }
                        market_data["last_q"][symbol] = q
                        print(f"[WebSocket] 新增币种: {symbol}")
                        continue

                    if symbol in market_data["symbols"]:
                        # 记录 delta_q
                        last_q = market_data["last_q"].get(symbol, q)
                        delta_q = q - last_q
                        # 如果 delta_q 为负，说明币安24h滚动统计重置了（每天北京8点）
                        # 此时直接用当前 q 作为这一段的累计值
                        if delta_q < 0:
                            delta_q = q
                        market_data["last_q"][symbol] = q

                        # 存入秒数据
                        if current_second not in market_data["second_deltas"]:
                            market_data["second_deltas"][current_second] = {}
                        market_data["second_deltas"][current_second][symbol] = delta_q

                        # 更新实时价格（只更新价格，不修改累计成交额）
                        old_price = market_data["symbols"][symbol].get("price", 0)
                        open_price = market_data["symbols"][symbol].get("o", 0)
                        
                        # 计算新的涨跌幅（基于北京8点开盘价）
                        if open_price > 0:
                            new_gain_pct = (price - open_price) / open_price * 100
                        else:
                            new_gain_pct = 0
                        
                        market_data["symbols"][symbol].update({
                            "price": price,
                            "q": q,  # 实时更新24h滚动成交额
                            "v": float(item.get("v", 0)),  # 实时更新24h成交量
                            "h": float(item.get("h", price)),  # 实时更新24h最高
                            "l": float(item.get("l", price)),  # 实时更新24h最低
                            "priceChangePercent": new_gain_pct,  # 基于北京8点的涨跌幅
                            "updated_at": time.time()
                        })

                        # 更新当前分钟状态
                        if symbol not in market_data["minute_state"]:
                            market_data["minute_state"][symbol] = {
                                "open": price,
                                "high": price,
                                "low": price,
                                "close": price,
                                "vol": 0,
                                "q": 0,
                                "first_price": price,
                                "buy_q": 0,
                                "sell_q": 0,
                                "last_price": price
                            }
                        
                        ms = market_data["minute_state"][symbol]
                        ms["high"] = max(ms["high"], price)
                        ms["low"] = min(ms["low"], price)
                        ms["close"] = price
                        ms["q"] += delta_q
                        
                        # ===== 新增：计算 buy_ratio 并估算买卖成交额 =====
                        price_delta = price - ms["last_price"]
                        buy_ratio = _calculate_buy_ratio(price_delta, ms["last_price"])
                        
                        # 更新买卖估算
                        if delta_q > 0:
                            ms["buy_q"] += delta_q * buy_ratio
                            ms["sell_q"] += delta_q * (1 - buy_ratio)
                        
                        ms["last_price"] = price
                        
                        # ===== delta_q 突增检测（仅买方主导>=70%）=====
                        if delta_q > DELTA_Q_SURGE_THRESHOLD and symbol not in SURGE_EXCLUDE_SYMBOLS:
                            if buy_ratio >= 0.7:
                                if symbol not in market_data["surge_cache"]:
                                    market_data["surge_cache"][symbol] = []
                                market_data["surge_cache"][symbol].append({
                                    "t": current_second,
                                    "delta_q": delta_q,
                                    "buy_ratio": buy_ratio,
                                    "price": price
                                })
                                import datetime
                                print(f"[SURGE] {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {symbol} delta_q={delta_q:,.0f} buy_ratio={buy_ratio:.3f} price={price}")
                                # 清理超过30分钟的记录
                                market_data["surge_cache"][symbol] = [
                                    r for r in market_data["surge_cache"][symbol]
                                    if current_second - r["t"] < SURGE_CACHE_MAX_MINUTES * 60
                                ]

                        updates.append({
                            "symbol": symbol,
                            "q": q,
                            "price": price
                        })

            if updates:
                socketio.emit("ws_update", {"data": updates}, namespace="/")
        except Exception as e:
            print(f"WebSocket消息处理失败: {e}")

    def on_error(ws, error):
        print(f"WebSocket错误: {error}")

    def on_close(ws, code, reason):
        print(f"WebSocket关闭: {code} {reason}")

    def on_open(ws):
        print("WebSocket连接成功！")

    ws = websocket.WebSocketApp(
        "wss://fstream.binance.com/ws/!miniTicker@arr",
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
        on_open=on_open
    )
    print("WebSocket连接中...")
    ws.run_forever(ping_interval=20, ping_timeout=10, reconnect=5)


def hyperliquid_ws_loop():
    """Hyperliquid WebSocket 数据流 - 订阅所有币种的 trades"""
    if not USE_HYPERLIQUID:
        print("[Hyperliquid WS] 未启用，跳过")
        return
    
    import websocket as ws_module
    from utils.trades_aggregator import get_aggregator
    aggregator = get_aggregator()
    
    # 获取所有交易对
    try:
        meta = fetch_hyperliquid_meta()
        all_coins = [asset["name"] for asset in meta.get("universe", [])]
        print(f"[Hyperliquid WS] 获取到 {len(all_coins)} 个交易对")
    except Exception as e:
        print(f"[Hyperliquid WS] 获取交易对失败: {e}")
        return
    
    # 获取当前价格用于初始化新币种
    all_mids = {}
    try:
        all_mids = fetch_hyperliquid_all_mids()
        print(f"[Hyperliquid WS] 获取到 {len(all_mids)} 个币种价格")
    except Exception as e:
        print(f"[Hyperliquid WS] 获取价格失败: {e}")
    
    def on_message(ws, message):
        try:
            data = json.loads(message)
            
            if data.get("channel") == "subscriptionResponse":
                return
            
            if data.get("channel") == "trades":
                trades = data.get("data", [])
                for trade in trades:
                    aggregator.add_trade(trade)
                    
                    # 自动补充新币种到 symbols
                    coin = trade.get("coin")
                    if coin:
                        symbol = f"{coin}USDT"
                        with data_lock:
                            symbols = market_data.get("symbols", {})
                            if symbol not in symbols:
                                # 从 all_mids 获取价格
                                price = float(all_mids.get(coin, trade.get("px", 0)))
                                if price > 0:
                                    symbols[symbol] = {
                                        "q": 0,
                                        "v": 0,
                                        "price": price,
                                        "o": price,
                                        "h": price,
                                        "l": price,
                                        "priceChangePercent": 0,
                                        "updated_at": time.time()
                                    }
                                    market_data["symbols"] = symbols
                    
        except Exception as e:
            print(f"[Hyperliquid WS] 消息处理错误: {e}")
    
    def on_error(ws, error):
        print(f"[Hyperliquid WS] 错误: {error}")
    
    def on_close(ws, code, reason):
        print(f"[Hyperliquid WS] 关闭: {code} {reason}")
    
    def on_open(ws):
        print("[Hyperliquid WS] 已连接，开始订阅...")
        # 订阅所有币种
        for coin in all_coins:
            try:
                ws.send(json.dumps({
                    "method": "subscribe",
                    "subscription": {"type": "trades", "coin": coin}
                }))
            except Exception as e:
                print(f"[Hyperliquid WS] 订阅 {coin} 失败: {e}")
        print(f"[Hyperliquid WS] 已订阅 {len(all_coins)} 个币种")
    
    ws = ws_module.WebSocketApp(
        "wss://api.hyperliquid.xyz/ws",
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )
    ws.run_forever(ping_interval=20, ping_timeout=10, reconnect=5)


def _aggregate_minute_kline():
    """聚合上一分钟的K线数据"""
    try:
        minute_ts = market_data["current_minute"]
        minute_state = market_data["minute_state"]
        
        # 找出上一分钟内的所有秒级key
        minute_start = minute_ts
        minute_end = minute_ts + 60
        
        # 收集上一分钟的所有秒数据
        minute_seconds_data = {}
        second_deltas_keys_to_delete = []
        
        for sec_ts, sec_data in market_data["second_deltas"].items():
            if minute_start <= sec_ts < minute_end:
                minute_seconds_data.update(sec_data)
                second_deltas_keys_to_delete.append(sec_ts)
        
        if not minute_seconds_data:
            print(f"分钟 {minute_ts} 无秒数据")
            return
        
        # 按symbol聚合
        symbol_totals = {}
        for symbol, delta_q in minute_seconds_data.items():
            if symbol not in symbol_totals:
                symbol_totals[symbol] = 0
            symbol_totals[symbol] += delta_q
        
        # 生成K线
        new_klines = []
        for symbol, total_q in symbol_totals.items():
            if symbol not in minute_state:
                continue
            
            ms = minute_state[symbol]
            
            # 计算分钟级的 buy_ratio
            total_buy_q = ms.get("buy_q", 0)
            total_sell_q = ms.get("sell_q", 0)
            minute_buy_ratio = total_buy_q / (total_buy_q + total_sell_q) if (total_buy_q + total_sell_q) > 0 else 0.5
            
            kline = {
                "symbol": symbol,
                "t": minute_ts,
                "o": ms["first_price"],
                "h": ms["high"],
                "l": ms["low"],
                "c": ms["close"],
                "v": 0,  # 成交量暂时用0
                "q": total_q,  # 成交额
                "buy_q": round(total_buy_q, 2),
                "sell_q": round(total_sell_q, 2),
                "buy_ratio": round(minute_buy_ratio, 3)
            }
            
            if symbol not in market_data["minute_klines"]:
                market_data["minute_klines"][symbol] = []
            market_data["minute_klines"][symbol].append(kline)
            new_klines.append(kline)
        
        # 清理已聚合的秒数据
        for key in second_deltas_keys_to_delete:
            del market_data["second_deltas"][key]

        if new_klines:
            print(f"聚合分钟K线: {len(new_klines)} 条")

        # 15分钟区间统计
        current_15m_slot = get_current_15m_slot()
        last_15m_slot = market_data.get("vol_15m_slot")
        
        # 累加当前分钟的成交额到15分钟统计和今日累计
        for symbol, total_q in symbol_totals.items():
            # 累加到当前15分钟
            if symbol not in market_data["vol_15m_current"]:
                market_data["vol_15m_current"][symbol] = 0
            market_data["vol_15m_current"][symbol] += total_q
            
            # 累加到今日成交额
            if symbol not in market_data["vol_24h_today"]:
                market_data["vol_24h_today"][symbol] = 0
            market_data["vol_24h_today"][symbol] += total_q
        
        # 检查是否进入新的15分钟区间
        if last_15m_slot is None:
            market_data["vol_15m_slot"] = current_15m_slot
        elif current_15m_slot > last_15m_slot:
            # 15分钟区间结束，保存历史并检测突增
            print(f"[15M_SLOT] 区间 {last_15m_slot} 结束，保存历史并检测突增...")
            
            # 收集当前15分钟所有币种的成交额
            all_symbols = set(market_data.get("symbols", {}).keys())
            vol_data = {}
            for symbol in all_symbols:
                current_vol = market_data["vol_15m_current"].get(symbol, 0)
                vol_data[symbol] = current_vol
                market_data["vol_15m_last"][symbol] = current_vol
            
            # 写入COS（持久化）
            save_vol_15m_to_cos(last_15m_slot, vol_data)
            
            # 从COS加载最近4小时的严格时间窗口数据
            cos_history = load_vol_15m_from_cos(last_15m_slot, slots_count=16)
            
            # 合并到内存缓存（以slot_ts为key的字典）
            for symbol, slots in cos_history.items():
                if symbol not in market_data["vol_15m_history"]:
                    market_data["vol_15m_history"][symbol] = {}
                market_data["vol_15m_history"][symbol].update(slots)
                # 清理超过4小时的旧缓存
                cutoff_slot = last_15m_slot - 16 * 900
                for ts in list(market_data["vol_15m_history"][symbol].keys()):
                    if ts < cutoff_slot:
                        del market_data["vol_15m_history"][symbol][ts]
            
            # 严格4小时时间窗口计算均值（分母恒为16，缺失补0）
            for symbol in all_symbols:
                current_vol = vol_data.get(symbol, 0)
                avg_4h = calc_vol_15m_avg_strict(symbol, last_15m_slot, market_data["vol_15m_history"])
                market_data["vol_15m_avg_4h"][symbol] = avg_4h
                
                # 检测突增：当前15分钟 > 前4小时均值 × 1.5（且当前有成交）
                if current_vol > 0 and avg_4h > 0 and current_vol > avg_4h * 1.5:
                    check_volume_surge(symbol, current_vol, avg_4h)
            
            # 重置当前15分钟累计
            market_data["vol_15m_current"] = {}
            market_data["vol_15m_slot"] = current_15m_slot
            
            # 清理过期的突增记录
            cleanup_surge_records()

        # 同步更新 hourly_kline_cache：如果当前分钟是整小时的最后一分钟，聚合该小时
        if minute_ts > 0:
            next_minute_ts = minute_ts + 60
            # 检查下一分钟是否进入了新的小时
            current_hour = (minute_ts // 3600) * 3600
            next_hour = (next_minute_ts // 3600) * 3600
            if next_hour > current_hour:
                # 刚完成一个整小时，从minute_klines中聚合该小时并更新hourly_kline_cache
                _update_hourly_cache_for_hour(current_hour)
    except Exception as e:
        print(f"聚合失败: {e}")

def _update_hourly_cache_for_hour(hour_ts: int):
    """从minute_klines中聚合指定小时的数据，更新hourly_kline_cache"""
    hourly_cache = market_data.get("hourly_kline_cache", {})
    minute_klines = market_data.get("minute_klines", {})

    for symbol, klines in minute_klines.items():
        # 找出属于该小时的分钟K线
        hour_minutes = [k for k in klines if (k["t"] // 3600) * 3600 == hour_ts]
        if len(hour_minutes) < 2:  # 至少2分钟数据才算有效
            continue

        buy_q = sum(k.get("buy_q", 0) for k in hour_minutes)
        sell_q = sum(k.get("sell_q", 0) for k in hour_minutes)
        total_q = buy_q + sell_q

        hourly_kline = {
            "t": hour_ts,
            "o": hour_minutes[0]["o"],
            "h": max(k["h"] for k in hour_minutes),
            "l": min(k["l"] for k in hour_minutes),
            "c": hour_minutes[-1]["c"],
            "v": sum(k.get("v", 0) for k in hour_minutes),
            "q": sum(k.get("q", 0) for k in hour_minutes),
            "buy_q": buy_q,
            "sell_q": sell_q,
            "buy_ratio": buy_q / total_q if total_q > 0 else 0.5,
        }

        if symbol not in hourly_cache:
            hourly_cache[symbol] = []
        # 按时间戳去重：替换已有的或追加新的
        existing = {h["t"]: i for i, h in enumerate(hourly_cache[symbol])}
        if hour_ts in existing:
            hourly_cache[symbol][existing[hour_ts]] = hourly_kline
        else:
            hourly_cache[symbol].append(hourly_kline)
            hourly_cache[symbol].sort(key=lambda x: x["t"])

    market_data["hourly_kline_cache"] = hourly_cache

def minute_aggregator_loop():
    """每分钟触发聚合，并上传COS"""
    last_save_time = time.time()
    last_check_minute = None
    
    while True:
        time.sleep(1)
        
        # 每5分钟上传一次COS（必须在continue之前）
        if time.time() - last_save_time >= 300:
            print(f"[COS] 5分钟定时上传触发")
            with data_lock:
                klines_copy = dict(market_data["minute_klines"])
            save_minute_klines_to_cos(klines_copy)
            _save_hourly_cache_to_cos()
            last_save_time = time.time()
        
        current_minute = get_current_minute_ts()
        
        # 每秒检查是否进入新分钟（只在秒变化时检查）
        if current_minute == last_check_minute:
            continue
        last_check_minute = current_minute
        
        with data_lock:
            if market_data["current_minute"] is not None and current_minute > market_data["current_minute"]:
                # 触发聚合
                _aggregate_minute_kline()
                market_data["current_minute"] = current_minute
                market_data["minute_state"] = {}
                
                # 清理超过12天的分钟K线数据
                cutoff = time.time() - (MAX_MINUTE_KLINES * 60)
                for symbol in list(market_data["minute_klines"].keys()):
                    market_data["minute_klines"][symbol] = [
                        k for k in market_data["minute_klines"][symbol]
                        if k.get("t", 0) >= cutoff
                    ]
                
                # 清理超过15分钟的大单追踪记录
                surge_cutoff = time.time() - (SURGE_CACHE_MAX_MINUTES * 60)
                for symbol in list(market_data["surge_cache"].keys()):
                    market_data["surge_cache"][symbol] = [
                        r for r in market_data["surge_cache"][symbol]
                        if r["t"] >= surge_cutoff
                    ]
                    if not market_data["surge_cache"][symbol]:
                        del market_data["surge_cache"][symbol]
                
                # 清理超过2分钟的秒级delta数据（防止旧数据堆积）
                second_cutoff = time.time() - 120
                for sec_ts in list(market_data["second_deltas"].keys()):
                    if sec_ts < second_cutoff:
                        del market_data["second_deltas"][sec_ts]
                
                # 清理 hourly_kline_cache 只保留最近7天（168小时）
                hour_cutoff = (int(time.time()) // 3600 - 168) * 3600
                for symbol in list(market_data.get("hourly_kline_cache", {}).keys()):
                    market_data["hourly_kline_cache"][symbol] = [
                        h for h in market_data["hourly_kline_cache"][symbol]
                        if h.get("t", 0) >= hour_cutoff
                    ]

# ========== 定时写入 ==========
def write_loop():
    while True:
        time.sleep(WRITE_INTERVAL_SECONDS)
        save_snapshot()
        # 同步写入COS快照（策略数据持久化）
        save_symbols_snapshot_to_cos()
        save_vol_24h_today_to_cos()
        save_today_open_prices_to_cos()

# ========== 模拟交易状态 ==========
SIM_TRADE_STATE_FILE = "/tmp/sim_trade_state.json"

def load_sim_trade_state():
    """读取模拟交易状态文件，并用当前市场价格计算实时盈亏（带文件锁）"""
    try:
        if not os.path.exists(SIM_TRADE_STATE_FILE):
            return None
        with open(SIM_TRADE_STATE_FILE, "r") as f:
            try:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                state = json.load(f)
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except ImportError:
                state = json.load(f)
            except Exception:
                state = json.load(f)
        
        # 用当前 market_data 价格计算实时盈亏
        with data_lock:
            symbols = market_data.get("symbols", {})
        
        positions = state.get("positions", [])
        total_unrealized_pnl = 0
        total_margin = 0
        
        for pos in positions:
            symbol = pos.get("symbol", "")
            entry_price = pos.get("entry_price", 0)
            quantity = pos.get("quantity", 0)
            margin = pos.get("margin", 0)
            
            current_info = symbols.get(symbol, {})
            current_price = current_info.get("price", entry_price)
            
            if current_price > 0 and entry_price > 0:
                pnl = (current_price - entry_price) * quantity
                pnl_pct = (current_price - entry_price) / entry_price * 100
            else:
                pnl = 0
                pnl_pct = 0
            
            pos["current_price"] = current_price
            pos["unrealized_pnl"] = round(pnl, 4)
            pos["unrealized_pnl_pct"] = round(pnl_pct, 2)
            
            # 状态判断
            if current_price >= pos.get("take_profit_price", float('inf')):
                pos["status"] = "止盈"
                pos["status_color"] = "profit"
            elif current_price <= pos.get("liquidation_price", 0):
                pos["status"] = "爆仓"
                pos["status_color"] = "liquidation"
            elif current_price <= pos.get("stop_loss_price", 0):
                pos["status"] = "止损"
                pos["status_color"] = "loss"
            else:
                pos["status"] = "持仓中"
                pos["status_color"] = "hold"
            
            total_unrealized_pnl += pnl
            total_margin += margin
        
        account = state.get("account", {})
        effective_balance = account.get("balance", 0) + total_unrealized_pnl
        
        return {
            "account": account,
            "positions": positions,
            "summary": {
                "positions_count": len(positions),
                "total_unrealized_pnl": round(total_unrealized_pnl, 4),
                "total_margin": round(total_margin, 2),
                "effective_balance": round(effective_balance, 2),
                "max_positions": 5,
            }
        }
    except Exception as e:
        print(f"[SIM_TRADE] 读取状态失败: {e}")
        return None


@app.route("/api/sim_trade")
def api_sim_trade():
    """返回模拟交易实时状态"""
    state = load_sim_trade_state()
    if state is None:
        return jsonify({"code": 1, "msg": "无模拟交易数据"})
    return jsonify({"code": 0, "data": state})


def sim_trade_broadcast_loop():
    """每5秒推送模拟交易状态到所有连接的客户端"""
    while True:
        try:
            time.sleep(5)
            state = load_sim_trade_state()
            if state:
                socketio.emit("sim_trade_update", state, namespace="/")
        except Exception as e:
            print(f"[SIM_TRADE] 广播失败: {e}")


# ========== 每日北京时间08:00从WebSocket捕获今日开盘价 ==========
def capture_today_open_from_ws():
    """从WebSocket实时数据中捕获当前价格作为今日开盘价（无需API）"""
    with data_lock:
        symbols = market_data.get("symbols", {})
        today_open = {}
        for symbol, info in symbols.items():
            price = info.get("price", 0)
            if price > 0:
                today_open[symbol] = price
    
    with data_lock:
        market_data["today_open_prices"] = today_open
        market_data["today_open_updated"] = time.time()
    
    print(f"今日开盘价已捕获: {len(today_open)} 个币 (直接从WebSocket)")

def daily_open_price_update_loop():
    """每天北京时间08:00（UTC 00:00）从WebSocket捕获今日开盘价"""
    while True:
        now = datetime.now(tz=timezone.utc)
        # 下一个 UTC 00:00 就是北京时间 08:00
        next_utc_midnight = datetime(now.year, now.month, now.day, tzinfo=timezone.utc) + timedelta(days=1)
        seconds_until = (next_utc_midnight - now).total_seconds()
        
        if seconds_until > 0:
            print(f"等待北京08:00... ({seconds_until/3600:.1f}小时后更新今日开盘价)")
            time.sleep(seconds_until)
        
        # 北京时间08:00了，捕获今日开盘价
        print("=== 北京时间08:00，捕获今日开盘价 ===")
        capture_today_open_from_ws()
        
        # 保存今日开盘价到COS
        save_today_open_prices_to_cos()
        
        # 重置今日累计成交额
        print("=== 重置今日累计成交额 ===")
        with data_lock:
            market_data["vol_24h_today"] = {}
            print(f"已重置 {len(market_data['symbols'])} 个币种的今日累计成交额")
        
        # 清空COS中的昨日vol_24h_today（写入空数据）
        save_vol_24h_today_to_cos()

def hyperliquid_backfill_loop():
    """后台补充缺失的币种数据"""
    if not USE_HYPERLIQUID:
        return
    
    time.sleep(30)  # 等待初始化完成
    
    while True:
        try:
            time.sleep(60)  # 每分钟检查一次
            
            with data_lock:
                symbols = market_data.get("symbols", {})
                trading_symbols = market_data.get("trading_symbols", set())
            
            # 找出缺失的币种
            missing_symbols = trading_symbols - set(symbols.keys())
            
            if not missing_symbols:
                continue
            
            print(f"[BACKFILL] 发现 {len(missing_symbols)} 个缺失币种，尝试补充...")
            
            # 获取当前价格
            mids = fetch_hyperliquid_all_mids()
            if not mids:
                continue
            
            # 补充缺失的币种（每次最多10个）
            added = 0
            for symbol in list(missing_symbols)[:10]:
                coin = symbol.replace("USDT", "")
                price = float(mids.get(coin, 0))
                if price > 0:
                    with data_lock:
                        market_data["symbols"][symbol] = {
                            "q": 0,
                            "v": 0,
                            "price": price,
                            "o": price,
                            "h": price,
                            "l": price,
                            "priceChangePercent": 0,
                            "updated_at": time.time()
                        }
                    added += 1
                time.sleep(0.5)  # 避免频率限制
            
            if added > 0:
                print(f"[BACKFILL] 补充了 {added} 个币种")
                
        except Exception as e:
            print(f"[BACKFILL] 补充失败: {e}")

# ========== 回溯API ==========
@app.route("/api/backtest/bollinger_climb", methods=["POST"])
def api_backtest_bollinger_climb():
    """布林爬坡策略回溯API
    
    请求参数:
    {
        "symbol": "HIGHUSDT",
        "start_time": "2026-04-17 00:00:00",
        "end_time": "2026-04-19 00:00:00",
        "config": {}  # 可选
    }
    """
    try:
        data = request.get_json() or {}
        symbol = data.get("symbol", "").upper()
        start_time = data.get("start_time", "")
        end_time = data.get("end_time")
        config = data.get("config", {})
        
        if not symbol:
            return jsonify({"code": 1, "msg": "缺少symbol参数"})
        if not start_time:
            return jsonify({"code": 1, "msg": "缺少start_time参数"})
        
        if not symbol.endswith("USDT"):
            symbol += "USDT"
        
        from core.backtest_bollinger import run_bollinger_climb_backtest
        
        result = run_bollinger_climb_backtest(
            symbol=symbol,
            start_time=start_time,
            end_time=end_time,
            config=config
        )
        
        return jsonify({"code": 0, "data": result})
        
    except Exception as e:
        print(f"布林爬坡回溯失败: {e}")
        return jsonify({"code": 1, "msg": str(e)})


@app.route("/api/backtest/bollinger_candidate", methods=["POST"])
def api_backtest_bollinger_candidate():
    """候选蓄力策略回溯API
    
    请求参数:
    {
        "symbol": "HIGHUSDT",
        "start_time": "2026-04-17 00:00:00",
        "end_time": "2026-04-19 00:00:00",
        "config": {}  # 可选
    }
    """
    try:
        data = request.get_json() or {}
        symbol = data.get("symbol", "").upper()
        start_time = data.get("start_time", "")
        end_time = data.get("end_time")
        config = data.get("config", {})
        
        if not symbol:
            return jsonify({"code": 1, "msg": "缺少symbol参数"})
        if not start_time:
            return jsonify({"code": 1, "msg": "缺少start_time参数"})
        
        if not symbol.endswith("USDT"):
            symbol += "USDT"
        
        from core.backtest_bollinger import run_bollinger_candidate_backtest
        
        result = run_bollinger_candidate_backtest(
            symbol=symbol,
            start_time=start_time,
            end_time=end_time,
            config=config
        )
        
        return jsonify({"code": 0, "data": result})
        
    except Exception as e:
        print(f"候选蓄力回溯失败: {e}")
        return jsonify({"code": 1, "msg": str(e)})


@app.route("/api/backtest/batch", methods=["POST"])
def api_backtest_batch():
    """批量回溯多个币种
    
    请求参数:
    {
        "symbols": ["HIGHUSDT", "ORDIUSDT"],
        "start_time": "2026-04-17 00:00:00",
        "end_time": "2026-04-19 00:00:00",
        "strategy": "bollinger_climb"  # 或 "bollinger_candidate"
    }
    """
    try:
        data = request.get_json() or {}
        symbols = data.get("symbols", [])
        start_time = data.get("start_time", "")
        end_time = data.get("end_time")
        strategy = data.get("strategy", "bollinger_climb")
        
        if not symbols:
            return jsonify({"code": 1, "msg": "缺少symbols参数"})
        if not start_time:
            return jsonify({"code": 1, "msg": "缺少start_time参数"})
        
        from core.backtest_bollinger import run_bollinger_climb_backtest, run_bollinger_candidate_backtest
        
        run_func = run_bollinger_climb_backtest if strategy == "bollinger_climb" else run_bollinger_candidate_backtest
        
        results = []
        for symbol in symbols:
            symbol = symbol.upper()
            if not symbol.endswith("USDT"):
                symbol += "USDT"
            
            try:
                result = run_func(
                    symbol=symbol,
                    start_time=start_time,
                    end_time=end_time
                )
                results.append({
                    "symbol": symbol,
                    "signals_count": result.get("summary", {}).get("total_signals", 0),
                    "failed_checks_count": result.get("summary", {}).get("total_failed_checks", 0),
                    "signals": result.get("signals", [])[:3]
                })
            except Exception as e:
                results.append({
                    "symbol": symbol,
                    "error": str(e)
                })
        
        return jsonify({
            "code": 0,
            "data": {
                "strategy": strategy,
                "start_time": start_time,
                "end_time": end_time,
                "results": results
            }
        })
        
    except Exception as e:
        print(f"批量回溯失败: {e}")
        return jsonify({"code": 1, "msg": str(e)})


@app.route("/api/backtest/debug", methods=["POST"])
def api_backtest_debug():
    """调试指定时间点的信号条件
    
    请求参数:
    {
        "symbol": "HIGHUSDT",
        "timestamp": "2026-04-18 05:00:00",
        "strategy": "bollinger_climb"
    }
    
    返回所有条件的检查结果，不只是第一个失败的
    """
    try:
        data = request.get_json() or {}
        symbol = data.get("symbol", "").upper()
        timestamp = data.get("timestamp", "")
        strategy = data.get("strategy", "bollinger_climb")
        
        if not symbol:
            return jsonify({"code": 1, "msg": "缺少symbol参数"})
        if not timestamp:
            return jsonify({"code": 1, "msg": "缺少timestamp参数"})
        
        if not symbol.endswith("USDT"):
            symbol += "USDT"
        
        from core.backtest_bollinger import debug_check_at_timestamp
        
        with data_lock:
            hourly_cache = market_data.get("hourly_kline_cache", {})
        
        result = debug_check_at_timestamp(
            symbol=symbol,
            timestamp=timestamp,
            strategy=strategy,
            hourly_kline_cache=hourly_cache
        )
        
        return jsonify({"code": 0, "data": result})
        
    except Exception as e:
        print(f"调试检查失败: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"code": 1, "msg": str(e)})


@app.route("/api/backtest/top_symbols", methods=["GET"])
def api_backtest_top_symbols():
    """获取成交额前N的币种列表，用于批量回溯"""
    try:
        n = int(request.args.get("n", 20))
        
        with data_lock:
            symbols = market_data.get("symbols", {})
        
        sorted_symbols = sorted(symbols.items(), key=lambda x: x[1].get("q", 0), reverse=True)
        top_symbols = [s[0] for s in sorted_symbols[:n]]
        
        return jsonify({
            "code": 0,
            "data": {
                "symbols": top_symbols,
                "count": len(top_symbols)
            }
        })
        
    except Exception as e:
        return jsonify({"code": 1, "msg": str(e)})


# ========== 启动 ==========
if __name__ == "__main__":
    init_market_data()
    
    # 尝试从COS加载历史K线
    try:
        historical_klines = load_minute_klines_from_cos()
        with data_lock:
            if historical_klines:
                market_data["minute_klines"] = historical_klines
    except Exception as e:
        print(f"加载历史K线失败: {e}")
    
    # 从COS加载最近4小时的15分钟成交量历史
    try:
        current_slot = get_current_15m_slot()
        vol_15m_hist = load_vol_15m_from_cos(current_slot, slots_count=16)
        with data_lock:
            for symbol, slots in vol_15m_hist.items():
                market_data["vol_15m_history"][symbol] = slots
            print(f"[VOL_15M_COS] 启动时加载了 {len(vol_15m_hist)} 个币种的15分钟历史")
    except Exception as e:
        print(f"[VOL_15M_COS] 启动加载失败: {e}")
    
    # 启动线程
    threading.Thread(target=ws_update_loop, daemon=True).start()
    threading.Thread(target=hyperliquid_ws_loop, daemon=True).start()  # Hyperliquid WebSocket
    threading.Thread(target=write_loop, daemon=True).start()
    threading.Thread(target=minute_aggregator_loop, daemon=True).start()
    threading.Thread(target=daily_open_price_update_loop, daemon=True).start()
    threading.Thread(target=hyperliquid_backfill_loop, daemon=True).start()  # 后台补充缺失币种
    threading.Thread(target=sim_trade_broadcast_loop, daemon=True).start()  # 模拟交易状态广播
    threading.Thread(target=bollinger_climb_background_loop, daemon=True).start()  # 布林爬坡缓存刷新

    # 优先从COS加载1h K线缓存，不够再API回填
    hourly_cache = _load_hourly_cache_from_cos()
    if hourly_cache and len(hourly_cache) >= 100:
        # 清理非TRADING状态的币种
        trading_symbols = market_data.get("trading_symbols", set())
        if trading_symbols:
            before = len(hourly_cache)
            hourly_cache = {s: v for s, v in hourly_cache.items() if s in trading_symbols}
            removed = before - len(hourly_cache)
            if removed:
                print(f"[HOURLY-COS] 清理了 {removed} 个非TRADING币种的1h缓存")
        with data_lock:
            market_data["hourly_kline_cache"] = hourly_cache
            market_data["bb_backfill_done"] = True
        print(f"[HOURLY-COS] 直接从COS加载了 {len(hourly_cache)} 个币种的1h K线，无需API回填")

        # 用1h K线更新今日开盘价（更精确）
        _update_today_open_from_hourly_cache(hourly_cache)
    else:
        print("[HOURLY-COS] COS无1h缓存或数据不足，启动API回填...")
        threading.Thread(target=backfill_hourly_klines, daemon=True).start()

    print(f"=" * 50)
    print(f"行情监控服务启动: http://0.0.0.0:{PORT}")
    print(f"分钟K线存储: COS ({COS_KEY})")
    print(f"delta_q突增阈值: {DELTA_Q_SURGE_THRESHOLD/1e6:.1f}M USDT")
    print("=" * 50)
    socketio.run(app, host="0.0.0.0", port=PORT, debug=False, allow_unsafe_werkzeug=True)
