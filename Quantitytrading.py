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
# 移除了异步相关导入，使用requests进行HTTP通信

load_dotenv()

# 模型配置
MODEL_NAME = os.getenv('AI_MODEL_NAME', 'qwen3-max')  # 默认使用qwen3-max

# Telegram配置 - 使用HTTP API，无需异步
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
    'leverage': 20,  # 🔧 提高杠杆倍数，增加盈利潜力（原10→20）
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
        'base_usdt_amount': 30,  # 🔧 增加基础仓位（原10→30 USDT）
        'high_confidence_multiplier': 2.0,  # 🔧 提高高信心倍数（原1.5→2.0）
        'medium_confidence_multiplier': 1.2,  # 🔧 提高中等信心倍数（原1.0→1.2）
        'low_confidence_multiplier': 0.6,  # 🔧 提高低信心倍数（原0.5→0.6）
        'max_position_ratio': 0.8,  # 
        'trend_strength_multiplier': 1.5,  # 🔧 提高趋势强度倍数（原1.2→1.5）
        'min_profit_ratio': 0.003,  # 🆕 最小盈利比例（0.3%），确保覆盖手续费
        'fee_rate': 0.0005,  # 🆕 手续费率（0.05%），用于盈亏计算
        # 新增：同方向微调的相对阈值，避免高频微调耗尽频次
        'min_relative_adjust_ratio': 0.03  # 仅当|Δsize|/current_size≥此比例才同向调仓
    },
    # 🛡️ 风险控制参数 - 防黑天鹅和插针
    'risk_management': {
        'enable_anomaly_detection': True,  # 启用价格异常检测
        'max_price_change_1m': 0.05,  # 1分钟最大价格变化（5%）
        'max_price_change_5m': 0.10,  # 5分钟最大价格变化（10%）
        'max_volatility_threshold': 0.15,  # 最大波动率阈值（15%）
        'circuit_breaker_enabled': True,  # 启用熔断机制
        'max_consecutive_losses': 3,  # 最大连续亏损次数
        'max_daily_loss_ratio': 0.20,  # 最大日亏损比例（20%）
        'slippage_protection': True,  # 启用滑点保护
        'max_slippage_ratio': 0.005,  # 最大滑点比例（0.5%）
        'emergency_stop_enabled': True,  # 启用紧急停止
        'price_deviation_threshold': 0.03,  # 价格偏差阈值（3%）
        'volatility_window': 20,  # 波动率计算窗口（分钟）
        'anomaly_cooldown': 300,  # 异常检测后的冷却时间（秒）
        # 🆕 交易频率控制
        'min_trade_interval': 900,  # 最小交易间隔（15分钟 = 900秒）
        'max_trades_per_hour': 6,  # 每小时最大交易次数
        'max_trades_per_day': 40,  # 每日最大交易次数
        # 🔒 锁盈（可选）配置
        'profit_lock_enabled': False,  # 是否启用锁盈机制（默认关闭）
        'profit_lock_trigger_ratio': 0.02,  # 触发锁盈的收益比例（2%）
        'profit_lock_step_ratio': 0.2,  # 每次锁盈的合约比例（例如20%）
        'profit_lock_cooldown': 600,  # 锁盈冷却时间（秒）
        'profit_lock_min_contracts': 0.01  # 每次最少锁盈的合约张数
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
    """发送Telegram消息 - 使用HTTP API避免异步问题"""
    if not TELEGRAM_ENABLED or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    
    try:
        # 使用Telegram Bot API的HTTP接口，完全避免异步问题
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        
        data = {
            'chat_id': TELEGRAM_CHAT_ID,
            'text': message,
            'parse_mode': parse_mode
        }
        
        response = requests.post(url, data=data, timeout=10)
        
        if response.status_code == 200:
            return True
        else:
            print(f"❌ Telegram API错误: {response.status_code} - {response.text}")
            return False
            
    except requests.exceptions.Timeout:
        print("❌ Telegram消息发送超时")
        return False
    except Exception as e:
        print(f"❌ Telegram消息发送失败: {e}")
        return False


# 🧩 Telegram批量消息收集与汇总
# 默认启用批量模式，减少消息碎片化
TELEGRAM_BATCH_MODE = True
_telegram_sections = []

def start_telegram_cycle():
    """开始一个Telegram汇总周期（清空缓冲）"""
    global _telegram_sections
    _telegram_sections = []

def add_telegram_section(title, body):
    """添加一个消息板块到汇总缓冲"""
    if not TELEGRAM_ENABLED:
        return
    _telegram_sections.append((title, body))

def send_telegram_report(header_title="📑 交易周期汇总"):
    """将缓冲中的消息板块汇总为一条或多条消息并发送"""
    if not TELEGRAM_ENABLED:
        return
    if not _telegram_sections:
        return

    # 组装消息，控制在Telegram单条消息的长度限制内（约4096字符）
    max_len = 3800
    current = f"{header_title}\n\n⏰ 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    parts_to_send = []

    for title, body in _telegram_sections:
        section = f"\n———\n{title}\n\n{body.strip()}\n"
        if len(current) + len(section) > max_len:
            parts_to_send.append(current)
            current = f"{header_title}\n"
        current += section

    if current.strip():
        parts_to_send.append(current)

    for msg in parts_to_send:
        send_telegram_message(msg, parse_mode='HTML')

    # 发送后清空缓冲
    start_telegram_cycle()

def dual_output(message, telegram_enabled=True, console_prefix="", telegram_parse_mode='HTML'):
    """
    统一输出函数：同时输出到控制台和Telegram
    
    Args:
        message: 要输出的消息内容
        telegram_enabled: 是否发送到Telegram（默认True）
        console_prefix: 控制台输出的前缀（可选）
        telegram_parse_mode: Telegram消息解析模式
    """
    # 输出到控制台
    console_message = f"{console_prefix}{message}" if console_prefix else message
    print(console_message)
    
    # 同时发送到Telegram（如果启用）
    if telegram_enabled and TELEGRAM_ENABLED:
        # 清理HTML标签用于Telegram显示
        telegram_message = message
        if telegram_parse_mode == 'HTML':
            # 保持HTML格式
            pass
        else:
            # 移除HTML标签用于纯文本显示
            import re
            telegram_message = re.sub(r'<[^>]+>', '', message)
        
        # 批量模式下加入缓冲；否则即时发送
        if TELEGRAM_BATCH_MODE:
            add_telegram_section("📜 日志", telegram_message)
        else:
            send_telegram_message(telegram_message, telegram_parse_mode)


def log_info(message, telegram_enabled=True):
    """记录信息日志（同时输出到控制台和Telegram）"""
    dual_output(f"ℹ️ {message}", telegram_enabled, "")


def log_success(message, telegram_enabled=True):
    """记录成功日志（同时输出到控制台和Telegram）"""
    dual_output(f"✅ {message}", telegram_enabled, "")


def log_warning(message, telegram_enabled=True):
    """记录警告日志（同时输出到控制台和Telegram）"""
    dual_output(f"⚠️ {message}", telegram_enabled, "")


def log_error(message, telegram_enabled=True):
    """记录错误日志（同时输出到控制台和Telegram）"""
    dual_output(f"❌ {message}", telegram_enabled, "")


def log_trading(message, telegram_enabled=True):
    """记录交易相关日志（同时输出到控制台和Telegram）"""
    dual_output(f"📊 {message}", telegram_enabled, "")


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


def format_position_message(position):
    """格式化持仓信息消息"""
    if position is None:
        return """
📦 <b>当前持仓</b>

🚫 <b>无持仓</b>

⏰ <b>时间:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
""".format(datetime=datetime)
    
    # 计算盈亏百分比
    pnl_percentage = 0
    if position.get('entry_price', 0) > 0:
        current_price = position.get('current_price', position.get('entry_price', 0))
        if position['side'] == 'long':
            pnl_percentage = ((current_price - position['entry_price']) / position['entry_price']) * 100
        else:  # short
            pnl_percentage = ((position['entry_price'] - current_price) / position['entry_price']) * 100
    
    # 选择方向图标
    side_emoji = "📈" if position['side'] == 'long' else "📉"
    side_text = "多头" if position['side'] == 'long' else "空头"
    
    # 选择盈亏颜色图标
    pnl_emoji = "💚" if position.get('unrealized_pnl', 0) >= 0 else "❤️"
    
    message = f"""
📦 <b>当前持仓</b>

{side_emoji} <b>方向:</b> {side_text}
📊 <b>合约:</b> {position.get('symbol', 'N/A')}
💰 <b>数量:</b> {position.get('size', 0):.4f} 张
💵 <b>开仓价:</b> ${position.get('entry_price', 0):,.2f}
{pnl_emoji} <b>未实现盈亏:</b> ${position.get('unrealized_pnl', 0):,.2f} ({pnl_percentage:+.2f}%)
⚡ <b>杠杆:</b> {position.get('leverage', 0):.0f}x

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
            
        # 批量模式下加入缓冲；否则即时发送
        if TELEGRAM_BATCH_MODE:
            add_telegram_section("📣 播报", message)
        else:
            send_telegram_message(message)
        
    except Exception as e:
        print(f"⚠️ 控制台信息播报失败: {e}")
    return message


# 全局变量
price_history = []
signal_history = []
position = None

# 🛡️ 风险控制全局变量
risk_state = {
    'consecutive_losses': 0,  # 连续亏损次数
    'daily_pnl': 0.0,  # 当日盈亏
    'last_anomaly_time': 0,  # 上次异常检测时间
    'circuit_breaker_active': False,  # 熔断状态
    'emergency_stop': False,  # 紧急停止状态
    'trading_suspended': False,  # 交易暂停状态
    'last_price_check': None,  # 上次价格检查
    'volatility_history': [],  # 波动率历史
    # 🆕 交易频率控制
    'last_trade_time': 0,  # 上次交易时间
    'trades_today': 0,  # 今日交易次数
    'trades_this_hour': 0,  # 本小时交易次数
    'last_hour_reset': 0,  # 上次小时重置时间（旧逻辑保留）
    'last_day_reset': 0,  # 上次日期重置时间（旧逻辑保留）
    # 🆕 使用自然时间边界的重置标记
    'last_hour': None,  # 最近一次记录的自然小时（0-23）
    'last_day': None,  # 最近一次记录的自然日期（date 对象）
    # 🔒 锁盈状态
    'profit_lock_reference_price': None,  # 锁盈参考价
    'last_profit_lock_time': 0,  # 上次锁盈时间
    'profit_locked_today': 0  # 当日锁盈总量（合约张数）
}


# 🛡️ 风险控制函数

def detect_price_anomaly(current_price, price_history):
    """检测价格异常（插针、闪崩等）"""
    global risk_state
    
    risk_config = TRADE_CONFIG['risk_management']
    if not risk_config.get('enable_anomaly_detection', True):
        return False, "异常检测已禁用"
    
    current_time = time.time()
    
    # 检查冷却时间
    if current_time - risk_state['last_anomaly_time'] < risk_config['anomaly_cooldown']:
        return False, "异常检测冷却中"
    
    if len(price_history) < 5:
        return False, "价格历史数据不足"
    
    try:
        # 获取最近的价格数据
        recent_prices = [p['price'] for p in price_history[-5:]]
        
        # 1分钟价格变化检测
        if len(recent_prices) >= 2:
            price_change_1m = abs(current_price - recent_prices[-1]) / recent_prices[-1]
            if price_change_1m > risk_config['max_price_change_1m']:
                risk_state['last_anomaly_time'] = current_time
                return True, f"1分钟价格异常变化: {price_change_1m:.2%}"
        
        # 5分钟价格变化检测
        if len(recent_prices) >= 5:
            price_change_5m = abs(current_price - recent_prices[0]) / recent_prices[0]
            if price_change_5m > risk_config['max_price_change_5m']:
                risk_state['last_anomaly_time'] = current_time
                return True, f"5分钟价格异常变化: {price_change_5m:.2%}"
        
        # 价格偏差检测（与均价比较）
        avg_price = sum(recent_prices) / len(recent_prices)
        price_deviation = abs(current_price - avg_price) / avg_price
        if price_deviation > risk_config['price_deviation_threshold']:
            risk_state['last_anomaly_time'] = current_time
            return True, f"价格偏差异常: {price_deviation:.2%}"
        
        return False, "价格正常"
        
    except Exception as e:
        log_error(f"价格异常检测失败: {e}")
        return False, "检测失败"


def calculate_volatility(price_history, window=20):
    """计算价格波动率"""
    if len(price_history) < window:
        return 0.0
    
    try:
        prices = [p['price'] for p in price_history[-window:]]
        returns = []
        
        for i in range(1, len(prices)):
            ret = (prices[i] - prices[i-1]) / prices[i-1]
            returns.append(ret)
        
        if not returns:
            return 0.0
        
        # 计算标准差作为波动率
        mean_return = sum(returns) / len(returns)
        variance = sum((r - mean_return) ** 2 for r in returns) / len(returns)
        volatility = variance ** 0.5
        
        return volatility
        
    except Exception as e:
        log_error(f"波动率计算失败: {e}")
        return 0.0


def check_volatility_protection(price_history):
    """检查波动率保护"""
    risk_config = TRADE_CONFIG['risk_management']
    
    volatility = calculate_volatility(price_history, risk_config['volatility_window'])
    risk_state['volatility_history'].append(volatility)
    
    # 保持波动率历史长度
    if len(risk_state['volatility_history']) > 100:
        risk_state['volatility_history'] = risk_state['volatility_history'][-100:]
    
    if volatility > risk_config['max_volatility_threshold']:
        return True, f"波动率过高: {volatility:.2%}"
    
    return False, f"波动率正常: {volatility:.2%}"


def check_circuit_breaker():
    """检查熔断机制"""
    global risk_state
    
    risk_config = TRADE_CONFIG['risk_management']
    if not risk_config.get('circuit_breaker_enabled', True):
        return False, "熔断机制已禁用"
    
    # 检查连续亏损
    if risk_state['consecutive_losses'] >= risk_config['max_consecutive_losses']:
        risk_state['circuit_breaker_active'] = True
        return True, f"连续亏损{risk_state['consecutive_losses']}次，触发熔断"
    
    # 检查日亏损比例
    try:
        balance = exchange.fetch_balance()
        total_balance = balance['USDT']['total']
        
        if total_balance > 0:
            daily_loss_ratio = abs(risk_state['daily_pnl']) / total_balance
            if risk_state['daily_pnl'] < 0 and daily_loss_ratio > risk_config['max_daily_loss_ratio']:
                risk_state['circuit_breaker_active'] = True
                return True, f"日亏损比例{daily_loss_ratio:.2%}，触发熔断"
    
    except Exception as e:
        log_warning(f"熔断检查失败: {e}")
    
    return False, "熔断检查正常"


def check_slippage_protection(expected_price, actual_price):
    """检查滑点保护"""
    risk_config = TRADE_CONFIG['risk_management']
    if not risk_config.get('slippage_protection', True):
        return True, "滑点保护已禁用"
    
    slippage = abs(actual_price - expected_price) / expected_price
    if slippage > risk_config['max_slippage_ratio']:
        return False, f"滑点过大: {slippage:.2%}"
    
    return True, f"滑点正常: {slippage:.2%}"


def update_risk_state(trade_result):
    """更新风险状态"""
    global risk_state
    
    if trade_result.get('pnl'):
        pnl = float(trade_result['pnl'])
        risk_state['daily_pnl'] += pnl
        
        if pnl < 0:
            risk_state['consecutive_losses'] += 1
        else:
            risk_state['consecutive_losses'] = 0  # 重置连续亏损


def is_trading_allowed():
    """检查是否允许交易"""
    global risk_state
    
    if risk_state['emergency_stop']:
        return False, "紧急停止状态"
    
    if risk_state['circuit_breaker_active']:
        return False, "熔断状态"
    
    if risk_state['trading_suspended']:
        return False, "交易暂停"
    
    return True, "允许交易"


def check_trading_frequency():
    """检查交易频率限制（按自然小时/自然日边界重置）"""
    global risk_state
    
    try:
        risk_config = TRADE_CONFIG['risk_management']
        current_time = time.time()
        now_dt = datetime.now()
        current_hour = now_dt.hour
        current_day = now_dt.date()
        
        # 使用自然小时重置（避免滑动24小时导致午夜无法交易）
        if risk_state.get('last_hour') is None or risk_state.get('last_hour') != current_hour:
            if risk_state.get('last_hour') is not None:
                log_info("⏱️ 已进入新小时，小时交易计数已重置")
            risk_state['trades_this_hour'] = 0
            risk_state['last_hour'] = current_hour
        
        # 使用自然日重置（本地日期变化即重置）
        if risk_state.get('last_day') is None or risk_state.get('last_day') != current_day:
            if risk_state.get('last_day') is not None:
                log_info("📆 已进入新的一天，今日交易计数已重置")
            risk_state['trades_today'] = 0
            risk_state['last_day'] = current_day
        
        # 检查最小交易间隔（秒）
        if risk_state['last_trade_time'] > 0:
            time_since_last = current_time - risk_state['last_trade_time']
            if time_since_last < risk_config['min_trade_interval']:
                remaining = risk_config['min_trade_interval'] - time_since_last
                return False, f"交易间隔不足，还需等待 {remaining:.0f} 秒"
        
        # 检查小时交易次数
        if risk_state['trades_this_hour'] >= risk_config['max_trades_per_hour']:
            return False, f"本小时交易次数已达上限 ({risk_config['max_trades_per_hour']}次)"
        
        # 检查日交易次数
        if risk_state['trades_today'] >= risk_config['max_trades_per_day']:
            return False, f"今日交易次数已达上限 ({risk_config['max_trades_per_day']}次)"
        
        return True, "交易频率检查通过"
        
    except Exception as e:
        log_error(f"交易频率检查失败: {e}")
        return True, "检查失败，允许交易"


def update_trading_frequency():
    """更新交易频率统计"""
    global risk_state
    
    current_time = time.time()
    risk_state['last_trade_time'] = current_time
    risk_state['trades_this_hour'] += 1
    risk_state['trades_today'] += 1
    
    log_info(f"📊 交易频率统计: 本小时 {risk_state['trades_this_hour']} 次，今日 {risk_state['trades_today']} 次")


def evaluate_profit_lock(current_price):
    """评估并执行锁盈（可选功能）"""
    try:
        cfg = TRADE_CONFIG.get('risk_management', {})
        if not cfg.get('profit_lock_enabled', False):
            return False, "未启用锁盈"

        pos = get_current_position()
        if not pos or pos.get('size', 0) <= 0:
            return False, "无持仓"

        entry = pos.get('entry_price', 0) or 0
        if entry <= 0:
            return False, "入场价缺失"

        # 冷却检查
        now = time.time()
        last_lock = risk_state.get('last_profit_lock_time', 0)
        if now - last_lock < cfg.get('profit_lock_cooldown', 600):
            return False, "锁盈冷却中"

        # 计算收益比例（按方向）
        if pos['side'] == 'long':
            profit_ratio = (current_price - entry) / entry
        else:  # short
            profit_ratio = (entry - current_price) / entry

        if profit_ratio < cfg.get('profit_lock_trigger_ratio', 0.02):
            return False, "未达到锁盈阈值"

        # 计算本次锁盈张数
        step_ratio = cfg.get('profit_lock_step_ratio', 0.1)
        min_contracts = cfg.get('profit_lock_min_contracts', TRADE_CONFIG.get('min_amount', 0.01))
        step_contracts = max(min_contracts, round(pos['size'] * step_ratio, 2))
        step_contracts = min(step_contracts, pos['size'])
        if step_contracts <= 0:
            return False, "锁盈张数无效"

        # 下单（reduceOnly）
        close_side = 'sell' if pos['side'] == 'long' else 'buy'
        log_trading(f"🔒 锁盈触发: 收益比例 {profit_ratio:.2%}，执行{step_contracts:.2f}张减仓")
        exchange.create_market_order(
            TRADE_CONFIG['symbol'],
            close_side,
            step_contracts,
            params={'reduceOnly': True, 'tag': '60bb4a8d3416BCDE'}
        )

        # 更新状态与播报
        risk_state['last_profit_lock_time'] = now
        risk_state['profit_locked_today'] = risk_state.get('profit_locked_today', 0) + step_contracts

        section_body = (
            f"<b>锁盈执行</b>\n"
            f"📈 收益比例: {profit_ratio:.2%}\n"
            f"🎯 锁盈张数: {step_contracts:.2f} 张\n"
            f"📦 当前方向: {pos['side']}\n"
            f"💵 现价: {current_price:.2f}, 入场价: {entry:.2f}"
        )

        if TELEGRAM_ENABLED:
            if TELEGRAM_BATCH_MODE:
                add_telegram_section("🔒 锁盈", section_body)
            else:
                send_telegram_message(section_body)

        log_success("锁盈完成")
        return True, "锁盈完成"
    except Exception as e:
        log_error(f"锁盈评估失败: {e}")
        return False, f"错误: {e}"


def reset_circuit_breaker():
    """重置熔断状态（手动调用）"""
    global risk_state
    
    risk_state['circuit_breaker_active'] = False
    risk_state['consecutive_losses'] = 0
    risk_state['emergency_stop'] = False
    risk_state['trading_suspended'] = False
    
    log_info("🔄 风险控制状态已重置")


def check_profit_potential(signal_data, price_data, position_size):
    """检查交易的盈利潜力是否足够覆盖手续费"""
    try:
        config = TRADE_CONFIG['position_management']
        current_price = price_data['price']
        
        # 统一名义价值与手续费计算（按合约规格）
        contract_size = TRADE_CONFIG.get('contract_size', 0.01)
        nominal_value = position_size * contract_size * current_price
        total_fee = nominal_value * config['fee_rate'] * 2  # 开平仓手续费
        
        # 根据信号强度估算盈利潜力
        confidence = signal_data.get('confidence', 'MEDIUM')
        
        # 预期盈利比例（基于历史经验）
        expected_profit_ratios = {
            'HIGH': 0.008,    # 高信心信号预期0.8%盈利
            'MEDIUM': 0.005,  # 中等信心信号预期0.5%盈利
            'LOW': 0.003      # 低信心信号预期0.3%盈利
        }
        
        expected_profit_ratio = expected_profit_ratios.get(confidence, 0.005)
        expected_profit = nominal_value * expected_profit_ratio
        
        # 计算盈亏比
        profit_to_fee_ratio = expected_profit / total_fee if total_fee > 0 else 0
        
        log_info(f"📊 盈亏比分析:")
        log_info(f"   - 仓位大小: {position_size:.4f} 张")
        log_info(f"   - 合约规格: {contract_size} /合约")
        log_info(f"   - 名义价值: {nominal_value:.2f} USDT")
        log_info(f"   - 预计手续费: {total_fee:.4f} USDT")
        log_info(f"   - 预期盈利: {expected_profit:.4f} USDT ({expected_profit_ratio:.1%})")
        log_info(f"   - 盈亏比: {profit_to_fee_ratio:.1f}:1")
        
        # 盈亏比至少要2:1才值得交易
        min_ratio = 2.0
        if profit_to_fee_ratio >= min_ratio:
            log_info(f"✅ 盈亏比良好 ({profit_to_fee_ratio:.1f}:1 >= {min_ratio}:1)")
            return True, f"盈亏比: {profit_to_fee_ratio:.1f}:1"
        else:
            log_warning(f"⚠️ 盈亏比不足 ({profit_to_fee_ratio:.1f}:1 < {min_ratio}:1)")
            return False, f"盈亏比不足: {profit_to_fee_ratio:.1f}:1"
            
    except Exception as e:
        log_error(f"盈亏比检查失败: {e}")
        return True, "检查失败，允许交易"  # 出错时允许交易


def safe_create_market_order(symbol, side, amount, expected_price, params=None):
    """安全的市价单执行，包含滑点保护"""
    try:
        # 执行订单
        order = exchange.create_market_order(symbol, side, amount, params=params)
        
        # 获取实际成交价格
        if order and 'average' in order and order['average']:
            actual_price = float(order['average'])
            
            # 滑点检查
            slippage_ok, slippage_msg = check_slippage_protection(expected_price, actual_price)
            if not slippage_ok:
                log_warning(f"⚠️ {slippage_msg}")
                # 注意：订单已经执行，这里只是记录警告
            else:
                log_info(f"✅ {slippage_msg}")
        
        return order
        
    except Exception as e:
        log_error(f"订单执行失败: {e}")
        return None


def calculate_intelligent_position(signal_data, price_data, current_position):
    """计算智能仓位大小 - 修复版"""
    config = TRADE_CONFIG['position_management']

    # 🆕 新增：如果禁用智能仓位，使用固定仓位
    if not config.get('enable_intelligent_position', True):
        fixed_contracts = 0.1  # 固定仓位大小，可以根据需要调整
        log_info(f"🔧 智能仓位已禁用，使用固定仓位: {fixed_contracts} 张")
        return fixed_contracts

    try:
        # 获取账户余额
        balance = exchange.fetch_balance()
        usdt_balance = balance['USDT']['free']

        # 基础USDT投入
        base_usdt = config['base_usdt_amount']
        log_info(f"💰 可用USDT余额: {usdt_balance:.2f}, 下单基数{base_usdt}")

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

        log_info(f"📊 仓位计算详情:")
        log_info(f"   - 基础USDT: {base_usdt}")
        log_info(f"   - 信心倍数: {confidence_multiplier}")
        log_info(f"   - 趋势倍数: {trend_multiplier}")
        log_info(f"   - RSI倍数: {rsi_multiplier}")
        log_info(f"   - 建议USDT: {suggested_usdt:.2f}")
        log_info(f"   - 最终USDT(保证金): {final_usdt:.2f}")
        log_info(f"   - 杠杆倍数: {TRADE_CONFIG['leverage']}x")
        log_info(f"   - 名义价值: {final_usdt * TRADE_CONFIG['leverage']:.2f} USDT")
        log_info(f"   - 合约乘数: {TRADE_CONFIG['contract_size']}")
        log_info(f"   - 计算合约: {contract_size:.4f} 张")
        
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
            log_warning(f"⚠️ 仓位小于最小值，调整为: {contract_size} 张")

        # 🆕 手续费计算和盈亏比检查
        nominal_value = final_usdt * TRADE_CONFIG['leverage']  # 名义价值
        total_fee = nominal_value * config['fee_rate'] * 2  # 开仓+平仓手续费
        min_profit_needed = nominal_value * config['min_profit_ratio']  # 最小盈利需求
        
        log_info(f"💰 手续费分析:")
        log_info(f"   - 名义价值: {nominal_value:.2f} USDT")
        log_info(f"   - 预计手续费: {total_fee:.4f} USDT (开平仓)")
        log_info(f"   - 最小盈利需求: {min_profit_needed:.4f} USDT")
        log_info(f"   - 盈亏比要求: {config['min_profit_ratio']:.1%}")
        
        # 检查仓位是否足够覆盖手续费
        if min_profit_needed < total_fee * 1.5:  # 盈利至少是手续费的1.5倍
            log_warning(f"⚠️ 仓位可能过小，建议盈利至少 {total_fee * 1.5:.4f} USDT")

        log_info(f"🎯 最终仓位: {final_usdt:.2f} USDT → {contract_size:.2f} 张合约")
        return contract_size

    except Exception as e:
        log_error(f"❌ 仓位计算失败，使用基础仓位: {e}")
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
        log_error(f"技术指标计算失败: {e}")
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
        log_error(f"支撑阻力计算失败: {e}")
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

                        log_info(f"✅ 使用情绪数据时间: {period['startTime']} (延迟: {data_delay}分钟)")

                        return {
                            'positive_ratio': positive,
                            'negative_ratio': negative,
                            'net_sentiment': net_sentiment,
                            'data_time': period['startTime'],
                            'data_delay_minutes': data_delay
                        }

                log_warning("❌ 所有时间段数据都为空")
                return None

        return None
    except Exception as e:
        log_error(f"情绪指标获取失败: {e}")
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
        log_error(f"趋势分析失败: {e}")
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
        log_error(f"获取增强K线数据失败: {e}")
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
        log_error(f"获取持仓失败: {e}")
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
        log_info(f"Bailian原始回复: {result}")

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
        log_info(f"信号统计: {signal_data['signal']} (最近{total_signals}次中出现{signal_count}次)")

        # 信号连续性检查
        if len(signal_history) >= 3:
            last_three = [s['signal'] for s in signal_history[-3:]]
            if len(set(last_three)) == 1:
                log_warning(f"⚠️ 注意：连续3次{signal_data['signal']}信号")

        return signal_data

    except Exception as e:
        log_error(f"DeepSeek分析失败: {e}")
        return create_fallback_signal(price_data)


def execute_intelligent_trade(signal_data, price_data):
    """执行智能交易 - OKX版本（支持同方向加仓减仓）"""
    global position, risk_state

    # 🛡️ 风险控制检查
    # 1. 检查是否允许交易
    trading_allowed, reason = is_trading_allowed()
    if not trading_allowed:
        log_warning(f"🚫 交易被阻止: {reason}")
        return

    # 2. 价格异常检测
    anomaly_detected, anomaly_reason = detect_price_anomaly(price_data['price'], price_history)
    if anomaly_detected:
        log_warning(f"🚨 检测到价格异常: {anomaly_reason}")
        risk_state['trading_suspended'] = True
        return

    # 3. 波动率保护检查
    high_volatility, volatility_reason = check_volatility_protection(price_history)
    if high_volatility:
        log_warning(f"⚡ 波动率保护触发: {volatility_reason}")
        risk_state['trading_suspended'] = True
        return

    # 4. 熔断机制检查
    circuit_breaker_triggered, breaker_reason = check_circuit_breaker()
    if circuit_breaker_triggered:
        log_error(f"🔴 熔断机制触发: {breaker_reason}")
        return

    # 5. 交易频率检查
    frequency_allowed, frequency_reason = check_trading_frequency()
    if not frequency_allowed:
        log_warning(f"⏰ 交易频率限制: {frequency_reason}")
        return

    log_info("✅ 风险控制检查通过，允许交易")

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

    # 🆕 盈亏比检查
    profit_ok, profit_reason = check_profit_potential(signal_data, price_data, position_size)
    if not profit_ok:
        log_warning(f"💸 {profit_reason}，跳过此次交易")
        return

    # 格式化当前持仓信息
    position_info = "无持仓" if current_position is None else f"{current_position['side']}仓 {current_position['size']:.2f}张"
    log_trading(f"<b>交易信号生成</b>\n📊 信号: {signal_data['signal']}\n🎯 信心程度: {signal_data['confidence']}\n💰 智能仓位: {position_size:.2f} 张\n💡 理由: {signal_data['reason']}\n📦 当前持仓: {position_info}")
    
    # 🆕 发送Telegram交易信号通知（批量模式优先）
    if TELEGRAM_ENABLED:
        telegram_message = format_trading_signal_message(signal_data, price_data, position_size)
        if TELEGRAM_BATCH_MODE:
            add_telegram_section("🎯 交易信号", telegram_message)
        else:
            send_telegram_message(telegram_message)

    # 🆕 保证金预检查
    try:
        balance = exchange.fetch_balance()
        usdt_balance = balance['USDT']['free']
        
        # 计算所需保证金
        required_margin = (position_size * TRADE_CONFIG['contract_size'] * price_data['price']) / TRADE_CONFIG['leverage']
        
        log_info(f"<b>💳 保证金检查</b>\n💰 可用余额: {usdt_balance:.2f} USDT\n💵 所需保证金: {required_margin:.2f} USDT\n📊 安全余量: {usdt_balance - required_margin:.2f} USDT")
        
        # 播报保证金检查信息
        if required_margin > usdt_balance * 0.95:  # 保留5%安全余量
            log_warning("保证金不足！正在调整仓位大小...")
            # 重新计算安全仓位
            safe_margin = usdt_balance * 0.9  # 使用90%的余额
            position_size = (safe_margin * TRADE_CONFIG['leverage']) / (price_data['price'] * TRADE_CONFIG['contract_size'])
            position_size = round(position_size, 2)
            log_info(f"🔧 调整后仓位: {position_size:.2f} 张")
            
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
                log_warning("调整后仓位仍小于最小值，跳过交易")
                return
                
    except Exception as e:
        log_warning(f"保证金检查失败: {e}")
        # 继续执行，但使用更保守的仓位
        position_size = min(position_size, 0.01)

    # 风险管理
    if signal_data['confidence'] == 'LOW' and not TRADE_CONFIG['test_mode']:
        log_warning("低信心信号，跳过执行")
        return

    if TRADE_CONFIG['test_mode']:
        log_info("测试模式 - 仅模拟交易")
        return

    # 🛡️ 下单前滑点保护预检
    try:
        ticker = exchange.fetch_ticker(TRADE_CONFIG['symbol'])
        actual_price = float(ticker.get('last') or ticker.get('close') or price_data['price'])
        ok, reason = check_slippage_protection(price_data['price'], actual_price)
        if not ok:
            log_warning(f"⛔ {reason}，跳过下单")
            return
        else:
            log_info(f"✅ 滑点检查通过: {reason}")
    except Exception as e:
        log_warning(f"滑点保护检查失败: {e}")

    try:
        # 执行交易逻辑 - 支持同方向加仓减仓
        if signal_data['signal'] == 'BUY':
            if current_position and current_position['side'] == 'short':
                # 先检查空头持仓是否真实存在且数量正确
                if current_position['size'] > 0:
                    log_trading(f"🔄 平空仓 {current_position['size']:.2f} 张并开多仓 {position_size:.2f} 张...")
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
                    log_warning("检测到空头持仓但数量为0，直接开多仓")
                    exchange.create_market_order(
                        TRADE_CONFIG['symbol'],
                        'buy',
                        position_size,
                        params={'tag': '60bb4a8d3416BCDE'}
                    )

            elif current_position and current_position['side'] == 'long':
                # 同方向，检查是否需要调整仓位（加入相对阈值）
                size_diff = position_size - current_position['size']
                min_amount = TRADE_CONFIG.get('min_amount', 0.01)
                min_rel = TRADE_CONFIG['position_management'].get('min_relative_adjust_ratio', 0.0)
                relative_diff = abs(size_diff) / max(current_position['size'], min_amount)

                if abs(size_diff) >= min_amount and relative_diff >= min_rel:  # 有可调整的差异且满足比例
                    if size_diff > 0:
                        # 加仓
                        add_size = round(size_diff, 2)
                        log_trading(f"📈 多仓加仓 {add_size:.2f} 张 (当前:{current_position['size']:.2f} → 目标:{position_size:.2f})")
                        exchange.create_market_order(
                            TRADE_CONFIG['symbol'],
                            'buy',
                            add_size,
                            params={'tag': '60bb4a8d3416BCDE'}
                        )
                    else:
                        # 减仓
                        reduce_size = round(abs(size_diff), 2)
                        log_trading(f"📉 多仓减仓 {reduce_size:.2f} 张 (当前:{current_position['size']:.2f} → 目标:{position_size:.2f})")
                        exchange.create_market_order(
                            TRADE_CONFIG['symbol'],
                            'sell',
                            reduce_size,
                            params={'reduceOnly': True, 'tag': '60bb4a8d3416BCDE'}
                        )
                else:
                    log_info(f"已有多头持仓，微调未达阈值保持现状 (当前:{current_position['size']:.2f}, 目标:{position_size:.2f}, 相对差异:{relative_diff:.2%})")
            else:
                # 无持仓时开多仓
                log_trading(f"🟢 开多仓 {position_size:.2f} 张...")
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
                    log_trading(f"🔄 平多仓 {current_position['size']:.2f} 张并开空仓 {position_size:.2f} 张...")
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
                    log_warning("检测到多头持仓但数量为0，直接开空仓")
                    exchange.create_market_order(
                        TRADE_CONFIG['symbol'],
                        'sell',
                        position_size,
                        params={'tag': '60bb4a8d3416BCDE'}
                    )

            elif current_position and current_position['side'] == 'short':
                # 同方向，检查是否需要调整仓位（加入相对阈值）
                size_diff = position_size - current_position['size']
                min_amount = TRADE_CONFIG.get('min_amount', 0.01)
                min_rel = TRADE_CONFIG['position_management'].get('min_relative_adjust_ratio', 0.0)
                relative_diff = abs(size_diff) / max(current_position['size'], min_amount)

                if abs(size_diff) >= min_amount and relative_diff >= min_rel:  # 有可调整的差异且满足比例
                    if size_diff > 0:
                        # 加仓
                        add_size = round(size_diff, 2)
                        log_trading(f"📈 空仓加仓 {add_size:.2f} 张 (当前:{current_position['size']:.2f} → 目标:{position_size:.2f})")
                        exchange.create_market_order(
                            TRADE_CONFIG['symbol'],
                            'sell',
                            add_size,
                            params={'tag': '60bb4a8d3416BCDE'}
                        )
                    else:
                        # 减仓
                        reduce_size = round(abs(size_diff), 2)
                        log_trading(f"📉 空仓减仓 {reduce_size:.2f} 张 (当前:{current_position['size']:.2f} → 目标:{position_size:.2f})")
                        exchange.create_market_order(
                            TRADE_CONFIG['symbol'],
                            'buy',
                            reduce_size,
                            params={'reduceOnly': True, 'tag': '60bb4a8d3416BCDE'}
                        )
                else:
                    log_info(f"已有空头持仓，微调未达阈值保持现状 (当前:{current_position['size']:.2f}, 目标:{position_size:.2f}, 相对差异:{relative_diff:.2%})")
            else:
                # 无持仓时开空仓
                log_trading(f"🔴 开空仓 {position_size:.2f} 张...")
                exchange.create_market_order(
                    TRADE_CONFIG['symbol'],
                    'sell',
                    position_size,
                    params={'tag': '60bb4a8d3416BCDE'}
                )

        elif signal_data['signal'] == 'HOLD':
            log_info("建议观望，不执行交易")
            return

        log_success("智能交易执行成功")
        
        # 🆕 更新交易频率统计
        update_trading_frequency()
        
        time.sleep(2)
        position = get_current_position()
        log_info(format_position_message(position))
        
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
📊 <b>当前持仓:</b> {'无持仓' if position is None else f"{position['side']}仓 {position['size']:.2f}张"}

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

            log_warning(f"第{attempt + 1}次尝试失败，进行重试...")
            time.sleep(1)

        except Exception as e:
            log_error(f"第{attempt + 1}次尝试异常: {e}")
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
    global price_history, risk_state
    
    log_info("\n" + "=" * 60)
    log_info(f"执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log_info("=" * 60)

    # 1. 获取增强版K线数据
    price_data = get_btc_ohlcv_enhanced()
    if not price_data:
        return

    # 🛡️ 更新价格历史（用于风险控制）
    price_history.append({
        'price': price_data['price'],
        'timestamp': time.time(),
        'datetime': datetime.now()
    })
    
    # 保持价格历史长度（保留最近100个数据点）
    if len(price_history) > 100:
        price_history = price_history[-100:]

    # 🛡️ 每日重置风险状态（在新的一天开始时）
    current_date = datetime.now().date()
    # 修复：risk_state是字典，hasattr恒False；改为直接比对last_reset_date
    if risk_state.get('last_reset_date') != current_date:
        risk_state['daily_pnl'] = 0.0
        risk_state['last_reset_date'] = current_date
        log_info("🔄 每日风险状态已重置")

    log_info(f"BTC当前价格: ${price_data['price']:,.2f}")
    log_info(f"数据周期: {TRADE_CONFIG['timeframe']}")
    log_info(f"价格变化: {price_data['price_change']:+.2f}%")
    
    # 🛡️ 显示风险状态
    log_info(f"🛡️ 风险状态: 连续亏损{risk_state['consecutive_losses']}次, 日盈亏{risk_state['daily_pnl']:+.2f}USDT")
    
    # 播报交易分析开始信息
    broadcast_console_info("trading_start", 
                          timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                          price=price_data['price'],
                          price_change=price_data['price_change'],
                          timeframe=TRADE_CONFIG['timeframe'])

    # 🧰 开启Telegram批量汇总周期
    if TELEGRAM_ENABLED and TELEGRAM_BATCH_MODE:
        start_telegram_cycle()

    # 2. 使用Bailian分析（带重试）
    signal_data = analyze_with_bailian_with_retry(price_data)

    if signal_data.get('is_fallback', False):
        log_warning("⚠️ 使用备用交易信号")
    
    # 播报信号生成信息
    broadcast_console_info("signal_generated",
                          signal=signal_data.get('signal', 'N/A'),
                          confidence=signal_data.get('confidence', 0),
                          reasoning=signal_data.get('reasoning', 'N/A'),
                          is_fallback=signal_data.get('is_fallback', False))

    # 3. 执行智能交易
    execute_intelligent_trade(signal_data, price_data)

    # 🔒 可选：评估锁盈
    try:
        evaluate_profit_lock(price_data['price'])
    except Exception as e:
        log_warning(f"锁盈评估异常: {e}")

    # 📨 结束本周期并发送汇总
    if TELEGRAM_ENABLED and TELEGRAM_BATCH_MODE:
        send_telegram_report(header_title="📑 交易周期汇总")


def main():
    """主函数"""
    log_success("BTC/USDT OKX自动交易机器人启动成功！")
    log_info("融合技术指标策略 + OKX实盘接口")

    if TRADE_CONFIG['test_mode']:
        log_warning("当前为模拟模式，不会真实下单")
    else:
        log_warning("实盘交易模式，请谨慎操作！")

    log_info(f"交易周期: {TRADE_CONFIG['timeframe']}")
    log_info("已启用完整技术指标分析和持仓跟踪功能")
    
    # 🛡️ 显示风险控制配置
    risk_config = TRADE_CONFIG['risk_management']
    log_info("🛡️ 风险控制配置:")
    log_info(f"   - 价格异常检测: {'启用' if risk_config['enable_anomaly_detection'] else '禁用'}")
    log_info(f"   - 最大1分钟变化: {risk_config['max_price_change_1m']:.1%}")
    log_info(f"   - 最大5分钟变化: {risk_config['max_price_change_5m']:.1%}")
    log_info(f"   - 波动率阈值: {risk_config['max_volatility_threshold']:.1%}")
    log_info(f"   - 熔断机制: {'启用' if risk_config['circuit_breaker_enabled'] else '禁用'}")
    log_info(f"   - 最大连续亏损: {risk_config['max_consecutive_losses']}次")
    log_info(f"   - 最大日亏损比例: {risk_config['max_daily_loss_ratio']:.1%}")
    log_info(f"   - 滑点保护: {'启用' if risk_config['slippage_protection'] else '禁用'}")
    log_info(f"   - 最大滑点: {risk_config['max_slippage_ratio']:.1%}")
    
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
        log_error("交易所初始化失败，程序退出")
        return

    # 测试大模型API
    if not test_bailian_api():
        log_warning("⚠️ 大模型API不可用，程序将使用备用交易信号")
        log_info("💡 建议修复API配置后重新启动以获得最佳交易效果")
        input("按回车键继续运行（将使用技术指标备用信号）...")

    log_info("执行频率: 每15分钟整点执行")
    if TELEGRAM_ENABLED:
        log_info("已启用Telegram播报：交易信号、余额更新、错误通知")

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
                    log_error(f"⚠️ 余额报告发送失败: {e}")

            # 执行完后等待一段时间再检查（避免频繁循环）
            time.sleep(60)  # 每分钟检查一次
    except KeyboardInterrupt:
        log_info("\n程序已停止")
        
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