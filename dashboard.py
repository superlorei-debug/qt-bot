"""
Dashboard Backend
端口: 8888
访问: http://192.168.31.167:8888
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
import json, requests, time, hmac, hashlib, os, csv, concurrent.futures
from datetime import datetime
from urllib.parse import urlparse

BASE_URL = "https://demo-fapi.binance.com"
API_KEY = os.environ.get("BINANCE_API_KEY") or ""
API_SECRET = os.environ.get("BINANCE_API_SECRET") or ""
LOG_DIR = os.path.expanduser("~/Desktop/trade_logs")

_lv_cache = {}
_acc_cache = (None, 0)
_fr_cache = (None, 0)


def api_get(path, params=None, timeout=8):
    ts = int(time.time() * 1000)
    if params is None:
        params = {}
    params["timestamp"] = ts
    query = "&".join(str(k) + "=" + str(v) for k, v in sorted(params.items()))
    sig = hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
    params["signature"] = sig
    try:
        r = requests.get(BASE_URL + path, params=params, headers={"X-MBX-APIKEY": API_KEY}, timeout=timeout)
        return r.json()
    except:
        return {}


def get_price(sym):
    try:
        r = requests.get("https://fapi.binance.com/fapi/v1/ticker/price?symbol=" + sym, timeout=4)
        return float(r.json().get("price", 0))
    except:
        return 0


def get_levels(symbol):
    global _lv_cache
    now = time.time()
    if symbol in _lv_cache:
        data, ts_cache = _lv_cache[symbol]
        if now - ts_cache < 60:
            return data
    try:
        url = "https://api.binance.com/api/v3/klines?symbol=" + symbol + "&interval=4h&limit=50"
        df = requests.get(url, timeout=5).json()
        if not df:
            return None
        h4_hi = max(float(k[2]) for k in df)
        h4_lo = min(float(k[3]) for k in df)
        fib50 = h4_lo + (h4_hi - h4_lo) * 0.5
        sub_r = fib50 + (fib50 - h4_lo)
        sub_s = h4_lo - (h4_hi - fib50)
        cur = get_price(symbol)
        br = ""
        if cur >= h4_hi * 0.999:
            br = "向上试探强阻"
        elif cur <= h4_lo * 1.001:
            br = "向下试探强撑"
        result = {
            "box_range": "$" + str(int(h4_lo)) + "~$" + str(int(h4_hi)),
            "fib": "$" + str(int(fib50)),
            "fib50": fib50,
            "sub_r": sub_r,
            "sub_s": sub_s,
            "cur": cur,
            "breakout": br,
        }
        _lv_cache[symbol] = (result, now)
        return result
    except:
        return None


def get_funding(sym):
    global _fr_cache
    now = time.time()
    if sym in _fr_cache:
        data, ts_cache = _fr_cache[sym]
        if now - ts_cache < 300:
            return data
    try:
        d = api_get("/fapi/v1/premiumIndex", {"symbol": sym}, timeout=5)
        fr = d.get("lastFundingRate", "0")
        result = float(fr) * 100 if fr else 0.0
        _fr_cache[sym] = (result, now)
        return result
    except:
        return 0.0


def get_sentiment():
    try:
        import sys
        sys.path.insert(0, "/Users/mac/Desktop")
        from sentiment import check_sentiment
        return check_sentiment()
    except:
        return {"sentiment": "neutral", "fg_val": 0}


def get_protections():
    try:
        import sys
        sys.path.insert(0, "/Users/mac/Desktop")
        bot = _bot
        if bot and hasattr(bot, "protections"):
            return bot.protections
    except:
        pass
    return {}


def get_equity_snapshot():
    snap_dir = os.path.join(LOG_DIR, "equity_snapshots")
    os.makedirs(snap_dir, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    snap_file = os.path.join(snap_dir, today + ".csv")
    resp = api_get("/fapi/v2/account", {}, timeout=5)
    equity = float(resp.get("totalMarginBalance", 0))
    with open(snap_file, "a") as f:
        f.write(datetime.now().strftime("%H:%M:%S") + "," + str(equity) + "\n")


def get_equity_history():
    history = []
    snap_file = os.path.join(LOG_DIR, "equity_snapshots", datetime.now().strftime("%Y-%m-%d") + ".csv")
    if os.path.exists(snap_file):
        with open(snap_file, "r") as f:
            for line in f:
                parts = line.strip().split(",")
                if len(parts) >= 2:
                    try:
                        history.append({"time": parts[0], "equity": float(parts[1])})
                    except:
                        pass
    history.reverse()
    return history


def get_history():
    trades = []
    csv_path = os.path.join(LOG_DIR, "trades_" + datetime.now().strftime("%Y-%m-%d") + ".csv")
    if os.path.exists(csv_path):
        with open(csv_path, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                trades.append(dict(row))
    return trades


def collect_all():
    """优先返回缓存数据，后台异步刷新账户（账户API慢，约10秒）"""
    global _acc_cache
    now = datetime.now()
    syms = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

    # 先用缓存，立即返回
    if _acc_cache[0] is not None:
        account_raw = _acc_cache[0]
    else:
        # 没缓存，强制同步拿一次（阻塞）
        _acc_cache = (api_get("/fapi/v2/account"), time.time())
        account_raw = _acc_cache[0]

    account = {
        "total_equity": float(account_raw.get("totalMarginBalance", 0)),
        "available": float(account_raw.get("availableBalance", 0)),
        "unrealized_pnl": float(account_raw.get("totalUnrealizedProfit", 0)),
    }

    # 其余全部并发（不阻塞）
    fr_map = {s: get_funding(s) for s in syms}
    lv_map = {s: get_levels(s) for s in syms}
    sentiment = get_sentiment()
    protections = get_protections()

    # 后台更新账户缓存（下次就快了）
    def refresh_account():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            ex.submit(lambda: (_acc_cache.__setitem__(0, api_get("/fapi/v2/account")), _acc_cache.__setitem__(1, time.time())))
    

    # 持仓
    positions = []
    for pos in account_raw.get("positions", []):
        amt = float(pos.get("positionAmt", 0))
        if abs(amt) < 0.0001:
            continue
        sym = pos["symbol"]
        direction = "long" if amt > 0 else "short"
        entry = float(pos.get("entryPrice", 0))
        lv = lv_map.get(sym)
        prot = protections.get(sym, {})
        cur = get_price(sym)
        if prot:
            tp1, tp2, sl = prot.get("tp1_price", 0), prot.get("tp2_price", 0), prot.get("sl_price", 0)
            if prot.get("all_closed"):
                status = "已全平"
            elif prot.get("tp1_triggered"):
                status = "TP1已平50%"
            else:
                status = "持仓中"
        elif lv:
            sl = entry * (0.995 if direction == "long" else 1.005)
            tp1 = lv["fib50"]
            tp2 = lv["sub_r"] if direction == "long" else lv["sub_s"]
            status = "持仓中"
        else:
            tp1 = tp2 = sl = 0
            status = "持仓中"
        positions.append({
            "symbol": sym,
            "direction": direction,
            "quantity": abs(amt),
            "entry_price": entry,
            "current_price": cur,
            "unrealized_pnl": float(pos.get("unrealizedProfit", 0)),
            "leverage": int(pos.get("leverage", 20)),
            "margin_type": pos.get("marginType", "cross"),
            "tp1": tp1,
            "tp2": tp2,
            "sl": sl,
            "status": status,
        })

    # 箱体和信号（只针对无持仓的币种）
    boxes = {}
    signals = []
    for sym in syms:
        lv = lv_map.get(sym)
        amt = float(next((x.get("positionAmt", 0) for x in account_raw.get("positions", []) if x.get("symbol") == sym), 0))
        if not lv or abs(amt) > 0.0001:
            continue
        boxes[sym] = {"range": lv["box_range"], "fib": lv["fib"]}
        if lv["breakout"]:
            signals.append({"tag": "fire", "type": "fire", "text": sym + ": " + lv["breakout"] + " | 价格:$" + str(int(lv["cur"]))})
        else:
            signals.append({"tag": "box", "type": "box", "text": sym + ": " + lv["box_range"] + " 斐波50%=" + lv["fib"] + " | 价格:$" + str(int(lv["cur"]))})

    return {
        "time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "account": account,
        "positions": positions,
        "boxes": boxes,
        "signals": signals,
        "kol_sentiment": sentiment.get("sentiment", "neutral"),
        "fear_greed": sentiment.get("fg_val", 0),
        "funding": fr_map,
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html", "/dashboard"):
            self.send_html()
        elif path == "/api/all" or path == "/api/status":
            self.send_json(collect_all())
        elif path == "/api/history":
            self.send_json({"trades": get_history()})
        elif path == "/api/equity":
            self.send_json({"history": get_equity_history()})
        elif path == "/api/funding":
            fr = {s: get_funding(s) for s in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]}
            self.send_json({"funding": fr})
        else:
            self.send_error(404)

    def send_json(self, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self):
        try:
            with open("/Users/mac/Desktop/dashboard.html", "rb") as f:
                body = f.read()
        except:
            body = b"<html><body><h1>HTML not found. Please check dashboard.html location.</h1></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


_bot = None


def inject_bot(bot):
    global _bot
    _bot = bot


def start_server(port=8888, bot=None):
    inject_bot(bot)
    server = HTTPServer(("0.0.0.0", port), Handler)
    print("Dashboard 已启动: http://192.168.31.167:" + str(port))
    server.serve_forever()
