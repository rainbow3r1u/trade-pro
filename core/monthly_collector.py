"""
月度K线数据采集器
每月1号 UTC 00:05 执行，采集上月的月度K线数据
慢速采集模式，避免触发币安API限流
"""
import io
import time
import pandas as pd
from datetime import datetime, timezone
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

from qcloud_cos import CosConfig, CosS3Client
from requests.exceptions import RequestException
import requests

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from configs import config
from utils.logger import get_logger

logger = get_logger('monthly_collector')

request_lock = threading.Lock()
request_count = 0
_BANNED_UNTIL = 0
_BANNED_RETRY_DELAY = 1.0  # 月度采集使用更长的延迟


class MonthlyKlineCollector:
    """月度K线数据采集器 - 慢速模式"""
    
    def __init__(self):
        self.api_base = config.BINANCE_API
        self.max_retries = config.MAX_RETRIES
        self.retry_delays = config.RETRY_DELAY_SECONDS
        self.timeout = config.REQUEST_TIMEOUT
        self.concurrent_workers = 1  # 月度采集使用单线程，避免限流
        self.request_delay = 2.0  # 每个请求间隔2秒

    def _api_request(self, path: str, params: Dict = None) -> Any:
        """API请求，带熔断和限流保护"""
        global request_count, _BANNED_UNTIL, _BANNED_RETRY_DELAY
        url = f"{self.api_base}{path}"
        
        for attempt in range(self.max_retries):
            # 熔断检查
            if time.time() < _BANNED_UNTIL:
                wait_time = _BANNED_UNTIL - time.time()
                logger.warning(f"熔断中，等待 {wait_time:.0f} 秒")
                time.sleep(wait_time)
            
            try:
                resp = requests.get(url, params=params, timeout=self.timeout)
                
                # 418熔断判断
                if resp.status_code == 418:
                    wait_sec = int(resp.headers.get("Retry-After", 300))
                    _BANNED_UNTIL = time.time() + wait_sec
                    _BANNED_RETRY_DELAY = min(_BANNED_RETRY_DELAY * 2, 3600)
                    logger.error(f"触发418熔断，封锁至 {datetime.fromtimestamp(_BANNED_UNTIL)}")
                    break
                
                # 429限流判断
                if resp.status_code == 429:
                    wait_sec = int(resp.headers.get("Retry-After", 60))
                    logger.warning(f"触发429限流，等待 {wait_sec} 秒")
                    time.sleep(wait_sec)
                    continue
                
                resp.raise_for_status()
                with request_lock:
                    request_count += 1
                return resp.json()
                
            except Exception as e:
                logger.warning(f"请求失败 (尝试 {attempt+1}/{self.max_retries}): {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delays[attempt])
                else:
                    logger.error(f"请求最终失败: {url}")
                    return None
        return None

    def get_perpetual_symbols(self) -> List[str]:
        """获取所有USDT永续合约"""
        data = self._api_request("/fapi/v1/exchangeInfo")
        if not data:
            logger.error("获取交易对信息失败")
            return []

        symbols = []
        for s in data.get('symbols', []):
            if (s.get('contractType') == 'PERPETUAL'
                    and s.get('quoteAsset') == 'USDT'
                    and s.get('status') == 'TRADING'
                    and s.get('isInverse') is not True):
                symbol = s['symbol']
                if 'UP' not in symbol and 'DOWN' not in symbol:
                    symbols.append(symbol)

        logger.info(f"获取到 {len(symbols)} 个 USDT 永续合约")
        return symbols

    def fetch_monthly_klines(self, symbol: str) -> List[Dict]:
        """
        获取月度K线数据
        使用 1M 间隔，获取最近2个月的K线
        """
        params = {
            'symbol': symbol,
            'interval': '1M',
            'limit': 2  # 最近2个月
        }
        data = self._api_request("/fapi/v1/klines", params=params)
        if not data:
            return []

        rows = []
        for k in data:
            rows.append({
                'symbol': symbol,
                'timestamp': k[0],
                'open': float(k[1]),
                'high': float(k[2]),
                'low': float(k[3]),
                'close': float(k[4]),
                'volume': float(k[5]),
                'quote_volume': float(k[7])  # 成交额
            })
        return rows

    def _fetch_task(self, symbol: str, idx: int, total: int) -> tuple:
        """单个币种的采集任务"""
        rows = self.fetch_monthly_klines(symbol)
        if idx % 10 == 0 or idx == total:
            logger.info(f"进度: {idx}/{total} - {symbol}")
        
        # 慢速模式：每个请求后等待
        time.sleep(self.request_delay)
        return symbol, rows

    def _upload_to_cos(self, local_file: str, cos_key: str) -> bool:
        """上传文件到COS"""
        cos_config = CosConfig(
            Region=config.COS_REGION,
            SecretId=config.COS_SECRET_ID,
            SecretKey=config.COS_SECRET_KEY,
            Endpoint=config.COS_ENDPOINT
        )
        client = CosS3Client(cos_config)

        for attempt in range(self.max_retries):
            try:
                resp = client.upload_file(
                    Bucket=config.COS_BUCKET,
                    LocalFilePath=local_file,
                    Key=cos_key,
                    EnableMD5=False
                )
                logger.info(f"上传成功，ETag: {resp['ETag']}")
                return True
            except RequestException as e:
                logger.warning(f"上传失败 (尝试 {attempt+1}/{self.max_retries}): {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delays[attempt])
                else:
                    logger.error(f"上传最终失败: {local_file}")
                    return False
        return False

    def run(self) -> bool:
        """执行月度数据采集"""
        global request_count
        start_time = datetime.now(timezone.utc)
        
        # 获取当前年月
        now = datetime.now(timezone.utc)
        year_month = now.strftime('%Y%m')
        
        logger.info("=" * 60)
        logger.info(f"开始采集月度K线数据 - {year_month}")
        logger.info(f"慢速模式: 单线程，请求间隔 {self.request_delay} 秒")
        logger.info("=" * 60)

        symbols = self.get_perpetual_symbols()
        if not symbols:
            logger.error("未获取到任何永续合约，退出")
            return False

        total = len(symbols)
        all_rows = []
        success_count = 0
        fail_count = 0

        # 单线程慢速采集
        for idx, symbol in enumerate(symbols, 1):
            try:
                _, rows = self._fetch_task(symbol, idx, total)
                if rows:
                    all_rows.extend(rows)
                    success_count += 1
                else:
                    fail_count += 1
            except Exception as e:
                logger.warning(f"{symbol} 获取失败: {e}")
                fail_count += 1

        if not all_rows:
            logger.error("未采集到任何月度K线数据，退出")
            return False

        # 构建DataFrame
        df = pd.DataFrame(all_rows)
        df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
        
        # 计算月涨幅
        df['monthly_gain'] = (df['close'] - df['open']) / df['open']
        
        logger.info(f"共采集到 {len(df)} 条月度K线记录, {df['symbol'].nunique()} 个币种")
        logger.info(f"成功: {success_count}, 失败: {fail_count}, 总请求: {request_count}")

        # 保存到本地
        local_file = f"/tmp/monthly_klines_{year_month}.parquet"
        df.to_parquet(local_file, index=False)
        
        # 上传到COS
        cos_key = f"klines/monthly/{year_month}.parquet"
        success = self._upload_to_cos(local_file, cos_key)

        # 清理本地文件
        import os
        if os.path.exists(local_file):
            os.remove(local_file)

        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
        logger.info(f"总耗时: {elapsed:.2f} 秒")

        if success:
            logger.info("=" * 60)
            logger.info("月度数据采集上传成功")
            logger.info("=" * 60)
        else:
            logger.error("月度数据上传失败")

        return success


def run_collector() -> bool:
    """外部调用入口"""
    collector = MonthlyKlineCollector()
    return collector.run()


if __name__ == '__main__':
    run_collector()
