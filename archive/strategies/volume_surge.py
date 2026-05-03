"""
策略: 成交量异动
条件: 成交额前10% + 月涨幅≤100% + 1h涨幅3-10%（排除BTC、ETH）
"""
import pandas as pd
import io
from datetime import datetime
from typing import List, Dict, Any
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from strategies.base import BaseStrategy
from core.data_loader import DataLoader
from configs import config
from qcloud_cos import CosConfig, CosS3Client


class VolumeSurgeStrategy(BaseStrategy):
    """成交量异动策略"""
    strategy_id = 'volume_surge'
    strategy_name = '成交量异动'

    def __init__(self,
                 min_gain_1h: float = 0.03,
                 max_gain_1h: float = 0.10,
                 max_monthly_gain: float = 1.0,
                 volume_top_pct: float = 0.10):
        super().__init__()
        self.min_gain_1h = min_gain_1h
        self.max_gain_1h = max_gain_1h
        self.max_monthly_gain = max_monthly_gain
        self.volume_top_pct = volume_top_pct
        self.monthly_df = None

    def _load_monthly_data(self) -> pd.DataFrame:
        """从COS加载月度K线数据"""
        if self.monthly_df is not None:
            return self.monthly_df

        try:
            # 获取当前年月
            now = datetime.utcnow()
            year_month = now.strftime('%Y%m')
            cos_key = f"{config.COS_MONTHLY_KEY_PREFIX}/{year_month}.parquet"

            self.logger.info(f"从COS加载月度数据: {cos_key}")

            # 初始化COS客户端
            cos_config = CosConfig(
                Region=config.COS_REGION,
                SecretId=config.COS_SECRET_ID,
                SecretKey=config.COS_SECRET_KEY,
                Endpoint=config.COS_ENDPOINT
            )
            client = CosS3Client(cos_config)

            # 从COS下载
            response = client.get_object(
                Bucket=config.COS_BUCKET,
                Key=cos_key
            )
            data = response['Body'].get_raw_stream().read()

            # 读取parquet
            self.monthly_df = pd.read_parquet(io.BytesIO(data))
            self.logger.info(f"月度数据加载成功: {len(self.monthly_df)} 条记录")
            return self.monthly_df

        except Exception as e:
            self.logger.warning(f"月度数据加载失败: {e}")
            return pd.DataFrame()

    def _get_monthly_gains(self) -> Dict[str, float]:
        """获取各币种的月涨幅"""
        df = self._load_monthly_data()
        if df.empty:
            return {}

        # 获取最新的月度数据（按timestamp排序取最后一条）
        df_sorted = df.sort_values(['symbol', 'timestamp'])
        latest = df_sorted.groupby('symbol').last().reset_index()

        # 返回symbol到monthly_gain的映射
        return latest.set_index('symbol')['monthly_gain'].to_dict()

    def scan(self) -> Dict[str, Any]:
        """主扫描逻辑"""
        df = DataLoader.get_klines(use_cache=True)
        df = df.sort_values(['symbol', 'timestamp'])

        self.logger.info("=" * 60)
        self.logger.info(f"{self.strategy_name} 扫描开始")
        self.logger.info(f"筛选条件: 涨幅{self.min_gain_1h*100:.0f}%-{self.max_gain_1h*100:.0f}%, 成交额前{self.volume_top_pct*100:.0f}%, 月涨幅≤{self.max_monthly_gain*100:.0f}%")
        self.logger.info("=" * 60)

        # 只取已完整走完的24小时数据计算成交额（排除最后1根可能未完成的K线）
        max_time = df['timestamp'].max() - pd.Timedelta(hours=1)
        df_24h = df[(df['timestamp'] >= max_time - pd.Timedelta(hours=24)) & (df['timestamp'] <= max_time)]
        
        # Step 1: 成交额前X%（排除BTC、ETH）
        vol_24h = df_24h.groupby('symbol')['quote_volume'].sum()
        exclude = {'BTCUSDT', 'ETHUSDT'}
        vol_sorted = vol_24h.drop(exclude, errors='ignore').sort_values(ascending=False)
        top10_pct_count = max(1, int(len(vol_sorted) * self.volume_top_pct))
        top_symbols = set(vol_sorted.head(top10_pct_count).index)
        vol_threshold = vol_sorted.iloc[top10_pct_count - 1] if len(vol_sorted) >= top10_pct_count else 0

        self.logger.info(f"成交额前{self.volume_top_pct*100:.0f}%阈值: {vol_threshold/1e6:.2f}M USDT, 共 {len(top_symbols)} 个候选")

        step_symbols = {
            'step1': [],  # 成交额前10%
            'step2': [],  # 月涨幅过滤后
            'step3': []   # 1h涨幅筛选后
        }

        # 记录step1的币种
        for sym in top_symbols:
            step_symbols['step1'].append({
                'symbol': sym,
                'vol_24h': round(vol_24h.get(sym, 0), 0)
            })

        # Step 2: 月涨幅过滤（≤100%）
        monthly_gains = self._get_monthly_gains()
        filtered_symbols = []

        for sym in top_symbols:
            monthly_gain = monthly_gains.get(sym)
            if monthly_gain is not None and monthly_gain > self.max_monthly_gain:
                self.logger.info(f"  剔除 {sym}: 月涨幅 {monthly_gain*100:.1f}% > {self.max_monthly_gain*100:.0f}%")
                continue
            filtered_symbols.append(sym)

        self.logger.info(f"月涨幅过滤后: {len(filtered_symbols)} 个候选")

        # 记录step2的币种
        for sym in filtered_symbols:
            step_symbols['step2'].append({
                'symbol': sym,
                'vol_24h': round(vol_24h.get(sym, 0), 0),
                'monthly_gain': monthly_gains.get(sym, 0)
            })

        # Step 3: 1h涨幅3-10%（使用已完整走完的K线）
        results = []
        all_symbols_bars = []

        for sym in filtered_symbols:
            # 获取最后3根K线（最后1根可能未完成，用倒数第2根）
            grp = df[df['symbol'] == sym].tail(3)
            if len(grp) < 2:
                continue

            # 使用倒数第2根已完整走完的K线计算涨跌幅
            last_complete = grp.iloc[-2]
            
            if last_complete['open'] <= 0:
                continue

            # 单根K线涨跌幅 = (收盘价 - 开盘价) / 开盘价
            gain_1h = (last_complete['close'] - last_complete['open']) / last_complete['open']

            if self.min_gain_1h <= gain_1h <= self.max_gain_1h:
                # 构建bars数据（最近6根K线用于展示，排除最后1根未完成的）
                bars_raw = []
                recent_6h = df[df['symbol'] == sym].tail(7).head(6)  # 排除最后1根
                for _, row in recent_6h.iterrows():
                    bars_raw.append({
                        't': row['timestamp'].strftime('%m-%d %H:%M'),
                        'o': round(row['open'], 8),
                        'h': round(row['high'], 8),
                        'l': round(row['low'], 8),
                        'c': round(row['close'], 8),
                        'v': round(row['quote_volume']/1e6, 4)
                    })

                result_item = {
                    'symbol': sym,
                    'price': round(last_complete['close'], 6),
                    'gain_1h': round(gain_1h * 100, 2),
                    'vol_24h': round(vol_24h.get(sym, 0), 0),
                    'monthly_gain': round(monthly_gains.get(sym, 0) * 100, 2) if sym in monthly_gains else None,
                    'time': last_complete['timestamp'].strftime('%H:%M'),
                    'bars': bars_raw
                }
                results.append(result_item)

                all_symbols_bars.append({
                    'symbol': sym,
                    'bars': bars_raw
                })

                step_symbols['step3'].append(result_item)

        results.sort(key=lambda x: -x['gain_1h'])

        self.logger.info(f"成交量异动: 符合条件 {len(results)} 个")

        # 构建check_stats
        check_stats = {
            'total': len(top_symbols),
            'step1': len(step_symbols['step1']),
            'step2': len(step_symbols['step2']),
            'step3': len(step_symbols['step3'])
        }

        return {
            'items': results,
            'conditions': [
                f"成交额前{self.volume_top_pct*100:.0f}%（排除BTC、ETH）",
                f"月涨幅≤{self.max_monthly_gain*100:.0f}%",
                f"1小时涨幅{self.min_gain_1h*100:.0f}%-{self.max_gain_1h*100:.0f}%"
            ],
            'summary': {
                'total_signals': len(results),
                'check_stats': check_stats,
                'step_symbols': step_symbols,
                'all_symbols_bars': all_symbols_bars
            }
        }


def run_strategy():
    """运行策略的入口函数"""
    import json
    import os
    from datetime import datetime
    
    strategy = VolumeSurgeStrategy()
    result = strategy.run(generate_charts=False, save_to_db=True)
    
    # 同时保存到 /var/www/ 目录供前端API读取
    try:
        www_file = '/var/www/volume_surge.json'
        # 确保目录存在
        os.makedirs(os.path.dirname(www_file), exist_ok=True)
        with open(www_file, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        strategy.logger.info(f"报告已同步保存到: {www_file}")
    except Exception as e:
        strategy.logger.warning(f"保存到 /var/www/ 失败: {e}")
    
    return result


if __name__ == '__main__':
    run_strategy()
