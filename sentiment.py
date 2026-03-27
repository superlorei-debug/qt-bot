"""
KOL 情绪系统 - 独立模块
给 auto_trade.py 调用
"""

import requests
import time
from datetime import datetime

def get_fear_greed():
    try:
        r = requests.get("https://api.alternative.me/fng/", timeout=6)
        d = r.json()["data"][0]
        return int(d["value"])
    except:
        return None

def get_orderbook(symbol):
    try:
        r = requests.get(f"https://fapi.binance.com/fapi/v1/depth?symbol={symbol}&limit=20", timeout=6)
        d = r.json()
        bids = sum(float(b[1]) for b in d["bids"])
        asks = sum(float(a[1]) for a in d["asks"])
        total = bids + asks
        if total == 0:
            return 0
        return round((bids - asks) / total * 100, 1)
    except:
        return 0

def check_sentiment():
    """
    返回情绪分析结果，包含:
    - fg_val: 恐惧贪婪值
    - sentiment: 综合方向 ("bullish"/"bearish"/"neutral")
    - confidence: 信心等级 1-5
    - advice: 操作建议
    - blockers: 阻止下单的因素列表
    """
    fg_val = get_fear_greed()
    ob_btc = get_orderbook("BTCUSDT")
    ob_eth = get_orderbook("ETHUSDT")
    ob_sol = get_orderbook("SOLUSDT")

    bullish_count = 0
    bearish_count = 0
    signals = []
    blockers = []

    # 恐惧贪婪
    if fg_val is not None:
        if fg_val <= 15:
            bullish_count += 2
            signals.append(f"恐惧指数{fg_val}极低→超卖，可能蓄势反弹")
        elif fg_val <= 30:
            bullish_count += 1
            signals.append(f"恐惧指数{fg_val}偏低→下跌空间有限")
        elif fg_val >= 80:
            bearish_count += 2
            blockers.append(f"恐惧指数{fg_val}极高→不追多")
        elif fg_val >= 70:
            bearish_count += 1
            signals.append(f"恐惧指数{fg_val}偏高→谨慎")

    # 盘口
    for ob_val, name in [(ob_btc, "BTC"), (ob_eth, "ETH"), (ob_sol, "SOL")]:
        if ob_val > 10:
            bullish_count += 1
            signals.append(f"{name}盘口偏多+{ob_val}%")
        elif ob_val < -10:
            bearish_count += 1
            signals.append(f"{name}盘口偏空{ob_val}%")
        elif ob_val < -30:
            bearish_count += 2
            blockers.append(f"{name}盘口极度偏空{ob_val}%")

    # 综合判断
    if bullish_count > bearish_count:
        sentiment = "bullish"
        confidence = min(5, bullish_count + 1)
    elif bearish_count > bullish_count:
        sentiment = "bearish"
        confidence = min(5, bearish_count + 1)
    else:
        sentiment = "neutral"
        confidence = 1

    # 操作建议
    if blockers:
        advice = " ⚠️ " + " | ".join(blockers)
    elif sentiment == "bullish" and fg_val and fg_val < 30:
        advice = "✅ 情绪面偏多 + 低恐惧，入场赔率较好"
    elif sentiment == "bearish" and fg_val and fg_val > 70:
        advice = "⚠️ 一致性偏多，警惕诱多，不追高"
    elif sentiment == "bearish" and fg_val and fg_val < 30:
        advice = "🔍 偏空但极度恐惧，不追空，等修复"
    elif sentiment == "bullish":
        advice = "✅ 情绪面支持做多"
    elif sentiment == "bearish":
        advice = "⚠️ 情绪面偏空，操作谨慎"
    else:
        advice = "➡️ 情绪中性，无明确方向"

    return {
        "fg_val": fg_val,
        "sentiment": sentiment,
        "confidence": confidence,
        "signals": signals,
        "advice": advice,
        "blockers": blockers,
        "ob": {"BTC": ob_btc, "ETH": ob_eth, "SOL": ob_sol},
    }

def get_sentiment_summary():
    """简报格式输出"""
    s = check_sentiment()
    fg = s["fg_val"]
    emoji = "📈" if s["sentiment"] == "bullish" else "📉" if s["sentiment"] == "bearish" else "➡️"
    fg_text = f"😱{fg}" if fg and fg < 30 else f"😐{fg}" if fg and fg < 60 else f"😄{fg}" if fg and fg < 80 else f"🤑{fg}"
    stars = "⭐" * s["confidence"]

    lines = [
        f"  情绪: {fg_text} | {emoji}{s['sentiment'].upper()} {stars} | {s['advice']}",
    ]
    if s["signals"]:
        for sig in s["signals"]:
            lines.append(f"    • {sig}")
    return "\n".join(lines)

if __name__ == "__main__":
    print(get_sentiment_summary())
