#!/usr/bin/env python3
"""
快速回填1h K线：从币安现货API批量拉取，保存到本地JSON文件
每个币种拉 720 根1h K线（≈30天），供布林带爬坡使用
"""
import os
import sys
import json
import time
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

# ========== 配置 ==========
BINANCE_API = "https://api.binance.com"
OUTPUT_FILE = Path(__file__).parent.parent / "data" / "hourly_backfill.json"
LIMIT = 720              # 720根1h K线 ≈ 30天
WORKERS = 20             # 并发数
DELAY_ON_429 = 60        # 429限流等待秒数
MAX_RETRIES = 3

# 排除的币种
EXCLUDE_SYMBOLS = {
    'BTCUSDT', 'ETHUSDT', 'SOLUSDT',
    'TSLAUSDT', 'NVDAUSDT', 'AMZNUSDT', 'GOOGLUSDT', 'AAPLUSDT',
    'COINUSDT', 'MSTRUSDT', 'METAUSDT', 'TSMUSDT',
    'XAUUSDT', 'XAGUSDT', 'XAUTUSDT', 'NATGASUSDT',
    'USDCUSDT', 'RLUSDUSDT', 'UUSDT', 'XUSDUSDT', 'USD1USDT',
    'FDUSDUSDT', 'TUSDUSDT', 'PAXUSDT', 'BUSDUSDT', 'SUSDT', 'USDEUSDT',
}

session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0"})

# ========== 获取所有USDT交易对 ==========
def get_all_usdt_symbols():
    print("[STEP 1] 获取所有USDT交易对...")
    resp = session.get(f"{BINANCE_API}/api/v3/exchangeInfo", timeout=15)
    resp.raise_for_status()
    data = resp.json()

    symbols = []
    for s in data.get("symbols", []):
        if s["quoteAsset"] != "USDT":
            continue
        if s["status"] != "TRADING":
            continue
        sym = s["symbol"]
        if sym in EXCLUDE_SYMBOLS:
            continue
        symbols.append(sym)

    print(f"  → 获取到 {len(symbols)} 个USDT交易对")
    return symbols

# ========== 拉取单个币种1h K线（带重试）==========
def fetch_hourly_klines(symbol: str) -> list | None:
    for attempt in range(MAX_RETRIES):
        try:
            resp = session.get(
                f"{BINANCE_API}/api/v3/klines",
                params={"symbol": symbol, "interval": "1h", "limit": LIMIT},
                timeout=15
            )
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", DELAY_ON_429))
                print(f"  [429] {symbol}: 等待{retry_after}s (尝试{attempt+1})")
                time.sleep(retry_after)
                continue
            if resp.status_code == 418:
                retry_after = int(resp.headers.get("Retry-After", 300))
                print(f"  [418] {symbol}: 熔断等待{retry_after}s")
                time.sleep(retry_after)
                continue
            resp.raise_for_status()
            data = resp.json()
            if not data:
                return None

            result = []
            for k in data:
                q = float(k[7])
                result.append({
                    "t": int(k[0]) // 1000,
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
            if attempt == MAX_RETRIES - 1:
                print(f"  [FAIL] {symbol}: {e}")
                return None
            time.sleep(2)

    return None

# ========== 主流程 ==========
def main():
    global BINANCE_API
    # 瑞士API
    swiss_api = "https://api.binance.com"
    BINANCE_API = swiss_api

    symbols = get_all_usdt_symbols()
    if not symbols:
        print("❌ 没有获取到任何交易对")
        sys.exit(1)

    print(f"\n[STEP 2] 并发回填 {len(symbols)} 个币种（{WORKERS}线程, limit={LIMIT}）...")
    print(f"  API: {BINANCE_API}")
    print(f"  输出: {OUTPUT_FILE}")

    result_dict = {}
    lock = threading.Lock()
    done = 0
    errors = 0
    total = len(symbols)
    start_ts = time.time()

    def progress():
        nonlocal done
        with lock:
            done += 1
            elapsed = time.time() - start_ts
            rate = done / elapsed if elapsed > 0 else 0
            eta = (total - done) / rate if rate > 0 else 0
            print(f"  [{done}/{total}] {rate:.1f}/s ETA:{eta:.0f}s")

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        fut_map = {}
        for sym in symbols:
            fut = pool.submit(fetch_hourly_klines, sym)
            fut_map[fut] = sym

        for fut in as_completed(fut_map):
            sym = fut_map[fut]
            try:
                klines = fut.result()
                if klines:
                    with lock:
                        result_dict[sym] = klines
                else:
                    with lock:
                        errors += 1
            except Exception:
                with lock:
                    errors += 1
            progress()

    elapsed = time.time() - start_ts
    print(f"\n[STEP 3] 回填完成!")
    print(f"  成功: {len(result_dict)}/{total}")
    print(f"  失败: {errors}")
    print(f"  耗时: {elapsed:.1f}s")

    # 保存到文件
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    cache_data = {
        "version": 1,
        "generated_at": time.time(),
        "limit": LIMIT,
        "source": BINANCE_API,
        "symbols": sorted(result_dict.keys()),
        "data": result_dict,
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(cache_data, f)

    file_size = OUTPUT_FILE.stat().st_size / 1024 / 1024
    print(f"\n✅ 保存完成: {OUTPUT_FILE}")
    print(f"   文件大小: {file_size:.1f}MB")
    print(f"   币种数: {len(result_dict)}")
    if result_dict:
        sample = list(result_dict.values())[0]
        print(f"   每币种K线数: {len(sample)} 根")
        print(f"   覆盖天数: ~{len(sample) / 24:.1f} 天")


if __name__ == "__main__":
    main()
