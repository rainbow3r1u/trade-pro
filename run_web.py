#!/usr/bin/env python3
"""
Docker 内启动 5003 端口 Web 服务（带 eventlet monkey_patch）
"""
import eventlet
eventlet.monkey_patch()

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from market_monitor_app import (
    app, socketio, init_market_data,
    ws_update_loop, write_loop,
    minute_aggregator_loop, daily_open_price_update_loop,
    sim_trade_broadcast_loop,
    bb_daily_background_loop,
    load_vol_15m_from_cos, get_current_15m_slot, market_data, data_lock,
    _refresh_snapshot_cache,
)
import threading

init_market_data()

# 从COS加载最近8小时的15分钟成交量历史（扩大窗口减少停机影响）
try:
    current_slot = get_current_15m_slot()
    vol_15m_hist = load_vol_15m_from_cos(current_slot, slots_count=32)
    with data_lock:
        for symbol, slots in vol_15m_hist.items():
            market_data["vol_15m_history"][symbol] = slots
        print(f"[VOL_15M_COS] 启动时加载了 {len(vol_15m_hist)} 个币种的15分钟历史")
except Exception as e:
    print(f"[VOL_15M_COS] 启动加载失败: {e}")

threading.Thread(target=ws_update_loop, daemon=True).start()
threading.Thread(target=write_loop, daemon=True).start()
threading.Thread(target=minute_aggregator_loop, daemon=True).start()
threading.Thread(target=daily_open_price_update_loop, daemon=True).start()
threading.Thread(target=sim_trade_broadcast_loop, daemon=True).start()
threading.Thread(target=bb_daily_background_loop, daemon=True).start()
threading.Thread(target=_refresh_snapshot_cache, daemon=True).start()

socketio.run(
    app,
    host='0.0.0.0',
    port=5003,
    debug=False,
    allow_unsafe_werkzeug=True,
    log_output=False
)
