"""
策略: 成交量进阶
逻辑: 第一步: 从 strategy1_pro.json 读取稳步抬升PRO的step步骤数据，让用户选择step
      第二步: 实时扫描成交量异动（用户设置涨幅范围、成交量范围）
      两个策略都有的币种，生成新的信号
"""
import pandas as pd
import json as _json
import os
import glob
import requests
from datetime import datetime
from typing import List, Dict, Any
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from strategies.base import BaseStrategy
from strategies.volume_surge import VolumeSurgeStrategy
from core.data_loader import DataLoader
from configs import config


class VolumeAdvancedStrategy(BaseStrategy):
    """成交量进阶策略"""
    strategy_id = 'volume_advanced'
    strategy_name = '成交量进阶'

    def __init__(self,
                 min_gain_1h: float = 0.01,
                 max_gain_1h: float = 0.05,
                 volume_top_pct: float = 0.50,
                 selected_step: str = 'step1'):
        super().__init__()
        self.min_gain_1h = min_gain_1h
        self.max_gain_1h = max_gain_1h
        self.volume_top_pct = volume_top_pct
        self.selected_step = selected_step  # 用户选择的step: step1~step6

    def _fetch_strategy1_pro_data(self) -> Dict[str, Any]:
        """通过 API 获取 strategy1_pro 数据，失败则 fallback 到本地文件"""
        # 优先通过 API 获取，确保和网站显示的数据源一致
        ports = [getattr(config, 'WEB_PORT', 5000), 5002, 5000]
        for port in set(ports):
            try:
                url = f'http://127.0.0.1:{port}/api/report/strategy1_pro'
                resp = requests.get(url, timeout=5)
                if resp.status_code == 200:
                    api_data = resp.json()
                    if api_data.get('code') == 0 and api_data.get('data'):
                        self.logger.info(f"通过 API 获取 strategy1_pro 数据成功: {url}")
                        return api_data['data']
            except Exception as e:
                self.logger.debug(f"API 端口 {port} 获取失败: {e}")
                continue

        self.logger.warning("API 获取 strategy1_pro 失败，fallback 到本地文件")

        # fallback: 本地文件
        candidates = []
        main_file = '/home/ubuntu/crypto-scanner/output/strategy1_pro.json'
        if os.path.exists(main_file):
            candidates.append(main_file)

        output_dir = '/home/ubuntu/crypto-scanner/output'
        files = glob.glob(os.path.join(output_dir, 'strategy1_pro_*.json'))
        if files:
            files.sort(key=os.path.getmtime, reverse=True)
            candidates.append(files[0])

        www_file = '/var/www/strategy1_pro.json'
        if os.path.exists(www_file):
            candidates.append(www_file)

        if candidates:
            candidates.sort(key=os.path.getmtime, reverse=True)
            latest_file = candidates[0]
            try:
                with open(latest_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                self.logger.error(f"读取本地文件失败: {e}")

        return {}

    def get_available_steps(self) -> Dict[str, Any]:
        """获取 strategy1_pro 最新的 step 数据，返回可选的 step 选项"""
        try:
            data = self._fetch_strategy1_pro_data()
            if not data:
                return {
                    'error': 'strategy1_pro 数据不存在',
                    'steps': {},
                    'file': '',
                    'timestamp': ''
                }

            step_symbols = data.get('summary', {}).get('step_symbols', {})
            timestamp = data.get('timestamp', '')
            scan_cutoff = data.get('summary', {}).get('scan_cutoff_hour', '')

            # 构建每个 step 的概览
            steps_info = {}
            for step_name in ['step1', 'step2', 'step3', 'step4', 'step5', 'step6']:
                symbols = step_symbols.get(step_name, [])
                # 必须以后端实际返回的 symbols 列表长度为准，避免 check_stats 与 step_symbols 不一致导致前端显示错误
                passed_count = len(symbols)
                steps_info[step_name] = {
                    'count': passed_count,
                    'symbols': [
                        {
                            'symbol': s.get('symbol', ''),
                            'price': s.get('price', 0),
                            'bars_count': len(s.get('bars', []))
                        }
                        for s in symbols
                    ]
                }

            return {
                'error': None,
                'steps': steps_info,
                'file': 'api:/api/report/strategy1_pro',
                'timestamp': timestamp,
                'scan_cutoff': scan_cutoff,
                'total_signals': data.get('summary', {}).get('total_signals', 0)
            }

        except Exception as e:
            self.logger.error(f"读取 strategy1_pro 数据失败: {e}")
            return {
                'error': f'读取失败: {str(e)}',
                'steps': {},
                'file': '',
                'timestamp': ''
            }

    def _load_strategy1_pro_from_cache(self) -> Dict[str, Dict[str, Any]]:
        """从 strategy1_pro 读取指定 step 的币种数据"""
        result_symbols = {}
        data = self._fetch_strategy1_pro_data()

        if not data:
            self.logger.warning("strategy1_pro 数据不存在")
            return result_symbols

        try:
            step_symbols = data.get('summary', {}).get('step_symbols', {})

            # 根据用户选择的 step 获取对应的币种
            # 如果选 step3，就取 step3 及以后所有 step 的并集（更严格的筛选）
            step_num = int(self.selected_step.replace('step', ''))
            selected_symbols = set()

            for i in range(step_num, 7):
                step_key = f'step{i}'
                if step_key in step_symbols:
                    for item in step_symbols[step_key]:
                        selected_symbols.add(item.get('symbol', ''))

            self.logger.info(f"strategy1_pro({self.selected_step}): 选中 {len(selected_symbols)} 个币种")

            # 从所有 step 中合并这些币种的详细信息（取最详细的）
            all_step_data = {}
            for step_key in ['step1', 'step2', 'step3', 'step4', 'step5', 'step6']:
                for item in step_symbols.get(step_key, []):
                    symbol = item.get('symbol', '')
                    if symbol in selected_symbols:
                        # 保留最详细的 step 数据
                        if symbol not in all_step_data or len(item.get('bars', [])) > len(all_step_data[symbol].get('bars', [])):
                            all_step_data[symbol] = item

            # 构建返回结果
            for symbol in selected_symbols:
                if symbol in all_step_data:
                    item = all_step_data[symbol]
                    bars = item.get('bars', [])
                    result_symbols[symbol] = {
                        'price': item.get('price', 0),
                        'bars': bars,
                        'hrs': len(bars),
                        'step': self.selected_step
                    }

            self.logger.info(f"strategy1_pro({self.selected_step}): 详细信息 {len(result_symbols)} 个")

        except Exception as e:
            self.logger.error(f"读取 strategy1_pro 缓存失败: {e}")

        return result_symbols

    def scan(self) -> Dict[str, Any]:
        """主扫描逻辑：读取PRO的step数据 + 实时扫描成交量，交叉比较"""
        df = DataLoader.get_klines(use_cache=True)
        df = df.sort_values(['symbol', 'timestamp'])

        self.logger.info("=" * 60)
        self.logger.info(f"{self.strategy_name} 扫描开始")
        self.logger.info(f"条件: strategy1_pro({self.selected_step}) ∩ 成交量异动(涨幅{self.min_gain_1h*100:.0f}%-{self.max_gain_1h*100:.0f}%, 成交额前{self.volume_top_pct*100:.0f}%)")
        self.logger.info("=" * 60)

        # Step 1: 读取 strategy1_pro 指定 step 的币种
        strategy1_pro_symbols = self._load_strategy1_pro_from_cache()

        # Step 2: 实时扫描成交量异动，使用用户设置的参数
        volume_surge_symbols = {}
        try:
            volume_strategy = VolumeSurgeStrategy(
                min_gain_1h=self.min_gain_1h,
                max_gain_1h=self.max_gain_1h,
                volume_top_pct=self.volume_top_pct
            )
            volume_strategy.df = df.copy()
            volume_result = volume_strategy.scan()

            for item in volume_result.get('items', []):
                symbol = item.get('symbol', '')
                volume_surge_symbols[symbol] = {
                    'price': item.get('price', 0),
                    'gain_1h': item.get('gain_1h', 0),
                    'vol_24h': item.get('vol_24h', 0),
                    'monthly_gain': item.get('monthly_gain'),
                    'time': item.get('time', ''),
                    'bars': item.get('bars', [])
                }

            self.logger.info(f"成交量异动(实时): 涨幅{self.min_gain_1h*100:.0f}%-{self.max_gain_1h*100:.0f}%, 成交额前{self.volume_top_pct*100:.0f}% → {len(volume_surge_symbols)} 个")

        except Exception as e:
            self.logger.error(f"成交量异动实时扫描失败: {e}")

        # Step 3: 交叉比较
        common_symbols = set(strategy1_pro_symbols.keys()) & set(volume_surge_symbols.keys())

        self.logger.info(f"交叉比较: strategy1_pro({self.selected_step}) {len(strategy1_pro_symbols)} 个, 成交量异动 {len(volume_surge_symbols)} 个, 共同 {len(common_symbols)} 个")

        # Step 4: 构建结果
        results = []
        all_symbols_bars = []

        for symbol in common_symbols:
            pro_info = strategy1_pro_symbols[symbol]
            volume_info = volume_surge_symbols[symbol]

            result_item = {
                'symbol': symbol,
                'price': pro_info['price'],
                'vol_24h': volume_info['vol_24h'],
                'gain_1h': volume_info['gain_1h'],
                'monthly_gain': volume_info.get('monthly_gain'),
                'time': volume_info['time'],
                'pro_step': pro_info['step'],
                'pro_hrs': pro_info['hrs'],
                'bars': pro_info.get('bars', []),
                'volume_bars': volume_info.get('bars', [])
            }

            results.append(result_item)
            all_symbols_bars.append({
                'symbol': symbol,
                'bars': pro_info.get('bars', []),
                'volume_bars': volume_info.get('bars', [])
            })

        results.sort(key=lambda x: -x['vol_24h'])

        self.logger.info(f"成交量进阶: 最终符合条件 {len(results)} 个")

        check_stats = {
            'total_symbols': len(df['symbol'].unique()),
            'strategy1_pro_step': self.selected_step,
            'strategy1_pro_count': len(strategy1_pro_symbols),
            'volume_surge_count': len(volume_surge_symbols),
            'advanced_count': len(results)
        }

        return {
            'items': results,
            'conditions': [
                f"strategy1_pro: {self.selected_step} 及以后步骤",
                f"成交量异动: 成交额前{self.volume_top_pct*100:.0f}%",
                f"1小时涨幅{self.min_gain_1h*100:.0f}%-{self.max_gain_1h*100:.0f}%"
            ],
            'summary': {
                'total_signals': len(results),
                'check_stats': check_stats,
                'all_symbols_bars': all_symbols_bars,
                'strategy1_pro_symbols': list(strategy1_pro_symbols.keys()),
                'volume_surge_symbols': list(volume_surge_symbols.keys())
            }
        }

    def run(self, generate_charts: bool = False, save_to_db: bool = True):
        """主运行逻辑：读取PRO的step数据 + 实时扫描成交量，交叉比较，并写入文件"""
        self.logger.info("=" * 60)
        self.logger.info(f"{self.strategy_name} 扫描开始")
        self.logger.info(f"条件: strategy1_pro({self.selected_step}) ∩ 成交量异动(涨幅{self.min_gain_1h*100:.0f}%-{self.max_gain_1h*100:.0f}%, 成交额前{self.volume_top_pct*100:.0f}%)")
        self.logger.info("=" * 60)

        if self.df is None:
            self.load_data()

        scan_result = self.scan()
        items = scan_result.get('items', [])
        summary = scan_result.get('summary', {})

        # 写入统一报告文件
        output_path = Path(os.environ.get('NGINX_WWW_DIR', '/var/www')) / f'{self.strategy_id}.json'
        output_path.parent.mkdir(exist_ok=True, parents=True)
        save_data = {
            'items': items,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'summary': summary
        }
        with open(output_path, 'w', encoding='utf-8') as f:
            _json.dump(save_data, f, ensure_ascii=False, indent=2)

        self.logger.info(f"成交量进阶: 最终符合条件 {len(items)} 个")
        return save_data


def run_strategy(selected_step='step1', min_gain=0.01, max_gain=0.05, volume_pct=0.50):
    """运行策略的入口函数"""
    strategy = VolumeAdvancedStrategy(
        selected_step=selected_step,
        min_gain_1h=min_gain,
        max_gain_1h=max_gain,
        volume_top_pct=volume_pct
    )
    return strategy.run(generate_charts=False, save_to_db=True)


if __name__ == '__main__':
    run_strategy()
