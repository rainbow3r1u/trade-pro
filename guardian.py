#!/usr/bin/env python3
"""进程守护 — 每分钟检查关键服务，挂了自动重启"""
import subprocess, time, os, sys

CHECKS = [
    {
        "name": "web5003",
        "check": "port", "port": 5003,
        "start": "cd /home/myuser/websocket_new && nohup python3 run_web.py > /tmp/web5003.log 2>&1 &",
    },
    {
        "name": "sim_trade",
        "check": "process", "pattern": "sim_trade.py",
        "start": "screen -dmS trade bash -c 'cd /home/myuser/websocket_new && python3 -u sim_trade.py > /tmp/trade.log 2>&1'",
    },
    {
        "name": "OI采集",
        "check": "process", "pattern": "oi_collector.py",
        "start": "screen -dmS oi_collector bash -c 'cd /home/myuser/backtester/cos_service && python3 -u oi_collector.py > /tmp/oi_collector.log 2>&1'",
    },
    {
        "name": "CDD链上",
        "check": "process", "pattern": "blockchair_collector.py",
        "start": "screen -dmS bc_collector bash -c 'python3 -u /home/myuser/blockchair_collector.py > /tmp/bc_collector.log 2>&1'",
    },
]

def check_port(port):
    import socket
    try:
        s = socket.socket()
        s.settimeout(2)
        s.connect(('localhost', port))
        s.close()
        return True
    except:
        return False

def check_process(pattern):
    try:
        result = subprocess.run(['pgrep', '-f', pattern], capture_output=True, text=True)
        return result.returncode == 0
    except:
        return False

def main():
    log_file = "/tmp/guardian.log"
    status = []
    for svc in CHECKS:
        alive = check_port(svc["port"]) if svc["check"] == "port" else check_process(svc["pattern"])
        status.append({"name": svc["name"], "alive": alive})
        if not alive:
            msg = f"[{time.strftime('%m-%d %H:%M')}] {svc['name']} 挂了，重启..."
            print(msg)
            with open(log_file, 'a') as f:
                f.write(msg + '\n')
            os.system(svc["start"])
    # 写状态文件供网站读取
    with open("/tmp/guardian_status.json", "w") as f:
        import json
        json.dump({"services": status, "updated": time.time()}, f)

if __name__ == "__main__":
    main()
