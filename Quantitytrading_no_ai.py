import os
import time
import ccxt
import pandas as pd
import requests
from dotenv import load_dotenv
from datetime import datetime
from order_flow_manager import OrderFlowManager

# 加载环境变量
load_dotenv()

# ==========================================
# 1. 配置区域
# ==========================================

# 运行模式配置
DRY_RUN = True  # 模拟盘模式 (True: 不发送真实订单, False: 实盘)

# Telegram配置
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN') or os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
TELEGRAM_ENABLED = os.getenv('TELEGRAM_ENABLED', 'false').lower() == 'true'

# 验证Telegram配置
if TELEGRAM_ENABLED:
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        print("✅ Telegram 配置已启用")
    else:
        print("❌ Telegram 配置不完整，将禁用通知功能")
        TELEGRAM_ENABLED = False

# 交易所配置
exchange_config = {
    'options': {
        'defaultType': 'swap',  # OKX使用swap表示永续合约
    },
    'apiKey': os.getenv('OKX_API_KEY'),
    'secret': os.getenv('OKX_SECRET'),
    'password': os.getenv('OKX_PASSWORD'),
    'timeout': 30000,
    'enableRateLimit': True,
}

# 代理配置
USE_PROXY = False # 强制关闭代理，使用直连模式
# if os.getenv('USE_PROXY', 'false').lower() == 'true':
#     exchange_config['proxies'] = {
#         'http': 'http://127.0.0.1:7890',
#         'https': 'http://127.0.0.1:7890',
#     }
#     print("🌐 使用本地代理: http://127.0.0.1:7890")
# else:
print("🌐 直连模式 (无代理)")

# WebSocket 配置
USE_WEBSOCKET = True  # 启用 WebSocket 获取实时订单流数据

# 初始化交易所实例
exchange = ccxt.okx(exchange_config)
# 强制禁用 fetchCurrencies 以免触发私有接口鉴权错误 (Common issue with OKX V5 API keys)
exchange.has['fetchCurrencies'] = False

# 核心交易参数配置
TRADE_CONFIG = {
    'symbol': 'ETH/USDT:USDT', # 切换为 ETH
    'leverage': 20,
    'timeframe': '15m',
    'data_points': 100,
    
    # 策略参数
    'rsi_period': 14,
    'rsi_overbought': 70,
    'rsi_oversold': 30,
    
    # 风险管理参数 (针对ETH高波动性进行优化)
    # ETH 波动通常比 BTC 大，因此止损和回撤参数稍微放宽
    'stop_loss_pct': 0.012,          # 固定止损 1.2% (BTC: 0.8%)
    'trailing_activation': 0.008,    # 盈利达到 0.8% 激活追踪 (BTC: 0.5%)
    'trailing_callback': 0.004,      # 最高点回撤 0.4% 止盈 (BTC: 0.3%)
    
    'position_size_usdt': 1000, # 每次交易名义价值 (USDT)
}

# Telegram批量发送模式
TELEGRAM_BATCH_MODE = True
_telegram_sections = []

# ==========================================
# 2. Telegram 工具函数 (提前定义)
# ==========================================

def send_telegram_message(message):
    if not TELEGRAM_ENABLED: return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {'chat_id': TELEGRAM_CHAT_ID, 'text': message, 'parse_mode': 'HTML'}
        requests.post(url, data=data, timeout=5)
    except Exception as e:
        print(f"Telegram Error: {e}")

def log_and_notify(message):
    print(message)
    if TELEGRAM_ENABLED:
        send_telegram_message(message)

# ==========================================
# 3. 模拟账户 (Virtual Account)
# ==========================================
class VirtualAccount:
    def __init__(self, initial_balance=10000):
        self.balance = initial_balance
        self.initial_balance = initial_balance
        self.position = None  # { 'side': 'long'/'short', 'entry_price': float, 'size': float, 'time': str }
        self.trades = []

    def open_position(self, side, price, size_usdt, time_str):
        if self.position:
            print("⚠️ [模拟] 已有持仓，无法开新仓")
            return False
        
        # 计算数量 (BTC)
        size_btc = size_usdt / price
        self.position = {
            'side': side,
            'entry_price': price,
            'size': size_btc,
            'entry_time': time_str,
            'cost': size_usdt,
            'highest_price': price, # 用于追踪止盈 (多头最高价)
            'lowest_price': price,  # 用于追踪止盈 (空头最低价)
            'trailing_active': False # 是否已激活追踪
        }
        msg = f"🚀 [模拟开仓] {side.upper()} @ {price:.2f} | 数量: {size_btc:.4f} BTC"
        log_and_notify(msg)
        return True

    def close_position(self, price, reason, time_str):
        if not self.position:
            return False

        side = self.position['side']
        entry = self.position['entry_price']
        size = self.position['size']
        
        # 计算盈亏 (简化计算，不含手续费)
        if side == 'long':
            pnl = (price - entry) * size
        else:
            pnl = (entry - price) * size
            
        pnl_pct = (pnl / self.position['cost']) * 100
        
        self.balance += pnl
        self.trades.append({
            'entry_time': self.position['entry_time'],
            'exit_time': time_str,
            'side': side,
            'entry': entry,
            'exit': price,
            'pnl': pnl,
            'pnl_pct': pnl_pct,
            'reason': reason
        })
        
        msg = f"🏁 [模拟平仓] {reason}\n价格: {price:.2f}\nPnL: {pnl:.2f} U ({pnl_pct:.2f}%)\n💰 当前余额: {self.balance:.2f} U"
        log_and_notify(msg)
        
        self.position = None
        return True

    def get_status(self):
        status = f"当前余额: {self.balance:.2f} U | 累计盈亏: {self.balance - self.initial_balance:.2f} U"
        if self.position:
            status += f"\n持仓: {self.position['side'].upper()} @ {self.position['entry_price']:.2f}"
        else:
            status += "\n持仓: 空仓"
        return status

# 全局模拟账户
virtual_account = VirtualAccount()

# ==========================================
# 3. 核心功能函数
# ==========================================

def setup_exchange():
    """设置交易所参数"""
    try:
        # 即使是模拟盘，我们也需要获取合约信息来计算
        print(f"🔍 获取 {TRADE_CONFIG['symbol']} 合约规格...")
        
        # 尝试只获取 SWAP 市场以减少数据量和避免超时
        try:
            markets_list = exchange.fetch_markets({'instType': 'SWAP'})
            # 手动构建 market 字典供后续使用
            for m in markets_list:
                exchange.markets[m['symbol']] = m
                exchange.ids[m['id']] = m['symbol']
                
            btc_market = next((m for m in markets_list if m['symbol'] == TRADE_CONFIG['symbol']), None)
        except Exception as e:
            print(f"⚠️ fetch_markets 失败，尝试 load_markets: {e}")
            exchange.load_markets()
            btc_market = exchange.market(TRADE_CONFIG['symbol'])

        if btc_market:
            TRADE_CONFIG['contract_size'] = float(btc_market['contractSize'])
            TRADE_CONFIG['min_amount'] = btc_market['limits']['amount']['min']
            print(f"✅ 合约规格: 1张 = {TRADE_CONFIG['contract_size']} {TRADE_CONFIG['symbol'].split('/')[0]}")
        else:
            print("⚠️ 未找到合约规格，使用默认值")
            TRADE_CONFIG['contract_size'] = 0.01 if 'BTC' in TRADE_CONFIG['symbol'] else 0.1
            TRADE_CONFIG['min_amount'] = 1

        if not DRY_RUN:
            # 实盘才进行的设置
            print("⚙️ [实盘] 设置全仓模式和杠杆...")
            exchange.set_leverage(TRADE_CONFIG['leverage'], TRADE_CONFIG['symbol'], {'mgnMode': 'cross'})
        
        return True
    except Exception as e:
        print(f"❌ 交易所设置失败: {e}")
        # 模拟盘允许失败继续 (使用默认值)
        if DRY_RUN:
             print("⚠️ 模拟盘模式：忽略设置错误，使用默认参数继续...")
             if 'contract_size' not in TRADE_CONFIG: 
                 TRADE_CONFIG['contract_size'] = 0.01 if 'BTC' in TRADE_CONFIG['symbol'] else 0.1
             return True
        return False

def get_btc_ohlcv_enhanced():
    """获取K线并计算指标"""
    try:
        ohlcv = exchange.fetch_ohlcv(TRADE_CONFIG['symbol'], TRADE_CONFIG['timeframe'], limit=TRADE_CONFIG['data_points'])
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')

        # 计算指标
        # 1. RSI
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(TRADE_CONFIG['rsi_period']).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(TRADE_CONFIG['rsi_period']).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))

        # 2. MACD
        exp12 = df['close'].ewm(span=12, adjust=False).mean()
        exp26 = df['close'].ewm(span=26, adjust=False).mean()
        df['macd'] = exp12 - exp26
        df['signal'] = df['macd'].ewm(span=9, adjust=False).mean()

        # 3. ATR (用于波动率参考)
        high_low = df['high'] - df['low']
        high_close = (df['high'] - df['close'].shift()).abs()
        low_close = (df['low'] - df['close'].shift()).abs()
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = ranges.max(axis=1)
        df['atr'] = true_range.rolling(14).mean()

        current = df.iloc[-1]
        return {
            'price': current['close'],
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'technical': {
                'rsi': current['rsi'],
                'macd': current['macd'],
                'macd_signal': current['signal'],
                'atr': current['atr']
            },
            'df': df
        }
    except Exception as e:
        print(f"❌ 获取K线失败: {e}")
        return None

# ==========================================
# 4. 策略逻辑
# ==========================================

def analyze_market(price_data, order_flow_metrics):
    """
    综合分析市场
    策略逻辑:
    1. 技术面: RSI不过热 + MACD趋势
    2. 资金面: 订单流Delta方向确认 + 盘口失衡确认
    """
    signal = 'hold'
    reason = []

    rsi = price_data['technical']['rsi']
    macd = price_data['technical']['macd']
    macd_signal = price_data['technical']['macd_signal']
    
    delta = order_flow_metrics.get('delta_1m', 0)
    imbalance = order_flow_metrics.get('imbalance', 0)
    
    # 阈值
    rsi_high = TRADE_CONFIG['rsi_overbought']
    rsi_low = TRADE_CONFIG['rsi_oversold']

    # --- 做多逻辑 ---
    # 技术面: RSI < 70 (未超买) 且 MACD > Signal (金叉状态)
    tech_long = rsi < rsi_high and macd > macd_signal
    # 资金面: 1分钟主动买入更多 (Delta > 0) 且 盘口买单厚 (Imbalance > 0)
    flow_long = delta > 0 and imbalance > 0.1 # 0.1 表示买盘比卖盘多10%以上
    
    if tech_long and flow_long:
        signal = 'buy'
        reason.append(f"RSI({rsi:.1f})健康")
        reason.append("MACD看涨")
        reason.append(f"资金流Delta({delta:.2f})为正")

    # --- 做空逻辑 ---
    # 技术面: RSI > 30 (未超卖) 且 MACD < Signal (死叉状态)
    tech_short = rsi > rsi_low and macd < macd_signal
    # 资金面: 1分钟主动卖出更多 (Delta < 0) 且 盘口卖单厚 (Imbalance < -0.1)
    flow_short = delta < 0 and imbalance < -0.1
    
    if tech_short and flow_short:
        signal = 'sell'
        reason.append(f"RSI({rsi:.1f})健康")
        reason.append("MACD看跌")
        reason.append(f"资金流Delta({delta:.2f})为负")

    return signal, ", ".join(reason)

def check_risk_management(current_price, timestamp):
    """检查持仓风险 (动态追踪止盈 + 固定止损)"""
    if DRY_RUN:
        pos = virtual_account.position
        if not pos: return False
        
        entry = pos['entry_price']
        side = pos['side']
        
        # 1. 更新最高/最低价
        if side == 'long':
            if current_price > pos['highest_price']:
                pos['highest_price'] = current_price
            
            # 计算当前浮动盈亏比例
            pnl_pct = (current_price - entry) / entry
            
        else: # short
            if current_price < pos['lowest_price']:
                pos['lowest_price'] = current_price
                
            # 计算当前浮动盈亏比例
            pnl_pct = (entry - current_price) / entry

        # 2. 检查固定止损
        if pnl_pct <= -TRADE_CONFIG['stop_loss_pct']:
            virtual_account.close_position(current_price, "固定止损触发", timestamp)
            return True

        # 3. 动态追踪止盈逻辑
        # 激活条件: 盈利超过 trailing_activation
        if not pos['trailing_active']:
            if pnl_pct >= TRADE_CONFIG['trailing_activation']:
                pos['trailing_active'] = True
                print(f"🎯 [追踪激活] 当前盈利 {pnl_pct*100:.2f}% >= {TRADE_CONFIG['trailing_activation']*100}%")
        
        # 执行追踪: 如果已激活
        if pos['trailing_active']:
            callback_rate = TRADE_CONFIG['trailing_callback']
            
            if side == 'long':
                # 触发价 = 最高价 * (1 - 回撤比例)
                trigger_price = pos['highest_price'] * (1 - callback_rate)
                if current_price <= trigger_price:
                    reason = f"追踪止盈触发 (最高:{pos['highest_price']:.1f}, 回撤:{callback_rate*100}%)"
                    virtual_account.close_position(current_price, reason, timestamp)
                    return True
            else: # short
                # 触发价 = 最低价 * (1 + 回撤比例)
                trigger_price = pos['lowest_price'] * (1 + callback_rate)
                if current_price >= trigger_price:
                    reason = f"追踪止盈触发 (最低:{pos['lowest_price']:.1f}, 回撤:{callback_rate*100}%)"
                    virtual_account.close_position(current_price, reason, timestamp)
                    return True
            
    return False

# ==========================================
# 6. 主循环
# ==========================================

def run_strategy_loop():
    print("🚀 启动策略引擎...")
    if DRY_RUN:
        print("🧪 当前模式: 模拟盘 (Dry Run)")
        print(f"💰 初始模拟资金: {virtual_account.balance} U")
    else:
        print("⚠️⚠️⚠️ 当前模式: 实盘交易 (Real Trading) ⚠️⚠️⚠️")
        print("请确保您已充分了解风险！")

    # 初始化订单流管理器
    print(f"🌊 初始化订单流管理器 (WebSocket: {USE_WEBSOCKET})...")
    of_manager = OrderFlowManager(exchange, TRADE_CONFIG['symbol'], use_ws=USE_WEBSOCKET)
    
    # 等待 WebSocket 数据预热
    if USE_WEBSOCKET:
        print("⏳ 等待 WebSocket 数据预热 (5秒)...")
        time.sleep(5)
    
    log_and_notify(f"🤖 策略已启动\n交易对: {TRADE_CONFIG['symbol']}\n模式: {'模拟盘' if DRY_RUN else '实盘'}\n数据源: {'WebSocket' if USE_WEBSOCKET else 'REST API'}")

    while True:
        try:
            timestamp = datetime.now().strftime('%H:%M:%S')
            
            # 1. 获取数据
            price_data = get_btc_ohlcv_enhanced()
            if not price_data:
                time.sleep(10)
                continue
                
            current_price = price_data['price']
            
            # 更新订单流数据
            of_metrics = of_manager.update_metrics()
            
            # 2. 打印状态 (每分钟一次，或者有信号时)
            rsi = price_data['technical']['rsi']
            delta = of_metrics.get('delta_1m', 0)
            
            print(f"[{timestamp}] 价格:{current_price:.1f} | RSI:{rsi:.1f} | Delta:{delta:.2f}")

            # 3. 风险管理 (检查现有持仓)
            if check_risk_management(current_price, timestamp):
                # 如果触发了止盈止损，本轮不再开仓
                pass
            
            # 4. 信号分析 (如果没持仓)
            elif (DRY_RUN and not virtual_account.position) or (not DRY_RUN and False): # 实盘持仓检查暂略
                signal, reason = analyze_market(price_data, of_metrics)
                
                if signal == 'buy':
                    log_and_notify(f"� [买入信号] {reason} @ {current_price:.1f}")
                    if DRY_RUN:
                        virtual_account.open_position('long', current_price, TRADE_CONFIG['position_size_usdt'], timestamp)
                
                elif signal == 'sell':
                    log_and_notify(f"🔴 [卖出信号] {reason} @ {current_price:.1f}")
                    if DRY_RUN:
                        virtual_account.open_position('short', current_price, TRADE_CONFIG['position_size_usdt'], timestamp)

        except KeyboardInterrupt:
            print("\n� 用户停止程序")
            break
        except Exception as e:
            print(f"❌ 循环错误: {e}")
            time.sleep(5)
            
        time.sleep(15) # 15秒轮询一次

def main():
    if not setup_exchange():
        return
    run_strategy_loop()

if __name__ == "__main__":
    main()
