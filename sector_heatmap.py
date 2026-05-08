#!/usr/bin/env python3
"""板块热力图数据层 — CoinGecko categories → JSON"""
import requests, json, time, os

CACHE = "/tmp/sector_heatmap.json"
TARGETS = {
    'meme-token': 'Meme',
    'artificial-intelligence': 'AI',
    'layer-1': 'L1',
    'layer-2': 'L2',
    'decentralized-finance-defi': 'DeFi',
    'real-world-assets-rwa': 'RWA',
    'gaming': '游戏',
    'depin': 'DePIN',
    'oracle': '预言机',
    'privacy': '隐私',
    'solana-ecosystem': 'Solana',
    'ethereum-ecosystem': 'ETH生态',
    'decentralized-exchange': 'DEX',
    'liquid-staking': '流动性质押',
    'restaking': '再质押',
    'ai-agents': 'AI Agent',
    'desci': 'DeSci',
    'chain-abstraction': '链抽象',
    'parallel-evm': '并行EVM',
    'bitcoin-ecosystem': 'BTC生态',
    'ton-ecosystem': 'TON生态',
    'base-ecosystem': 'Base生态',
}

def fetch():
    try:
        resp = requests.get('https://api.coingecko.com/api/v3/coins/categories',
                           params={'order': 'market_cap_desc'}, timeout=30)
        if resp.status_code != 200:
            return
        all_cats = resp.json()
        result = []
        for c in all_cats:
            cid = c.get('id','')
            if cid not in TARGETS:
                continue
            mc_change = c.get('market_cap_change_24h', 0) or 0
            vol = c.get('volume_24h', 0) or 0
            result.append({
                'id': cid,
                'name': TARGETS[cid],
                'mc_change_pct': round(mc_change, 1),
                'volume_24h': vol,
            })
        result.sort(key=lambda x: -x['mc_change_pct'])
        with open(CACHE, 'w') as f:
            json.dump(result, f, default=str)
        print(f'Heatmap: {len(result)} sectors cached, top: {result[0]["name"]} +{result[0]["mc_change_pct"]}%')
    except Exception as e:
        print(f'Heatmap error: {e}')

if __name__ == '__main__':
    fetch()
