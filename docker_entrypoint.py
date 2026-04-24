#!/usr/bin/env python3
"""
Docker 入口：同时启动 5003 端口 Web 服务 + 模拟交易脚本
"""
import subprocess
import sys
import time
import signal
import os

processes = []


def shutdown(signum, frame):
    print("\n[Entrypoint] Received shutdown signal, stopping all processes...")
    for p in processes:
        if p.poll() is None:
            p.terminate()
    # 等待最多 5 秒优雅退出
    deadline = time.time() + 5
    for p in processes:
        while p.poll() is None and time.time() < deadline:
            time.sleep(0.2)
    for p in processes:
        if p.poll() is None:
            p.kill()
    sys.exit(0)


signal.signal(signal.SIGTERM, shutdown)
signal.signal(signal.SIGINT, shutdown)

if __name__ == "__main__":
    os.chdir("/home/ubuntu/crypto-scanner")

    # 1. 启动 Web 服务
    print("[Entrypoint] Starting web service (port 5003)...")
    web_proc = subprocess.Popen([sys.executable, "run_web.py"])
    processes.append(web_proc)

    # 2. 等待 Web 服务就绪
    time.sleep(10)

    # 3. 启动模拟交易脚本
    print("[Entrypoint] Starting sim_trade.py...")
    sim_proc = subprocess.Popen([sys.executable, "sim_trade.py"])
    processes.append(sim_proc)

    # 4. 守护进程：Web 挂了则整体退出；sim_trade 挂了则自动重启
    while True:
        time.sleep(3)

        web_status = web_proc.poll()
        sim_status = sim_proc.poll()

        if web_status is not None:
            print(f"[Entrypoint] Web service exited with code {web_status}, shutting down...")
            shutdown(None, None)

        if sim_status is not None:
            print(f"[Entrypoint] sim_trade.py exited with code {sim_status}, restarting in 5s...")
            time.sleep(5)
            sim_proc = subprocess.Popen([sys.executable, "sim_trade.py"])
            processes[1] = sim_proc
