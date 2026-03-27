import pandas as pd, requests, time

def fetch(sym, tf, limit):
    url = f"https://api.binance.com/api/v3/klines?symbol={sym}&interval={tf}&limit={limit}"
    r = requests.get(url, timeout=10)
    df = pd.DataFrame(r.json(), columns=['o','h','l','c','v','ct','q','n','f','T','Q','I'])
    for c in ['h','l','c']: df[c] = df[c].astype(float)
    return df

def analyze(sym):
    try:
        df4 = fetch(sym, "4h", 50)
        h4_hi = df4['h'].max()
        h4_lo = df4['l'].min()
        diff4 = h4_hi - h4_lo
        fib50 = h4_lo + diff4 * 0.5

        df1 = fetch(sym, "1h", 200)
        df1['e20'] = df1['c'].ewm(span=20).mean()
        df1['e50'] = df1['c'].ewm(span=50).mean()
        cur = df1['c'].iloc[-1]
        e20 = df1['e20'].iloc[-1]
        e50 = df1['e50'].iloc[-1]

        inbox = df1[(df1['h'] < h4_hi) & (df1['l'] > h4_lo)]
        sub_r = inbox['h'].max() if len(inbox) > 0 else h4_hi
        sub_s = inbox['l'].min() if len(inbox) > 0 else h4_lo
        bullish = e20 > e50

        print(f"\n{'='*50}")
        print(f"  {sym} 分析")
        print(f"{'='*50}")
        print(f"4H阻力 ${h4_hi:.0f}  4H支撑 ${h4_lo:.0f}")
        print(f"斐波50% ${fib50:.0f}  次阻 ${sub_r:.0f}  次撑 ${sub_s:.0f}")
        print(f"EMA20 ${e20:.0f}  EMA50 ${e50:.0f}  当前 ${cur:.0f}")
        print(f"趋势: {'多头↑' if bullish else '空头↓'}")
        print()
        if cur > fib50 and cur < h4_hi:
            if bullish:
                print(f"✅ 可做多，回踩不破 ${fib50:.0f} 入，目标 ${sub_r:.0f}")
                print(f"  突破 ${h4_hi:.0f} 可持有")
            else:
                print(f"⚠️ 偏多但EMA未确认，等待")
        elif cur < fib50 and cur > h4_lo:
            if not bullish:
                print(f"✅ 可做空，反弹 ${fib50:.0f} 不破入，目标 ${sub_s:.0f}")
                print(f"  跌破 ${h4_lo:.0f} 观望")
            else:
                print(f"⚠️ 偏空但EMA未确认，等待")
        elif cur >= h4_hi:
            print(f"✅ 突破4H阻力，多头延续")
        elif cur <= h4_lo:
            print(f"❌ 跌破4H支撑，观望")
        print(f"止损参考: 做多 ${fib50*0.995:.0f} | 做空 ${fib50*1.005:.0f}")
        time.sleep(3)
    except Exception as e:
        print(f"{sym} 失败: {e}")

for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
    analyze(sym)
