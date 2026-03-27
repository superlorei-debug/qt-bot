"""
KOL 情绪监控系统 v2
- 情绪数据 + 直接的交易参考建议
- 结合恐惧贪婪 + 盘口多空 + 热门币 综合给出操作参考
"""

import requests
import time
import json
import os
from datetime import datetime

LOG_DIR = os.path.expanduser("~/Desktop/trade_logs")
os.makedirs(LOG_DIR, exist_ok=True)

def log(msg, tag="INFO"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{tag}] {msg}"
    print(line)
    f = open(os.path.join(LOG_DIR, f"kol_{datetime.now().strftime('%Y-%m-%d')}.log"), "a", encoding="utf-8")
    f.write(line + "\n")
    f.close()

# ========== 数据获取 ==========

def get_fear_greed():
    try:
        r = requests.get("https://api.alternative.me/fng/", timeout=8)
        d = r.json()["data"][0]
        return {"value": int(d["value"]), "classification": d["value_classification"]}
    except:
        return None

def get_btc_data():
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/coins/bitcoin?localization=false&tickers=false&community_data=false&developer_data=false&sparkline=false",
            timeout=8
        )
        m = r.json()["market_data"]
        return {
            "price": m["current_price"]["usd"],
            "change_24h": m["price_change_percentage_24h"],
            "change_7d": m["price_change_percentage_7d"],
            "high_24h": m["high_24h"]["usd"],
            "low_24h": m["low_24h"]["usd"],
        }
    except:
        return None

def get_eth_data():
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/coins/ethereum?localization=false&tickers=false&community_data=false&developer_data=false&sparkline=false",
            timeout=8
        )
        m = r.json()["market_data"]
        return {
            "price": m["current_price"]["usd"],
            "change_24h": m["price_change_percentage_24h"],
            "high_24h": m["high_24h"]["usd"],
            "low_24h": m["low_24h"]["usd"],
        }
    except:
        return None

def get_sol_data():
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/coins/solana?localization=false&tickers=false&community_data=false&developer_data=false&sparkline=false",
            timeout=8
        )
        m = r.json()["market_data"]
        return {
            "price": m["current_price"]["usd"],
            "change_24h": m["price_change_percentage_24h"],
            "high_24h": m["high_24h"]["usd"],
            "low_24h": m["low_24h"]["usd"],
        }
    except:
        return None

def get_orderbook(symbol):
    try:
        r = requests.get(f"https://fapi.binance.com/fapi/v1/depth?symbol={symbol}&limit=20", timeout=8)
        d = r.json()
        bids = sum(float(b[1]) for b in d["bids"])
        asks = sum(float(a[1]) for a in d["asks"])
        total = bids + asks
        if total == 0:
            return None
        imbalance = round((bids - asks) / total * 100, 1)
        return {"imbalance": imbalance, "bid_vol": round(bids, 0), "ask_vol": round(asks, 0)}
    except:
        return None

def get_top_gainers_losers(limit=5):
    """获取主流币涨跌情况"""
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids=bitcoin,ethereum,tether,binancecoin,solana,ripple,cardano,avalanche-2,chainlink,dogecoin&order=market_cap_desc&per_page=10&page=1&sparkline=false&price_change_percentage=24h",
            timeout=8
        )
        coins = []
        for c in r.json():
            coins.append({
                "name": c["name"],
                "symbol": c["symbol"].upper(),
                "change_24h": c.get("price_change_percentage_24h", 0),
                "price": c["current_price"],
            })
        gainers = sorted(coins, key=lambda x: x["change_24h"], reverse=True)
        losers = sorted(coins, key=lambda x: x["change_24h"])[:3]
        return gainers[:3], losers[:3]
    except:
        return None, None

# ========== 情绪判断逻辑 ==========

def interpret_fear_greed(value):
    if value is None:
        return "未知"
    if value <= 15:
        return "😱 极度恐惧（可能超卖，关注低位机会）"
    elif value <= 25:
        return "😰 严重恐惧（谨慎但不恐慌）"
    elif value <= 45:
        return "😟 恐惧（偏空，等待）"
    elif value <= 55:
        return "😐 中性（无方向）"
    elif value <= 75:
        return "😄 贪婪（偏多，不追）"
    else:
        return "🤑 极度贪婪（可能见顶，警惕）"

def interpret_ob(imbalance):
    if imbalance is None:
        return "未知"
    if imbalance >= 20:
        return f"🟢 明显偏多（+{imbalance}%）"
    elif imbalance >= 5:
        return f"🟢 略偏多（+{imbalance}%）"
    elif imbalance <= -20:
        return f"🔴 明显偏空（{imbalance}%）"
    elif imbalance <= -5:
        return f"🔴 略偏空（{imbalance}%）"
    else:
        return f"⚪ 基本平衡（{imbalance}%）"

def overall_signal(fg_val, ob_btc, ob_eth, ob_sol):
    """
    综合多空信号
    返回: (信号方向, 信心等级 1-5, 说明)
    """
    bullish_signals = 0
    bearish_signals = 0
    reasons = []

    # 恐惧贪婪
    if fg_val is not None:
        if fg_val < 30:
            bullish_signals += 1
            reasons.append(f"恐惧指数{fg_val}极低→资金可能蓄势")
        elif fg_val > 70:
            bearish_signals += 1
            reasons.append(f"恐惧指数{fg_val}极高→警惕诱多")

    # 盘口
    for ob_val, name in [(ob_btc, "BTC"), (ob_eth, "ETH"), (ob_sol, "SOL")]:
        if ob_val is not None:
            if ob_val > 10:
                bullish_signals += 1
                reasons.append(f"{name}盘口偏多+{ob_val}%")
            elif ob_val < -10:
                bearish_signals += 1
                reasons.append(f"{name}盘口偏空{ob_val}%")

    total = bullish_signals + bearish_signals
    if total == 0:
        return "观望", 1, ["数据不足，方向不明"]
    elif bullish_signals > bearish_signals:
        confidence = min(5, bullish_signals + 1)
        return "偏多", confidence, reasons
    elif bearish_signals > bullish_signals:
        confidence = min(5, bearish_signals + 1)
        return "偏空", confidence, reasons
    else:
        return "中性", 2, reasons

def confidence_stars(n):
    return "⭐" * n

# ========== 主报告 ==========

def generate_report():
    now = datetime.now()

    print("")
    log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "KOL")
    log(f"   📊 市场情绪简报 | {now.strftime('%Y-%m-%d %H:%M')}", "KOL")
    log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "KOL")

    # 1. 恐惧贪婪
    fg = get_fear_greed()
    fg_val = fg["value"] if fg else None
    fg_text = interpret_fear_greed(fg_val)
    if fg:
        log(f"   😱 恐惧贪婪: {fg_val}/100 | {fg_text}", "KOL")
    else:
        log(f"   ❌ 恐惧贪婪: 获取失败", "KOL")

    # 2. 主流币行情
    btc = get_btc_data()
    eth = get_eth_data()
    sol = get_sol_data()

    log("   ─── 主流币行情 ───", "KOL")
    for coin, data in [("BTC", btc), ("ETH", eth), ("SOL", sol)]:
        if data:
            ch = data["change_24h"] or 0
            arrow = "🔴" if ch < 0 else "🟢"
            log(f"   {arrow} {coin}: ${data['price']:,.2f} | 24h {ch:+.2f}%", "KOL")

    # 3. 盘口多空意向
    log("   ─── 盘口多空 ───", "KOL")
    ob_btc = get_orderbook("BTCUSDT")
    ob_eth = get_orderbook("ETHUSDT")
    ob_sol = get_orderbook("SOLUSDT")
    for sym, ob in [("BTC", ob_btc), ("ETH", ob_eth), ("SOL", ob_sol)]:
        if ob:
            text = interpret_ob(ob["imbalance"])
            log(f"   {text} ({sym}) | 买:{ob['bid_vol']:,.0f} 卖:{ob['ask_vol']:,.0f}", "KOL")
        time.sleep(0.3)

    # 4. 涨跌排行
    gainers, losers = get_top_gainers_losers()
    if gainers and losers:
        log("   ─── 热门币涨跌 ───", "KOL")
        log("   🟢 涨幅榜:", "KOL")
        for g in gainers:
            ch = g["change_24h"] or 0
            log(f"     {g['name']}({g['symbol']}): {ch:+.2f}%", "KOL")
        log("   🔴 跌幅榜:", "KOL")
        for l in losers:
            ch = l["change_24h"] or 0
            log(f"     {l['name']}({l['symbol']}): {ch:+.2f}%", "KOL")

    # 5. 综合信号
    signal, confidence, reasons = overall_signal(fg_val,
        ob_btc["imbalance"] if ob_btc else None,
        ob_eth["imbalance"] if ob_eth else None,
        ob_sol["imbalance"] if ob_sol else None,
    )

    log("   ─── 综合信号 ───", "KOL")
    emoji = "📈" if "偏多" in signal else "📉" if "偏空" in signal else "➡️"
    stars = confidence_stars(confidence)
    log(f"   {emoji} 方向: {signal} {stars}（信心{confidence}/5）", "KOL")
    for r in reasons:
        log(f"   • {r}", "KOL")

    # 6. 操作建议（结合恐惧指数）
    log("   ─── 操作参考 ───", "KOL")
    if signal == "偏多" and fg_val and fg_val < 30:
        log(f"   ✅ 技术面+情绪面共振 | 低恐惧入场，赔率较好", "KOL")
        log(f"   ✅ 可关注做多机会，止损设宽一写", "KOL")
    elif signal == "偏空" and fg_val and fg_val > 70:
        log(f"   ⚠️ 一致性看多 | 警惕诱多，不追高", "KOL")
    elif signal == "偏空" and fg_val and fg_val < 30:
        log(f"   🔍 偏空但极度恐惧 | 不追空，等恐慌结束", "KOL")
    elif fg_val and fg_val < 20:
        log(f"   👀 极度恐惧 | 不是入场时机，等情绪修复再说", "KOL")
    elif fg_val and fg_val > 80:
        log(f"   👀 极度贪婪 | 不是入场时机，不要追多", "KOL")
    else:
        log(f"   ➡️ 无明确方向 | 等待技术面信号出现再操作", "KOL")

    log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "KOL")
    print("")

    return {
        "fg": fg,
        "btc": btc,
        "eth": eth,
        "sol": sol,
        "ob": {"BTC": ob_btc, "ETH": ob_eth, "SOL": ob_sol},
        "signal": signal,
        "confidence": confidence,
        "reasons": reasons,
        "timestamp": now.isoformat()
    }

if __name__ == "__main__":
    generate_report()
