"""
Binance Futures 永续合约交易机器人 (Demo盘)
- 支持止盈、止损、追踪止盈
- 所有操作落盘日志
- 订单同步
"""

import requests
import time
import hmac
import hashlib
import json
import os
from datetime import datetime

# ========== 配置区 ==========
BASE_URL = "https://demo-fapi.binance.com"
API_KEY = "YOUR_API_KEY_HERE"
API_SECRET = "YOUR_API_SECRET_HERE"

LOG_DIR = os.path.expanduser("~/Desktop/trade_logs")
os.makedirs(LOG_DIR, exist_ok=True)

# ========== 日志系统 ==========
def log(msg, tag="INFO"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{tag}] {msg}"
    print(line)
    log_file = os.path.join(LOG_DIR, f"trade_{datetime.now().strftime('%Y-%m-%d')}.log")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def log_order(order, action):
    """记录订单详情"""
    log_file = os.path.join(LOG_DIR, f"orders_{datetime.now().strftime('%Y-%m-%d')}.jsonl")
    record = {
        "time": datetime.now().isoformat(),
        "action": action,
        "order": order
    }
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

# ========== 签名工具 ==========
def sign(params, secret):
    p = []
    for k, v in sorted(params.items()):
        p.append(f"{k}={v}")
    query = "&".join(p)
    signature = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    return signature

# ========== HTTP请求 ==========
def http_request(method, path, params=None, signed=False):
    url = f"{BASE_URL}{path}"
    headers = {"X-MBX-APIKEY": API_KEY}
    if params is None:
        params = {}
    if signed:
        params["timestamp"] = int(time.time() * 1000)
        params["signature"] = sign(params, API_SECRET)
    if method == "GET":
        r = requests.get(url, params=params, headers=headers, timeout=10)
    elif method == "POST":
        r = requests.post(url, params=params, headers=headers, timeout=10)
    elif method == "DELETE":
        r = requests.delete(url, params=params, headers=headers, timeout=10)
    else:
        raise ValueError(f"Unsupported method: {method}")
    return r.json()

# ========== 核心功能 ==========
class TradeBot:
    def __init__(self):
        self.account = None
        self.positions = {}
        self.pending_orders = {}

    # 1. 连接交易所
    def connect(self):
        log("策略程序启动........", "SYSTEM")
        log("连接交易所获取实时行情.......", "SYSTEM")
        # 测试连通性
        resp = http_request("GET", "/fapi/v1/ping")
        if "msg" in resp and resp["msg"] != "success":
            log(f"连接失败: {resp}", "ERROR")
            return False
        log("连接交易所成功 ✅", "SYSTEM")
        return True

    # 2. 获取账户信息
    def get_account(self):
        log("获取账户信息中.......", "INFO")
        resp = http_request("GET", "/fapi/v2/account", signed=True)
        if "accountImUsed" in resp:
            self.account = resp
            assets = resp.get("assets", [])
            positions = resp.get("positions", [])
            total_margin = sum(float(a.get("marginBalance", 0)) for a in assets)
            log(f"账户信息 ✅", "INFO")
            log(f"  总权益: ${total_margin:.2f}", "INFO")
            for pos in positions:
                if float(pos.get("positionAmt", 0)) != 0:
                    log(f"  持仓: {pos['symbol']} | 数量: {pos['positionAmt']} | 盈亏: {pos['unrealizedProfit']}", "INFO")
            self.sync_positions()
            return resp
        else:
            log(f"获取账户失败: {resp.get('msg', resp)}", "ERROR")
            return None

    # 3. 同步持仓
    def sync_positions(self):
        if not self.account:
            return
        self.positions = {}
        for pos in self.account.get("positions", []):
            amt = float(pos.get("positionAmt", 0))
            if amt != 0:
                self.positions[pos["symbol"]] = pos

    # 4. 获取当前持仓
    def get_position(self, symbol):
        self.get_account()
        return self.positions.get(symbol)

    # 5. 订阅行情 (这里用轮询，真实可用 WebSocket)
    def get_ticker(self, symbol):
        resp = http_request("GET", "/fapi/v1/ticker/price", {"symbol": symbol})
        if "price" in resp:
            return float(resp["price"])
        return None

    # 6. 下单
    def place_order(self, symbol, side, order_type, quantity, price=None,
                    take_profit_price=None, stop_price=None, trailing_delta=None):
        """
        side: BUY / SELL
        order_type: MARKET / LIMIT
        quantity: 数量
        price: 限价价格（仅LIMIT）
        take_profit_price: 止盈价
        stop_price: 止损价
        trailing_delta: 追踪止盈回撤幅度 (仅MARKET用)
        """
        params = {
            "symbol": symbol,
            "side": side,
            "positionSide": "BOTH",
            "type": order_type,
            "quantity": quantity,
        }
        if order_type == "LIMIT":
            params["price"] = price
            params["timeInForce"] = "GTC"
        if stop_price:
            params["stopPrice"] = stop_price
            params["workType"] = "STOP"
        if trailing_delta:
            params["trailingDelta"] = trailing_delta

        resp = http_request("POST", "/fapi/v1/order", params, signed=True)
        log_order(resp, f"place_{side}")
        if "orderId" in resp:
            log(f"下单成功 ✅ | 订单ID: {resp['orderId']} | {symbol} | {side} | {order_type} | 数量: {quantity}", "ORDER")
            if price:
                log(f"  价格: ${price}", "ORDER")
            if take_profit_price:
                log(f"  止盈价: ${take_profit_price}", "ORDER")
            if stop_price:
                log(f"  止损价: ${stop_price}", "ORDER")
            if trailing_delta:
                log(f"  追踪止盈回撤: {trailing_delta}", "ORDER")
            return resp
        else:
            log(f"下单失败: {resp.get('msg', resp)}", "ERROR")
            return resp

    # 7. 市价下单 + 止盈止损
    def market_order_with_protection(self, symbol, side, quantity,
                                     take_profit_price=None, stop_loss_price=None,
                                     trailing_delta=None):
        """开仓 + 自动附加止盈止损"""
        # 先下市价单
        order_resp = self.place_order(symbol, side, "MARKET", quantity,
                                      take_profit_price=take_profit_price,
                                      stop_price=stop_loss_price,
                                      trailing_delta=trailing_delta)
        if "orderId" not in order_resp:
            return order_resp

        # 附加止盈止损 (用条件单)
        if take_profit_price and stop_loss_price:
            tp_side = "SELL" if side == "BUY" else "BUY"
            tp_params = {
                "symbol": symbol,
                "side": tp_side,
                "positionSide": "BOTH",
                "type": "TAKE_PROFIT_MARKET",
                "quantity": quantity,
                "stopPrice": take_profit_price,
                "workingType": "CONTRACT_PRICE",
                "reduceOnly": True,
            }
            sl_params = {
                "symbol": symbol,
                "side": tp_side,
                "positionSide": "BOTH",
                "type": "STOP_MARKET",
                "quantity": quantity,
                "stopPrice": stop_loss_price,
                "workingType": "CONTRACT_PRICE",
                "reduceOnly": True,
            }
            # 追踪止损
            if trailing_delta:
                ts_params = {
                    "symbol": symbol,
                    "side": tp_side,
                    "positionSide": "BOTH",
                    "type": "TRAILING_STOP_MARKET",
                    "quantity": quantity,
                    "trailingDelta": trailing_delta,
                    "reduceOnly": True,
                }
                ts_resp = http_request("POST", "/fapi/v1/order", ts_params, signed=True)
                log_order(ts_resp, "place_trailing_stop")
                if "orderId" in ts_resp:
                    log(f"追踪止损挂单成功 ✅ | 订单ID: {ts_resp['orderId']}", "ORDER")
                else:
                    log(f"追踪止损挂单失败: {ts_resp.get('msg', ts_resp)}", "ERROR")

            sl_resp = http_request("POST", "/fapi/v1/order", sl_params, signed=True)
            log_order(sl_resp, "place_stop_loss")
            if "orderId" in sl_resp:
                log(f"止损单挂单成功 ✅ | 订单ID: {sl_resp['orderId']}", "ORDER")
            else:
                log(f"止损单挂单失败: {sl_resp.get('msg', sl_resp)}", "ERROR")

        return order_resp

    # 8. 查询订单
    def get_order(self, symbol, order_id):
        params = {"symbol": symbol, "orderId": order_id}
        resp = http_request("GET", "/fapi/v1/order", params, signed=True)
        log_order(resp, "query_order")
        return resp

    # 9. 撤销订单
    def cancel_order(self, symbol, order_id):
        params = {"symbol": symbol, "orderId": order_id}
        resp = http_request("DELETE", "/fapi/v1/order", params, signed=True)
        log_order(resp, "cancel_order")
        if "orderId" in resp:
            log(f"撤单成功 ✅ | 订单ID: {order_id}", "ORDER")
        else:
            log(f"撤单失败: {resp.get('msg', resp)}", "ERROR")
        return resp

    # 10. 查询所有挂单
    def get_open_orders(self, symbol=None):
        params = {}
        if symbol:
            params["symbol"] = symbol
        resp = http_request("GET", "/fapi/v1/openOrders", params, signed=True)
        return resp

    # 11. 平仓
    def close_position(self, symbol):
        pos = self.get_position(symbol)
        if not pos:
            log(f"{symbol} 无持仓，无需平仓", "INFO")
            return None
        amt = float(pos["positionAmt"])
        side = "SELL" if amt > 0 else "BUY"
        log(f"平仓操作 | {symbol} | {side} | 数量: {abs(amt)}", "ORDER")
        return self.place_order(symbol, side, "MARKET", abs(amt))

    # 12. 根据信号交易
    def trade_from_signal(self, sym, signal):
        """
        signal 格式:
        {
            "action": "long" | "short" | "close",
            "quantity": 0.01,  (合约数量)
            "entry_price": 65000,  (参考入场价)
            "take_profit": 66000,
            "stop_loss": 64000,
            "trailing_delta": 50,  (可选，追踪止盈回撤点数)
            "reason": "EMA金叉+突破4H阻力"
        }
        """
        action = signal.get("action")
        quantity = signal.get("quantity")
        tp = signal.get("take_profit")
        sl = signal.get("stop_loss")
        ts = signal.get("trailing_delta")
        reason = signal.get("reason", "")
        cur_price = self.get_ticker(sym) or signal.get("entry_price")

        log(f"===== 币种信号信息 =====", "SIGNAL")
        log(f"交易对: {sym}", "SIGNAL")
        log(f"信号方向: {action}", "SIGNAL")
        log(f"参考价格: ${cur_price:.2f}", "SIGNAL")
        log(f"入场数量: {quantity}", "SIGNAL")
        log(f"止盈价: ${tp:.2f}" if tp else "止盈价: 无", "SIGNAL")
        log(f"止损价: ${sl:.2f}" if sl else "止损价: 无", "SIGNAL")
        log(f"追踪止盈回撤: {ts}" if ts else "追踪止盈: 无", "SIGNAL")
        log(f"信号原因: {reason}", "SIGNAL")

        if action == "close":
            self.close_position(sym)
            return

        side = "BUY" if action == "long" else "SELL"
        self.market_order_with_protection(sym, side, quantity, tp, sl, ts)
        log(f"止盈/止损/追踪止盈 — 止盈挂单: ${tp} | 止损挂单: ${sl} | 追踪回撤: {ts}", "SIGNAL")

    # 同步账户信息
    def sync(self):
        log("同步账户信息.......", "INFO")
        self.get_account()
        open_orders = self.get_open_orders()
        log(f"当前挂单数: {len(open_orders)}", "INFO")
        for o in open_orders:
            log(f"  挂单: {o['symbol']} | {o['side']} | {o['type']} | 数量: {o['origQty']} | 价格: {o.get('price','市价')}", "INFO")

# ========== 主程序 ==========
if __name__ == "__main__":
    bot = TradeBot()
    if not bot.connect():
        exit(1)
    bot.sync()

    # 示例：收到做多信号时这样调用
    # bot.trade_from_signal("BTCUSDT", {
    #     "action": "long",
    #     "quantity": 0.01,
    #     "entry_price": 65000,
    #     "take_profit": 66000,
    #     "stop_loss": 64000,
    #     "trailing_delta": 50,
    #     "reason": "EMA金叉+突破4H阻力"
    # })

    # 示例：收到做空信号时这样调用
    # bot.trade_from_signal("ETHUSDT", {
    #     "action": "short",
    #     "quantity": 0.1,
    #     "entry_price": 3500,
    #     "take_profit": 3400,
    #     "stop_loss": 3600,
    #     "trailing_delta": 30,
    #     "reason": "EMA死叉+跌破4H支撑"
    # })

    # 示例：平仓
    # bot.trade_from_signal("BTCUSDT", {"action": "close"})
