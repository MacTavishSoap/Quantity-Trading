import os
import json
import time
import threading
from collections import deque
import pandas as pd
try:
    import websocket
except Exception:
    websocket = None

class OrderFlowManager:
    def __init__(self, exchange, symbol, use_ws=True, proxy_host=None, proxy_port=None, is_sandbox=False):
        self.exchange = exchange
        self.symbol = symbol
        self.is_sandbox = is_sandbox
        try:
            self.market_id = self.exchange.market(self.symbol)['id']
        except Exception:
            # Fallback for BTC/USDT:USDT and ETH/USDT:USDT if markets not loaded
            if symbol == 'BTC/USDT:USDT':
                self.market_id = 'BTC-USDT-SWAP'
            elif symbol == 'ETH/USDT:USDT':
                self.market_id = 'ETH-USDT-SWAP'
            else:
                self.market_id = None
                print(f"⚠️ 无法获取 market_id for {symbol}")

        self.max_trade_history = 1000
        self.trades_history = deque(maxlen=self.max_trade_history)
        self.last_book = None
        self.use_ws = use_ws
        self.ws = None
        self.ws_thread = None
        self.ws_running = False
        
        # 优先使用传入的代理，否则检查环境变量，默认关闭
        if proxy_host:
            self.proxy_host = proxy_host
            self.proxy_port = proxy_port
        else:
            self.proxy_host = "127.0.0.1" if (os.getenv("USE_PROXY", "false").lower() == "true") else None
            self.proxy_port = 7890 if self.proxy_host else None
        
        self.current_metrics = {
            'delta_1m': 0.0,      # 1分钟主动买卖差
            'delta_5m': 0.0,      # 5分钟主动买卖差
            'cvd': 0.0,           # 累计成交量Delta (Cumulative Volume Delta)
            'oi': 0.0,            # 持仓量
            'oi_change_1h': 0.0,  # 1小时持仓变化
            'imbalance': 0.0,     # 盘口不平衡度 (买单量-卖单量)/(买单量+卖单量)
            'funding_rate': 0.0,  # 资金费率
            'taker_buy_ratio': 0.5 # 主动买入占比
        }
        
        self.last_update_time = 0
        self.cvd_cumulative = 0.0
        if self.use_ws and websocket is not None:
            self.start_ws()

    def update_metrics(self):
        """更新所有订单流指标"""
        try:
            # 1. 更新成交流 (Delta, CVD)
            self._update_trade_flow()
            
            # 2. 更新盘口压力 (Imbalance)
            self._update_order_book_pressure()
            
            # 3. 更新持仓数据 (OI, Funding)
            self._update_open_interest()
            
            self.last_update_time = time.time()
            return self.current_metrics
            
        except Exception as e:
            print(f"❌ 订单流数据更新失败: {e}")
            return None

    def _update_trade_flow(self):
        trades = None
        if not self.ws_running:
            trades = self.exchange.fetch_trades(self.symbol, limit=100)
        
        current_time = self.exchange.milliseconds()
        one_min_ago = current_time - 60000
        five_min_ago = current_time - 300000
        
        buy_vol_1m = 0.0
        sell_vol_1m = 0.0
        buy_vol_5m = 0.0
        sell_vol_5m = 0.0
        
        if trades:
            for trade in trades:
                if not self.trades_history or trade['id'] != self.trades_history[-1].get('id'):
                    self.trades_history.append(trade)
                    vol = trade['amount']
                    if trade['side'] == 'buy':
                        self.cvd_cumulative += vol
                    else:
                        self.cvd_cumulative -= vol
        
        # 重新计算统计量
        for trade in self.trades_history:
            timestamp = trade['timestamp']
            vol = trade['amount']
            side = trade['side']
            
            if timestamp > one_min_ago:
                if side == 'buy': buy_vol_1m += vol
                else: sell_vol_1m += vol
            
            if timestamp > five_min_ago:
                if side == 'buy': buy_vol_5m += vol
                else: sell_vol_5m += vol
                
        self.current_metrics['delta_1m'] = buy_vol_1m - sell_vol_1m
        self.current_metrics['delta_5m'] = buy_vol_5m - sell_vol_5m
        self.current_metrics['cvd'] = self.cvd_cumulative
        
        total_vol_1m = buy_vol_1m + sell_vol_1m
        if total_vol_1m > 0:
            self.current_metrics['taker_buy_ratio'] = buy_vol_1m / total_vol_1m

    def _update_order_book_pressure(self):
        if self.last_book:
            bids_vol = sum([float(x[1]) for x in self.last_book.get('bids', [])])
            asks_vol = sum([float(x[1]) for x in self.last_book.get('asks', [])])
        else:
            order_book = self.exchange.fetch_order_book(self.symbol, limit=20)
            bids_vol = sum([x[1] for x in order_book['bids']])
            asks_vol = sum([x[1] for x in order_book['asks']])
        
        # 计算不平衡度 (-1 到 1)
        # > 0 表示买盘强，< 0 表示卖盘强
        if bids_vol + asks_vol > 0:
            self.current_metrics['imbalance'] = (bids_vol - asks_vol) / (bids_vol + asks_vol)

    def _update_open_interest(self):
        try:
            # 获取持仓量
            # 注意：ccxt okx fetch_open_interest 可能需要特定的参数或接口
            ticker = self.exchange.fetch_ticker(self.symbol)
            # 有些交易所ticker里包含openInterest，如果不行则需要专门的接口
            if 'openInterest' in ticker and ticker['openInterest']:
                self.current_metrics['oi'] = float(ticker['openInterest'])
            else:
                # 尝试专门的接口
                oi_data = self.exchange.fetch_open_interest(self.symbol)
                self.current_metrics['oi'] = float(oi_data['openInterest'])
                
            # 资金费率通常也在ticker或者fundingRate接口
            if 'info' in ticker and 'fundingRate' in ticker['info']:
                 self.current_metrics['funding_rate'] = float(ticker['info']['fundingRate'])
                 
        except Exception as e:
            # OI数据获取经常因为API限制失败，不阻断主流程
            # print(f"⚠️ OI数据获取微瑕: {e}") 
            pass

    def analyze_signal(self):
        """
        基于订单流生成信号
        返回: (signal, confidence, reason)
        """
        m = self.current_metrics
        signal = "HOLD"
        confidence = "LOW"
        reasons = []
        
        delta_bullish = False
        book_bullish = False
        
        # 1. Delta分析
        if m['delta_1m'] > 0 and m['taker_buy_ratio'] > 0.6:
            reasons.append(f"主动买入强势(占比{m['taker_buy_ratio']:.0%})")
            delta_bullish = True
        elif m['delta_1m'] < 0 and m['taker_buy_ratio'] < 0.4:
            reasons.append(f"主动卖出强势(占比{m['taker_buy_ratio']:.0%})")
            delta_bullish = False # Bearish
            
        # 2. 盘口分析
        if m['imbalance'] > 0.3:
            reasons.append("盘口买单支撑强")
            book_bullish = True
        elif m['imbalance'] < -0.3:
            reasons.append("盘口卖压沉重")
            book_bullish = False # Bearish
            
        # 3. 综合判断
        if delta_bullish and book_bullish:
            signal = "BUY"
            confidence = "MEDIUM"
            if m['funding_rate'] < 0: # 费率为负，空头支付多头，利好上涨
                confidence = "HIGH"
                reasons.append("资金费率利好")
                
        elif not delta_bullish and not book_bullish and (m['delta_1m'] < 0 and m['imbalance'] < -0.3):
            signal = "SELL"
            confidence = "MEDIUM"
            if m['funding_rate'] > 0.01: # 费率过高，多头拥挤
                confidence = "HIGH"
                reasons.append("多头拥挤(费率高)")
                
        return signal, confidence, " | ".join(reasons)

    def start_ws(self):
        if self.ws_running or websocket is None or not self.market_id:
            return
        
        if self.is_sandbox:
            url = "wss://wspap.okx.com:8443/ws/v5/public?brokerId=9999"
            print("🌐 使用模拟盘 WebSocket 地址")
        else:
            url = "wss://ws.okx.com:8443/ws/v5/public"
            print("🌐 使用实盘 WebSocket 地址")

        def on_open(ws):
            print("🌐 WebSocket 连接已建立")
            sub = {
                "op": "subscribe",
                "args": [
                    {"channel": "trades", "instId": self.market_id},
                    {"channel": "books5", "instId": self.market_id}
                ]
            }
            ws.send(json.dumps(sub))
            print(f"📡 已订阅频道: trades, books5 ({self.market_id})")

        def on_message(ws, message):
            try:
                msg = json.loads(message)
                if not isinstance(msg, dict):
                    return
                if msg.get("event") == "subscribe":
                    return
                arg = msg.get("arg", {})
                channel = arg.get("channel")
                data = msg.get("data", [])
                
                if channel == "trades":
                    # print(f"DEBUG: 收到成交数据 {len(data)} 条")
                    for t in data:
                        side = t.get("side")
                        vol = float(t.get("sz", 0) or 0)
                        ts = int(t.get("ts", 0) or 0)
                        trade_id = t.get("tradeId") or f"{ts}-{vol}-{side}"
                        trade = {
                            "id": trade_id,
                            "timestamp": ts,
                            "amount": vol,
                            "side": "buy" if side == "buy" else "sell"
                        }
                        self.trades_history.append(trade)
                        if side == "buy":
                            self.cvd_cumulative += vol
                        else:
                            self.cvd_cumulative -= vol
                elif channel == "books5":
                    if data:
                        book = data[0]
                        bids = book.get("bids", [])
                        asks = book.get("asks", [])
                        self.last_book = {
                            "bids": [[float(b[0]), float(b[1])] for b in bids],
                            "asks": [[float(a[0]), float(a[1])] for a in asks]
                        }
            except Exception as e:
                print(f"WS Message Error: {e}")

        def on_error(ws, error):
            print(f"❌ WebSocket 错误: {error}")
            self.ws_running = False

        def on_close(ws, status_code, msg):
            print(f"🔌 WebSocket 连接关闭: {msg}")
            self.ws_running = False

        self.ws = websocket.WebSocketApp(url, on_open=on_open, on_message=on_message, on_error=on_error, on_close=on_close)
        kw = {}
        if self.proxy_host and self.proxy_port:
            kw = {"http_proxy_host": self.proxy_host, "http_proxy_port": self.proxy_port}
        def run():
            self.ws_running = True
            try:
                self.ws.run_forever(**kw)
            finally:
                self.ws_running = False
        self.ws_thread = threading.Thread(target=run, daemon=True)
        self.ws_thread.start()

    def stop_ws(self):
        try:
            self.ws_running = False
            if self.ws:
                self.ws.close()
        except Exception:
            pass
