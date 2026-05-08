#!/usr/bin/env python3
"""每日涨跌预测器 — 用K线+OI特征选出明天最可能涨>5%的币种"""
import json, requests, numpy as np, os, time, pickle
from datetime import datetime, timezone
from xgboost import XGBClassifier

CACHE = "/tmp/daily_predictions.json"
MODEL_FILE = "/tmp/xgb_daily_model.pkl"
LOG_DIR = "/home/myuser/blockchair_data/predictions"
TRACK_FILE = os.path.join(LOG_DIR, "prediction_tracker.json")

def fetch_klines():
    """拉全币种日线（实时API优先，缓存补漏）"""
    klines = {}
    import concurrent.futures

    # 获取所有合约币种
    try:
        resp = requests.get('https://fapi.binance.com/fapi/v1/exchangeInfo', timeout=15)
        fut_syms = [s['symbol'] for s in resp.json()['symbols']
                    if s.get('status')=='TRADING' and s.get('quoteAsset')=='USDT' and s.get('contractType')=='PERPETUAL']
    except:
        fut_syms = []

    # 并发拉合约日线（实时数据，优先使用）
    def _fetch_fut(sym):
        try:
            r = requests.get('https://fapi.binance.com/fapi/v1/klines',
                params={'symbol': sym, 'interval': '1d', 'limit': 90}, timeout=10)
            if r.status_code == 200:
                kls = r.json()
                if len(kls) >= 30:
                    return sym, [{'t':int(k[0]),'o':float(k[1]),'h':float(k[2]),'l':float(k[3]),'c':float(k[4]),'v':float(k[5]),'q':float(k[7])} for k in kls]
        except: pass
        return sym, []

    print(f"拉取{len(fut_syms)}个合约币种日线...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
        futures = {pool.submit(_fetch_fut, s): s for s in fut_syms[:400]}
        for f in concurrent.futures.as_completed(futures):
            s, kls = f.result()
            if kls: klines[s] = kls

    # 缓存补漏（合约API没覆盖的现货币种）
    try:
        with open('/home/myuser/backtester/data_cache/notusdt_1d.json') as f:
            cached = json.load(f)['klines']
        added = 0
        for sym, kls in cached.items():
            if sym not in klines and len(kls) >= 30:
                klines[sym] = kls
                added += 1
        print(f"K线数据: {len(klines)}币种 (合约{len(klines)-added}+缓存{added})")
    except:
        print(f"K线数据: {len(klines)}币种 (全合约API)")

    return klines

def fetch_oi(syms, limit=30):
    """拉全币种30天OI"""
    import concurrent.futures
    oi_data = {}
    def _fetch(sym):
        try:
            r = requests.get('https://fapi.binance.com/futures/data/openInterestHist',
                params={'symbol': sym, 'period': '1d', 'limit': limit}, timeout=10)
            if r.status_code == 200:
                return sym, {int(o['timestamp'])//1000: float(o['sumOpenInterest']) for o in r.json()}
        except: pass
        return sym, {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as pool:
        futures = {pool.submit(_fetch, s): s for s in syms[:400]}
        for f in concurrent.futures.as_completed(futures):
            s, d = f.result()
            if d: oi_data[s] = d
    return oi_data

def build_features(klines_all, oi_data):
    """构建特征矩阵"""
    X, symbols, timestamps_list = [], [], []
    for sym, kls in klines_all.items():
        if len(kls) < 30: continue
        oi_map = oi_data.get(sym, {})
        closes = [k['c'] if isinstance(k,dict) else float(k[4]) for k in kls]
        opens = [k['o'] if isinstance(k,dict) else float(k[1]) for k in kls]
        highs = [k['h'] if isinstance(k,dict) else float(k[2]) for k in kls]
        lows = [k['l'] if isinstance(k,dict) else float(k[3]) for k in kls]
        vols = [k['q'] if isinstance(k,dict) else float(k[7]) for k in kls]
        timestamps = [k.get('t',0)//1000 if isinstance(k,dict) else int(k[0])//1000 for k in kls]
        n = len(kls)
        i = n - 2  # 用最近一根已完成的K线
        if i < 25: continue
        try:
            ret_1d = (closes[i]-closes[i-1])/closes[i-1]
            ret_3d = (closes[i]-closes[max(0,i-3)])/closes[max(0,i-3)]
            ret_5d = (closes[i]-closes[max(0,i-5)])/closes[max(0,i-5)]
            if i >= 5:
                daily_rets = [(closes[j]-closes[j-1])/closes[j-1] for j in range(i-4,i+1)]
                volatility = np.std(daily_rets)
            else: volatility = 0
            vol_ratio = vols[i]/np.mean(vols[max(0,i-5):i]) if i>=5 and np.mean(vols[max(0,i-5):i])>0 else 1
            if i >= 20:
                c20 = closes[i-20:i+1]
                price_position = (closes[i]-min(c20))/(max(c20)-min(c20)) if max(c20)!=min(c20) else 0.5
            else: price_position = 0.5
            amplitude = (highs[i]-lows[i])/opens[i] if opens[i]>0 else 0
            streak = 0
            for j in range(i, max(0,i-7)-1,-1):
                if closes[j]>opens[j]: streak+=1
                else: break
            div_sign = 1 if (closes[i]>closes[i-3] and vols[i]<vols[i-3]*0.7) else 0
            ts = timestamps[i]
            oi_now = oi_map.get(ts, 0); oi_prev = oi_map.get(ts-86400, 0)
            oi_chg = (oi_now-oi_prev)/oi_prev if oi_prev>0 else 0

            X.append([ret_1d,ret_3d,ret_5d,volatility,vol_ratio,price_position,amplitude,streak,div_sign,oi_chg])
            symbols.append(sym)
            timestamps_list.append(ts)
        except: continue
    return np.array(X), symbols, timestamps_list

def train(klines_all, oi_data):
    """训练模型 — 用前一天数据预测当天是否涨>5%"""
    Xall, yall = [], []
    for sym, kls in klines_all.items():
        if len(kls) < 30: continue
        oi_map = oi_data.get(sym, {})
        closes = [k['c'] if isinstance(k,dict) else float(k[4]) for k in kls]
        opens = [k['o'] if isinstance(k,dict) else float(k[1]) for k in kls]
        highs = [k['h'] if isinstance(k,dict) else float(k[2]) for k in kls]
        lows = [k['l'] if isinstance(k,dict) else float(k[3]) for k in kls]
        vols = [k['q'] if isinstance(k,dict) else float(k[7]) for k in kls]
        timestamps = [k.get('t',0)//1000 if isinstance(k,dict) else int(k[0])//1000 for k in kls]
        n = len(kls)

        for i in range(25, n-1):
            try:
                ret_1d = (closes[i]-closes[i-1])/closes[i-1]
                ret_3d = (closes[i]-closes[max(0,i-3)])/closes[max(0,i-3)]
                ret_5d = (closes[i]-closes[max(0,i-5)])/closes[max(0,i-5)]
                if i >= 5:
                    daily_rets = [(closes[j]-closes[j-1])/closes[j-1] for j in range(i-4,i+1)]
                    volatility = np.std(daily_rets)
                else: volatility = 0
                vol_ratio = vols[i]/np.mean(vols[max(0,i-5):i]) if i>=5 and np.mean(vols[max(0,i-5):i])>0 else 1
                if i >= 20:
                    c20 = closes[i-20:i+1]
                    price_position = (closes[i]-min(c20))/(max(c20)-min(c20)) if max(c20)!=min(c20) else 0.5
                else: price_position = 0.5
                amplitude = (highs[i]-lows[i])/opens[i] if opens[i]>0 else 0
                streak = 0
                for j in range(i, max(0,i-7)-1,-1):
                    if closes[j]>opens[j]: streak+=1
                    else: break
                div_sign = 1 if (closes[i]>closes[i-3] and vols[i]<vols[i-3]*0.7) else 0
                ts = timestamps[i]
                oi_now = oi_map.get(ts, 0); oi_prev = oi_map.get(ts-86400, 0)
                oi_chg = (oi_now-oi_prev)/oi_prev if oi_prev>0 else 0

                feat = [ret_1d,ret_3d,ret_5d,volatility,vol_ratio,price_position,amplitude,streak,div_sign,oi_chg]
                next_ret = (closes[i+1]-closes[i])/closes[i]
                label = 1 if next_ret > 0.05 else 0
                Xall.append(feat); yall.append(label)
            except: continue

    X=np.array(Xall); y=np.array(yall)
    pos=sum(y)
    print(f"训练样本: {len(y)} 涨>5%: {pos} ({pos/len(y)*100:.1f}%)")
    if pos < 10: return None

    model = XGBClassifier(n_estimators=200, max_depth=5, learning_rate=0.05,
                          scale_pos_weight=(len(y)-pos)/pos if pos>0 else 1,
                          random_state=42, eval_metric='logloss')
    model.fit(X, y)
    with open(MODEL_FILE, 'wb') as f: pickle.dump(model, f)
    return model

def predict(klines_all, oi_data, model):
    """预测今天哪些币明天可能涨>5%"""
    X, syms, tss = build_features(klines_all, oi_data)
    if len(X) == 0 or model is None:
        # 无模型则用启发式
        results = []
        for sym, kls in list(klines_all.items())[:100]:
            if len(kls) < 10: continue
            c = [k['c'] if isinstance(k,dict) else float(k[4]) for k in kls]
            ret5 = (c[-1]-c[-5])/c[-5] if len(c)>=5 else 0
            results.append({'symbol': sym, 'prob': round(ret5*100, 1)})
        results.sort(key=lambda x:-x['prob'])
        return results[:30]

    probs = model.predict_proba(X)[:, 1]
    results = []
    for i in range(len(syms)):
        results.append({'symbol': syms[i], 'prob': round(probs[i]*100, 1)})
    # 过滤一级市场/新币（数据<60天 或 成交量<50万U）
    filtered = []
    for r in zip(syms, probs):
        sym, prob = r
        kls = klines_all.get(sym, [])
        if len(kls) < 60:
            continue
        vols = [k['q'] if isinstance(k,dict) else float(k[7]) for k in kls[-5:]]
        avg_vol = np.mean(vols) if vols else 0
        if avg_vol < 500000:
            continue
        filtered.append({'symbol': sym, 'prob': round(float(prob)*100, 1)})
    filtered.sort(key=lambda x:-x['prob'])
    return filtered[:50]

def run():
    klines = fetch_klines()
    if not klines:
        print("无K线数据")
        return

    # 拉合约币种
    try:
        resp = requests.get('https://fapi.binance.com/fapi/v1/exchangeInfo', timeout=15)
        fut_syms = [s['symbol'] for s in resp.json()['symbols']
                    if s.get('status')=='TRADING' and s.get('quoteAsset')=='USDT' and s.get('contractType')=='PERPETUAL']
    except:
        fut_syms = list(klines.keys())

    print(f"拉取OI: {len(fut_syms)}个币种...")
    oi_data = fetch_oi(fut_syms)
    print(f"OI数据: {len(oi_data)}币种")

    # 训练或加载模型
    model = None
    if os.path.exists(MODEL_FILE):
        try:
            with open(MODEL_FILE, 'rb') as f: model = pickle.load(f)
            print("加载已有模型")
        except: pass

    if model is None:
        print("训练新模型...")
        model = train(klines, oi_data)
        print("训练完成" if model else "训练失败(样本不足)")

    # 预测
    results = predict(klines, oi_data, model)
    print(f"预测结果: {len(results)}个候选")

    # 保存
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    pred_data = {
        'predictions': results,
        'date': today,
        'updated': time.time(),
        'model_available': model is not None,
    }
    with open(CACHE, 'w') as f:
        json.dump(pred_data, f, default=str)

    # 存档预测到本地
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(os.path.join(LOG_DIR, f'pred_{today}.json'), 'w') as f:
        json.dump(pred_data, f, default=str)

    # 验证昨天的预测
    verify_yesterday(klines)

    # 打印TOP20
    for i, r in enumerate(results[:20]):
        print(f"  {i+1:2d}. {r['symbol']:<14s} {r['prob']:5.1f}%")

def verify_yesterday(klines_all=None):
    """验证最近一条未验证的预测（用实时API，不走缓存）"""
    import datetime as _dt
    # 找最近一条有预测但未验证的文件
    pred_files = sorted([f for f in os.listdir(LOG_DIR) if f.startswith('pred_') and f.endswith('.json')])
    if not pred_files:
        print("[验证] 无预测文件")
        return
    # 排除今天刚生成的文件（还没跑完一天）
    today_str = _dt.datetime.now(timezone.utc).strftime('%Y-%m-%d')
    yesterday_files = [f for f in pred_files if today_str not in f]
    if not yesterday_files:
        print("[验证] 仅有今日预测，无待验证文件")
        return
    pred_file = os.path.join(LOG_DIR, yesterday_files[-1])
    yesterday = yesterday_files[-1].replace('pred_','').replace('.json','')
    # 检查是否已验证过
    if os.path.exists(TRACK_FILE):
        with open(TRACK_FILE) as f:
            tracker = json.load(f)
        if any(t['date'] == yesterday for t in tracker):
            print(f"[验证] {yesterday} 已验证过，跳过")
            return

    with open(pred_file) as f:
        pred = json.load(f)

    print(f"[验证] 对比{yesterday}预测, 共{len(pred['predictions'])}个币种")
    results = []
    for p in pred.get('predictions', [])[:50]:
        sym = p['symbol']
        try:
            # 先试现货API
            resp = requests.get('https://api.binance.com/api/v3/klines',
                params={'symbol': sym, 'interval': '1d', 'limit': 3}, timeout=10)
            if resp.status_code != 200:
                # 合约备用
                resp = requests.get('https://fapi.binance.com/fapi/v1/klines',
                    params={'symbol': sym, 'interval': '1d', 'limit': 3}, timeout=10)
                if resp.status_code != 200:
                    continue
            kls = resp.json()
            if len(kls) < 2: continue
            yesterday_close = float(kls[-2][4])
            prev_close = float(kls[-3][4]) if len(kls) >= 3 else yesterday_close
            actual_ret = (yesterday_close - prev_close) / prev_close * 100
            hit = actual_ret > 5
            results.append({
                'symbol': sym, 'prob': p['prob'],
                'actual_ret': round(actual_ret, 2), 'hit': hit,
            })
        except Exception as e:
            continue

    if not results: return
    hits = sum(1 for r in results if r['hit'])
    top20_hits = sum(1 for r in results[:20] if r['hit'])
    top10_hits = sum(1 for r in results[:10] if r['hit'])

    track = {
        'date': yesterday, 'total': len(results),
        'hits': hits, 'hit_rate': round(hits/len(results)*100, 1),
        'top10_hits': top10_hits, 'top20_hits': top20_hits,
        'details': results[:30],
    }

    # 追加到跟踪文件
    tracker = []
    if os.path.exists(TRACK_FILE):
        with open(TRACK_FILE) as f:
            tracker = json.load(f)
    tracker.append(track)
    with open(TRACK_FILE, 'w') as f:
        json.dump(tracker, f, indent=2, default=str)

    print(f"\n===== 昨日验证: {yesterday} =====")
    print(f"TOP10命中: {top10_hits}/10  TOP20: {top20_hits}/20  总命中: {hits}/{len(results)} ({track['hit_rate']}%)")

    # 上传COS
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
        from qcloud_cos import CosConfig, CosS3Client
        config = CosConfig(
            Region=os.environ.get('COS_REGION', 'ap-seoul'),
            SecretId=os.environ.get('COS_SECRET_ID', ''),
            SecretKey=os.environ.get('COS_SECRET_KEY', ''),
            Endpoint=os.environ.get('COS_ENDPOINT', 'cos.ap-seoul.myqcloud.com'),
        )
        cos = CosS3Client(config)
        bucket = os.environ.get('COS_BUCKET', 'lhsj-1h-1314017643')
        # 上传跟踪文件
        cos.put_object(Bucket=bucket, Key='klines/predictions/prediction_tracker.json',
                       Body=json.dumps(tracker, indent=2).encode('utf-8'), ContentType='application/json')
        # 上传当日预测
        cos.put_object(Bucket=bucket, Key=f'klines/predictions/pred_{yesterday}.json',
                       Body=json.dumps(pred, default=str).encode('utf-8'), ContentType='application/json')
        print("[COS] 验证数据已上传")
    except Exception as e:
        print(f"[COS] 上传失败: {e}")

if __name__ == '__main__':
    run()
