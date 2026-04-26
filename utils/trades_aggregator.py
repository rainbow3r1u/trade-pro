"""
Hyperliquid Trades 聚合器

功能：
1. 接收 WebSocket trades 数据
2. 聚合成分钟K线
3. 计算买卖比例
"""
import time
import threading
from typing import Dict, List, Optional, Any
from datetime import datetime


class TradesAggregator:
    """成交数据聚合器 - 将秒级成交聚合成分钟K线"""
    
    def __init__(self, max_minutes: int = 10):
        """
        Args:
            max_minutes: 保留最近多少分钟的原始数据
        """
        self.trades: Dict[str, Dict[int, List[dict]]] = {}  # {coin: {minute_ts: [trades]}}
        self.minute_klines: Dict[str, Dict[int, dict]] = {}  # {coin: {minute_ts: kline}}
        self.max_minutes = max_minutes
        self.lock = threading.Lock()
        self.last_aggregate_time = 0
    
    def add_trade(self, trade: dict):
        """
        添加成交记录
        
        Args:
            trade: {
                "coin": "BTC",
                "side": "B",  # B=买入, A=卖出
                "px": 75958.0,
                "sz": 0.01541,
                "time": 1776605949753,  # 毫秒
                ...
            }
        """
        coin = trade.get("coin")
        if not coin:
            return
        
        # 计算分钟时间戳（秒）
        trade_time_ms = trade.get("time", int(time.time() * 1000))
        minute_ts = trade_time_ms // 1000 // 60 * 60
        
        with self.lock:
            if coin not in self.trades:
                self.trades[coin] = {}
            
            if minute_ts not in self.trades[coin]:
                self.trades[coin][minute_ts] = []
            
            self.trades[coin][minute_ts].append({
                "side": trade.get("side"),
                "px": float(trade.get("px", 0)),
                "sz": float(trade.get("sz", 0)),
                "time": trade_time_ms,
            })
    
    def aggregate_minute(self, coin: str, minute_ts: int) -> Optional[dict]:
        """
        聚合成分钟K线
        
        Args:
            coin: 币种
            minute_ts: 分钟时间戳（秒）
        
        Returns:
            分钟K线数据
        """
        with self.lock:
            trades = self.trades.get(coin, {}).get(minute_ts, [])
        
        if not trades:
            return None
        
        prices = [t["px"] for t in trades if t["px"] > 0]
        sizes = [t["sz"] for t in trades]
        
        if not prices:
            return None
        
        # 分离买卖
        buy_trades = [t for t in trades if t["side"] == "B"]
        sell_trades = [t for t in trades if t["side"] == "A"]
        
        buy_volume = sum(t["sz"] for t in buy_trades)
        sell_volume = sum(t["sz"] for t in sell_trades)
        buy_amount = sum(t["px"] * t["sz"] for t in buy_trades)
        sell_amount = sum(t["px"] * t["sz"] for t in sell_trades)
        total_amount = buy_amount + sell_amount
        
        kline = {
            "t": minute_ts,  # 分钟时间戳（秒）
            "o": trades[0]["px"],  # 开盘价
            "h": max(prices),  # 最高价
            "l": min(prices),  # 最低价
            "c": trades[-1]["px"],  # 收盘价
            "v": sum(sizes),  # 成交量
            "q": sum(t["px"] * t["sz"] for t in trades),  # 成交额
            "buy_v": buy_volume,  # 买入量
            "sell_v": sell_volume,  # 卖出量
            "buy_q": buy_amount,  # 买入金额
            "sell_q": sell_amount,  # 卖出金额
            "buy_ratio": buy_amount / total_amount if total_amount > 0 else 0.5,  # 买入比例
            "n": len(trades),  # 成交笔数
        }
        
        # 缓存结果
        with self.lock:
            if coin not in self.minute_klines:
                self.minute_klines[coin] = {}
            self.minute_klines[coin][minute_ts] = kline
        
        return kline
    
    def get_current_minute_kline(self, coin: str) -> Optional[dict]:
        """获取当前分钟的K线（实时聚合）"""
        current_minute = int(time.time()) // 60 * 60
        return self.aggregate_minute(coin, current_minute)
    
    def get_last_complete_minute_kline(self, coin: str) -> Optional[dict]:
        """获取上一根完整分钟K线"""
        last_minute = int(time.time()) // 60 * 60 - 60
        return self.aggregate_minute(coin, last_minute)
    
    def get_all_current_klines(self) -> Dict[str, dict]:
        """获取所有币种的当前分钟K线"""
        current_minute = int(time.time()) // 60 * 60
        result = {}
        
        with self.lock:
            coins = list(self.trades.keys())
        
        for coin in coins:
            kline = self.aggregate_minute(coin, current_minute)
            if kline:
                result[coin] = kline
        
        return result
    
    def cleanup_old_data(self):
        """清理过期数据"""
        current_minute = int(time.time()) // 60 * 60
        cutoff = current_minute - self.max_minutes * 60
        
        with self.lock:
            for coin in list(self.trades.keys()):
                for minute_ts in list(self.trades[coin].keys()):
                    if minute_ts < cutoff:
                        del self.trades[coin][minute_ts]
            
            for coin in list(self.minute_klines.keys()):
                for minute_ts in list(self.minute_klines[coin].keys()):
                    if minute_ts < cutoff:
                        del self.minute_klines[coin][minute_ts]
    
    def get_stats(self) -> dict:
        """获取统计信息"""
        with self.lock:
            total_trades = sum(
                len(trades_list)
                for coin_trades in self.trades.values()
                for trades_list in coin_trades.values()
            )
            
            return {
                "coins": len(self.trades),
                "total_trades": total_trades,
                "minute_klines": sum(len(k) for k in self.minute_klines.values()),
            }


# 全局聚合器实例
_aggregator: Optional[TradesAggregator] = None
_aggregator_lock = threading.Lock()

def _cleanup_loop():
    """后台线程：每60秒清理一次过期数据"""
    while True:
        time.sleep(60)
        try:
            agg = get_aggregator()
            agg.cleanup_old_data()
        except Exception:
            pass

def get_aggregator() -> TradesAggregator:
    """获取全局聚合器实例"""
    global _aggregator
    with _aggregator_lock:
        if _aggregator is None:
            _aggregator = TradesAggregator()
            # 启动后台清理线程
            t = threading.Thread(target=_cleanup_loop, daemon=True)
            t.start()
        return _aggregator


if __name__ == "__main__":
    print("=== Trades 聚合器测试 ===\n")
    
    agg = TradesAggregator()
    
    # 模拟一些成交
    test_trades = [
        {"coin": "BTC", "side": "B", "px": 75000, "sz": 0.1, "time": int(time.time() * 1000)},
        {"coin": "BTC", "side": "B", "px": 75100, "sz": 0.2, "time": int(time.time() * 1000) + 100},
        {"coin": "BTC", "side": "A", "px": 75050, "sz": 0.15, "time": int(time.time() * 1000) + 200},
        {"coin": "BTC", "side": "B", "px": 75200, "sz": 0.3, "time": int(time.time() * 1000) + 300},
        {"coin": "ETH", "side": "A", "px": 2300, "sz": 1.0, "time": int(time.time() * 1000)},
        {"coin": "ETH", "side": "B", "px": 2310, "sz": 2.0, "time": int(time.time() * 1000) + 100},
    ]
    
    for trade in test_trades:
        agg.add_trade(trade)
    
    # 获取当前分钟K线
    print("BTC 当前分钟K线:")
    kline = agg.get_current_minute_kline("BTC")
    if kline:
        print(f"  开盘: {kline['o']}, 最高: {kline['h']}, 最低: {kline['l']}, 收盘: {kline['c']}")
        print(f"  成交量: {kline['v']}, 成交额: {kline['q']:.2f}")
        print(f"  买入量: {kline['buy_v']}, 卖出量: {kline['sell_v']}")
        print(f"  买入比例: {kline['buy_ratio']:.2%}")
    
    print("\nETH 当前分钟K线:")
    kline = agg.get_current_minute_kline("ETH")
    if kline:
        print(f"  开盘: {kline['o']}, 最高: {kline['h']}, 最低: {kline['l']}, 收盘: {kline['c']}")
        print(f"  买入比例: {kline['buy_ratio']:.2%}")
    
    print(f"\n统计: {agg.get_stats()}")
    print("\n测试完成!")
