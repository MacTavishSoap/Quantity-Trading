import os
import time
import schedule
from openai import OpenAI
import ccxt
import pandas as pd
import re
from dotenv import load_dotenv
import json
import requests
from datetime import datetime, timedelta
import asyncio
from telegram import Bot
from telegram.error import TelegramError

load_dotenv()

# 模型配置
MODEL_NAME = os.getenv('AI_MODEL_NAME', 'qwen3-max')  # 默认使用qwen3-max

# Telegram配置 - Token从系统环境变量读取，更安全
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN') or os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
TELEGRAM_ENABLED = os.getenv('TELEGRAM_ENABLED', 'false').lower() == 'true'

# 初始化Telegram Bot
telegram_bot = None
if TELEGRAM_ENABLED and TELEGRAM_BOT_TOKEN:
    try:
        telegram_bot = Bot(token=TELEGRAM_BOT_TOKEN)
        print("✅ Telegram Bot 初始化成功")
    except Exception as e:
        print(f"❌ Telegram Bot 初始化失败: {e}")
        TELEGRAM_ENABLED = False

# 初始化阿里云百炼客户端
bailian_client = OpenAI(
    api_key=os.getenv('DASHSCOPE_API_KEY'),
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
)

# 初始化OKX交易所
exchange = ccxt.okx({
    'options': {
        'defaultType': 'swap',  # OKX使用swap表示永续合约
    },
    'apiKey': os.getenv('OKX_API_KEY'),
    'secret': os.getenv('OKX_SECRET'),
    'password': os.getenv('OKX_PASSWORD'),  # OKX需要交易密码
})

# 交易参数配置 - 结合两个版本的优点
TRADE_CONFIG = {
    'symbol': 'BTC/USDT:USDT',  # OKX的合约符号格式
    'leverage': 10,  # 杠杆倍数,只影响保证金不影响下单价值
    'timeframe': '15m',  # 使用15分钟K线
    'test_mode': False,  # 测试模式
    'data_points': 96,  # 24小时数据（96根15分钟K线）
    'analysis_periods': {
        'short_term': 20,  # 短期均线
        'medium_term': 50,  # 中期均线
        'long_term': 96  # 长期趋势
    },
    # 新增智能仓位参数
    'position_management': {
        'enable_intelligent_position': True,  # 🆕 新增：是否启用智能仓位管理
        'base_usdt_amount': 100,  # USDT投入下单基数
        'high_confidence_multiplier': 1.5,
        'medium_confidence_multiplier': 1.0,
        'low_confidence_multiplier': 0.5,
        'max_position_ratio': 0.8,  # 单次最大仓位比例（80%的余额）
        'trend_strength_multiplier': 1.2
    }
}


def setup_exchange():
    """设置交易所参数 - 强制全仓模式"""
    try:

        # 首先获取合约规格信息
        print("🔍 获取BTC合约规格...")
        markets = exchange.load_markets()
        btc_market = markets[TRADE_CONFIG['symbol']]

        # 获取合约乘数
        contract_size = float(btc_market['contractSize'])
        print(f"✅ 合约规格: 1张 = {contract_size} BTC")

        # 存储合约规格到全局配置
        TRADE_CONFIG['contract_size'] = contract_size
        TRADE_CONFIG['min_amount'] = btc_market['limits']['amount']['min']

        print(f"📏 最小交易量: {TRADE_CONFIG['min_amount']} 张")

        # 先检查现有持仓
        print("🔍 检查现有持仓模式...")
        positions = exchange.fetch_positions([TRADE_CONFIG['symbol']])

        has_isolated_position = False
        isolated_position_info = None

        for pos in positions:
            if pos['symbol'] == TRADE_CONFIG['symbol']:
                contracts = float(pos.get('contracts', 0))
                mode = pos.get('mgnMode')

                if contracts > 0 and mode == 'isolated':
                    has_isolated_position = True
                    isolated_position_info = {
                        'side': pos.get('side'),
                        'size': contracts,
                        'entry_price': pos.get('entryPrice'),
                        'mode': mode
                    }
                    break

        # 2. 如果有逐仓持仓，提示并退出
        if has_isolated_position:
            print("❌ 检测到逐仓持仓，程序无法继续运行！")
            print(f"📊 逐仓持仓详情:")
            print(f"   - 方向: {isolated_position_info['side']}")
            print(f"   - 数量: {isolated_position_info['size']}")
            print(f"   - 入场价: {isolated_position_info['entry_price']}")
            print(f"   - 模式: {isolated_position_info['mode']}")
            print("\n🚨 解决方案:")
            print("1. 手动平掉所有逐仓持仓")
            print("2. 或者将逐仓持仓转为全仓模式")
            print("3. 然后重新启动程序")
            return False

        # 3. 设置单向持仓模式
        print("🔄 设置单向持仓模式...")
        try:
            exchange.set_position_mode(False, TRADE_CONFIG['symbol'])  # False表示单向持仓
            print("✅ 已设置单向持仓模式")
        except Exception as e:
            print(f"⚠️ 设置单向持仓模式失败 (可能已设置): {e}")

        # 4. 设置全仓模式和杠杆
        print("⚙️ 设置全仓模式和杠杆...")
        exchange.set_leverage(
            TRADE_CONFIG['leverage'],
            TRADE_CONFIG['symbol'],
            {'mgnMode': 'cross'}  # 强制全仓模式
        )
        print(f"✅ 已设置全仓模式，杠杆倍数: {TRADE_CONFIG['leverage']}x")

        # 5. 验证设置
        print("🔍 验证账户设置...")
        balance = exchange.fetch_balance()
        
        # 安全获取USDT余额
        usdt_balance = 0.0
        if 'USDT' in balance and 'free' in balance['USDT']:
            usdt_balance = float(balance['USDT']['free'])
        elif 'USDT' in balance and 'total' in balance['USDT']:
            usdt_balance = float(balance['USDT']['total'])
        else:
            # 打印可用的币种信息以便调试
            available_currencies = list(balance.keys())
            print(f"⚠️ 未找到USDT余额，可用币种: {available_currencies}")
            
            # 尝试查找其他可能的USDT表示方式
            for currency in available_currencies:
                if 'USDT' in currency.upper():
                    if 'free' in balance[currency]:
                        usdt_balance = float(balance[currency]['free'])
                        print(f"💰 找到{currency}余额: {usdt_balance:.2f}")
                        break
        
        print(f"💰 当前USDT余额: {usdt_balance:.2f}")
        
        # 检查余额是否足够交易
        min_balance_required = TRADE_CONFIG['position_management']['base_usdt_amount']
        if usdt_balance < min_balance_required:
            print(f"⚠️ 警告: USDT余额({usdt_balance:.2f})低于最小交易金额({min_balance_required})")
            print("💡 建议: 请充值USDT到账户或调整base_usdt_amount配置")
        else:
            print(f"✅ 余额充足，可进行交易")

        # 获取当前持仓状态
        current_pos = get_current_position()
        if current_pos:
            print(f"📦 当前持仓: {current_pos['side']}仓 {current_pos['size']}张")
        else:
            print("📦 当前无持仓")

        print("🎯 程序配置完成：全仓模式 + 单向持仓")
        return True

    except Exception as e:
        print(f"❌ 交易所设置失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_bailian_api():
    """测试阿里云百炼API是否可用"""
    try:
        print("🔍 检测大模型接口可用性...")
        
        # 发送一个简单的测试请求
        test_response = bailian_client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": "你是一个测试助手。"},
                {"role": "user", "content": "请回复'API测试成功'"}
            ],
            stream=False,
            temperature=0.1,
            max_tokens=50
        )
        
        response_content = test_response.choices[0].message.content
        print(f"✅ 大模型API测试成功: {response_content}")
        return True
        
    except Exception as e:
        print(f"❌ 大模型API测试失败: {e}")
        print("💡 请检查:")
        print("   1. DASHSCOPE_API_KEY是否正确配置")
        print("   2. 网络连接是否正常")
        print("   3. API密钥是否有效且有足够余额")
        return False


# Telegram消息发送功能
def send_telegram_message(message, parse_mode='HTML'):
    """发送Telegram消息"""
    if not TELEGRAM_ENABLED or not telegram_bot or not TELEGRAM_CHAT_ID:
        return False
    
    try:
        # 使用同步方式发送消息
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        async def send_async():
            await telegram_bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=message,
                parse_mode=parse_mode
            )
        
        loop.run_until_complete(send_async())
        loop.close()
        return True
        
    except Exception as e:
        print(f"❌ Telegram消息发送失败: {e}")
        return False


def format_trading_signal_message(signal_data, price_data, position_size):
    """格式化交易信号消息"""
    signal_emoji = {
        'BUY': '🟢',
        'SELL': '🔴',
        'HOLD': '🟡'
    }
    
    confidence_emoji = {
        'HIGH': '🔥',
        'MEDIUM': '⚡',
        'LOW': '💡'
    }
    
    message = f"""
🤖 <b>量化交易信号</b>

{signal_emoji.get(signal_data['signal'], '❓')} <b>信号:</b> {signal_data['signal']}
{confidence_emoji.get(signal_data['confidence'], '❓')} <b>信心:</b> {signal_data['confidence']}
💰 <b>仓位:</b> {position_size:.2f} 张
💵 <b>价格:</b> ${price_data['price']:,.2f}

📊 <b>技术指标:</b>
• RSI: {price_data.get('rsi', 'N/A')}
• 趋势: {price_data.get('trend', 'N/A')}

⏰ <b>时间:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
    return message


def format_balance_message(balance_info):
    """格式化余额信息消息"""
    message = f"""
💳 <b>账户余额更新</b>

💰 <b>USDT余额:</b> {balance_info.get('usdt', 0):.2f}
📈 <b>持仓价值:</b> {balance_info.get('position_value', 0):.2f}
📊 <b>总资产:</b> {balance_info.get('total', 0):.2f}

⏰ <b>时间:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
    return message


def format_error_message(error_type, error_msg):
    """格式化错误消息"""
    return f"""
❌ <b>交易错误</b>

🚨 <b>错误类型:</b> {error_type}
📝 <b>错误详情:</b> {error_msg}

⏰ <b>时间:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""

def broadcast_console_info(info_type, **kwargs):
    """同步控制台信息到Telegram播报"""
    if not TELEGRAM_ENABLED:
        return
    
    try:
        if info_type == "trading_start":
            message = f"""
📊 <b>交易分析开始</b>

⏰ <b>执行时间:</b> {kwargs.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}
💰 <b>当前价格:</b> ${kwargs.get('price', 0):,.2f}
📈 <b>价格变化:</b> {kwargs.get('price_change', 0):+.2f}%
⏱️ <b>数据周期:</b> {kwargs.get('timeframe', 'N/A')}
"""
            
        elif info_type == "signal_generated":
            fallback_note = "\n⚠️ 使用备用交易信号" if kwargs.get('is_fallback', False) else ""
            message = f"""
🎯 <b>交易信号生成</b>

📊 <b>信号:</b> {kwargs.get('signal', 'N/A')}
🎯 <b>置信度:</b> {kwargs.get('confidence', 0)}%
💡 <b>分析:</b> {kwargs.get('reasoning', 'N/A')[:100]}...{fallback_note}
"""
            
        elif info_type == "position_calculation":
            message = f"""
🧮 <b>仓位计算详情</b>

💰 <b>基础金额:</b> {kwargs.get('base_amount', 0)} USDT
📊 <b>置信度倍数:</b> {kwargs.get('confidence_multiplier', 0):.1f}x
📈 <b>趋势强度倍数:</b> {kwargs.get('trend_multiplier', 0):.1f}x
⚡ <b>杠杆:</b> {kwargs.get('leverage', 0)}x
💎 <b>名义价值:</b> {kwargs.get('nominal_value', 0):.2f} USDT
🎯 <b>最终仓位:</b> {kwargs.get('position_size', 0):.4f} 张
"""
            
        elif info_type == "margin_check":
            message = f"""
🔍 <b>保证金检查</b>

💵 <b>可用余额:</b> {kwargs.get('available_balance', 0):.2f} USDT
💰 <b>所需保证金:</b> {kwargs.get('required_margin', 0):.2f} USDT
✅ <b>检查结果:</b> {kwargs.get('check_result', 'N/A')}
"""
            if kwargs.get('adjusted_size'):
                message += f"\n🔧 <b>调整后仓位:</b> {kwargs.get('adjusted_size', 0):.4f} 张"
                
        else:
            return
            
        send_telegram_message(message)
        
    except Exception as e:
        print(f"⚠️ 控制台信息播报失败: {e}")
    return message


# 全局变量
price_history = []
signal_history = []
position = None


def calculate_intelligent_position(signal_data, price_data, current_position):
    """计算智能仓位大小 - 修复版"""
    config = TRADE_CONFIG['position_management']

    # 🆕 新增：如果禁用智能仓位，使用固定仓位
    if not config.get('enable_intelligent_position', True):
        fixed_contracts = 0.1  # 固定仓位大小，可以根据需要调整
        print(f"🔧 智能仓位已禁用，使用固定仓位: {fixed_contracts} 张")
        return fixed_contracts

    try:
        # 获取账户余额
        balance = exchange.fetch_balance()
        usdt_balance = balance['USDT']['free']

        # 基础USDT投入
        base_usdt = config['base_usdt_amount']
        print(f"💰 可用USDT余额: {usdt_balance:.2f}, 下单基数{base_usdt}")

        # 根据信心程度调整 - 修复这里
        confidence_multiplier = {
            'HIGH': config['high_confidence_multiplier'],
            'MEDIUM': config['medium_confidence_multiplier'],
            'LOW': config['low_confidence_multiplier']
        }.get(signal_data['confidence'], 1.0)  # 添加默认值

        # 根据趋势强度调整
        trend = price_data['trend_analysis'].get('overall', '震荡整理')
        if trend in ['强势上涨', '强势下跌']:
            trend_multiplier = config['trend_strength_multiplier']
        else:
            trend_multiplier = 1.0

        # 根据RSI状态调整（超买超卖区域减仓）
        rsi = price_data['technical_data'].get('rsi', 50)
        if rsi > 75 or rsi < 25:
            rsi_multiplier = 0.7
        else:
            rsi_multiplier = 1.0

        # 计算建议投入USDT金额
        suggested_usdt = base_usdt * confidence_multiplier * trend_multiplier * rsi_multiplier

        # 风险管理：不超过总资金的指定比例 - 删除重复定义
        max_usdt = usdt_balance * config['max_position_ratio']
        final_usdt = min(suggested_usdt, max_usdt)

        # 正确的合约张数计算！
        # 公式：合约张数 = (投入USDT * 杠杆) / (当前价格 * 合约乘数)
        # 因为投入USDT是保证金，需要乘以杠杆得到名义价值，再除以单张合约价值
        contract_size = (final_usdt * TRADE_CONFIG['leverage']) / (price_data['price'] * TRADE_CONFIG['contract_size'])

        print(f"📊 仓位计算详情:")
        print(f"   - 基础USDT: {base_usdt}")
        print(f"   - 信心倍数: {confidence_multiplier}")
        print(f"   - 趋势倍数: {trend_multiplier}")
        print(f"   - RSI倍数: {rsi_multiplier}")
        print(f"   - 建议USDT: {suggested_usdt:.2f}")
        print(f"   - 最终USDT(保证金): {final_usdt:.2f}")
        print(f"   - 杠杆倍数: {TRADE_CONFIG['leverage']}x")
        print(f"   - 名义价值: {final_usdt * TRADE_CONFIG['leverage']:.2f} USDT")
        print(f"   - 合约乘数: {TRADE_CONFIG['contract_size']}")
        print(f"   - 计算合约: {contract_size:.4f} 张")
        
        # 播报仓位计算详情
        broadcast_console_info("position_calculation",
                              base_amount=base_usdt,
                              confidence_multiplier=confidence_multiplier,
                              trend_multiplier=trend_multiplier,
                              leverage=TRADE_CONFIG['leverage'],
                              nominal_value=final_usdt * TRADE_CONFIG['leverage'],
                              position_size=contract_size)

        # 精度处理：OKX BTC合约最小交易单位为0.01张
        contract_size = round(contract_size, 2)  # 保留2位小数

        # 确保最小交易量
        min_contracts = TRADE_CONFIG.get('min_amount', 0.01)
        if contract_size < min_contracts:
            contract_size = min_contracts
            print(f"⚠️ 仓位小于最小值，调整为: {contract_size} 张")

        print(f"🎯 最终仓位: {final_usdt:.2f} USDT → {contract_size:.2f} 张合约")
        return contract_size

    except Exception as e:
        print(f"❌ 仓位计算失败，使用基础仓位: {e}")
        # 紧急备用计算
        base_usdt = config['base_usdt_amount']
        contract_size = (base_usdt * TRADE_CONFIG['leverage']) / (
                    price_data['price'] * TRADE_CONFIG.get('contract_size', 0.01))
        return round(max(contract_size, TRADE_CONFIG.get('min_amount', 0.01)), 2)


def calculate_technical_indicators(df):
    """计算技术指标 - 来自第一个策略"""
    try:
        # 移动平均线
        df['sma_5'] = df['close'].rolling(window=5, min_periods=1).mean()
        df['sma_20'] = df['close'].rolling(window=20, min_periods=1).mean()
        df['sma_50'] = df['close'].rolling(window=50, min_periods=1).mean()

        # 指数移动平均线
        df['ema_12'] = df['close'].ewm(span=12).mean()
        df['ema_26'] = df['close'].ewm(span=26).mean()
        df['macd'] = df['ema_12'] - df['ema_26']
        df['macd_signal'] = df['macd'].ewm(span=9).mean()
        df['macd_histogram'] = df['macd'] - df['macd_signal']

        # 相对强弱指数 (RSI)
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))

        # 布林带
        df['bb_middle'] = df['close'].rolling(20).mean()
        bb_std = df['close'].rolling(20).std()
        df['bb_upper'] = df['bb_middle'] + (bb_std * 2)
        df['bb_lower'] = df['bb_middle'] - (bb_std * 2)
        df['bb_position'] = (df['close'] - df['bb_lower']) / (df['bb_upper'] - df['bb_lower'])

        # 成交量均线
        df['volume_ma'] = df['volume'].rolling(20).mean()
        df['volume_ratio'] = df['volume'] / df['volume_ma']

        # 支撑阻力位
        df['resistance'] = df['high'].rolling(20).max()
        df['support'] = df['low'].rolling(20).min()

        # 填充NaN值
        df = df.bfill().ffill()

        return df
    except Exception as e:
        print(f"技术指标计算失败: {e}")
        return df


def get_support_resistance_levels(df, lookback=20):
    """计算支撑阻力位"""
    try:
        recent_high = df['high'].tail(lookback).max()
        recent_low = df['low'].tail(lookback).min()
        current_price = df['close'].iloc[-1]

        resistance_level = recent_high
        support_level = recent_low

        # 动态支撑阻力（基于布林带）
        bb_upper = df['bb_upper'].iloc[-1]
        bb_lower = df['bb_lower'].iloc[-1]

        return {
            'static_resistance': resistance_level,
            'static_support': support_level,
            'dynamic_resistance': bb_upper,
            'dynamic_support': bb_lower,
            'price_vs_resistance': ((resistance_level - current_price) / current_price) * 100,
            'price_vs_support': ((current_price - support_level) / support_level) * 100
        }
    except Exception as e:
        print(f"支撑阻力计算失败: {e}")
        return {}


def get_sentiment_indicators():
    """获取情绪指标 - 简洁版本"""
    try:
        API_URL = "https://service.cryptoracle.network/openapi/v2/endpoint"
        API_KEY = "7ad48a56-8730-4238-a714-eebc30834e3e"

        # 获取最近4小时数据
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=4)

        request_body = {
            "apiKey": API_KEY,
            "endpoints": ["CO-A-02-01", "CO-A-02-02"],  # 只保留核心指标
            "startTime": start_time.strftime("%Y-%m-%d %H:%M:%S"),
            "endTime": end_time.strftime("%Y-%m-%d %H:%M:%S"),
            "timeType": "15m",
            "token": ["BTC"]
        }

        headers = {"Content-Type": "application/json", "X-API-KEY": API_KEY}
        response = requests.post(API_URL, json=request_body, headers=headers)

        if response.status_code == 200:
            data = response.json()
            if data.get("code") == 200 and data.get("data"):
                time_periods = data["data"][0]["timePeriods"]

                # 查找第一个有有效数据的时间段
                for period in time_periods:
                    period_data = period.get("data", [])

                    sentiment = {}
                    valid_data_found = False

                    for item in period_data:
                        endpoint = item.get("endpoint")
                        value = item.get("value", "").strip()

                        if value:  # 只处理非空值
                            try:
                                if endpoint in ["CO-A-02-01", "CO-A-02-02"]:
                                    sentiment[endpoint] = float(value)
                                    valid_data_found = True
                            except (ValueError, TypeError):
                                continue

                    # 如果找到有效数据
                    if valid_data_found and "CO-A-02-01" in sentiment and "CO-A-02-02" in sentiment:
                        positive = sentiment['CO-A-02-01']
                        negative = sentiment['CO-A-02-02']
                        net_sentiment = positive - negative

                        # 正确的时间延迟计算
                        data_delay = int((datetime.now() - datetime.strptime(
                            period['startTime'], '%Y-%m-%d %H:%M:%S')).total_seconds() // 60)

                        print(f"✅ 使用情绪数据时间: {period['startTime']} (延迟: {data_delay}分钟)")

                        return {
                            'positive_ratio': positive,
                            'negative_ratio': negative,
                            'net_sentiment': net_sentiment,
                            'data_time': period['startTime'],
                            'data_delay_minutes': data_delay
                        }

                print("❌ 所有时间段数据都为空")
                return None

        return None
    except Exception as e:
        print(f"情绪指标获取失败: {e}")
        return None


def get_market_trend(df):
    """判断市场趋势"""
    try:
        current_price = df['close'].iloc[-1]

        # 多时间框架趋势分析
        trend_short = "上涨" if current_price > df['sma_20'].iloc[-1] else "下跌"
        trend_medium = "上涨" if current_price > df['sma_50'].iloc[-1] else "下跌"

        # MACD趋势
        macd_trend = "bullish" if df['macd'].iloc[-1] > df['macd_signal'].iloc[-1] else "bearish"

        # 综合趋势判断
        if trend_short == "上涨" and trend_medium == "上涨":
            overall_trend = "强势上涨"
        elif trend_short == "下跌" and trend_medium == "下跌":
            overall_trend = "强势下跌"
        else:
            overall_trend = "震荡整理"

        return {
            'short_term': trend_short,
            'medium_term': trend_medium,
            'macd': macd_trend,
            'overall': overall_trend,
            'rsi_level': df['rsi'].iloc[-1]
        }
    except Exception as e:
        print(f"趋势分析失败: {e}")
        return {}


def get_btc_ohlcv_enhanced():
    """增强版：获取BTC K线数据并计算技术指标"""
    try:
        # 获取K线数据
        ohlcv = exchange.fetch_ohlcv(TRADE_CONFIG['symbol'], TRADE_CONFIG['timeframe'],
                                     limit=TRADE_CONFIG['data_points'])

        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')

        # 计算技术指标
        df = calculate_technical_indicators(df)

        current_data = df.iloc[-1]
        previous_data = df.iloc[-2]

        # 获取技术分析数据
        trend_analysis = get_market_trend(df)
        levels_analysis = get_support_resistance_levels(df)

        return {
            'price': current_data['close'],
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'high': current_data['high'],
            'low': current_data['low'],
            'volume': current_data['volume'],
            'timeframe': TRADE_CONFIG['timeframe'],
            'price_change': ((current_data['close'] - previous_data['close']) / previous_data['close']) * 100,
            'kline_data': df[['timestamp', 'open', 'high', 'low', 'close', 'volume']].tail(10).to_dict('records'),
            'technical_data': {
                'sma_5': current_data.get('sma_5', 0),
                'sma_20': current_data.get('sma_20', 0),
                'sma_50': current_data.get('sma_50', 0),
                'rsi': current_data.get('rsi', 0),
                'macd': current_data.get('macd', 0),
                'macd_signal': current_data.get('macd_signal', 0),
                'macd_histogram': current_data.get('macd_histogram', 0),
                'bb_upper': current_data.get('bb_upper', 0),
                'bb_lower': current_data.get('bb_lower', 0),
                'bb_position': current_data.get('bb_position', 0),
                'volume_ratio': current_data.get('volume_ratio', 0)
            },
            'trend_analysis': trend_analysis,
            'levels_analysis': levels_analysis,
            'full_data': df
        }
    except Exception as e:
        print(f"获取增强K线数据失败: {e}")
        return None


def generate_technical_analysis_text(price_data):
    """生成技术分析文本"""
    if 'technical_data' not in price_data:
        return "技术指标数据不可用"

    tech = price_data['technical_data']
    trend = price_data.get('trend_analysis', {})
    levels = price_data.get('levels_analysis', {})

    # 检查数据有效性
    def safe_float(value, default=0):
        return float(value) if value and pd.notna(value) else default

    analysis_text = f"""
    【技术指标分析】
    📈 移动平均线:
    - 5周期: {safe_float(tech['sma_5']):.2f} | 价格相对: {(price_data['price'] - safe_float(tech['sma_5'])) / safe_float(tech['sma_5']) * 100:+.2f}%
    - 20周期: {safe_float(tech['sma_20']):.2f} | 价格相对: {(price_data['price'] - safe_float(tech['sma_20'])) / safe_float(tech['sma_20']) * 100:+.2f}%
    - 50周期: {safe_float(tech['sma_50']):.2f} | 价格相对: {(price_data['price'] - safe_float(tech['sma_50'])) / safe_float(tech['sma_50']) * 100:+.2f}%

    🎯 趋势分析:
    - 短期趋势: {trend.get('short_term', 'N/A')}
    - 中期趋势: {trend.get('medium_term', 'N/A')}
    - 整体趋势: {trend.get('overall', 'N/A')}
    - MACD方向: {trend.get('macd', 'N/A')}

    📊 动量指标:
    - RSI: {safe_float(tech['rsi']):.2f} ({'超买' if safe_float(tech['rsi']) > 70 else '超卖' if safe_float(tech['rsi']) < 30 else '中性'})
    - MACD: {safe_float(tech['macd']):.4f}
    - 信号线: {safe_float(tech['macd_signal']):.4f}

    🎚️ 布林带位置: {safe_float(tech['bb_position']):.2%} ({'上部' if safe_float(tech['bb_position']) > 0.7 else '下部' if safe_float(tech['bb_position']) < 0.3 else '中部'})

    💰 关键水平:
    - 静态阻力: {safe_float(levels.get('static_resistance', 0)):.2f}
    - 静态支撑: {safe_float(levels.get('static_support', 0)):.2f}
    """
    return analysis_text


def get_current_position():
    """获取当前持仓情况 - OKX版本"""
    try:
        positions = exchange.fetch_positions([TRADE_CONFIG['symbol']])

        for pos in positions:
            if pos['symbol'] == TRADE_CONFIG['symbol']:
                contracts = float(pos['contracts']) if pos['contracts'] else 0

                if contracts > 0:
                    return {
                        'side': pos['side'],  # 'long' or 'short'
                        'size': contracts,
                        'entry_price': float(pos['entryPrice']) if pos['entryPrice'] else 0,
                        'unrealized_pnl': float(pos['unrealizedPnl']) if pos['unrealizedPnl'] else 0,
                        'leverage': float(pos['leverage']) if pos['leverage'] else TRADE_CONFIG['leverage'],
                        'symbol': pos['symbol']
                    }

        return None

    except Exception as e:
        print(f"获取持仓失败: {e}")
        import traceback
        traceback.print_exc()
        return None


def safe_json_parse(json_str):
    """安全解析JSON，处理格式不规范的情况"""
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        try:
            # 修复常见的JSON格式问题
            json_str = json_str.replace("'", '"')
            json_str = re.sub(r'(\w+):', r'"\1":', json_str)
            json_str = re.sub(r',\s*}', '}', json_str)
            json_str = re.sub(r',\s*]', ']', json_str)
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            print(f"JSON解析失败，原始内容: {json_str}")
            print(f"错误详情: {e}")
            return None


def create_fallback_signal(price_data):
    """创建备用交易信号"""
    return {
        "signal": "HOLD",
        "reason": "因技术分析暂时不可用，采取保守策略",
        "stop_loss": price_data['price'] * 0.98,  # -2%
        "take_profit": price_data['price'] * 1.02,  # +2%
        "confidence": "LOW",
        "is_fallback": True
    }


def analyze_with_bailian(price_data):
    """使用阿里云百炼分析市场并生成交易信号（增强版）"""

    # 生成技术分析文本
    technical_analysis = generate_technical_analysis_text(price_data)

    # 构建K线数据文本
    kline_text = f"【最近5根{TRADE_CONFIG['timeframe']}K线数据】\n"
    for i, kline in enumerate(price_data['kline_data'][-5:]):
        trend = "阳线" if kline['close'] > kline['open'] else "阴线"
        change = ((kline['close'] - kline['open']) / kline['open']) * 100
        kline_text += f"K线{i + 1}: {trend} 开盘:{kline['open']:.2f} 收盘:{kline['close']:.2f} 涨跌:{change:+.2f}%\n"

    # 添加上次交易信号
    signal_text = ""
    if signal_history:
        last_signal = signal_history[-1]
        signal_text = f"\n【上次交易信号】\n信号: {last_signal.get('signal', 'N/A')}\n信心: {last_signal.get('confidence', 'N/A')}"

    # 获取情绪数据
    sentiment_data = get_sentiment_indicators()
    # 简化情绪文本 多了没用
    if sentiment_data:
        sign = '+' if sentiment_data['net_sentiment'] >= 0 else ''
        sentiment_text = f"【市场情绪】乐观{sentiment_data['positive_ratio']:.1%} 悲观{sentiment_data['negative_ratio']:.1%} 净值{sign}{sentiment_data['net_sentiment']:.3f}"
    else:
        sentiment_text = "【市场情绪】数据暂不可用"

    # 添加当前持仓信息
    current_pos = get_current_position()
    position_text = "无持仓" if not current_pos else f"{current_pos['side']}仓, 数量: {current_pos['size']}, 盈亏: {current_pos['unrealized_pnl']:.2f}USDT"
    pnl_text = f", 持仓盈亏: {current_pos['unrealized_pnl']:.2f} USDT" if current_pos else ""

    prompt = f"""
    你是一个专业的加密货币交易分析师。请基于以下BTC/USDT {TRADE_CONFIG['timeframe']}周期数据进行分析：

    {kline_text}

    {technical_analysis}

    {signal_text}

    {sentiment_text}  # 添加情绪分析

    【当前行情】
    - 当前价格: ${price_data['price']:,.2f}
    - 时间: {price_data['timestamp']}
    - 本K线最高: ${price_data['high']:,.2f}
    - 本K线最低: ${price_data['low']:,.2f}
    - 本K线成交量: {price_data['volume']:.2f} BTC
    - 价格变化: {price_data['price_change']:+.2f}%
    - 当前持仓: {position_text}{pnl_text}

    【防频繁交易重要原则】
    1. **趋势持续性优先**: 不要因单根K线或短期波动改变整体趋势判断
    2. **持仓稳定性**: 除非趋势明确强烈反转，否则保持现有持仓方向
    3. **反转确认**: 需要至少2-3个技术指标同时确认趋势反转才改变信号
    4. **成本意识**: 减少不必要的仓位调整，每次交易都有成本

    【交易指导原则 - 必须遵守】
    1. **技术分析主导** (权重60%)：趋势、支撑阻力、K线形态是主要依据
    2. **市场情绪辅助** (权重30%)：情绪数据用于验证技术信号，不能单独作为交易理由  
    - 情绪与技术同向 → 增强信号信心
    - 情绪与技术背离 → 以技术分析为主，情绪仅作参考
    - 情绪数据延迟 → 降低权重，以实时技术指标为准
    3. **风险管理** (权重10%)：考虑持仓、盈亏状况和止损位置
    4. **趋势跟随**: 明确趋势出现时立即行动，不要过度等待
    5. 因为做的是btc，做多权重可以大一点点
    6. **信号明确性**:
    - 强势上涨趋势 → BUY信号
    - 强势下跌趋势 → SELL信号  
    - 仅在窄幅震荡、无明确方向时 → HOLD信号
    7. **技术指标权重**:
    - 趋势(均线排列) > RSI > MACD > 布林带
    - 价格突破关键支撑/阻力位是重要信号 


    【当前技术状况分析】
    - 整体趋势: {price_data['trend_analysis'].get('overall', 'N/A')}
    - 短期趋势: {price_data['trend_analysis'].get('short_term', 'N/A')} 
    - RSI状态: {price_data['technical_data'].get('rsi', 0):.1f} ({'超买' if price_data['technical_data'].get('rsi', 0) > 70 else '超卖' if price_data['technical_data'].get('rsi', 0) < 30 else '中性'})
    - MACD方向: {price_data['trend_analysis'].get('macd', 'N/A')}

    【智能仓位管理规则 - 必须遵守】

    1. **减少过度保守**：
       - 明确趋势中不要因轻微超买/超卖而过度HOLD
       - RSI在30-70区间属于健康范围，不应作为主要HOLD理由
       - 布林带位置在20%-80%属于正常波动区间

    2. **趋势跟随优先**：
       - 强势上涨趋势 + 任何RSI值 → 积极BUY信号
       - 强势下跌趋势 + 任何RSI值 → 积极SELL信号
       - 震荡整理 + 无明确方向 → HOLD信号

    3. **突破交易信号**：
       - 价格突破关键阻力 + 成交量放大 → 高信心BUY
       - 价格跌破关键支撑 + 成交量放大 → 高信心SELL

    4. **持仓优化逻辑**：
       - 已有持仓且趋势延续 → 保持或BUY/SELL信号
       - 趋势明确反转 → 及时反向信号
       - 不要因为已有持仓而过度HOLD

    【重要】请基于技术分析做出明确判断，避免因过度谨慎而错过趋势行情！

    【分析要求】
    基于以上分析，请给出明确的交易信号

    请用以下JSON格式回复：
    {{
        "signal": "BUY|SELL|HOLD",
        "reason": "简要分析理由(包含趋势判断和技术依据)",
        "stop_loss": 具体价格,
        "take_profit": 具体价格, 
        "confidence": "HIGH|MEDIUM|LOW"
    }}
    """

    try:
        response = bailian_client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system",
                 "content": f"您是一位急需资金来治疗母亲疾病的专业交易员，你需要从加密货币市场获得足够的资金用于治疗你的母亲，专注于{TRADE_CONFIG['timeframe']}周期趋势分析。请结合K线形态和技术指标做出判断，并严格遵循JSON格式要求。"},
                {"role": "user", "content": prompt}
            ],
            stream=False,
            temperature=0.1
        )

        # 安全解析JSON
        result = response.choices[0].message.content
        print(f"Bailian原始回复: {result}")

        # 提取JSON部分
        start_idx = result.find('{')
        end_idx = result.rfind('}') + 1

        if start_idx != -1 and end_idx != 0:
            json_str = result[start_idx:end_idx]
            signal_data = safe_json_parse(json_str)

            if signal_data is None:
                signal_data = create_fallback_signal(price_data)
        else:
            signal_data = create_fallback_signal(price_data)

        # 验证必需字段
        required_fields = ['signal', 'reason', 'stop_loss', 'take_profit', 'confidence']
        if not all(field in signal_data for field in required_fields):
            signal_data = create_fallback_signal(price_data)

        # 保存信号到历史记录
        signal_data['timestamp'] = price_data['timestamp']
        signal_history.append(signal_data)
        if len(signal_history) > 30:
            signal_history.pop(0)

        # 信号统计
        signal_count = len([s for s in signal_history if s.get('signal') == signal_data['signal']])
        total_signals = len(signal_history)
        print(f"信号统计: {signal_data['signal']} (最近{total_signals}次中出现{signal_count}次)")

        # 信号连续性检查
        if len(signal_history) >= 3:
            last_three = [s['signal'] for s in signal_history[-3:]]
            if len(set(last_three)) == 1:
                print(f"⚠️ 注意：连续3次{signal_data['signal']}信号")

        return signal_data

    except Exception as e:
        print(f"DeepSeek分析失败: {e}")
        return create_fallback_signal(price_data)


def execute_intelligent_trade(signal_data, price_data):
    """执行智能交易 - OKX版本（支持同方向加仓减仓）"""
    global position

    current_position = get_current_position()

    # 防止频繁反转的逻辑保持不变
    if current_position and signal_data['signal'] != 'HOLD':
        current_side = current_position['side']  # 'long' 或 'short'

        if signal_data['signal'] == 'BUY':
            new_side = 'long'
        elif signal_data['signal'] == 'SELL':
            new_side = 'short'
        else:
            new_side = None

        # 如果方向相反，需要高信心才执行
        # if new_side != current_side:
        #     if signal_data['confidence'] != 'HIGH':
        #         print(f"🔒 非高信心反转信号，保持现有{current_side}仓")
        #         return

        #     if len(signal_history) >= 2:
        #         last_signals = [s['signal'] for s in signal_history[-2:]]
        #         if signal_data['signal'] in last_signals:
        #             print(f"🔒 近期已出现{signal_data['signal']}信号，避免频繁反转")
        #             return

    # 计算智能仓位
    position_size = calculate_intelligent_position(signal_data, price_data, current_position)

    print(f"交易信号: {signal_data['signal']}")
    print(f"信心程度: {signal_data['confidence']}")
    print(f"智能仓位: {position_size:.2f} 张")
    print(f"理由: {signal_data['reason']}")
    print(f"当前持仓: {current_position}")
    
    # 🆕 发送Telegram交易信号通知
    if TELEGRAM_ENABLED:
        telegram_message = format_trading_signal_message(signal_data, price_data, position_size)
        send_telegram_message(telegram_message)

    # 🆕 保证金预检查
    try:
        balance = exchange.fetch_balance()
        usdt_balance = balance['USDT']['free']
        
        # 计算所需保证金
        required_margin = (position_size * TRADE_CONFIG['contract_size'] * price_data['price']) / TRADE_CONFIG['leverage']
        
        print(f"💳 保证金检查:")
        print(f"   - 可用余额: {usdt_balance:.2f} USDT")
        print(f"   - 所需保证金: {required_margin:.2f} USDT")
        print(f"   - 安全余量: {usdt_balance - required_margin:.2f} USDT")
        
        # 播报保证金检查信息
        if required_margin > usdt_balance * 0.95:  # 保留5%安全余量
            print(f"❌ 保证金不足！调整仓位大小...")
            # 重新计算安全仓位
            safe_margin = usdt_balance * 0.9  # 使用90%的余额
            position_size = (safe_margin * TRADE_CONFIG['leverage']) / (price_data['price'] * TRADE_CONFIG['contract_size'])
            position_size = round(position_size, 2)
            print(f"🔧 调整后仓位: {position_size:.2f} 张")
            
            broadcast_console_info("margin_check",
                                  available_balance=usdt_balance,
                                  required_margin=required_margin,
                                  check_result="保证金不足，已调整仓位",
                                  adjusted_size=position_size)
        else:
            broadcast_console_info("margin_check",
                                  available_balance=usdt_balance,
                                  required_margin=required_margin,
                                  check_result="保证金充足")
            
            if position_size < TRADE_CONFIG.get('min_amount', 0.01):
                print(f"⚠️ 调整后仓位仍小于最小值，跳过交易")
                return
                
    except Exception as e:
        print(f"⚠️ 保证金检查失败: {e}")
        # 继续执行，但使用更保守的仓位
        position_size = min(position_size, 0.01)

    # 风险管理
    if signal_data['confidence'] == 'LOW' and not TRADE_CONFIG['test_mode']:
        print("⚠️ 低信心信号，跳过执行")
        return

    if TRADE_CONFIG['test_mode']:
        print("测试模式 - 仅模拟交易")
        return

    try:
        # 执行交易逻辑 - 支持同方向加仓减仓
        if signal_data['signal'] == 'BUY':
            if current_position and current_position['side'] == 'short':
                # 先检查空头持仓是否真实存在且数量正确
                if current_position['size'] > 0:
                    print(f"平空仓 {current_position['size']:.2f} 张并开多仓 {position_size:.2f} 张...")
                    # 平空仓
                    exchange.create_market_order(
                        TRADE_CONFIG['symbol'],
                        'buy',
                        current_position['size'],
                        params={'reduceOnly': True, 'tag': '60bb4a8d3416BCDE'}
                    )
                    time.sleep(1)
                    # 开多仓
                    exchange.create_market_order(
                        TRADE_CONFIG['symbol'],
                        'buy',
                        position_size,
                        params={'tag': '60bb4a8d3416BCDE'}
                    )
                else:
                    print("⚠️ 检测到空头持仓但数量为0，直接开多仓")
                    exchange.create_market_order(
                        TRADE_CONFIG['symbol'],
                        'buy',
                        position_size,
                        params={'tag': '60bb4a8d3416BCDE'}
                    )

            elif current_position and current_position['side'] == 'long':
                # 同方向，检查是否需要调整仓位
                size_diff = position_size - current_position['size']

                if abs(size_diff) >= 0.01:  # 有可调整的差异
                    if size_diff > 0:
                        # 加仓
                        add_size = round(size_diff, 2)
                        print(
                            f"多仓加仓 {add_size:.2f} 张 (当前:{current_position['size']:.2f} → 目标:{position_size:.2f})")
                        exchange.create_market_order(
                            TRADE_CONFIG['symbol'],
                            'buy',
                            add_size,
                            params={'tag': '60bb4a8d3416BCDE'}
                        )
                    else:
                        # 减仓
                        reduce_size = round(abs(size_diff), 2)
                        print(
                            f"多仓减仓 {reduce_size:.2f} 张 (当前:{current_position['size']:.2f} → 目标:{position_size:.2f})")
                        exchange.create_market_order(
                            TRADE_CONFIG['symbol'],
                            'sell',
                            reduce_size,
                            params={'reduceOnly': True, 'tag': '60bb4a8d3416BCDE'}
                        )
                else:
                    print(
                        f"已有多头持仓，仓位合适保持现状 (当前:{current_position['size']:.2f}, 目标:{position_size:.2f})")
            else:
                # 无持仓时开多仓
                print(f"开多仓 {position_size:.2f} 张...")
                exchange.create_market_order(
                    TRADE_CONFIG['symbol'],
                    'buy',
                    position_size,
                    params={'tag': '60bb4a8d3416BCDE'}
                )

        elif signal_data['signal'] == 'SELL':
            if current_position and current_position['side'] == 'long':
                # 先检查多头持仓是否真实存在且数量正确
                if current_position['size'] > 0:
                    print(f"平多仓 {current_position['size']:.2f} 张并开空仓 {position_size:.2f} 张...")
                    # 平多仓
                    exchange.create_market_order(
                        TRADE_CONFIG['symbol'],
                        'sell',
                        current_position['size'],
                        params={'reduceOnly': True, 'tag': '60bb4a8d3416BCDE'}
                    )
                    time.sleep(1)
                    # 开空仓
                    exchange.create_market_order(
                        TRADE_CONFIG['symbol'],
                        'sell',
                        position_size,
                        params={'tag': '60bb4a8d3416BCDE'}
                    )
                else:
                    print("⚠️ 检测到多头持仓但数量为0，直接开空仓")
                    exchange.create_market_order(
                        TRADE_CONFIG['symbol'],
                        'sell',
                        position_size,
                        params={'tag': '60bb4a8d3416BCDE'}
                    )

            elif current_position and current_position['side'] == 'short':
                # 同方向，检查是否需要调整仓位
                size_diff = position_size - current_position['size']

                if abs(size_diff) >= 0.01:  # 有可调整的差异
                    if size_diff > 0:
                        # 加仓
                        add_size = round(size_diff, 2)
                        print(
                            f"空仓加仓 {add_size:.2f} 张 (当前:{current_position['size']:.2f} → 目标:{position_size:.2f})")
                        exchange.create_market_order(
                            TRADE_CONFIG['symbol'],
                            'sell',
                            add_size,
                            params={'tag': '60bb4a8d3416BCDE'}
                        )
                    else:
                        # 减仓
                        reduce_size = round(abs(size_diff), 2)
                        print(
                            f"空仓减仓 {reduce_size:.2f} 张 (当前:{current_position['size']:.2f} → 目标:{position_size:.2f})")
                        exchange.create_market_order(
                            TRADE_CONFIG['symbol'],
                            'buy',
                            reduce_size,
                            params={'reduceOnly': True, 'tag': '60bb4a8d3416BCDE'}
                        )
                else:
                    print(
                        f"已有空头持仓，仓位合适保持现状 (当前:{current_position['size']:.2f}, 目标:{position_size:.2f})")
            else:
                # 无持仓时开空仓
                print(f"开空仓 {position_size:.2f} 张...")
                exchange.create_market_order(
                    TRADE_CONFIG['symbol'],
                    'sell',
                    position_size,
                    params={'tag': '60bb4a8d3416BCDE'}
                )

        elif signal_data['signal'] == 'HOLD':
            print("建议观望，不执行交易")
            return

        print("智能交易执行成功")
        time.sleep(2)
        position = get_current_position()
        print(f"更新后持仓: {position}")
        
        # 🆕 发送交易成功通知和余额更新
        if TELEGRAM_ENABLED:
            try:
                # 获取最新余额信息
                balance = exchange.fetch_balance()
                balance_info = {
                    'usdt': balance['USDT']['free'],
                    'position_value': position['size'] * price_data['price'] * TRADE_CONFIG['contract_size'] if position else 0,
                    'total': balance['USDT']['free'] + (position['size'] * price_data['price'] * TRADE_CONFIG['contract_size'] if position else 0)
                }
                
                # 发送成功消息
                success_message = f"""
✅ <b>交易执行成功</b>

🎯 <b>执行信号:</b> {signal_data['signal']}
💰 <b>执行仓位:</b> {position_size:.2f} 张
📊 <b>当前持仓:</b> {position['side'] if position else '无'} {position['size'] if position else 0:.2f} 张

⏰ <b>时间:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
                send_telegram_message(success_message)
                
                # 发送余额更新
                balance_message = format_balance_message(balance_info)
                send_telegram_message(balance_message)
                
            except Exception as e:
                print(f"⚠️ Telegram通知发送失败: {e}")

    except Exception as e:
        print(f"交易执行失败: {e}")
        
        # 🆕 发送错误通知
        if TELEGRAM_ENABLED:
            error_message = format_error_message("交易执行失败", str(e))
            send_telegram_message(error_message)

        # 如果是持仓不存在的错误，尝试直接开新仓
        if "don't have any positions" in str(e):
            print("尝试直接开新仓...")
            try:
                if signal_data['signal'] == 'BUY':
                    exchange.create_market_order(
                        TRADE_CONFIG['symbol'],
                        'buy',
                        position_size,
                        params={'tag': '60bb4a8d3416BCDE'}
                    )
                elif signal_data['signal'] == 'SELL':
                    exchange.create_market_order(
                        TRADE_CONFIG['symbol'],
                        'sell',
                        position_size,
                        params={'tag': '60bb4a8d3416BCDE'}
                    )
                print("直接开仓成功")
            except Exception as e2:
                print(f"直接开仓也失败: {e2}")

        import traceback
        traceback.print_exc()


def analyze_with_bailian_with_retry(price_data, max_retries=2):
    """带重试的Bailian分析"""
    for attempt in range(max_retries):
        try:
            signal_data = analyze_with_bailian(price_data)
            if signal_data and not signal_data.get('is_fallback', False):
                return signal_data

            print(f"第{attempt + 1}次尝试失败，进行重试...")
            time.sleep(1)

        except Exception as e:
            print(f"第{attempt + 1}次尝试异常: {e}")
            if attempt == max_retries - 1:
                return create_fallback_signal(price_data)
            time.sleep(1)

    return create_fallback_signal(price_data)


def wait_for_next_period():
    """等待到下一个15分钟整点"""
    now = datetime.now()
    current_minute = now.minute
    current_second = now.second

    # 计算下一个整点时间（00, 15, 30, 45分钟）
    next_period_minute = ((current_minute // 15) + 1) * 15
    if next_period_minute == 60:
        next_period_minute = 0

    # 计算需要等待的总秒数
    if next_period_minute > current_minute:
        minutes_to_wait = next_period_minute - current_minute
    else:
        minutes_to_wait = 60 - current_minute + next_period_minute

    seconds_to_wait = minutes_to_wait * 60 - current_second

    # 显示友好的等待时间
    display_minutes = minutes_to_wait - 1 if current_second > 0 else minutes_to_wait
    display_seconds = 60 - current_second if current_second > 0 else 0

    if display_minutes > 0:
        print(f"🕒 等待 {display_minutes} 分 {display_seconds} 秒到整点...")
    else:
        print(f"🕒 等待 {display_seconds} 秒到整点...")

    return seconds_to_wait


def trading_bot():
    # 等待到整点再执行
    wait_seconds = wait_for_next_period()
    if wait_seconds > 0:
        time.sleep(wait_seconds)

    """主交易机器人函数"""
    print("\n" + "=" * 60)
    print(f"执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # 1. 获取增强版K线数据
    price_data = get_btc_ohlcv_enhanced()
    if not price_data:
        return

    print(f"BTC当前价格: ${price_data['price']:,.2f}")
    print(f"数据周期: {TRADE_CONFIG['timeframe']}")
    print(f"价格变化: {price_data['price_change']:+.2f}%")
    
    # 播报交易分析开始信息
    broadcast_console_info("trading_start", 
                          timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                          price=price_data['price'],
                          price_change=price_data['price_change'],
                          timeframe=TRADE_CONFIG['timeframe'])

    # 2. 使用Bailian分析（带重试）
    signal_data = analyze_with_bailian_with_retry(price_data)

    if signal_data.get('is_fallback', False):
        print("⚠️ 使用备用交易信号")
    
    # 播报信号生成信息
    broadcast_console_info("signal_generated",
                          signal=signal_data.get('signal', 'N/A'),
                          confidence=signal_data.get('confidence', 0),
                          reasoning=signal_data.get('reasoning', 'N/A'),
                          is_fallback=signal_data.get('is_fallback', False))

    # 3. 执行智能交易
    execute_intelligent_trade(signal_data, price_data)


def main():
    """主函数"""
    print("BTC/USDT OKX自动交易机器人启动成功！")
    print("融合技术指标策略 + OKX实盘接口")

    if TRADE_CONFIG['test_mode']:
        print("当前为模拟模式，不会真实下单")
    else:
        print("实盘交易模式，请谨慎操作！")

    print(f"交易周期: {TRADE_CONFIG['timeframe']}")
    print("已启用完整技术指标分析和持仓跟踪功能")
    
    # 🆕 发送启动通知
    if TELEGRAM_ENABLED:
        startup_message = f"""
🚀 <b>交易机器人启动成功</b>

📊 <b>交易对:</b> {TRADE_CONFIG['symbol']}
⚡ <b>杠杆:</b> {TRADE_CONFIG['leverage']}x
⏰ <b>周期:</b> {TRADE_CONFIG['timeframe']}
🎯 <b>模式:</b> {'模拟模式' if TRADE_CONFIG['test_mode'] else '实盘模式'}

🔧 <b>功能:</b>
• 智能仓位管理
• 技术指标分析
• 实时信号播报

⏰ <b>启动时间:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        send_telegram_message(startup_message)

    # 设置交易所
    if not setup_exchange():
        print("交易所初始化失败，程序退出")
        return

    # 测试大模型API
    if not test_bailian_api():
        print("⚠️ 大模型API不可用，程序将使用备用交易信号")
        print("💡 建议修复API配置后重新启动以获得最佳交易效果")
        input("按回车键继续运行（将使用技术指标备用信号）...")

    print("执行频率: 每15分钟整点执行")
    if TELEGRAM_ENABLED:
        print("已启用Telegram播报：交易信号、余额更新、错误通知")

    # 🆕 定期余额播报计时器
    last_balance_report = datetime.now()
    balance_report_interval = timedelta(hours=1)  # 每小时播报一次

    # 循环执行（不使用schedule）
    try:
        while True:
            trading_bot()  # 函数内部会自己等待整点

            # 🆕 检查是否需要发送定期余额报告
            if TELEGRAM_ENABLED and datetime.now() - last_balance_report >= balance_report_interval:
                try:
                    balance = exchange.fetch_balance()
                    position = get_current_position()
                    price_data = get_btc_ohlcv_enhanced()
                    
                    if price_data:
                        balance_info = {
                            'usdt': balance['USDT']['free'],
                            'position_value': position['size'] * price_data['price'] * TRADE_CONFIG['contract_size'] if position else 0,
                            'total': balance['USDT']['free'] + (position['size'] * price_data['price'] * TRADE_CONFIG['contract_size'] if position else 0)
                        }
                        
                        report_message = f"""
📊 <b>定期余额报告</b>

{format_balance_message(balance_info)}

⏰ <b>报告时间:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
                        send_telegram_message(report_message)
                        last_balance_report = datetime.now()
                except Exception as e:
                    print(f"⚠️ 余额报告发送失败: {e}")

            # 执行完后等待一段时间再检查（避免频繁循环）
            time.sleep(60)  # 每分钟检查一次
    except KeyboardInterrupt:
        print("\n程序已停止")
        
        # 🆕 发送停止通知
        if TELEGRAM_ENABLED:
            stop_message = f"""
🛑 <b>交易机器人已停止</b>

⏰ <b>停止时间:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

感谢使用！
"""
            send_telegram_message(stop_message)


if __name__ == "__main__":
    main()