# 加密货币板块（Sector）完全指南

> CoinGecko API 数据 + 实战经验整理
> 最后更新：2026-05-06

## 目录

1. [核心板块（按市值排名）](#1-核心板块按市值排名)
2. [按叙事/概念分类](#2-按叙事概念分类)
3. [按公链生态分类](#3-按公链生态分类)
4. [Meme 币板块谱系](#4-meme-币板块谱系)
5. [DeFi 板块细分](#5-defi-板块细分)
6. [如何获取这些数据](#6-如何获取这些数据)
7. [实战应用：用板块做交易决策](#7-实战应用用板块做交易决策)

---

## 1. 核心板块（按市值排名）

| 板块 | 描述 | 代表币种 | 特点 |
|------|------|----------|------|
| **Smart Contract Platform** | 智能合约平台，公链赛道 | BTC, ETH, XRP | 整个加密世界的基石，市值最大 |
| **Layer 1 (L1)** | 底层公链 | BTC, ETH, XRP | 与智能合约平台高度重叠 |
| **Proof of Work (PoW)** | 工作量证明共识 | BTC, DOGE, ZEC | 挖矿/算力驱动 |
| **Proof of Stake (PoS)** | 权益证明共识 | ETH, BNB, SOL | 质押/验证者驱动 |
| **Stablecoins** | 稳定币 | USDT, USDC, USDS | 锚定法币，交易媒介 |
| **Exchange-based Tokens** | 交易所代币 | BNB, WBT, HYPE | 中心化/去中心化交易所 |
| **Real World Assets (RWA)** | 现实世界资产代币化 | Figure, LINK, ONDO | 连接传统金融 |
| **Decentralized Finance (DeFi)** | 去中心化金融 | stETH, HYPE, wstETH | 借贷/交易/收益 |
| **Meme** | 表情包币 | DOGE, PENGU, SHIB | 社区驱动，高波动 |
| **Artificial Intelligence (AI)** | 人工智能 | LINK, TAO, FET | AI + 区块链结合 |
| **Privacy** | 隐私链/隐私币 | ZEC, XMR, LINK | 匿名交易 |
| **Infrastructure** | 基础设施 | LINK, LTC, ICP | 预言机/存储/跨链 |
| **Gaming (GameFi)** | 链游 | GALA, SAND, AXS | 游戏内经济 |
| **DePIN** | 去中心化物理基础设施 | RNDR, FIL, AKT | 算力/存储/网络共享 |
| **Layer 2 (L2)** | 二层扩展 | MNT, POL, ARB | 解决 L1 性能瓶颈 |
| **Zero Knowledge (ZK)** | 零知识证明 | ZEC, POL, MIDNIGHT | 隐私 + 扩展性 |
| **Oracle** | 预言机 | LINK, PYTH, TRB | 链上数据喂价 |

---

## 2. 按叙事/概念分类

### 🔥 当前热门叙事（2026）

| 叙事 | 板块 ID | 说明 |
|------|---------|------|
| AI + Crypto | `artificial-intelligence` | AI Agent, AI Meme, DeFAI 等 |
| RWA 代币化 | `real-world-assets-rwa` | 美债/黄金/房地产上链 |
| DePIN | `depin` | 分布式算力/存储/网络 |
| 链抽象 | `chain-abstraction` | 跨链互操作，用户体验统一 |
| BTC Fi | `btcfi` | BTC 生态 DeFi |
| 并行 EVM | `parallel-evm` | 高性能兼容以太坊 |
| 再质押 (Restaking) | `restaking` | EigenLayer 模式 |
| 流动性质押 (LSD) | `liquid-staking` | stETH 等 |
| 意图 (Intent) | `intent` | 用户只需表达意图，后端自动执行 |
| AI Agent | `ai-agents` | 自主 AI 代理，自动交易/交互 |

### 🏛️ 权威/机构相关

| 板块 | 说明 | 代表 |
|------|------|------|
| **Trump-Affiliated** | 特朗普关联项目 | USD1, WLFI, TRUMP |
| **Made in USA** | 美国本土项目 | XRP, USDC, SOL |
| **Made in China** | 中国背景项目 | BNB, TRX, MNT |
| **Alleged SEC Securities** | 曾被 SEC 认定为证券 | BNB, SOL, ADA, MATIC |
| **World Liberty Financial Portfolio** | WLFI 持仓 | ETH, USDT, USDC |
| **YZi Labs Portfolio** | 币安实验室投资 | SUI, ENA, APT |

### 🎮 游戏细分

| 板块 | 说明 |
|------|------|
| `gaming` | 链游整体 |
| `play-to-earn` | 玩赚 |
| `gaming-blockchains` | 游戏专用链 |
| `gaming-platform` | 游戏平台 |
| `metaverse` | 元宇宙 |
| `rpg` | 角色扮演游戏 |
| `card-games` | 卡牌游戏 |
| `arcade-games` | 街机游戏 |
| `tap-to-earn` | 点击赚币（Telegram 类） |
| `mobile-mining` | 手机挖矿 |

### 💰 DeFi 细分

| 板块 | 说明 |
|------|------|
| `decentralized-exchange` | DEX 去中心化交易所 |
| `lending-borrowing` | 借贷协议 |
| `yield-farming` | 流动性挖矿 |
| `yield-aggregator` | 收益聚合器 |
| `liquid-staking` | 流动性质押 |
| `restaking` | 再质押 |
| `perpetuals` | 永续合约 DEX |
| `decentralized-options` | 期权 |
| `algorithmic-stablecoin` | 算法稳定币 |
| `synthetic` | 合成资产 |

---

## 3. 按公链生态分类

### 主流公链生态

| 生态 | 板块 ID | 说明 |
|------|---------|------|
| **Ethereum** | `ethereum-ecosystem` | 最成熟的 DeFi 生态 |
| **BNB Chain** | `binance-smart-chain` | 币安支持的侧链 |
| **Solana** | `solana-ecosystem` | 高性能，Meme 发源地之一 |
| **Base** | `base-ecosystem` | Coinbase L2，新热门 |
| **Arbitrum** | `arbitrum-ecosystem` | ETH L2，生态丰富 |
| **Optimism** | `optimism-ecosystem` | ETH L2，OP Stack |
| **TON** | `ton-ecosystem` | Telegram 生态链 |
| **Sui** | `sui-ecosystem` | Move 语言新公链 |
| **Aptos** | `aptos-ecosystem` | Move 语言新公链 |
| **HyperEVM** | `hyperevm-ecosystem` | Hyperliquid 生态 |
| **Berachain** | `berachain-ecosystem` | 流动性证明共识 |
| **Monad** | `monad-ecosystem` | 并行 EVM |
| **Bitcoin** | `bitcoin-ecosystem` | BTC L2 / Ordinals / Runes |
| **Cosmos** | `cosmos-ecosystem` | IBC 跨链生态 |
| **Polkadot** | `dot-ecosystem` | 平行链生态 |
| **Cardano** | `cardano-ecosystem` | 学术派公链 |
| **XRP Ledger** | `xrp-ledger-ecosystem` | Ripple 生态 |

### 二层 / 扩展生态

| 生态 | 说明 |
|------|------|
| **Superchain Ecosystem** | OP Superchain 生态系 |
| **Scroll Ecosystem** | zkEVM |
| **Starknet Ecosystem** | ZK-Rollup |
| **Linea Ecosystem** | ConsenSys zkEVM |
| **zksync Ecosystem** | ZK-Rollup |
| **Blast Ecosystem** | 原生收益 L2 |
| **Mode Ecosystem** | OP Stack L2 |
| **Mantle Ecosystem** | BitDAO 生态 L2 |

---

## 4. Meme 币板块谱系

Meme 币现在是个大分类，细分如下：

### 按主题/文化

| Meme 子板块 | 板块 ID | 代表币 |
|------------|---------|--------|
| **Dog-Themed** | `dog-themed-coins` | DOGE, SHIB, BONK |
| **Cat-Themed** | `cat-themed-coins` | TOSHI, MOG, POPCAT |
| **Frog-Themed** | `frog-themed-coins` | PEPE, APEPE, TURBO |
| **4chan-Themed** | `4chan-themed` | DOGE, PEPE, BONK |
| **Elon Musk-Inspired** | `elon-musk-inspired-coins` | DOGE, FLOKI |
| **PolitiFi** | `politifi` | TRUMP, MELANIA |
| **AI Meme** | `ai-meme-coins` | GOAT, FARTCOIN |
| **Chinese Meme** | `chinese-meme` | 币安人生 |

### 按公链 Meme

| 板块 | 说明 |
|------|------|
| `solana-meme-coins` | Solana 上的 Meme（pump.fun） |
| `base-meme-coins` | Base 链上的 Meme |
| `ton-meme-coins` | TON 链上的 Meme |
| `sui-meme` | Sui 链上的 Meme |
| `tron-meme` | TRON 上的 Meme |
| `bitcoin-meme` | BTC 上的 Ordinals/Runes Meme |

### Meme 发射平台

| 板块 | 说明 |
|------|------|
| `pump-fun` | Solana pump.fun 生态 |
| `sun-pump-ecosystem` | TRON SunPump |
| `four-meme-ecosystem` | BNB Chain Four.meme |
| `moonshot-ecosystem` | Moonshot 发射台 |

---

## 5. DeFi 板块细分

### 借贷

| 板块 | 代表协议 |
|------|---------|
| `lending-borrowing` | AAVE, Morpho, Compound |
| `morpho-ecosystem` | Morpho 生态 |
| `aave-tokens` | Aave 衍生代币 |

### DEX

| 板块 | 代表 |
|------|------|
| `decentralized-exchange` | Hyperliquid, UNI, ASTER |
| `automated-market-maker-amm` | UNI, PUMP, CAKE |
| `dex-aggregator` | JUP, 1INCH |
| `perpetuals` | Hyperliquid, ASTER, JUP |
| `decentralized-options` | 链上期权协议 |

### 质押/收益

| 板块 | 代表 |
|------|------|
| `liquid-staking` | stETH, wstETH, wBETH |
| `restaking` | weETH, LBTC |
| `liquid-restaking-governance-token` | ETHFI, PENDLE, KERNEL |
| `yield-farming` | UNI, AAVE, CAKE |
| `yield-aggregator` | CVX, YFI |
| `lsdfi` | PENDLE, 流动性质押衍生品 DeFi |
| `lrtfi` | 流动性再质押 DeFi |

### 稳定币

| 板块 | 类型 | 代表 |
|------|------|------|
| `stablecoins` | 稳定币总类 | USDT, USDC, USDS |
| `usd-stablecoin` | 美元稳定币 | USDT, USDC, USDS |
| `fiat-backed-stablecoin` | 法币抵押 | USDT, USDC |
| `crypto-backed-stablecoin` | 加密抵押 | DAI, FRAX |
| `algorithmic-stablecoin` | 算法稳定币 | UST（已崩）, FRAX |
| `yield-bearing-stablecoins` | 生息稳定币 | sUSDS, sUSDe, USDY |
| `synthetic-dollar` | 合成美元 | USDe, FRAX |

### RWA / TradFi 连接

| 板块 | 代表 |
|------|------|
| `real-world-assets-rwa` | Figure, LINK, ONDO |
| `tokenized-t-bills` | BlackRock BUIDL, USDY |
| `tokenized-gold` | PAXG, KAU |
| `tokenized-commodities` | 大宗商品代币 |
| `tokenized-products` | Hashnote, BlackRock |
| `tokenized-private-credit` | Figure |
| `ondo-tokenized-assets` | Ondo Finance |

---

## 6. 如何获取这些数据

### 方法一：CoinGecko API（推荐）

**获取所有分类列表：**
```bash
curl -s "https://api.coingecko.com/api/v3/coins/categories/list"
```
返回 700+ 个板块标签，每个有 `category_id` 和名称。
无需 API key，免费可用。

**获取分类详情（带币种、市值等）：**
```bash
curl -s "https://api.coingecko.com/api/v3/coins/categories"
```
返回每个板块的：名称、市值、涨幅、Top 3 币种图标（可从 URL 提取 ID）。

**获取特定分类的币种完整列表：**
```bash
curl -s "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&category=meme-token&order=market_cap_desc&per_page=250"
```
- 把 `category` 参数换成需要的板块 ID
- 支持 `per_page=250` 最大

### 方法二：CoinMarketCap API

```
GET https://pro-api.coinmarketcap.com/v1/cryptocurrency/categories
```
需要 API key（免费 tier 可用），返回类似数据。

### 方法三：TradingView 页面

打开 TradingView → 搜索框 → 选 "Topics/Sectors"

### 方法四：聚合数据网站

| 网站 | 特点 |
|------|------|
| [cryptobubbles.net](https://cryptobubbles.net) | 可视化气泡图，一目了然 |
| [coingecko.com](https://www.coingecko.com/en/categories) | 最全的分类页面 |
| [coinmarketcap.com](https://coinmarketcap.com/categories/) | CMC 板块分类 |
| [tradingview.com](https://www.tradingview.com/markets/cryptocurrencies/sectors-summary/) | 板块涨跌幅汇总 |

### 方法五：直接爬分类数据（可用 Python 脚本）

```python
import requests, json

# 获取所有分类
resp = requests.get("https://api.coingecko.com/api/v3/coins/categories")
categories = resp.json()

# 按市值排序
categories.sort(key=lambda x: x.get('market_cap', 0) or 0, reverse=True)

# 打印前 30 个板块及其代表币
for cat in categories[:30]:
    name = cat['name']
    mc = cat.get('market_cap', 0) or 0
    print(f"{name:40s} 💰 ${mc/1e9:.1f}B")
```

---

## 7. 实战应用：用板块做交易决策

### 🎯 交易策略思路

#### 1. 板块轮动扫描

**核心逻辑：** 资金在不同板块间轮动。发现某个板块集体放量/涨破时，说明资金进来了。

```python
# 伪代码：检测板块轮动
板块列表 = get_all_categories()
for 板块 in 板块列表:
    if 板块.24h涨幅 > 15% and 板块.交易量增幅 > 100%:
        alert(f"🔥 {板块.name} 异动！")
```

**典型轮动顺序：**
- BTC 涨 → L1 涨 → L2 涨 → DeFi 涨 → Meme 涨 → AI/GameFi 补涨 → 山寨季尾声

#### 2. 板块内龙头 + 跟风策略

- 每个板块的 **Top 3 币**是龙头，流动性最好
- 板块启动时，先买龙头；热度扩散后，找低市值跟风币

#### 3. 板块相关性交易

- 同一个板块内的币种正相关性高
- 例如：Solana Meme 板块中，BONK 涨了 → POPCAT 大概率跟涨
- 反过来：板块内 **一个龙头崩了** → 做空同板块其他币

#### 4. 板块 + K线形态（配合你的 BB_CLIMB 和 VOL_SURGE）

| 你的策略 | 板块应用 |
|----------|---------|
| **BB_CLIMB**（布林爬坡） | 用在 **整个板块** 的加权指数上，判断大方向 |
| **VOL_SURGE**（成交量突增） | 扫描所有板块，发现 **异动板块** 后进该板块的龙头 |

#### 5. 板块情绪指标

- **板块内上涨币数 / 总币数** > 80% → 板块过热，准备离场
- **板块内涨幅标准差** 增大 → 龙头分歧，尾部币补涨，可能是见顶信号
- **板块市值 / 全市场市值** 比率 → 判断该板块是否超配

### 📊 板块监控建议

可编写一个定时脚本（接进你的 web 项目）：

```python
# 定时任务示例
def monitor_sectors():
    for sector in ['meme-token', 'artificial-intelligence', 'real-world-assets-rwa',
                   'layer-2', 'decentralized-finance-defi', 'depin']:
        data = get_sector_data(sector)
        check_bb_climb(data)     # 布林爬坡
        check_vol_surge(data)    # 成交量突增
        alert_if_needed(sector, data)
```

### ⚠️ 注意事项

1. **板块分类不是绝对的** — 一个币可能属于多个板块（比如 LINK 同时是 Oracle、AI、Infrastructure）
2. **板块热度的变化速度** — Meme 板块几天就能翻几倍，追踪要快
3. **CoinGecko 数据延迟** — 实时性不如交易所直接数据，做高频交易需注意

---

## 附：核心板块 ID 速查表

```
# 大叙事类
meme-token               → Meme
artificial-intelligence  → AI
real-world-assets-rwa    → RWA
depin                    → DePIN
gaming                   → GameFi
decentralized-finance-defi → DeFi
privacy                  → 隐私

# 公链类
layer-1                  → L1
layer-2                  → L2
smart-contract-platform  → 智能合约
bitcoin-ecosystem        → BTC 生态
solana-ecosystem         → Solana 生态
ethereum-ecosystem       → ETH 生态

# 交易类
decentralized-exchange   → DEX
decentralized-perpetuals → 永续合约 DEX
stablecoins              → 稳定币
prediction-markets       → 预测市场

# 基础设施
oracle                   → 预言机
cross-chain-communication → 跨链
data-availability        → DA 层
infrastructure           → 基础设施

# 特色
politifi                 → 政治主题
trump-affiliated-tokens  → 特朗普相关
made-in-china            → 中国概念
made-in-usa             → 美国概念
```
