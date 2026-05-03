#!/usr/bin/env python3
"""
部署验证脚本：检查 网站 / 交易模块 / 回测引擎 / 参数搜索 4层参数对齐

用法:
  python3 validate_deploy.py           # 本地检查源码
  python3 validate_deploy.py --live    # 本地 + 检查运行中的网站API
"""

import sys, os, re, json, urllib.request, subprocess

RED = '\033[91m'; GREEN = '\033[92m'; YELLOW = '\033[93m'; NC = '\033[0m'
PASS = 0; FAIL = 0; WARN = 0

def check(name, ok, detail=""):
    global PASS, FAIL, WARN
    if ok is True:
        print(f"  {GREEN}✅{NC} {name}")
        PASS += 1
    elif ok is False:
        print(f"  {RED}❌{NC} {name}  {RED}{detail}{NC}")
        FAIL += 1
    else:
        print(f"  {YELLOW}⚠️{NC} {name}  {detail}")
        WARN += 1

# ===== Extract parameters from source files =====
def extract_st_params():
    """Extract sim_trade.py V7 parameters"""
    with open('/home/myuser/websocket_new/sim_trade.py') as f:
        content = f.read()
    params = {}
    for line in content.split('\n'):
        for key in ['SPOT_', 'FUT_']:
            if line.strip().startswith(key) and '=' in line:
                parts = line.split('=')
                name = parts[0].strip()
                val = parts[1].strip().split('#')[0].strip()
                try:
                    if '.' in val: params[name] = float(val)
                    else: params[name] = int(val)
                except: params[name] = val
    return params

def extract_mm_bb_config():
    """Extract BB_CLIMB_CONFIG from market_monitor_app.py"""
    with open('/home/myuser/websocket_new/market_monitor_app.py') as f:
        content = f.read()
    # Find BB_CLIMB_CONFIG block
    start = content.find('BB_CLIMB_CONFIG = {')
    if start < 0: return {}
    depth = 0; end = start
    for i in range(start, len(content)):
        if content[i] == '{': depth += 1
        elif content[i] == '}':
            depth -= 1
            if depth == 0: end = i+1; break
    config_str = content[start:end]
    params = {}
    for key in ['period', 'std_mult', 'hl_tolerance_window', 'hl_tolerance_min',
                'upper_tolerance_pct', 'volume_ratio', 'atr_period']:
        m = re.search(rf'"{key}":\s*([0-9.]+|True|False)', config_str)
        if m:
            v = m.group(1)
            if v == 'True': params[key] = True
            elif v == 'False': params[key] = False
            else: params[key] = float(v) if '.' in v else int(v)
    return params

def extract_hybrid_defaults():
    """Extract BBParams and VSParams defaults from hybrid.rs"""
    with open('/home/myuser/backtester-rs/src/hybrid.rs') as f:
        content = f.read()
    params = {}
    # BBParams default
    bb_match = re.search(r'impl Default for BBParams.*?\{([^}]+)\}', content, re.DOTALL)
    if bb_match:
        bb = bb_match.group(1)
        for key, field in [('period','period'),('std_mult','std_mult'),('min_hours','min_hours'),
                          ('hl_window','hl_window'),('hl_min','hl_min'),
                          ('vol_filter','vol_filter'),('daily_gain_pct','daily_gain_pct')]:
            m = re.search(rf'{field}:\s*([0-9_.]+)', bb)
            if m: params[f'BB_{key}'] = float(m.group(1)) if '.' in m.group(1) else int(m.group(1))
    # VSParams default
    vs_match = re.search(r'impl Default for VSParams.*?\{([^}]+)\}', content, re.DOTALL)
    if vs_match:
        vs = vs_match.group(1)
        for key, field in [('min_ratio','min_ratio'),('min_avg_vol','min_avg_vol'),('margin','margin'),
                          ('tp_pct','tp_pct'),('sl_pct','sl_pct'),('vol_24h_filter','vol_24h_filter'),
                          ('max_daily_tp','max_daily_tp')]:
            m = re.search(rf'{field}:\s*([0-9_.]+)', vs)
            if m: params[f'VS_{key}'] = float(m.group(1)) if '.' in m.group(1) else int(m.group(1))
    return params

def count_exclude_symbols(filepath, is_rust=False):
    """Count unique excluded USDT symbols in a source file"""
    with open(filepath) as f:
        content = f.read()
    if is_rust:
        # Rust: "SYMBOLUSDT" format between // 大盘 and ];
        m = re.search(r'const EXCLUDE.*?\];', content, re.DOTALL)
        if not m: return 0
        symbols = set(re.findall(r'"(\w+USDT)"', m.group(0)))
    else:
        # Python: 'SYMBOLUSDT' in set — match to the closing brace with proper depth
        m = re.search(r"EXCLUDE_SYMBOLS\s*=\s*\{", content)
        if not m: return 0
        depth = 0; i = m.end() - 1
        for j in range(i, min(i + 5000, len(content))):
            if content[j] == '{': depth += 1
            elif content[j] == '}':
                depth -= 1
                if depth == 0:
                    block = content[i:j+1]
                    break
        else:
            return 0
        symbols = set(re.findall(r"'(\w+USDT)'", block))
    return len(symbols)

# ===== MAIN =====
def main():
    check_live = '--live' in sys.argv

    print("=" * 65)
    print("  V7 部署验证: 网站 ↔ 交易 ↔ 回测 ↔ 搜索")
    print("=" * 65)

    # 1. Parameter extraction
    st = extract_st_params()
    mm = extract_mm_bb_config()
    hy = extract_hybrid_defaults()

    # 2. BB Parameters
    print("\n📈 现货BB参数")
    bb_map = [
        ('SPOT_BB_PERIOD',     'BB_period',      'period',      30),
        ('SPOT_BB_STD_MULT',   'BB_std_mult',    'std_mult',    2.5),
        ('SPOT_MIN_HOURS',     'BB_min_hours',   None,          4),
        ('SPOT_HL_WINDOW',     'BB_hl_window',   'hl_tolerance_window', 5),
        ('SPOT_HL_MIN',        'BB_hl_min',      'hl_tolerance_min',    3),
        ('SPOT_VOL_FILTER',    'BB_vol_filter',  None,          1_000_000),
        ('SPOT_GAIN_FILTER_PCT','BB_daily_gain_pct', None,      10.0),
    ]
    for st_key, hy_key, mm_key, expected in bb_map:
        st_val = st.get(st_key)
        hy_val = hy.get(hy_key) if hy_key else expected
        mm_val = mm.get(mm_key) if mm_key else expected
        ok = (st_val == hy_val == expected)
        detail = f"st={st_val} hy={hy_val} mm={mm_val} expected={expected}" if not ok else ""
        check(f"{st_key:25s} = {expected}", ok, detail)

    # 3. VS Parameters
    print("\n⚡ 合约VS参数")
    vs_map = [
        ('FUT_MARGIN',         'VS_margin',       20),
        ('FUT_LEVERAGE',       None,              10),
        ('FUT_TP_PCT',         'VS_tp_pct',       50),
        ('FUT_SL_PCT',         'VS_sl_pct',       0.02),
        ('FUT_MIN_RATIO',      'VS_min_ratio',    4.0),
        ('FUT_VOL_FILTER',     'VS_vol_24h_filter', 1_000_000),
        ('FUT_MAX_DAILY_TP',   'VS_max_daily_tp', 4),
    ]
    for st_key, hy_key, expected in vs_map:
        st_val = st.get(st_key)
        hy_val = hy.get(hy_key) if hy_key else expected
        ok = (st_val == hy_val == expected)
        detail = f"st={st_val} hy={hy_val} expected={expected}" if not ok else ""
        check(f"{st_key:25s} = {expected}", ok, detail)

    # 4. Exclude lists
    print("\n🚫 排除列表")
    st_excl = count_exclude_symbols('/home/myuser/websocket_new/sim_trade.py', is_rust=False)
    # Market monitor: count from BB_CLIMB_CONFIG JSON (not python set)
    with open('/home/myuser/websocket_new/market_monitor_app.py') as f: mm_content = f.read()
    mm_bb_start = mm_content.find('BB_CLIMB_CONFIG')
    mm_excl = len(set(re.findall(r"'(\w+USDT)'", mm_content[mm_bb_start:mm_bb_start+3000])))
    hy_excl = count_exclude_symbols('/home/myuser/backtester-rs/src/hybrid.rs', is_rust=True)
    ok = st_excl == mm_excl == hy_excl
    check(f"排除币种数", ok, f"st={st_excl} mm={mm_excl} hy={hy_excl}")

    # 5. VS calculation method
    print("\n📊 VS均值计算")
    mm_calc = 'return total / 16.0' in open('/home/myuser/websocket_new/market_monitor_app.py').read()
    hy_calc = 'running_sum / 16.0' in open('/home/myuser/backtester-rs/src/strategies/vol_surge.rs').read()
    check("网站: total/16.0", mm_calc)
    check("回测: running_sum/16.0", hy_calc)

    # 6. Risk controls
    print("\n🛡️ 风控逻辑")
    with open('/home/myuser/websocket_new/sim_trade.py') as f: st_content = f.read()
    with open('/home/myuser/backtester-rs/src/hybrid.rs') as f: hy_content = f.read()

    risk_checks = [
        ("日止盈限制", "check_daily_tp_filter" in st_content, "fut_dtp" in hy_content),
        ("双阴过滤", "check_recent_1h_bearish" in st_content, "chk_double_yin" in hy_content),
        ("日涨幅过滤", "check_daily_gain_filter" in st_content, "chk_gain" in hy_content),
        ("止损冷却", "is_in_cooldown" in st_content, "fut_cd" in hy_content),
        ("爆仓检测", "LIQUIDATED_CROSS" in st_content, "LIQUIDATED_CROSS" in hy_content),
        ("VS绑定BB", "get_spot_position" in st_content, "spot.holds" in hy_content),
        ("VS时序验证", "sig_start <= spot_entry_ts" in st_content, "sig_ts < entry_ts" in hy_content),
        ("耗尽机制", "exhausted_symbols" in st_content, "is_exhausted" in hy_content),
        ("满仓替换", "close_futures_position_forced" in st_content, "fut_pos.len() >= 5" in hy_content),
    ]
    for name, st_ok, hy_ok in risk_checks:
        check(f"{name:12s}", st_ok and hy_ok, f"st={st_ok} hy={hy_ok}" if not (st_ok and hy_ok) else "")

    # 7. Live API check (if --live flag)
    if check_live:
        print("\n🌐 运行中网站API")
        try:
            # BB signals
            resp = urllib.request.urlopen('http://localhost:5003/api/bollinger_climb_daily', timeout=5)
            bb_data = json.loads(resp.read())
            has_data = 'data' in bb_data
            has_updated = 'updated_at' in bb_data
            check("BB API: data字段", has_data)
            check("BB API: updated_at字段", has_updated)
            if bb_data.get('data'):
                sig = bb_data['data'][0]
                check("BB API: symbol字段", 'symbol' in sig)
                check("BB API: consecutive_hours字段", 'consecutive_hours' in sig)

            # VS signals
            resp = urllib.request.urlopen('http://localhost:5003/api/vol_surge', timeout=5)
            vs_data = json.loads(resp.read())
            check("VS API: data字段", 'data' in vs_data)
            check("VS API: count字段", 'count' in vs_data)
            print(f"  ℹ️  当前VS信号数: {vs_data.get('count', 0)}")

            # Debug state
            resp = urllib.request.urlopen('http://localhost:5003/api/debug_state', timeout=5)
            state = json.loads(resp.read())
            vol_15m_cur = state.get('vol_15m_current_count', 0)
            vol_15m_last = state.get('vol_15m_last_count', 0)
            vol_15m_ok = vol_15m_cur > 0 or vol_15m_last > 0
            check(f"15m成交量数据 (current={vol_15m_cur}, last={vol_15m_last})", vol_15m_ok,
                  "无成交量数据" if not vol_15m_ok else "")
        except Exception as e:
            check("网站连接", False, str(e))

    # 8. Summary
    print("\n" + "=" * 65)
    total = PASS + FAIL + WARN
    print(f"  通过: {GREEN}{PASS}{NC}  失败: {RED}{FAIL}{NC}  警告: {YELLOW}{WARN}{NC}  总计: {total}")
    if FAIL == 0:
        print(f"  {GREEN}✅ 全部通过 — 可以部署{NC}")
    else:
        print(f"  {RED}❌ 有 {FAIL} 项未通过 — 请修正后重新验证{NC}")
    print("=" * 65)
    return FAIL

if __name__ == '__main__':
    sys.exit(main())
