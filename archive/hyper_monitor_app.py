#!/usr/bin/env python3
"""
Hyperliquid 大币逐笔监控 - 独立 Flask 服务
端口: 5003
功能: 逐笔成交聚合、分钟K线、精确buy_ratio、COS持久化
只监控: BTC, ETH, SOL, BNB
"""
import os
import io
import json
import time
import threading
import traceback
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from dotenv import load_dotenv
load_dotenv('/home/ubuntu/crypto-scanner/.env')

from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO
import pandas as pd

# ========== 配置 ==========
PORT = 5003
HYPER_WS_URL = "wss://api.hyperliquid.xyz/ws"

# 只监控这4个大币
MONITOR_COINS = {"BTC", "ETH", "SOL", "BNB"}
COIN_SYMBOLS = {c: f"{c}USDT" for c in MONITOR_COINS}

# COS 配置
COS_REGION = os.environ.get('COS_REGION', 'ap-seoul')
COS_ENDPOINT = os.environ.get('COS_ENDPOINT', 'cos.ap-seoul.myqcloud.com')
COS_SECRET_ID = os.environ.get('COS_SECRET_ID', '')
COS_SECRET_KEY = os.environ.get('COS_SECRET_KEY', '')
COS_BUCKET = os.environ.get('COS_BUCKET', '')
COS_KLINE_PREFIX = "hyper_klines/minute_"

# 本地备份
LOCAL_KLINE_FILE = "/tmp/hyper_minute_klines.parquet"

# 聚合配置
MAX_MINUTE_KLINES = 24 * 60  # 保留最近1天分钟K线
MAX_RAW_TRADES = 5000        # 每币种最多保留多少条原始成交

# ========== Flask ==========
app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# ========== COS 客户端 ==========
_cos_client = None

def get_cos_client():
    global _cos_client
    current_sid = os.environ.get('COS_SECRET_ID', '')
    if _cos_client is None or (COS_SECRET_ID != current_sid and current_sid):
        from qcloud_cos import CosConfig, CosS3Client
        sid = current_sid or COS_SECRET_ID
        skey = os.environ.get('COS_SECRET_KEY', '') or COS_SECRET_KEY
        region = os.environ.get('COS_REGION', COS_REGION)
        endpoint = os.environ.get('COS_ENDPOINT', COS_ENDPOINT)
        if not sid or not skey:
            return None
        cos_config = CosConfig(Region=region, SecretId=sid, SecretKey=skey, Endpoint=endpoint)
        _cos_client = CosS3Client(cos_config)
    return _cos_client


def _get_kline_key(ts: int) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return f"{COS_KLINE_PREFIX}{dt.year}{dt.month:02d}.parquet"


def save_klines_to_cos(klines: list):
    """将分钟K线写入COS（按月份分文件）"""
    if not klines:
        return
    try:
        if not COS_SECRET_ID or not COS_SECRET_KEY or not COS_BUCKET:
            return
        client = get_cos_client()
        if client is None:
            return

        # 按月份分组
        by_month = {}
        for k in klines:
            key = _get_kline_key(k["t"])
            by_month.setdefault(key, []).append(k)

        for key, month_klines in by_month.items():
            new_df = pd.DataFrame(month_klines)
            # 去重标识
            new_df = new_df.drop_duplicates(subset=["symbol", "t"], keep="last")

            try:
                resp = client.get_object(Bucket=COS_BUCKET, Key=key)
                old_data = resp['Body'].get_raw_stream().read()
                old_df = pd.read_parquet(io.BytesIO(old_data))
                # 去重：同一 symbol+t 只保留新数据
                old_df = old_df[~old_df.set_index(["symbol", "t"]).index.isin(
                    new_df.set_index(["symbol", "t"]).index
                )]
                merged_df = pd.concat([old_df, new_df], ignore_index=True)
            except Exception:
                merged_df = new_df

            # 滚动清理：保留最近30天
            cutoff = int(time.time()) - 30 * 24 * 3600
            merged_df = merged_df[merged_df["t"] >= cutoff]

            buffer = io.BytesIO()
            merged_df.to_parquet(buffer, index=False)
            buffer.seek(0)
            client.put_object(Bucket=COS_BUCKET, Key=key, Body=buffer.read())
            print(f"[HYPER_COS] 已上传 {len(new_df)} 条K线到 {key}")
    except Exception as e:
        print(f"[HYPER_COS] 上传失败: {e}")


def load_recent_klines_from_cos(hours: int = 24) -> list:
    """从COS加载最近N小时的K线"""
    result = []
    try:
        if not COS_SECRET_ID or not COS_SECRET_KEY or not COS_BUCKET:
            return result
        client = get_cos_client()
        if client is None:
            return result

        now = int(time.time())
        cutoff = now - hours * 3600

        # 需要加载的月份
        keys_to_fetch = set()
        current = datetime.fromtimestamp(cutoff, tz=timezone.utc)
        end = datetime.fromtimestamp(now, tz=timezone.utc)
        ym = datetime(current.year, current.month, 1, tzinfo=timezone.utc)
        end_ym = datetime(end.year, end.month, 1, tzinfo=timezone.utc)
        while ym <= end_ym:
            keys_to_fetch.add(f"{COS_KLINE_PREFIX}{ym.year}{ym.month:02d}.parquet")
            if ym.month == 12:
                ym = datetime(ym.year + 1, 1, 1, tzinfo=timezone.utc)
            else:
                ym = datetime(ym.year, ym.month + 1, 1, tzinfo=timezone.utc)

        all_dfs = []
        for key in keys_to_fetch:
            try:
                resp = client.get_object(Bucket=COS_BUCKET, Key=key)
                data = resp['Body'].get_raw_stream().read()
                df = pd.read_parquet(io.BytesIO(data))
                all_dfs.append(df)
            except Exception:
                pass

        if all_dfs:
            merged = pd.concat(all_dfs, ignore_index=True)
            merged = merged[merged["t"] >= cutoff]
            merged = merged[merged["symbol"].isin(COIN_SYMBOLS.values())]
            result = merged.to_dict("records")
    except Exception as e:
        print(f"[HYPER_COS] 加载失败: {e}")
    return result


# ========== 数据模型 ==========
class HyperAggregator:
    """逐笔成交聚合器 - 精确到每笔的买卖分析"""

    def __init__(self):
        self.lock = threading.Lock()
        # 原始成交: {coin: {minute_ts: [trade, ...]}}
        self.raw_trades: Dict[str, Dict[int, List[dict]]] = {}
        # 分钟K线: {symbol: {minute_ts: kline}}
        self.minute_klines: Dict[str, Dict[int, dict]] = {}
        # 当前分钟实时统计: {symbol: {open, high, low, close, buy_q, sell_q, buy_v, sell_v, n, last_px}}
        self.current_minute_state: Dict[str, dict] = {}
        # 当前分钟时间戳
        self.current_minute_ts: int = 0
        # 统计
        self.stats = {
            "ws_connected": False,
            "ws_connected_at": 0,
            "total_trades": 0,
            "last_trade_at": 0,
        }

    def add_trade(self, trade: dict):
        """添加逐笔成交"""
        coin = trade.get("coin")
        if coin not in MONITOR_COINS:
            return

        symbol = COIN_SYMBOLS[coin]
        px = float(trade.get("px", 0))
        sz = float(trade.get("sz", 0))
        side = trade.get("side")  # "B" 或 "A"
        ts_ms = trade.get("time", int(time.time() * 1000))
        minute_ts = ts_ms // 1000 // 60 * 60

        with self.lock:
            self.stats["total_trades"] += 1
            self.stats["last_trade_at"] = time.time()

            # 如果进入新分钟，先结算上一分钟
            if self.current_minute_ts == 0:
                self.current_minute_ts = minute_ts
            elif minute_ts > self.current_minute_ts:
                self._finalize_minute(self.current_minute_ts)
                self.current_minute_ts = minute_ts
                self.current_minute_state = {}

            # 存储原始成交
            if coin not in self.raw_trades:
                self.raw_trades[coin] = {}
            if minute_ts not in self.raw_trades[coin]:
                self.raw_trades[coin][minute_ts] = []
            self.raw_trades[coin][minute_ts].append({
                "px": px,
                "sz": sz,
                "side": side,
                "time": ts_ms,
            })

            # 限制原始数据量
            self._cleanup_raw_trades(coin)

            # 更新当前分钟实时状态
            if symbol not in self.current_minute_state:
                self.current_minute_state[symbol] = {
                    "open": px,
                    "high": px,
                    "low": px,
                    "close": px,
                    "buy_q": 0.0,
                    "sell_q": 0.0,
                    "buy_v": 0.0,
                    "sell_v": 0.0,
                    "n": 0,
                }

            st = self.current_minute_state[symbol]
            st["high"] = max(st["high"], px)
            st["low"] = min(st["low"], px)
            st["close"] = px
            st["n"] += 1

            amount = px * sz
            if side == "B":
                st["buy_q"] += amount
                st["buy_v"] += sz
            else:
                st["sell_q"] += amount
                st["sell_v"] += sz

    def _cleanup_raw_trades(self, coin: str):
        """清理过期的原始成交数据"""
        cutoff = self.current_minute_ts - MAX_MINUTE_KLINES * 60
        for ts in list(self.raw_trades.get(coin, {}).keys()):
            if ts < cutoff:
                del self.raw_trades[coin][ts]

    def _finalize_minute(self, minute_ts: int):
        """结算上一分钟的K线"""
        new_klines = []
        for coin, minutes in self.raw_trades.items():
            trades = minutes.get(minute_ts, [])
            if not trades:
                continue

            symbol = COIN_SYMBOLS[coin]
            prices = [t["px"] for t in trades]
            buy_trades = [t for t in trades if t["side"] == "B"]
            sell_trades = [t for t in trades if t["side"] == "A"]

            buy_q = sum(t["px"] * t["sz"] for t in buy_trades)
            sell_q = sum(t["px"] * t["sz"] for t in sell_trades)
            total_q = buy_q + sell_q
            buy_v = sum(t["sz"] for t in buy_trades)
            sell_v = sum(t["sz"] for t in sell_trades)
            total_v = buy_v + sell_v

            kline = {
                "symbol": symbol,
                "t": minute_ts,
                "o": trades[0]["px"],
                "h": max(prices),
                "l": min(prices),
                "c": trades[-1]["px"],
                "v": round(total_v, 8),
                "q": round(total_q, 2),
                "buy_v": round(buy_v, 8),
                "sell_v": round(sell_v, 8),
                "buy_q": round(buy_q, 2),
                "sell_q": round(sell_q, 2),
                "buy_ratio": round(buy_q / total_q, 4) if total_q > 0 else 0.5,
                "n": len(trades),
            }

            if symbol not in self.minute_klines:
                self.minute_klines[symbol] = {}
            self.minute_klines[symbol][minute_ts] = kline
            new_klines.append(kline)

        if new_klines:
            print(f"[HYPER] 结算分钟K线: {len(new_klines)} 条 @ {datetime.fromtimestamp(minute_ts, tz=timezone.utc).strftime('%H:%M')}")
            # 写入COS
            threading.Thread(target=save_klines_to_cos, args=(new_klines,), daemon=True).start()
            # 广播给前端
            socketio.emit("minute_kline", {"klines": new_klines, "ts": minute_ts})

        # 清理旧K线
        cutoff = minute_ts - MAX_MINUTE_KLINES * 60
        for symbol in self.minute_klines:
            for ts in list(self.minute_klines[symbol].keys()):
                if ts < cutoff:
                    del self.minute_klines[symbol][ts]

    def get_current_state(self) -> dict:
        """获取当前分钟实时状态"""
        with self.lock:
            result = {}
            now = int(time.time())
            for symbol, st in self.current_minute_state.items():
                total_q = st["buy_q"] + st["sell_q"]
                result[symbol] = {
                    "symbol": symbol,
                    "t": self.current_minute_ts,
                    "o": st["open"],
                    "h": st["high"],
                    "l": st["low"],
                    "c": st["close"],
                    "buy_q": round(st["buy_q"], 2),
                    "sell_q": round(st["sell_q"], 2),
                    "buy_v": round(st["buy_v"], 8),
                    "sell_v": round(st["sell_v"], 8),
                    "buy_ratio": round(st["buy_q"] / total_q, 4) if total_q > 0 else 0.5,
                    "n": st["n"],
                    "elapsed": now - self.current_minute_ts,
                }
            return result

    def get_last_complete_klines(self, n: int = 10) -> list:
        """获取最近N根完整分钟K线"""
        with self.lock:
            result = []
            for symbol, klines in self.minute_klines.items():
                sorted_ts = sorted(klines.keys(), reverse=True)
                for ts in sorted_ts[:n]:
                    result.append(klines[ts])
            return sorted(result, key=lambda x: (x["t"], x["symbol"]), reverse=True)

    def get_recent_trades(self, coin: str, n: int = 50) -> list:
        """获取某币种的最近逐笔成交"""
        with self.lock:
            trades = []
            for ts in sorted(self.raw_trades.get(coin, {}).keys(), reverse=True):
                trades.extend(self.raw_trades[coin][ts])
                if len(trades) >= n:
                    break
            trades = sorted(trades, key=lambda x: x["time"], reverse=True)[:n]
            return trades

    def get_stats(self) -> dict:
        with self.lock:
            return dict(self.stats)


# 全局聚合器
aggregator = HyperAggregator()


# ========== WebSocket 线程 ==========
def hyper_ws_loop():
    """Hyperliquid WebSocket - 只订阅4个大币的逐笔成交"""
    import websocket as ws_module

    def on_open(ws):
        print("[HYPER_WS] 已连接，订阅大币逐笔...")
        for coin in MONITOR_COINS:
            try:
                ws.send(json.dumps({
                    "method": "subscribe",
                    "subscription": {"type": "trades", "coin": coin}
                }))
                print(f"[HYPER_WS] 已订阅 {coin}")
            except Exception as e:
                print(f"[HYPER_WS] 订阅 {coin} 失败: {e}")
        aggregator.stats["ws_connected"] = True
        aggregator.stats["ws_connected_at"] = time.time()

    def on_message(ws, message):
        try:
            data = json.loads(message)
            if data.get("channel") == "subscriptionResponse":
                return
            if data.get("channel") == "trades":
                trades = data.get("data", [])
                for trade in trades:
                    aggregator.add_trade(trade)
        except Exception as e:
            print(f"[HYPER_WS] 消息处理错误: {e}")

    def on_error(ws, error):
        print(f"[HYPER_WS] 错误: {error}")
        aggregator.stats["ws_connected"] = False

    def on_close(ws, code, reason):
        print(f"[HYPER_WS] 关闭: {code} {reason}")
        aggregator.stats["ws_connected"] = False

    while True:
        try:
            ws = ws_module.WebSocketApp(
                HYPER_WS_URL,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close
            )
            ws.run_forever(ping_interval=20, ping_timeout=10)
        except Exception as e:
            print(f"[HYPER_WS] 连接异常: {e}")
        print("[HYPER_WS] 5秒后重连...")
        time.sleep(5)


# ========== 定时广播线程 ==========
def broadcast_loop():
    """每2秒向前端广播当前实时状态"""
    while True:
        try:
            time.sleep(2)
            state = aggregator.get_current_state()
            stats = aggregator.get_stats()
            socketio.emit("current_state", {
                "klines": list(state.values()),
                "stats": stats,
                "ts": int(time.time()),
            })
        except Exception as e:
            print(f"[BROADCAST] 错误: {e}")


# ========== API 路由 ==========
@app.route("/")
def index():
    return render_template("hyper_monitor.html")


@app.route("/api/current")
def api_current():
    """当前分钟实时状态"""
    return jsonify({
        "data": list(aggregator.get_current_state().values()),
        "stats": aggregator.get_stats(),
    })


@app.route("/api/klines/<symbol>")
def api_klines_symbol(symbol):
    """某币种的最近分钟K线"""
    n = min(int(request.args.get("n", 60)), 1440)
    symbol = symbol.upper()
    if not symbol.endswith("USDT"):
        symbol += "USDT"

    with aggregator.lock:
        klines = aggregator.minute_klines.get(symbol, {})
        result = [klines[ts] for ts in sorted(klines.keys(), reverse=True)[:n]]
    return jsonify({"symbol": symbol, "data": result})


@app.route("/api/klines")
def api_klines():
    """所有币种的最近分钟K线"""
    n = min(int(request.args.get("n", 30)), 1440)
    return jsonify({"data": aggregator.get_last_complete_klines(n)})


@app.route("/api/trades/<coin>")
def api_trades(coin):
    """某币种的最近逐笔成交"""
    n = min(int(request.args.get("n", 50)), 200)
    coin = coin.upper()
    if coin not in MONITOR_COINS:
        return jsonify({"error": f"只支持 {', '.join(MONITOR_COINS)}"}), 400
    return jsonify({"coin": coin, "data": aggregator.get_recent_trades(coin, n)})


@app.route("/api/stats")
def api_stats():
    """统计信息"""
    return jsonify(aggregator.get_stats())


# ========== 启动 ==========
if __name__ == "__main__":
    # 从COS加载历史K线
    print("[INIT] 从COS加载历史K线...")
    historical = load_recent_klines_from_cos(hours=24)
    if historical:
        for k in historical:
            symbol = k.get("symbol")
            ts = k.get("t")
            if symbol and ts:
                if symbol not in aggregator.minute_klines:
                    aggregator.minute_klines[symbol] = {}
                aggregator.minute_klines[symbol][ts] = k
        print(f"[INIT] 加载了 {len(historical)} 条历史K线")
    else:
        print("[INIT] COS无历史数据，从零开始")

    # 启动 WebSocket 线程
    ws_thread = threading.Thread(target=hyper_ws_loop, daemon=True)
    ws_thread.start()

    # 启动广播线程
    broadcast_thread = threading.Thread(target=broadcast_loop, daemon=True)
    broadcast_thread.start()

    print(f"[START] Hyperliquid 大币逐笔监控启动 @ port {PORT}")
    socketio.run(app, host="0.0.0.0", port=PORT, debug=False, use_reloader=False, allow_unsafe_werkzeug=True)
