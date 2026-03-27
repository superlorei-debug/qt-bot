"""
Binance Futures 自动交易机器人 v3
- KOL情绪过滤（偏多→只看多，偏空→只看空）
- 4H箱体 + 1H斐波50% 关键位判断
- 分场景入场 + 分级止盈 + 追踪止盈
- 做空：强阻力受阻 / 斐波50%受阻
- 做多：强支撑受阻 / 斐波50%受阻
"""

import requests, time, hmac, hashlib, json, os, csv, threading
from datetime import datetime
import pandas as pd
from sentiment import check_sentiment
import dashboard

BASE_URL = "https://demo-fapi.binance.com"
API_KEY = "YOUR_API_KEY_HERE"
API_SECRET = "YOUR_API_SECRET_HERE"

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
BOX_CHECK_INTERVAL = 15
POSITION_CHECK_INTERVAL = 5

LOG_DIR = os.path.expanduser("~/Desktop/trade_logs")
os.makedirs(LOG_DIR, exist_ok=True)

def log(msg, tag="INFO"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{tag}] {msg}"
    print(line)
    f = open(os.path.join(LOG_DIR, f"trade_{datetime.now().strftime('%Y-%m-%d')}.log"), "a", encoding="utf-8")
    f.write(line + "\n"); f.close()

def log_order(order, action):
    f = open(os.path.join(LOG_DIR, f"orders_{datetime.now().strftime('%Y-%m-%d')}.jsonl"), "a", encoding="utf-8")
    f.write(json.dumps({"time": datetime.now().isoformat(), "action": action, "order": order}, ensure_ascii=False) + "\n")
    f.close()

def csv_path():
    return os.path.join(LOG_DIR, f"trades_{datetime.now().strftime('%Y-%m-%d')}.csv")

def ensure_csv_header():
    """确保CSV文件有表头"""
    p = csv_path()
    if not os.path.exists(p):
        with open(p, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([
                "开仓时间", "平仓时间", "币种", "方向",
                "入场价", "平仓价", "数量",
                "TP1触发价", "TP2触发价", "止损价",
                "平仓原因", "毛收益", "状态"
            ])

def csv_append(row):
    """追加一行到CSV"""
    ensure_csv_header()
    with open(csv_path(), "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(row)

def sign(params, secret):
    p = [f"{k}={v}" for k, v in params.items()]
    return hmac.new(secret.encode(), "&".join(p).encode(), hashlib.sha256).hexdigest()

def http_request(method, path, params=None, signed=False):
    url = f"{BASE_URL}{path}"
    headers = {"X-MBX-APIKEY": API_KEY}
    if params is None: params = {}
    if signed:
        params["timestamp"] = int(time.time() * 1000)
        params["signature"] = sign(params, API_SECRET)
    if method == "GET":   r = requests.get(url, params=params, headers=headers, timeout=10)
    elif method == "POST": r = requests.post(url, params=params, headers=headers, timeout=10)
    elif method == "DELETE": r = requests.delete(url, params=params, headers=headers, timeout=10)
    return r.json()

# ============================================================
#  交易机器人
# ============================================================
class TradeBot:
    def __init__(self):
        self.account = None
        self.positions = {}    # {symbol: pos_data}
        self.in_box = {}      # {symbol: True/False}
        # 持仓保护记录：{symbol: {"entry": float, "tp": float, "sl": float, "tp_triggered": bool, "sl_triggered": bool, "trail_activated": bool, "trail_high": float}}
        self.protections = {}

    def connect(self):
        log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "SYSTEM")
        log("  策略程序启动........", "SYSTEM")
        log("  连接交易所获取实时行情.......", "SYSTEM")
        http_request("GET", "/fapi/v1/ping")
        log("  连接交易所成功 ✅", "SYSTEM")
        self.sync()
        threading.Thread(target=dashboard.start_server, args=(8888, self), daemon=True).start()
        balance = self.get_account_balance()
        log(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "SYSTEM")
        log(f"  账户概览", "SYSTEM")
        log(f"  余额: ${balance:.2f} USDT", "INFO")
        log(f"  持仓: {len(self.positions)} 个", "INFO")
        for sym, pos in self.positions.items():
            amt = float(pos['positionAmt'])
            upnl = float(pos['unrealizedProfit'])
            log(f"    {sym}: {'多' if amt>0 else '空'} {abs(amt)}张 | 浮盈${upnl:.2f}", "INFO")
        log(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "SYSTEM")

    def sync(self):
        resp = http_request("GET", "/fapi/v2/account", signed=True)
        if "totalMarginBalance" in resp or "assets" in resp:
            self.account = resp
            self.positions = {}
            for pos in resp.get("positions", []):
                amt = float(pos.get("positionAmt", 0))
                if amt != 0:
                    self.positions[pos["symbol"]] = pos

    def get_account_balance(self):
        total = 0.0
        if not self.account:
            resp = http_request("GET", "/fapi/v2/account", signed=True)
            if "totalMarginBalance" not in resp: return 0
            self.account = resp
        for a in self.account.get("assets", []):
            if a.get("asset") in ("USDT", "USDC", "BTC"):
                total += float(a.get("marginBalance", 0)) or float(a.get("walletBalance", 0))
        return total

    def get_ticker(self, symbol):
        resp = http_request("GET", "/fapi/v1/ticker/price", {"symbol": symbol})
        return float(resp["price"]) if "price" in resp else None

    # ============================================================
    #  数据获取
    # ============================================================
    def fetch_klines(self, symbol, interval, limit):
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
        r = requests.get(url, timeout=10)
        cols = ['o','h','l','c','v','ct','q','n','f','T','Q','I']
        df = pd.DataFrame(r.json(), columns=cols)
        for c in ['h','l','c']: df[c] = df[c].astype(float)
        return df

    def get_levels(self, symbol):
        """
        动态箱体：始终用最近50根已确认的4H K线计算
        4H箱体 = 最近50根4H K线的最高点（强阻）和最低点（强撑）
        1H斐波50% = 箱体高度的50%回调位
        每根4H K线走完收盘后，箱体自动重新计算
        """
        df4 = self.fetch_klines(symbol, "4h", 50)
        df1 = self.fetch_klines(symbol, "1h", 50)

        h4_hi  = df4['h'].max()           # 4H强阻力 = 箱体上轨
        h4_lo  = df4['l'].min()           # 4H强支撑 = 箱体下轨
        diff4  = h4_hi - h4_lo
        fib50  = h4_lo + diff4 * 0.5     # 斐波50%回调位

        # 次阻/次撑 = 斐波50%的对称位置
        sub_r = fib50 + (fib50 - h4_lo)   # 次阻力（向上目标）
        sub_s = h4_lo - (h4_hi - fib50)   # 次支撑（向下目标）

        # 当前价格（用1H的最新收盘价，更准确）
        cur = df1['c'].iloc[-1]

        # 箱体范围标签
        box_range = f"${h4_lo:.0f}~${h4_hi:.0f}"

        # 突破检测：当前价格 vs 当前箱体边界（不等同于箱体重算）
        breakout = None
        buffer = cur * 0.001  # 0.1%容差
        if cur >= h4_hi - buffer:
            breakout = "向上试探强阻"
        elif cur <= h4_lo + buffer:
            breakout = "向下试探强撑"

        return {
            "cur": cur,
            "h4_hi": h4_hi,
            "h4_lo": h4_lo,
            "fib50": fib50,
            "sub_r": sub_r,
            "sub_s": sub_s,
            "breakout": breakout,
            "box_range": box_range,
        }

    # ============================================================
    #  策略分析核心
    # ============================================================
    def analyze(self, symbol, sentiment):
        """
        结合KOL情绪 + 技术位分析
        sentiment: bullish / bearish / neutral
        返回信号描述或 None
        """
        lv = self.get_levels(symbol)
        cur = lv["cur"]
        h4_hi = lv["h4_hi"]
        h4_lo = lv["h4_lo"]
        fib50 = lv["fib50"]
        sub_r = lv["sub_r"]
        sub_s = lv["sub_s"]

        # 容差（0.1%）
        def near(p, ref): return abs(p - ref) / ref < 0.001

        # -------- 做空分析 --------
        if sentiment in ("bearish", "neutral"):

            # 场景1：价格到4H强阻力，没过去 → 做空
            if near(cur, h4_hi) or (cur < h4_hi and cur > h4_hi * 0.998):
                log(f"  🎯 空信号-场景1 | 4H强阻力${h4_hi:.2f}受阻 | 当前${cur:.2f} | 目标1:斐波50%${fib50:.2f}", "SIGNAL")
                log(f"    止盈: 斐波50%@${fib50:.2f}全部平50% | 跌破持有至${sub_s:.2f} | 反弹至50%上方全部止盈", "SIGNAL")
                return {
                    "action": "short_s1",
                    "entry": cur,
                    "tp1_pct": 50,          # 斐波50%平50%
                    "tp1_price": fib50,
                    "tp2_price": sub_s,
                    "stop_on_rebound": fib50,  # 反弹到这个价格以上就全平
                    "description": f"4H强阻力{h4_hi:.2f}受阻空"
                }

            # 场景2：价格到斐波50%，没站上去 → 做空
            if near(cur, fib50) or (cur > fib50 * 0.999 and cur < fib50 * 1.001):
                log(f"  🎯 空信号-场景2 | 斐波50%${fib50:.2f}未突破 | 当前${cur:.2f} | 目标1:次支撑${sub_s:.2f} | 目标2:4H强支撑${h4_lo:.2f}", "SIGNAL")
                log(f"    止盈: 次支撑${sub_s:.2f}全部平 | 跌破4H强支撑持续持有 | 反弹全部止盈", "SIGNAL")
                return {
                    "action": "short_s2",
                    "entry": cur,
                    "tp1_pct": 100,
                    "tp1_price": sub_s,
                    "tp2_price": h4_lo,         # 跌破强支撑持有
                    "stop_on_rebound": sub_s,    # 从次支撑反弹就全部止盈
                    "description": f"斐波50%{fib50:.2f}未突破空"
                }

        # -------- 做多分析 --------
        if sentiment in ("bullish", "neutral"):

            # 场景1'：价格到4H强支撑，没跌破 → 做多
            if near(cur, h4_lo) or (cur > h4_lo and cur < h4_lo * 1.002):
                log(f"  🎯 多信号-场景1 | 4H强支撑${h4_lo:.2f}受撑 | 当前${cur:.2f} | 目标1:斐波50%${fib50:.2f}", "SIGNAL")
                log(f"    止盈: 斐波50%@${fib50:.2f}全部平50% | 突破持有至${sub_r:.2f} | 跌回50%下方全部止盈", "SIGNAL")
                return {
                    "action": "long_s1",
                    "entry": cur,
                    "tp1_pct": 50,
                    "tp1_price": fib50,
                    "tp2_price": sub_r,
                    "stop_on_rebound": fib50,
                    "description": f"4H强支撑{h4_lo:.2f}受撑多"
                }

            # 场景2'：价格到斐波50%，没站上去 → 做多（做空者止损反手）
            if near(cur, fib50) or (cur < fib50 * 1.001 and cur > fib50 * 0.999):
                log(f"  🎯 多信号-场景2 | 斐波50%${fib50:.2f}企稳 | 当前${cur:.2f} | 目标1:次阻力${sub_r:.2f} | 目标2:4H强阻力${h4_hi:.2f}", "SIGNAL")
                log(f"    止盈: 次阻力${sub_r:.2f}全部平 | 突破4H强阻力持续持有 | 跌回全部止盈", "SIGNAL")
                return {
                    "action": "long_s2",
                    "entry": cur,
                    "tp1_pct": 100,
                    "tp1_price": sub_r,
                    "tp2_price": h4_hi,
                    "stop_on_rebound": sub_r,
                    "description": f"斐波50%{fib50:.2f}企稳多"
                }

        return None  # 无信号

    # ============================================================
    #  下单
    # ============================================================
    def _calc_qty(self, symbol):
        # 用可用余额计算，不是总余额
        resp = http_request("GET", "/fapi/v2/account", signed=True)
        available = float(resp.get("availableBalance", 0))
        if available <= 0:
            return 0.001
        pos_val = available * 0.1 * 20  # 10%仓位，20x杠杆
        price = self.get_ticker(symbol)
        if not price:
            return 0.001
        qty = pos_val / price
        if "BTC" in symbol: return round(qty, 3)
        elif "ETH" in symbol: return round(qty, 2)
        return round(qty, 1)

    def place_market_order(self, symbol, side, quantity):
        params = {"symbol": symbol, "side": side, "type": "MARKET", "quantity": quantity}
        resp = http_request("POST", "/fapi/v1/order", params, signed=True)
        log_order(resp, f"market_{side}")
        if "orderId" in resp:
            log(f"  📝 {'开多' if side=='BUY' else '开空'} | {symbol} | {side} | {quantity} | ID:{resp['orderId']}", "ORDER")
            return resp
        else:
            log(f"  ❌ {'开多' if side=='BUY' else '开空'}失败: {resp.get('msg', resp)}", "ERROR")
            return resp

        """市价平仓（reduceOnly）"""
        params = {"symbol": symbol, "side": side, "type": "MARKET", "quantity": quantity, "reduceOnly": True}
        resp = http_request("POST", "/fapi/v1/order", params, signed=True)
        log_order(resp, f"close_{side}")
        if "orderId" in resp:
            log(f"  📝 平仓 | {symbol} | {side} | {quantity} | ID:{resp['orderId']}", "ORDER")
            return resp
        else:
            log(f"  ❌ 平仓失败: {resp.get('msg', resp)}", "ERROR")
            return resp

    def place_close_order(self, symbol, side, quantity):
        params = {"symbol": symbol, "side": side, "type": "MARKET", "quantity": quantity, "reduceOnly": True}
        resp = http_request("POST", "/fapi/v1/order", params, signed=True)
        log_order(resp, f"close_{side}")
        if "orderId" in resp:
            log(f"  📝 平仓 | {symbol} | {side} | {quantity} | ID:{resp['orderId']}", "ORDER")
            return resp
        return resp

    def place_tp(self, symbol, side, quantity, trigger_price):
        params = {
            "symbol": symbol, "side": side, "type": "TAKE_PROFIT_MARKET",
            "quantity": quantity, "stopPrice": trigger_price,
            "workingType": "CONTRACT_PRICE", "reduceOnly": True,
        }
        resp = http_request("POST", "/fapi/v1/order", params, signed=True)
        log_order(resp, f"tp_{side}")
        if "orderId" in resp:
            log(f"  📝 止盈挂单 | 触发价:${trigger_price} | ID:{resp['orderId']}", "ORDER")
        else:
            log(f"  ❌ 止盈挂单失败: {resp.get('msg', resp)}", "ERROR")
        return resp

    def place_sl(self, symbol, side, quantity, trigger_price):
        params = {
            "symbol": symbol, "side": side, "type": "STOP_MARKET",
            "quantity": quantity, "stopPrice": trigger_price,
            "workingType": "CONTRACT_PRICE", "reduceOnly": True,
        }
        resp = http_request("POST", "/fapi/v1/order", params, signed=True)
        log_order(resp, f"sl_{side}")
        if "orderId" in resp:
            log(f"  📝 止损挂单 | 触发价:${trigger_price} | ID:{resp['orderId']}", "ORDER")
        return resp

    def place_trailing(self, symbol, side, quantity, callback_rate=0.5):
        params = {
            "symbol": symbol, "side": side, "type": "TRAILING_STOP_MARKET",
            "quantity": quantity, "callbackRate": callback_rate, "reduceOnly": True,
        }
        resp = http_request("POST", "/fapi/v1/order", params, signed=True)
        log_order(resp, f"trailing_{side}")
        if "orderId" in resp:
            log(f"  📝 追踪止盈 | 回撤:{callback_rate}% | ID:{resp['orderId']}", "ORDER")
        return resp

    def open_position(self, sig, symbol, quantity, cur):
        """执行开仓 + 记录止盈止损计划（Demo盘不支持挂单，改为价格监控）"""
        direction = "long" if sig["action"].startswith("long") else "short"
        side = "BUY" if direction == "long" else "SELL"
        opp_side = "SELL" if side == "BUY" else "BUY"

        if direction == "long":
            sl_price = cur * 0.995
        else:
            sl_price = cur * 1.005

        log(f"━━━━━━ {sig['description']} ━━━━━━", "SIGNAL")
        log(f"  入场:${cur:.4f} | 数量:{quantity} | 方向:{direction}", "ORDER")
        log(f"  TP1: ${sig['tp1_price']:.4f} (平{sig['tp1_pct']}%) | TP2: ${sig['tp2_price']:.4f}", "ORDER")
        log(f"  止损: ${sl_price:.4f} | 反弹全平线: ${sig['stop_on_rebound']:.4f}", "ORDER")
        log(f"  Demo盘不支持挂单，将监控价格自动执行止盈止损", "ORDER")

        order_resp = self.place_market_order(symbol, side, quantity)
        if "orderId" not in order_resp:
            log(f"  ❌ 开仓失败", "ERROR")
            return None

        self.protections[symbol] = {
            "open_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "entry": cur,
            "direction": direction,
            "quantity": quantity,
            "tp1_price": sig["tp1_price"],
            "tp2_price": sig["tp2_price"],
            "sl_price": sl_price,
            "rebounce_line": sig["stop_on_rebound"],
            "tp1_triggered": False,
            "sl_triggered": False,
            "all_closed": False,
            "trail_high": cur,
            "trail_low": cur,
        }

        self.sync()
        log(f"━━━━━━ 开仓完毕，止盈止损监控已启动 ━━━━━━", "SIGNAL")

        # 写入CSV记录
        csv_append([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),  # 开仓时间
            "",  # 平仓时间（待填）
            symbol,
            direction,
            cur,
            "",  # 平仓价（待填）
            quantity,
            sig["tp1_price"],
            sig["tp2_price"],
            sl_price,
            "",  # 平仓原因（待填）
            "",  # 毛收益（待填）
            "持仓中"
        ])
        return order_resp

    def monitor_position(self, symbol):
        """
        持仓监控：通过价格监控执行止盈止损（Demo盘不支持挂单）
        逻辑：
          做多：
            - 涨到TP1（斐波50%）→ 平50% → 追踪剩余50%（追踪回撤0.5%平）
            - 跌破SL（止损价）→ 全平
            - 反弹跌破rebounce线 → 全平
            - 继续涨到TP2 → 全平
          做空：反之
        """
        if symbol not in self.positions:
            return
        self.sync()
        pos = self.positions.get(symbol)
        if not pos: return
        amt = float(pos["positionAmt"])
        if amt == 0: return

        cur = self.get_ticker(symbol) or 0
        direction = "long" if amt > 0 else "short"
        p = self.protections.get(symbol)

        entry = float(pos.get("entryPrice", 0))
        upnl = float(pos.get("unrealizedProfit", 0))
        log(f"[持仓] {symbol} | {'多' if direction=='long' else '空'} | {abs(amt)}张 | 入:${entry:.2f} | 现:${cur:.2f} | 浮:${upnl:.2f}", "POSITION")

        if not p:
            log(f"  ⚠️ 无保护记录，先记录", "WARN")
            return

        tp1 = p["tp1_price"]
        tp2 = p["tp2_price"]
        sl = p["sl_price"]
        rebounce = p["rebounce_line"]
        qty = abs(float(pos["positionAmt"]))

        # ===== 做多持仓 =====
        if direction == "long":
            # 止损触发
            if cur <= sl and not p["sl_triggered"]:
                log(f"  🛑 触发止损 | ${cur:.2f} <= ${sl:.2f} | 全平", "TP")
                resp = self.place_close_order(symbol, "SELL", qty)
                if resp and "orderId" in resp:
                    p["sl_triggered"] = True
                    p["all_closed"] = True
                    log(f"  ✅ 止损平仓成功 | ID:{resp['orderId']}", "TP")
                    csv_append([p.get("open_time",""), datetime.now().strftime("%Y-%m-%d %H:%M:%S"), symbol, direction, p["entry"], cur, abs(amt), p["tp1_price"], p["tp2_price"], sl, "止损触发", round(float(resp.get("avgPrice", 0)) * abs(amt) - p["entry"] * abs(amt), 2), "已止损"])
                return

            # TP1（斐波50%）触发 → 平50%
            if cur >= tp1 and not p["tp1_triggered"] and not p["all_closed"]:
                log(f"  🎯 触发TP1（斐波50%） | ${cur:.2f} >= ${tp1:.2f} | 平50%数量", "TP")
                close_qty = round(qty * 0.5, 3)
                resp = self.place_close_order(symbol, "SELL", close_qty)
                if resp and "orderId" in resp:
                    p["tp1_triggered"] = True
                    log(f"  ✅ TP1平仓成功 | 平{close_qty}张 | 剩余{qty - close_qty}张继续持盈", "TP")
                    csv_append([p.get("open_time",""), datetime.now().strftime("%Y-%m-%d %H:%M:%S"), symbol, direction, p["entry"], cur, close_qty, p["tp1_price"], p["tp2_price"], sl, "TP1触发", round((cur - p["entry"]) * close_qty, 2), "TP1已平"])
                    amt = float(pos.get("positionAmt", 0))
                return

            # TP1触发后，追踪止盈：价格从高点回撤0.5%则平剩余
            if p["tp1_triggered"] and not p["all_closed"]:
                trail_high = p.get("trail_high", cur)
                if cur > trail_high:
                    p["trail_high"] = cur
                    log(f"  📈 持盈中 | 最高:${trail_high:.2f} | 现:${cur:.2f}", "TP")
                elif cur <= trail_high * 0.995:
                    log(f"  🛑 追踪止盈触发 | ${cur:.2f} <= ${trail_high * 0.995:.2f}(高点回撤0.5%) | 全平剩余", "TP")
                    remaining_qty = round(qty * 0.5, 3)
                    resp = self.place_close_order(symbol, "SELL", remaining_qty)
                    if resp and "orderId" in resp:
                        p["all_closed"] = True
                        log(f"  ✅ 追踪止盈平仓成功 | ID:{resp['orderId']}", "TP")
                        csv_append([p.get("open_time",""), datetime.now().strftime("%Y-%m-%d %H:%M:%S"), symbol, direction, p["entry"], cur, remaining_qty, p["tp1_price"], p["tp2_price"], sl, "追踪止盈", round((cur - p["entry"]) * remaining_qty, 2), "追踪止盈"])
                        amt = float(pos.get("positionAmt", 0))
                return

            # TP2（次阻力） → 全平
            if cur >= tp2 and not p["all_closed"]:
                log(f"  🎯 触发TP2（次阻力） | ${cur:.2f} >= ${tp2:.2f} | 全平", "TP")
                resp = self.place_close_order(symbol, "SELL", qty)
                if resp and "orderId" in resp:
                    p["all_closed"] = True
                    log(f"  ✅ TP2全平成功 | ID:{resp['orderId']}", "TP")
                    csv_append([p.get("open_time",""), datetime.now().strftime("%Y-%m-%d %H:%M:%S"), symbol, direction, p["entry"], cur, qty, p["tp1_price"], p["tp2_price"], sl, "TP2触发", round((cur - p["entry"]) * qty, 2), "TP2全平"])
                return

        # ===== 做空持仓 =====
        if direction == "short":
            # 止损触发
            if cur >= sl and not p["sl_triggered"]:
                log(f"  🛑 触发止损 | ${cur:.2f} >= ${sl:.2f} | 全平", "TP")
                resp = self.place_close_order(symbol, "BUY", qty)
                if resp and "orderId" in resp:
                    p["sl_triggered"] = True
                    p["all_closed"] = True
                    log(f"  ✅ 止损平仓成功 | ID:{resp['orderId']}", "TP")
                    csv_append([p.get("open_time",""), datetime.now().strftime("%Y-%m-%d %H:%M:%S"), symbol, direction, p["entry"], cur, abs(amt), p["tp1_price"], p["tp2_price"], sl, "止损触发", round(float(resp.get("avgPrice", 0)) * abs(amt) - p["entry"] * abs(amt), 2), "已止损"])
                return

            # TP1触发 → 平50%
            if cur <= tp1 and not p["tp1_triggered"] and not p["all_closed"]:
                log(f"  🎯 触发TP1（斐波50%） | ${cur:.2f} <= ${tp1:.2f} | 平50%", "TP")
                close_qty = round(qty * 0.5, 3)
                resp = self.place_close_order(symbol, "BUY", close_qty)
                if resp and "orderId" in resp:
                    p["tp1_triggered"] = True
                    log(f"  ✅ TP1平仓成功 | 平{close_qty}张 | 剩余{qty - close_qty}张继续持盈", "TP")
                    csv_append([p.get("open_time",""), datetime.now().strftime("%Y-%m-%d %H:%M:%S"), symbol, direction, p["entry"], cur, close_qty, p["tp1_price"], p["tp2_price"], sl, "TP1触发", round((cur - p["entry"]) * close_qty, 2), "TP1已平"])
                    amt = float(pos.get("positionAmt", 0))
                return

            # 追踪止盈
            if p["tp1_triggered"] and not p["all_closed"]:
                trail_low = p.get("trail_low", cur)
                if cur < trail_low:
                    p["trail_low"] = cur
                    log(f"  📈 持盈中 | 最低:${trail_low:.2f} | 现:${cur:.2f}", "TP")
                elif cur >= trail_low * 1.005:
                    log(f"  🛑 追踪止盈触发 | ${cur:.2f} >= ${trail_low * 1.005:.2f}(低点回撤0.5%) | 全平剩余", "TP")
                    remaining_qty = round(qty * 0.5, 3)
                    resp = self.place_close_order(symbol, "BUY", remaining_qty)
                    if resp and "orderId" in resp:
                        p["all_closed"] = True
                        log(f"  ✅ 追踪止盈平仓成功 | ID:{resp['orderId']}", "TP")
                        csv_append([p.get("open_time",""), datetime.now().strftime("%Y-%m-%d %H:%M:%S"), symbol, direction, p["entry"], cur, remaining_qty, p["tp1_price"], p["tp2_price"], sl, "追踪止盈", round((cur - p["entry"]) * remaining_qty, 2), "追踪止盈"])
                        amt = float(pos.get("positionAmt", 0))
                return

            # TP2（次支撑） → 全平
            if cur <= tp2 and not p["all_closed"]:
                log(f"  🎯 触发TP2（次支撑） | ${cur:.2f} <= ${tp2:.2f} | 全平", "TP")
                resp = self.place_close_order(symbol, "BUY", qty)
                if resp and "orderId" in resp:
                    p["all_closed"] = True
                    log(f"  ✅ TP2全平成功 | ID:{resp['orderId']}", "TP")
                    csv_append([p.get("open_time",""), datetime.now().strftime("%Y-%m-%d %H:%M:%S"), symbol, direction, p["entry"], cur, qty, p["tp1_price"], p["tp2_price"], sl, "TP2触发", round((cur - p["entry"]) * qty, 2), "TP2全平"])
                return



    # ============================================================
    #  主循环
    # ============================================================
    def run(self):
        self.connect()
        log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "SYSTEM")
        log("  自动交易运行中 | v3 | 情绪+技术共振策略", "SYSTEM")
        log(f"  监控: {SYMBOLS}", "SYSTEM")
        log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "SYSTEM")

        last_full_check = {}  # {symbol: timestamp} 上次完整分析时间
        last_box_log = {}    # {symbol: timestamp} 上次箱体日志

        while True:
            try:
                now = datetime.now()
                self.sync()

                # ===== 获取KOL情绪 =====
                sentiment_data = check_sentiment()
                kol_sentiment = sentiment_data["sentiment"]
                fg_val = sentiment_data["fg_val"]
                log(f"═══ {now.strftime('%H:%M:%S')} KOL情绪: {kol_sentiment.upper()} | 恐惧:{fg_val} | {'偏多' if kol_sentiment=='bullish' else '偏空' if kol_sentiment=='bearish' else '中性'} ═══", "KOL")

                for sym in SYMBOLS:
                    cur_price = self.get_ticker(sym) or 0
                    has_pos = sym in self.positions

                    # 获取关键位
                    try:
                        lv = self.get_levels(sym)
                    except Exception as e:
                        log(f"  ❌ {sym} 获取价位失败: {e}", "ERROR")
                        time.sleep(5)
                        continue

                    # ===== 有持仓 → 监控模式 =====
                    if has_pos:
                        self.monitor_position(sym)
                        time.sleep(POSITION_CHECK_INTERVAL)
                        continue

                    # ===== 无持仓 → 信号检测 =====

                    # 显示当前箱体状态
                    box_changed = False
                    last_box = last_box_log.get(sym + "_box", "")
                    curr_box = lv["box_range"]
                    if last_box and last_box != curr_box:
                        log(f"🔄 [{sym}] 箱体更新: {last_box} → {curr_box}（4H收盘触发重算）", "WATCH")
                        box_changed = True
                    last_box_log[sym + "_box"] = curr_box

                    # 突破检测（价格试探箱体边界时提示）
                    if lv["breakout"]:
                        log(f"🔥 [{sym}] {lv['breakout']} | 箱体:{curr_box} | 价格:${cur_price:.2f} | 分析中...", "SIGNAL")

                    # 情绪过滤：只有方向匹配才分析
                    allowed = []
                    if kol_sentiment == "bullish":
                        allowed = ["long_s1", "long_s2"]
                    elif kol_sentiment == "bearish":
                        allowed = ["short_s1", "short_s2"]
                    else:
                        allowed = []  # 中性不交易

                    if not allowed:
                        last_log = last_box_log.get(sym, 0)
                        if now.timestamp() - last_log > 300:
                            log(f"[{sym}] 情绪{kol_sentiment} | 价格:${cur_price:.2f} | 箱体:{curr_box} | 斐波50%:${lv['fib50']:.2f} | 无信号...", "WATCH")
                            last_box_log[sym] = now.timestamp()
                        time.sleep(BOX_CHECK_INTERVAL)
                        continue

                    # 分析技术位
                    sig = self.analyze(sym, kol_sentiment)
                    if sig and sig["action"] in allowed:
                        qty = self._calc_qty(sym)
                        if qty > 0:
                            self.open_position(sig, sym, qty, cur_price)
                        else:
                            log(f"  ⚠️ 数量为0，跳过", "WARN")
                    else:
                        last_log = last_box_log.get(sym, 0)
                        if now.timestamp() - last_log > 300:
                            log(f"[{sym}] 情绪{kol_sentiment} | 价格:${cur_price:.2f} | 斐波50%:${lv['fib50']:.2f} | 未到关键位，等待...", "WATCH")
                            last_box_log[sym] = now.timestamp()

                    time.sleep(BOX_CHECK_INTERVAL)

                time.sleep(BOX_CHECK_INTERVAL)

            except KeyboardInterrupt:
                log("━━━━━━ 用户停止 ━━━━━━", "SYSTEM")
                break
            except Exception as e:
                log(f"❌ 循环异常: {e}", "ERROR")
                time.sleep(10)

if __name__ == "__main__":
    TradeBot().run()
