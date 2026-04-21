#!/bin/bash
cd /home/ubuntu/crypto-scanner
python3 -c "
import eventlet
eventlet.monkey_patch()
import sys, os
sys.path.insert(0, '/home/ubuntu/crypto-scanner')
os.chdir('/home/ubuntu/crypto-scanner')
from market_monitor_app import app, socketio, init_market_data, ws_update_loop, hyperliquid_ws_loop, write_loop, minute_aggregator_loop, daily_open_price_update_loop, hyperliquid_backfill_loop, sim_trade_broadcast_loop
import threading
init_market_data()
threading.Thread(target=ws_update_loop, daemon=True).start()
threading.Thread(target=hyperliquid_ws_loop, daemon=True).start()
threading.Thread(target=write_loop, daemon=True).start()
threading.Thread(target=minute_aggregator_loop, daemon=True).start()
threading.Thread(target=daily_open_price_update_loop, daemon=True).start()
threading.Thread(target=hyperliquid_backfill_loop, daemon=True).start()
threading.Thread(target=sim_trade_broadcast_loop, daemon=True).start()
socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True, log_output=False)
"
