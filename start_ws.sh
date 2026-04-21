#!/bin/bash
cd /home/ubuntu/crypto-scanner
python3 -c "
import eventlet
eventlet.monkey_patch()
from app import app, socketio, ws_manager
from utils.binance_account import BinanceAccountWS

# 启动 WebSocket 管理器（实时K线）
ws_manager.set_socketio(socketio)

# 启动账户WS（持仓更新）
BinanceAccountWS.start()

socketio.run(app, host='0.0.0.0', port=5002, allow_unsafe_werkzeug=True)
"
