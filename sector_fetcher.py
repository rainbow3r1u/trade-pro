#!/usr/bin/env python3
"""CoinGecko板块数据拉取 → 每天一次"""
import requests, json, time, os

CACHE_FILE = "/tmp/crypto_sectors.json"
TARGETS = {
    'meme-token': 'Meme', 'artificial-intelligence': 'AI',
    'layer-1': 'L1', 'layer-2': 'L2',
    'decentralized-finance-defi': 'DeFi', 'real-world-assets-rwa': 'RWA',
    'gaming': '游戏', 'depin': 'DePIN', 'oracle': '预言机',
    'privacy': '隐私', 'solana-ecosystem': 'Solana',
    'ethereum-ecosystem': 'ETH生态', 'decentralized-exchange': 'DEX',
    'ai-agents': 'AI Agent', 'desci': 'DeSci',
    'chain-abstraction': '链抽象', 'parallel-evm': '并行EVM',
    'bitcoin-ecosystem': 'BTC生态', 'ton-ecosystem': 'TON生态',
    'base-ecosystem': 'Base生态', 'liquid-staking': '流动性质押',
    'restaking': '再质押',
}

def fetch():
    symbol_map = {}
    for cat_id, label in TARGETS.items():
        try:
            resp = requests.get(
                'https://api.coingecko.com/api/v3/coins/markets',
                params={'vs_currency': 'usd', 'category': cat_id,
                        'order': 'market_cap_desc', 'per_page': 250},
                timeout=30
            )
            if resp.status_code == 429:
                print(f'  {label}: 限频，等待60s...')
                time.sleep(60)
                resp = requests.get(
                    'https://api.coingecko.com/api/v3/coins/markets',
                    params={'vs_currency': 'usd', 'category': cat_id,
                            'order': 'market_cap_desc', 'per_page': 250},
                    timeout=30
                )
            if resp.status_code != 200:
                print(f'  {label}: HTTP {resp.status_code}, skip')
                continue
            for c in resp.json():
                sym = c.get('symbol', '').upper()
                bsym = sym + 'USDT'
                if bsym not in symbol_map:
                    symbol_map[bsym] = []
                if label not in symbol_map[bsym]:
                    symbol_map[bsym].append(label)
            print(f'  {label}: {len(resp.json())} coins, mapped {sum(1 for v in symbol_map.values() if label in v)} to USDT pairs')
            time.sleep(3)
        except Exception as e:
            print(f'  {label}: {e}')
    with open(CACHE_FILE, 'w') as f:
        json.dump(symbol_map, f)
    print(f'Done: {len(symbol_map)} symbols cached to {CACHE_FILE}')

if __name__ == '__main__':
    fetch()
