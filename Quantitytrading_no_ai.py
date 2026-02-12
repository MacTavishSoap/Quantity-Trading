import os
import time
import ccxt
import pandas as pd
import requests
from dotenv import load_dotenv
from datetime import datetime
from order_flow_manager import OrderFlowManager
from ml_noise_filter import MarketNoiseFilter

# 加载环境变量
load_dotenv()

# ==========================================
# 1. 配置区域
# ==========================================

# 运行模式配置# 运行模式配置
# 可选值: 'LOCAL_SIMULATION' (本地模拟), 'OKX_TESTNET' (OKX模拟盘), 'REAL_TRADING' (实盘)
RUN_MODE = 'REAL_TRADING' 
DRY_RUN = (RUN_MODE == 'LOCAL_SIMULATION')
SAFE_MODE = True # 安全模式：禁止真实下单

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
    'timeout': 30000,
    'enableRateLimit': True,
}

# 根据运行模式加载对应的 API Key
if RUN_MODE == 'OKX_TESTNET':
    exchange_config['apiKey'] = os.getenv('OKX_TESTNET_API_KEY')
    exchange_config['secret'] = os.getenv('OKX_TESTNET_SECRET')
    exchange_config['password'] = os.getenv('OKX_TESTNET_PASSWORD')
    print("🔑 加载 OKX 模拟盘 (Testnet) API Key")
else:
    # LOCAL_SIMULATION (也可能需要行情数据) 或 REAL_TRADING
    exchange_config['apiKey'] = os.getenv('OKX_REAL_API_KEY')
    exchange_config['secret'] = os.getenv('OKX_REAL_SECRET')
    exchange_config['password'] = os.getenv('OKX_REAL_PASSWORD')
    if RUN_MODE == 'REAL_TRADING':
        print("🔑 加载 OKX 实盘 (Real) API Key")
    else:
        print("🔑 加载 OKX 实盘 Key 用于本地模拟行情获取")

# 代理配置
# 优先尝试本地常用代理端口
USE_PROXY = True
if USE_PROXY:
    exchange_config['proxies'] = {
        'http': 'http://127.0.0.1:7890',
        'https': 'http://127.0.0.1:7890',
    }
    print("🌐 使用本地代理: http://127.0.0.1:7890")
else:
    print("🌐 直连模式 (无代理)")

# WebSocket 配置
USE_WEBSOCKET = True  # 启用 WebSocket 获取实时订单流数据

# 初始化交易所实例
exchange = ccxt.okx(exchange_config)
if RUN_MODE == 'OKX_TESTNET':
    exchange.set_sandbox_mode(True)
    print("🧪 已启用 OKX 模拟盘模式 (Sandbox)")
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
    
    # 信号置信度参数
    'confidence_threshold': 75,      # 开单所需最低置信度分数 (0-100)
    'weights': {
        'trend': 20,         # 趋势权重 (大方向)
        'zone': 25,          # 供需区权重 (位置)
        'delta': 20,         # 资金流权重 (短期爆发)
        'imbalance': 15,     # 盘口权重 (支撑/阻力)
        'macd': 10,          # 动能权重
        'rsi': 10            # 震荡指标权重
    },
    
    # 趋势分析参数
    'trend_timeframe': '4h',         # 趋势判断周期
    'trend_ema_period': 50,          # 趋势EMA周期

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

# 市场噪音过滤器
noise_filter = MarketNoiseFilter()

# ==========================================
# 3.b 实盘/Testnet 交易辅助函数
# ==========================================

def get_exchange_position():
    """获取交易所真实持仓 (用于 OKX_TESTNET 或 REAL_TRADING)"""
    try:
        positions = exchange.fetch_positions([TRADE_CONFIG['symbol']])
        if positions:
            # 过滤出持仓量大于0的
            active_pos = [p for p in positions if float(p['contracts']) > 0]
            if active_pos:
                pos = active_pos[0]
                return {
                    'side': pos['side'], # long or short
                    'entry_price': float(pos['entryPrice']),
                    'contracts': float(pos['contracts']),
                    'unrealized_pnl': float(pos['unrealizedPnl']),
                    'entry_time': datetime.fromtimestamp(int(pos['timestamp'])/1000).strftime('%H:%M:%S') if 'timestamp' in pos else 'N/A'
                }
        return None
    except Exception as e:
        # print(f"⚠️ 获取持仓失败: {e}")  # 暂时屏蔽频繁报错
        return None

def execute_exchange_order(side, price, size_usdt):
    """执行交易所订单"""
    if SAFE_MODE:
        log_and_notify(f"🛡️ [安全模式] 拦截真实下单: {side.upper()} @ {price} | 价值: {size_usdt} U")
        return True

    try:
        # 计算张数
        contract_size = TRADE_CONFIG['contract_size']
        if contract_size <= 0: contract_size = 0.01 # 防止除零
        
        size_coin = size_usdt / price
        num_contracts = int(size_coin / contract_size)
        
        if num_contracts < 1:
            log_and_notify(f"⚠️ 下单数量不足 1 张 ({size_coin:.4f} < {contract_size})，忽略")
            return False
            
        print(f"📤 [API] 发送订单: {side.upper()} {num_contracts} 张 @ 市价")
        
        # 市价单
        # 开多: buy, 开空: sell
        order_side = 'buy' if side == 'long' else 'sell'
        
        order = exchange.create_order(
            symbol=TRADE_CONFIG['symbol'],
            type='market',
            side=order_side,
            amount=num_contracts,
            params={'tdMode': 'cross'}
        )
        log_and_notify(f"✅ 订单成功: {order['id']}")
        return True
    except Exception as e:
        log_and_notify(f"❌ 下单失败: {e}")
        return False

def close_exchange_position(position_info):
    """平仓"""
    if SAFE_MODE:
        log_and_notify(f"🛡️ [安全模式] 拦截真实平仓: {position_info['side'].upper()} | 数量: {position_info['contracts']} 张")
        return True

    try:
        side = position_info['side'] # long or short
        contracts = int(position_info['contracts'])
        
        # 平多: sell, 平空: buy
        close_side = 'sell' if side == 'long' else 'buy'
        
        print(f"📤 [API] 发送平仓订单: {close_side.upper()} {contracts} 张")
        
        order = exchange.create_order(
            symbol=TRADE_CONFIG['symbol'],
            type='market',
            side=close_side,
            amount=contracts,
            params={'tdMode': 'cross', 'reduceOnly': True}
        )
        log_and_notify(f"✅ 平仓成功: {order['id']}")
        return True
    except Exception as e:
        log_and_notify(f"❌ 平仓失败: {e}")
        return False

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
            if exchange.markets is None:
                exchange.markets = {}
            if exchange.ids is None:
                exchange.ids = {}
                
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

        if RUN_MODE in ['OKX_TESTNET', 'REAL_TRADING']:
            # 检查账户模式
            try:
                acc_config = exchange.private_get_account_config()
                if acc_config and 'data' in acc_config and len(acc_config['data']) > 0:
                    acct_lv = acc_config['data'][0]['acctLv']
                    # acctLv: 1: Simple, 2: Single-currency margin, 3: Multi-currency margin, 4: Portfolio
                    if str(acct_lv) == '1':
                        print("\n❌❌❌ 严重错误: 账户模式为 '简单模式' (Simple Mode) ❌❌❌")
                        print("⚠️ 此模式不支持合约交易。正在尝试自动升级账户模式为 '单币种保证金' (Level 2)...")
                        try:
                            # 尝试自动升级账户模式
                            exchange.private_post_account_set_account_level({'acctLv': '2'})
                            print("✅ 自动升级成功！账户模式已切换为 '单币种保证金'。")
                            # 重新获取配置确认
                            time.sleep(1)
                            acc_config = exchange.private_get_account_config()
                            new_lv = acc_config['data'][0]['acctLv']
                            print(f"ℹ️ 当前账户模式等级: {new_lv}")
                        except Exception as upgrade_error:
                            print(f"❌ 自动升级失败: {upgrade_error}")
                            print("请务必前往 OKX 网页端或 App 手动修改账户设置。")
                            print("操作路径: 交易设置 -> 账户模式 -> 单币种保证金模式")
                            print("程序将继续尝试运行，但下单可能会失败。\n")
            except Exception as e:
                print(f"⚠️ 无法获取账户配置信息: {e}")

            # 实盘/Testnet 才进行的设置
            print(f"⚙️ [{RUN_MODE}] 设置全仓模式和杠杆...")
            try:
                exchange.set_leverage(TRADE_CONFIG['leverage'], TRADE_CONFIG['symbol'], {'mgnMode': 'cross'})
            except Exception as e:
                print(f"⚠️ 设置杠杆失败 (可能是已设置): {e}")
        
        return True
    except Exception as e:
        print(f"❌ 交易所设置失败: {e}")
        # 本地模拟盘允许失败继续 (使用默认值)
        if RUN_MODE == 'LOCAL_SIMULATION':
             print("⚠️ 本地模拟模式：忽略设置错误，使用默认参数继续...")
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

def get_trend_data():
    """获取大周期趋势数据 (全局战略视角 - 增强版)"""
    try:
        # 获取大周期K线
        ohlcv = exchange.fetch_ohlcv(TRADE_CONFIG['symbol'], TRADE_CONFIG['trend_timeframe'], limit=TRADE_CONFIG['trend_ema_period'] + 10)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        # 计算EMA趋势线
        df['ema_trend'] = df['close'].ewm(span=TRADE_CONFIG['trend_ema_period'], adjust=False).mean()
        
        current = df.iloc[-1]
        ema_last = current['ema_trend']
        
        # 计算EMA斜率 (取最近3根K线的变化率)
        # 避免单根K线的噪音
        if len(df) >= 3:
            ema_prev = df['ema_trend'].iloc[-3]
            # 斜率 = (当前EMA - 前2根EMA) / 前2根EMA
            slope = (ema_last - ema_prev) / ema_prev
        else:
            slope = 0

        # 定义趋势强度阈值 (0.1% 的变化)
        slope_threshold = 0.001
        
        trend_state = 'NEUTRAL'
        current_price = current['close']
        
        # 判定逻辑: 价格位置 + EMA方向
        if current_price > ema_last:
            if slope > slope_threshold:
                trend_state = 'STRONG_BULL' # 价格在EMA之上且EMA强劲上扬
            elif slope > 0:
                trend_state = 'WEAK_BULL'   # 价格在EMA之上且EMA缓慢上扬
            else:
                trend_state = 'POSSIBLE_REVERSAL_TOP' # 价格在EMA之上但EMA开始下跌 (顶背离/减速)
        else:
            if slope < -slope_threshold:
                trend_state = 'STRONG_BEAR' # 价格在EMA之下且EMA强劲下跌
            elif slope < 0:
                trend_state = 'WEAK_BEAR'   # 价格在EMA之下且EMA缓慢下跌
            else:
                trend_state = 'POSSIBLE_REVERSAL_BOTTOM' # 价格在EMA之下但EMA开始上涨 (底背离/减速)
        
        return {
            'trend': trend_state,
            'ema': ema_last,
            'slope': slope,
            'price': current['close']
        }
    except Exception as e:
        print(f"⚠️ 获取趋势数据失败: {e}")
        return {'trend': 'NEUTRAL', 'ema': 0, 'slope': 0, 'price': 0}

def get_supply_demand_zones(df):
    """
    计算供给区和需求区
    逻辑: 寻找大阳线/大阴线前的盘整区 (Base)
    - 需求区 (Demand): 强劲上涨前的区域
    - 供给区 (Supply): 强劲下跌前的区域
    """
    zones = []
    atr = df['atr'].iloc[-1]
    
    # 简单算法: 遍历最近50根K线
    for i in range(len(df) - 50, len(df) - 1):
        if i < 1: continue
        
        curr = df.iloc[i]
        prev = df.iloc[i-1]
        
        body_size = abs(curr['close'] - curr['open'])
        
        # 识别"爆发K线" (Body > 1.5 * ATR)
        if body_size > 1.5 * atr:
            # 1. 需求区: 大阳线
            if curr['close'] > curr['open']:
                # 区域定义: 前一根K线的最低价到最高价
                zone_top = prev['high']
                zone_bottom = prev['low']
                zones.append({
                    'type': 'demand',
                    'top': zone_top,
                    'bottom': zone_bottom,
                    'created_at': df.iloc[i]['timestamp']
                })
            # 2. 供给区: 大阴线
            elif curr['close'] < curr['open']:
                # 区域定义: 前一根K线的最低价到最高价
                zone_top = prev['high']
                zone_bottom = prev['low']
                zones.append({
                    'type': 'supply',
                    'top': zone_top,
                    'bottom': zone_bottom,
                    'created_at': df.iloc[i]['timestamp']
                })
    
    # 过滤掉已经被击穿的区域 (简化版: 只保留最近的)
    valid_zones = []
    current_price = df['close'].iloc[-1]
    
    for zone in reversed(zones): # 从最新往回找
        # 简单过滤: 只保留最近的3个有效区域
        if len(valid_zones) >= 6: break
        valid_zones.append(zone)
            
    return valid_zones

# ==========================================
# 4. 策略逻辑
# ==========================================

def analyze_market(price_data, order_flow_metrics, trend_data, noise_state):
    """
    综合分析市场 (结合供需区 + 多周期 + 置信度评分系统 + 噪音状态)
    """
    signal = 'hold'
    score = 0
    reason = []

    # 提取数据
    current_price = price_data['price']
    df = price_data['df']
    rsi = price_data['technical']['rsi']
    macd = price_data['technical']['macd']
    macd_signal = price_data['technical']['macd_signal']
    
    delta_1m = order_flow_metrics.get('delta_1m', 0)
    delta_5m = order_flow_metrics.get('delta_5m', 0)
    imbalance = order_flow_metrics.get('imbalance', 0)
    
    trend = trend_data['trend']
    
    # --- 1. 宏观方向过滤 (Gatekeeper) ---
    # 结合 大周期趋势 (Trend) + 市场噪音状态 (Noise)
    allowed_direction = 'BOTH'
    regime_msg = ""
    
    if 'BULL' in trend:
        if 'STRONG' in trend and noise_state == 'TRENDING':
            allowed_direction = 'LONG_ONLY'
            regime_msg = "🚀单边牛市"
        elif noise_state == 'RANGING':
            allowed_direction = 'LONG_ONLY' # 牛市震荡，只接多
            regime_msg = "📈牛市震荡(只多)"
    elif 'BEAR' in trend:
        if 'STRONG' in trend and noise_state == 'TRENDING':
            allowed_direction = 'SHORT_ONLY'
            regime_msg = "📉单边熊市"
        elif noise_state == 'RANGING':
            allowed_direction = 'SHORT_ONLY' # 熊市震荡，只空
            regime_msg = "📉熊市震荡(只空)"
            
    if regime_msg:
        reason.append(f"宏观:{regime_msg}")

    # 0. 计算供需区
    zones = get_supply_demand_zones(df)
    
    # 权重配置
    W = TRADE_CONFIG['weights']
    
    # --- 动态阈值与权重调整 (基于噪音状态) ---
    
    # 默认阈值
    rsi_high = TRADE_CONFIG['rsi_overbought'] # 70
    rsi_low = TRADE_CONFIG['rsi_oversold']    # 30
    
    # 状态调整
    if noise_state == 'TRENDING':
        # 趋势市: RSI 阈值外扩，防止过早离场
        rsi_high = 80 
        rsi_low = 20
        # 增加趋势权重，减少震荡指标权重
        W = W.copy()
        W['trend'] += 10
        W['rsi'] -= 5
        # reason.append("🌊趋势模式:权重调整")
        
    elif noise_state == 'RANGING':
        # 震荡市: RSI 阈值内缩，灵敏捕捉反转
        rsi_high = 65
        rsi_low = 35
        # 增加震荡指标权重
        W = W.copy()
        W['rsi'] += 10
        W['zone'] += 5
        W['trend'] -= 10
        # reason.append("〰️震荡模式:权重调整")
        
    elif noise_state == 'CHAOTIC':
        # 混乱市: 严格防御
        return 'hold', 0, "⛔混乱行情-禁止开仓"

    # --- 评分逻辑 ---
    
    # 1. 供需区得分 (Zone Score) - 核心驱动
    zone_score = 0
    in_demand = False
    in_supply = False
    
    for zone in zones:
        # 检查是否在需求区附近 (价格在区域内或上方一点点)
        if zone['type'] == 'demand':
            if zone['bottom'] <= current_price <= zone['top'] * 1.002: # 允许0.2%误差
                in_demand = True
                zone_score = W['zone']
                reason.append(f"触及需求区[{zone['bottom']:.1f}-{zone['top']:.1f}](+{W['zone']})")
                break
        # 检查是否在供给区附近
        elif zone['type'] == 'supply':
            if zone['bottom'] * 0.998 <= current_price <= zone['top']:
                in_supply = True
                zone_score = W['zone']
                reason.append(f"触及供给区[{zone['bottom']:.1f}-{zone['top']:.1f}](+{W['zone']})")
                break
    
    # 2. 趋势得分 (Trend Score)
    trend_score = 0
    trend_direction = 'neutral'
    
    if 'BULL' in trend:
        trend_score = W['trend']
        trend_direction = 'long'
        if 'STRONG' in trend:
             trend_score *= 1.2 # 强趋势加分
             reason.append("🔥强多头")
        else:
             reason.append("↗️弱多头")
             
    elif 'BEAR' in trend:
        trend_score = W['trend']
        trend_direction = 'short'
        if 'STRONG' in trend:
             trend_score *= 1.2
             reason.append("🔥强空头")
        else:
             reason.append("↘️弱空头")

    # 3. 资金流得分 (Delta)
    flow_score = 0
    # 做多逻辑: 在需求区 或 顺势
    if (in_demand or trend_direction == 'long') and not in_supply:
        if delta_1m > 0 and delta_5m > 0:
            flow_score = W['delta']
            reason.append(f"资金流强劲买入(+{W['delta']})")
        elif delta_1m > 0:
            flow_score = W['delta'] * 0.6
            reason.append(f"短时买入(+{int(W['delta']*0.6)})")
            
    # 做空逻辑: 在供给区 或 顺势
    if (in_supply or trend_direction == 'short') and not in_demand:
        if delta_1m < 0 and delta_5m < 0:
            flow_score = W['delta']
            reason.append(f"资金流强劲卖出(+{W['delta']})")
        elif delta_1m < 0:
            flow_score = W['delta'] * 0.6
            reason.append(f"短时卖出(+{int(W['delta']*0.6)})")
            
    # 4. 盘口得分 (Imbalance)
    book_score = 0
    if imbalance > 0.05: # 买单多
        if in_demand or trend_direction == 'long':
            book_score = W['imbalance']
            reason.append(f"盘口支撑(+{W['imbalance']})")
    elif imbalance < -0.05: # 卖单多
        if in_supply or trend_direction == 'short':
            book_score = W['imbalance']
            reason.append(f"盘口压制(+{W['imbalance']})")
        
    # 5. 动能得分 (MACD)
    macd_score = 0
    if macd > macd_signal: # 金叉
        if in_demand or trend_direction == 'long':
            macd_score = W['macd']
            reason.append(f"MACD金叉(+{W['macd']})")
    elif macd < macd_signal: # 死叉
        if in_supply or trend_direction == 'short':
            macd_score = W['macd']
            reason.append(f"MACD死叉(+{W['macd']})")
            
    # 6. 震荡得分 (RSI) - 仅作为过滤
    rsi_score = 0
    if 40 <= rsi <= 60:
        rsi_score = W['rsi'] * 0.5 # 中性区间给一半分
    elif rsi < 40: # 超卖
        if in_demand or trend_direction == 'long':
            rsi_score = W['rsi']
            reason.append(f"RSI超卖回升(+{W['rsi']})")
    elif rsi > 60: # 超买
        if in_supply or trend_direction == 'short':
            rsi_score = W['rsi']
            reason.append(f"RSI超买回调(+{W['rsi']})")

    # --- 汇总得分 ---
    
    # 计算多头总分
    long_total_score = 0
    if in_demand or trend_direction == 'long':
        long_total_score = (trend_score if trend_direction == 'long' else 0) + \
                           (zone_score if in_demand else 0) + \
                           (flow_score if delta_1m > 0 else 0) + \
                           (book_score if imbalance > 0 else 0) + \
                           (macd_score if macd > macd_signal else 0) + \
                           (rsi_score if rsi < 60 else 0)

    # 计算空头总分
    short_total_score = 0
    if in_supply or trend_direction == 'short':
        short_total_score = (trend_score if trend_direction == 'short' else 0) + \
                            (zone_score if in_supply else 0) + \
                            (flow_score if delta_1m < 0 else 0) + \
                            (book_score if imbalance < 0 else 0) + \
                            (macd_score if macd < macd_signal else 0) + \
                            (rsi_score if rsi > 40 else 0)

    # 阈值判定
    threshold = TRADE_CONFIG['confidence_threshold']
    
    signal = 'hold'
    score = 0
    
    if long_total_score >= threshold and long_total_score > short_total_score:
        signal = 'buy'
        score = int(long_total_score)
    elif short_total_score >= threshold and short_total_score > long_total_score:
        signal = 'sell'
        score = int(short_total_score)
    else:
        signal = 'hold'
        score = int(max(long_total_score, short_total_score))
        
    # 如果分数很高但方向矛盾，保持hold
    if in_demand and in_supply: # 极小概率
        signal = 'hold'
        score = 0
        reason.append("同时处于供需区(矛盾)")

    # --- 噪音过滤 & 宏观拦截 ---
    state = noise_state
    
    # 根据市场状态动态调整信号逻辑
    if state == 'CHAOTIC':
        # 极度混乱，直接拦截
        if score > 0:
            score = 0
            signal = 'hold'
            reason.append(f"⛔混乱行情拦截")
            
    elif state == 'RANGING':
        # 震荡市: RSI 和 供需区 最有效，趋势指标失效
        if trend_score > 0:
            score -= int(trend_score * 0.8) # 削弱趋势分
            reason.append(f"📉震荡市削弱趋势分")
            
        if rsi_score > 0:
            score += 10 # 奖励 RSI
            reason.append(f"📈震荡市RSI加权")
            
    elif state == 'TRENDING':
        # 趋势市: 趋势指标 最有效
        if rsi_score > 0: 
            score -= rsi_score # 去掉 RSI 得分 (防止逆势摸顶/抄底)
            reason.append(f"📉强趋势忽略RSI反转")
            
        if trend_score > 0:
            score += 10 # 奖励顺势
            reason.append(f"📈强趋势顺势加权")

    # --- 最终宏观方向拦截 (Final Gatekeeper) ---
    if signal == 'buy':
        if allowed_direction == 'SHORT_ONLY':
            signal = 'hold'
            reason.append(f"⛔宏观趋势拦截看多({regime_msg})")
    elif signal == 'sell':
        if allowed_direction == 'LONG_ONLY':
            signal = 'hold'
            reason.append(f"⛔宏观趋势拦截看空({regime_msg})")

    return signal, score, ", ".join(reason)

# 实盘/Testnet 状态追踪器 (用于记录最高/最低价以实现追踪止盈)
REAL_POS_TRACKER = {
    'highest_price': 0,
    'lowest_price': 0,
    'trailing_active': False
}

def check_risk_management(current_price, timestamp):
    """检查持仓风险 (动态追踪止盈 + 固定止损)"""
    
    # 1. 获取持仓信息
    if RUN_MODE == 'LOCAL_SIMULATION':
        pos = virtual_account.position
    else:
        # 实盘/Testnet: 从交易所获取 + 本地追踪最高/最低价
        exch_pos = get_exchange_position()
        if not exch_pos:
            # 如果没持仓，重置追踪器
            REAL_POS_TRACKER['highest_price'] = 0
            REAL_POS_TRACKER['lowest_price'] = 0
            REAL_POS_TRACKER['trailing_active'] = False
            return False
            
        # 构造兼容的 pos 对象
        pos = exch_pos.copy()
        
        # 初始化/更新追踪器
        if pos['side'] == 'long':
            if REAL_POS_TRACKER['highest_price'] == 0: REAL_POS_TRACKER['highest_price'] = pos['entry_price']
            if current_price > REAL_POS_TRACKER['highest_price']: REAL_POS_TRACKER['highest_price'] = current_price
            pos['highest_price'] = REAL_POS_TRACKER['highest_price']
        else:
            if REAL_POS_TRACKER['lowest_price'] == 0: REAL_POS_TRACKER['lowest_price'] = pos['entry_price']
            if current_price < REAL_POS_TRACKER['lowest_price']: REAL_POS_TRACKER['lowest_price'] = current_price
            pos['lowest_price'] = REAL_POS_TRACKER['lowest_price']
            
        pos['trailing_active'] = REAL_POS_TRACKER['trailing_active']

    if not pos: return False
    
    entry = pos['entry_price']
    side = pos['side']
    
    # 2. 更新最高/最低价 (本地模拟盘已经在 VirtualAccount 中更新，但为了统一逻辑再检查一遍也无妨)
    if RUN_MODE == 'LOCAL_SIMULATION':
        if side == 'long':
            if current_price > pos['highest_price']: pos['highest_price'] = current_price
            pnl_pct = (current_price - entry) / entry
        else:
            if current_price < pos['lowest_price']: pos['lowest_price'] = current_price
            pnl_pct = (entry - current_price) / entry
    else:
        # 实盘 PnL 计算
        if side == 'long':
            pnl_pct = (current_price - entry) / entry
        else:
            pnl_pct = (entry - current_price) / entry

    # 3. 检查固定止损
    if pnl_pct <= -TRADE_CONFIG['stop_loss_pct']:
        reason = "固定止损触发"
        if RUN_MODE == 'LOCAL_SIMULATION':
            virtual_account.close_position(current_price, reason, timestamp)
        else:
            close_exchange_position(pos)
        return True

    # 4. 动态追踪止盈逻辑
    # 激活条件: 盈利超过 trailing_activation
    if not pos['trailing_active']:
        if pnl_pct >= TRADE_CONFIG['trailing_activation']:
            pos['trailing_active'] = True
            if RUN_MODE != 'LOCAL_SIMULATION': REAL_POS_TRACKER['trailing_active'] = True
            print(f"🎯 [追踪激活] 当前盈利 {pnl_pct*100:.2f}% >= {TRADE_CONFIG['trailing_activation']*100}%")
    
    # 执行追踪: 如果已激活
    if pos['trailing_active']:
        callback_rate = TRADE_CONFIG['trailing_callback']
        
        if side == 'long':
            # 触发价 = 最高价 * (1 - 回撤比例)
            trigger_price = pos['highest_price'] * (1 - callback_rate)
            if current_price <= trigger_price:
                reason = f"追踪止盈触发 (最高:{pos['highest_price']:.1f}, 回撤:{callback_rate*100}%)"
                if RUN_MODE == 'LOCAL_SIMULATION':
                    virtual_account.close_position(current_price, reason, timestamp)
                else:
                    close_exchange_position(pos)
                return True
        else: # short
            # 触发价 = 最低价 * (1 + 回撤比例)
            trigger_price = pos['lowest_price'] * (1 + callback_rate)
            if current_price >= trigger_price:
                reason = f"追踪止盈触发 (最低:{pos['lowest_price']:.1f}, 回撤:{callback_rate*100}%)"
                if RUN_MODE == 'LOCAL_SIMULATION':
                    virtual_account.close_position(current_price, reason, timestamp)
                else:
                    close_exchange_position(pos)
                return True
        
    return False

# ==========================================
# 6. 主循环
# ==========================================

def run_strategy_loop():
    print("🚀 启动策略引擎...")
    if RUN_MODE == 'LOCAL_SIMULATION':
        print("🧪 当前模式: 本地模拟盘 (Local Simulation)")
        print(f"💰 初始模拟资金: {virtual_account.balance} U")
    elif RUN_MODE == 'OKX_TESTNET':
        print("🧪 当前模式: OKX 模拟盘 (Testnet/Sandbox)")
        print("⚠️ 请确保 .env 中配置了 Testnet API Key")
    else:
        print("⚠️⚠️⚠️ 当前模式: 实盘交易 (Real Trading) ⚠️⚠️⚠️")
        if SAFE_MODE:
             print("🛡️ 安全模式已开启: 真实交易将被拦截")
        print("请确保您已充分了解风险！")

    # 初始化订单流管理器
    print(f"🌊 初始化订单流管理器 (WebSocket: {USE_WEBSOCKET})...")
    is_sandbox = (RUN_MODE == 'OKX_TESTNET')
    of_manager = OrderFlowManager(
        exchange, 
        TRADE_CONFIG['symbol'], 
        use_ws=USE_WEBSOCKET, 
        is_sandbox=is_sandbox,
        proxy_host='127.0.0.1' if USE_PROXY else None,
        proxy_port=7890 if USE_PROXY else None
    )
    
    # 等待 WebSocket 数据预热
    if USE_WEBSOCKET:
        print("⏳ 等待 WebSocket 数据预热 (5秒)...")
        time.sleep(5)
    
    log_and_notify(f"🤖 策略已启动\n交易对: {TRADE_CONFIG['symbol']}\n模式: {RUN_MODE}\n数据源: {'WebSocket' if USE_WEBSOCKET else 'REST API'}")

    while True:
        try:
            timestamp = datetime.now().strftime('%H:%M:%S')
            
            # 1. 获取数据
            price_data = get_btc_ohlcv_enhanced()
            trend_data = get_trend_data() # 获取大周期趋势
            
            if not price_data:
                time.sleep(10)
                continue
                
            current_price = price_data['price']
            
            # 更新订单流数据
            of_metrics = of_manager.update_metrics()
            
            # 2. 打印状态 (每分钟一次，或者有信号时)
            rsi = price_data['technical']['rsi']
            delta = of_metrics.get('delta_1m', 0)
            trend_str = f"{trend_data['trend'].upper()} (EMA:{trend_data['ema']:.1f})"
            
            # 计算噪音 (用于显示)
            noise_res = noise_filter.analyze(price_data['df'])
            noise_state = noise_res['state']
            noise_icon = {
                'TRENDING': '🚀', 
                'RANGING': '〰️', 
                'CHAOTIC': '⚠️', 
                'NEUTRAL': '✅'
            }.get(noise_state, '❓')
            
            ci_val = noise_res['features']['choppiness_index']
            
            print(f"[{timestamp}] 价格:{current_price:.1f} | 趋势:{trend_str} | Delta:{delta:.2f} | {noise_icon}{noise_state}(CI:{ci_val:.1f})")
            
            # 显示当前状态下的可信指标
            valid_indicators = {
                'TRENDING': '趋势(Trend), MACD, 资金流(Delta)',
                'RANGING': '震荡(RSI), 供需区(Zones), 盘口(Imbalance)',
                'CHAOTIC': '无 (市场噪音过大)',
                'NEUTRAL': '综合参考所有指标'
            }.get(noise_state, '综合参考')
            print(f"   ℹ️  当前可信信号源: {valid_indicators}")

            # 3. 风险管理 (检查现有持仓)
            if check_risk_management(current_price, timestamp):
                # 如果触发了止盈止损，本轮不再开仓
                pass
            
            # 4. 信号分析 (如果没持仓)
            else:
                # 检查是否有持仓
                has_position = False
                if RUN_MODE == 'LOCAL_SIMULATION':
                    has_position = (virtual_account.position is not None)
                else:
                    has_position = (get_exchange_position() is not None)

                if has_position:
                    signal = 'hold'
                    reason = []
                else:
                    signal, score, reason = analyze_market(price_data, of_metrics, trend_data, noise_state)
                
                # 打印分析结果 (可选)
                if score > 0:
                    print(f"   📊 信号分析: {signal.upper()} | 得分: {score}/{TRADE_CONFIG['confidence_threshold']} | 理由: {reason}")

                if signal == 'buy':
                    log_and_notify(f"🟢 [买入信号] 得分:{score} | {reason} @ {current_price:.1f}")
                    if RUN_MODE == 'LOCAL_SIMULATION':
                        virtual_account.open_position('long', current_price, TRADE_CONFIG['position_size_usdt'], timestamp)
                    else:
                        execute_exchange_order('long', current_price, TRADE_CONFIG['position_size_usdt'])
                
                elif signal == 'sell':
                    log_and_notify(f"🔴 [卖出信号] 得分:{score} | {reason} @ {current_price:.1f}")
                    if RUN_MODE == 'LOCAL_SIMULATION':
                        virtual_account.open_position('short', current_price, TRADE_CONFIG['position_size_usdt'], timestamp)
                    else:
                        execute_exchange_order('short', current_price, TRADE_CONFIG['position_size_usdt'])

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
