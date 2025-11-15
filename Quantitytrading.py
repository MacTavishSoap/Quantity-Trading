import os
import time
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
        'short_term': 12,   # 短线动量（约3小时，15m*12）
        'medium_term': 36,  # 会话节奏（约9小时）
        'long_term': 96,    # 日趋势（约24小时）
        'weekly_trend': 336,  # 保持原值（可后续优化）
        'monthly_trend': 1440  # 保持原值（可后续优化）
    },
    # 新增智能仓位参数
    'position_management': {
        'enable_intelligent_position': True,  # 🆕 新增：是否启用智能仓位管理
        'base_usdt_amount': 30,  # ⚠️ 已废弃：现在根据余额动态计算基础仓位
        'high_confidence_multiplier': 3.0,  # 🔧 大幅提高高信心倍数（原2.0→3.0）
        'medium_confidence_multiplier': 1.8,  # 🔧 大幅提高中等信心倍数（原1.2→1.8）
        'low_confidence_multiplier': 0.8,  # 🔧 提高低信心倍数（原0.6→0.8）
        'max_position_ratio': 0.8,  # 最大仓位比例限制
        'trend_strength_multiplier': 1.5,  # 🔧 提高趋势强度倍数（原1.2→1.5），增加趋势权重
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
        # 🎯 追踪止盈（唯一保留的锁盈方式）
        'trailing_stop': {
            'atr_window': 14,            # ATR窗口
            'atr_multiplier': 2.5,       # ATR倍数（2.0-3.0较稳健）
            'activation_ratio': 0.004,   # 启动追踪的最低盈利比例（0.4%）
            'break_even_buffer_ratio': 0.001,  # 首次保本缓冲（0.1%）
            'min_step_ratio': 0.002,     # 止损更新的最小步进（0.2%）
            'update_cooldown': 120,      # 止损更新冷却时间（秒）
            'close_all_on_hit': True,    # 触发即全仓平仓
            'partial_close_ratio': 0.5   # 非全平时的部分平仓比例
        },
        # ⏳ 时间止损：在设定的K线窗口内未达到最小推进则退出
        'time_stop': {
            'enabled': True,
            'window_bars': 2,            # 短线更紧，2根K线未推进则退出
            'min_progress_ratio': 0.004, # 最小推进比例（保持）
            'close_all': True            # 触发则全平
        },
        # 🧱 结构失效退出：趋势稳定性不足或方向冲突时退出
        'structural_exit': {
            'enabled': True,
            'stability_threshold': 50,   # 趋势稳定性阈值（百分制）
            'require_conflict': True     # 需要方向冲突时才触发
        },
        # 🆕 均线噪音过滤：用于过滤噪音区，避免将均线当作直接信号
        'moving_average_filter': {
            'enabled': True,            # 启用均线噪音过滤
            'band_ema12_pct': 0.6,      # 缩紧短线噪音带（±0.6%），对应EMA12
            'band_ema36_pct': 1.0,      # 缩紧中线噪音带（±1.0%），对应EMA36
            'apply_to_non_high_confidence_only': True  # 仅过滤非高置信度信号
        }
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
    
    # 简化消息格式，去除冗余信息
    message = f"""
🤖 <b>交易信号</b>

{signal_emoji.get(signal_data['signal'], '❓')} {signal_data['signal']} | {confidence_emoji.get(signal_data['confidence'], '❓')} {signal_data['confidence']}
💰 {position_size:.2f}张 | 💵 ${price_data['price']:,.2f}

📊 RSI: {price_data.get('rsi', 'N/A')} | 趋势: {price_data.get('trend', 'N/A')}
"""
    return message


def format_balance_message(balance_info):
    """格式化余额信息消息"""
    message = f"""
💳 <b>账户余额</b>

💰 USDT: {balance_info.get('usdt', 0):.2f}
📈 持仓: {balance_info.get('position_value', 0):.2f}
📊 总资产: {balance_info.get('total', 0):.2f}
"""
    return message


def format_position_message(position):
    """格式化持仓信息消息"""
    if position is None:
        return """
📦 <b>持仓状态</b>

🚫 无持仓
"""
    
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
    side_text = "多" if position['side'] == 'long' else "空"
    
    # 选择盈亏颜色图标
    pnl_emoji = "💚" if position.get('unrealized_pnl', 0) >= 0 else "❤️"
    
    message = f"""
📦 <b>持仓状态</b>

{side_emoji} {side_text} | {position.get('symbol', 'N/A')}
💰 {position.get('size', 0):.4f}张 | ⚡ {position.get('leverage', 0):.0f}x
💵 ${position.get('entry_price', 0):,.2f}
{pnl_emoji} ${position.get('unrealized_pnl', 0):,.2f} ({pnl_percentage:+.2f}%)
"""
    return message


def format_error_message(error_type, error_msg):
    """格式化错误消息"""
    return f"""
❌ <b>错误</b>

🚨 {error_type}
📝 {error_msg[:100]}{'...' if len(error_msg) > 100 else ''}
"""

def broadcast_console_info(info_type, **kwargs):
    """同步控制台信息到Telegram播报"""
    if not TELEGRAM_ENABLED:
        return
    
    try:
        if info_type == "trading_start":
            message = f"""
📊 <b>交易分析</b>

⏰ {kwargs.get('timestamp', datetime.now().strftime('%H:%M:%S'))}
💰 ${kwargs.get('price', 0):,.2f} | 📈 {kwargs.get('price_change', 0):+.2f}%
⏱️ {kwargs.get('timeframe', 'N/A')}
"""
            
        elif info_type == "signal_generated":
            fallback_note = " | ⚠️备用" if kwargs.get('is_fallback', False) else ""
            message = f"""
🎯 <b>信号生成</b>

📊 {kwargs.get('signal', 'N/A')}{fallback_note}
🎯 {kwargs.get('confidence', 0)}% | 💡 {kwargs.get('reasoning', 'N/A')[:80]}...
"""
            
        elif info_type == "position_calculation":
            message = f"""
🧮 <b>仓位计算</b>

💰 {kwargs.get('base_amount', 0)}U | 📊 {kwargs.get('confidence_multiplier', 0):.1f}x
📈 {kwargs.get('trend_multiplier', 0):.1f}x | ⚡ {kwargs.get('leverage', 0)}x
💎 {kwargs.get('nominal_value', 0):.2f}U | 🎯 {kwargs.get('position_size', 0):.4f}张
"""
            
        elif info_type == "margin_check":
            message = f"""
🔍 <b>保证金检查</b>

💵 {kwargs.get('available_balance', 0):.2f}U | 💰 {kwargs.get('required_margin', 0):.2f}U
✅ {kwargs.get('check_result', 'N/A')}
"""
            if kwargs.get('adjusted_size'):
                message += f"\n🔧 调整后: {kwargs.get('adjusted_size', 0):.4f}张"
                
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
    # 🎯 追踪止盈状态（仅保留）
    'trailing_stop_price': None,      # 当前追踪止损价格
    'position_high_price': None,      # 持仓期间的最高价（多头）
    'position_low_price': None,       # 持仓期间的最低价（空头）
    'last_trailing_update_time': 0,   # 最近一次追踪止损更新的时间戳
    # 🆕 AI融合的动态追踪参数（若存在则优先使用）
    'dynamic_trailing_cfg': None,
    # 🧭 战役状态：跟踪同方向交易的有效期与推进情况
    'campaign': {
        'start_time': 0,
        'bars_elapsed': 0,
        'side': None,
        'entry_price': None,
        'mae': 0.0,
        'mfe': 0.0,
        'planned_R': None
    }
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
            # 修正：日亏损比例应该基于初始余额计算，而不是当前余额
            # 因为当前余额已经包含了当日的亏损
            initial_balance = total_balance - risk_state['daily_pnl']
            if initial_balance > 0:
                daily_loss_ratio = abs(risk_state['daily_pnl']) / initial_balance
                if risk_state['daily_pnl'] < 0 and daily_loss_ratio > risk_config['max_daily_loss_ratio']:
                    risk_state['circuit_breaker_active'] = True
                    return True, f"日亏损比例{daily_loss_ratio:.2%}，触发熔断"
            else:
                log_warning("⚠️ 初始余额计算异常，跳过日亏损比例检查")
    
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
            
        log_info(f"📊 风险状态更新: PNL {pnl:+.2f} USDT, 日累计 {risk_state['daily_pnl']:+.2f} USDT, 连续亏损 {risk_state['consecutive_losses']}次")
    else:
        log_warning("⚠️ 交易结果缺少PNL数据，无法更新风险状态")


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


    # 已删除：锁盈减仓逻辑，改用统一的ATR追踪止盈


def auto_stop_profit_loss(price_data):
    """ATR稳定追踪止盈（统一版）

    仅使用追踪止盈：
    - 激活条件：达到最小盈利比例 `activation_ratio`
    - 止损轨迹：
      多头使用 `position_high_price - ATR*multiplier`，同时首段保障至保本缓冲上方；
      空头使用 `position_low_price + ATR*multiplier`，同时首段保障至保本缓冲下方。
    - 稳定更新：满足最小步进 `min_step_ratio` 且冷却结束 `update_cooldown` 才更新。
    - 触发方式：价格触及追踪止损即全平（可配置）。
    """
    try:
        pos = get_current_position()
        if not pos or pos.get('size', 0) <= 0:
            # 无持仓时，重置追踪状态
            risk_state['trailing_stop_price'] = None
            risk_state['position_high_price'] = None
            risk_state['position_low_price'] = None
            risk_state['last_trailing_update_time'] = 0
            return False, "无持仓"

        # 优先使用AI/趋势融合生成的动态参数
        cfg = risk_state.get('dynamic_trailing_cfg') or TRADE_CONFIG.get('risk_management', {}).get('trailing_stop', {})
        current_price = price_data.get('price')
        entry = pos.get('entry_price', 0) or 0
        if entry <= 0 or current_price is None:
            return False, "入场或现价缺失"

        side = pos.get('side')  # 'long' 或 'short'
        now = time.time()

        # 取最新ATR（若缺失，使用最近K线高低价差回退）
        df = price_data.get('full_data')
        atr = None
        if df is not None and 'atr' in df.columns:
            last_row = df.iloc[-1]
            atr_val = last_row.get('atr')
            try:
                atr = float(atr_val) if atr_val is not None else None
            except Exception:
                atr = None
        if atr is None or atr <= 0:
            # 回退使用当前k线的高低价差
            high = price_data.get('high', current_price)
            low = price_data.get('low', current_price)
            atr = abs(float(high) - float(low)) or max(1e-6, abs(current_price * 0.001))

        # 更新持仓高/低价与盈利比例
        if side == 'long':
            prev_high = risk_state.get('position_high_price') or entry
            risk_state['position_high_price'] = max(prev_high, current_price)
            profit_ratio = (current_price - entry) / entry
        else:
            prev_low = risk_state.get('position_low_price') or entry
            risk_state['position_low_price'] = min(prev_low, current_price)
            profit_ratio = (entry - current_price) / entry

        # 未达到激活盈利比例则不启动追踪（仅在未初始化时进行门槛检查）
        if risk_state.get('trailing_stop_price') is None and profit_ratio < cfg.get('activation_ratio', 0.004):
            return False, "未达追踪激活阈值"

        atr_mult = cfg.get('atr_multiplier', 2.5)
        min_step_ratio = cfg.get('min_step_ratio', 0.002)
        cooldown = cfg.get('update_cooldown', 120)
        break_even_buf = cfg.get('break_even_buffer_ratio', 0.001)

        # 计算候选追踪止损价
        if side == 'long':
            high_water = risk_state.get('position_high_price') or current_price
            candidate = max(entry * (1 + break_even_buf), high_water - atr_mult * atr)
            old = risk_state.get('trailing_stop_price')
            # 仅上移
            new_stop = candidate if old is None else max(old, candidate)
            step_diff_ratio = abs((new_stop - (old or new_stop)) / entry)
        else:  # short
            low_water = risk_state.get('position_low_price') or current_price
            candidate = min(entry * (1 - break_even_buf), low_water + atr_mult * atr)
            old = risk_state.get('trailing_stop_price')
            # 仅下移
            new_stop = candidate if old is None else min(old, candidate)
            step_diff_ratio = abs(((old or new_stop) - new_stop) / entry)

        # 冷却与步进判断后更新
        if risk_state.get('trailing_stop_price') is None or (
            now - risk_state.get('last_trailing_update_time', 0) >= cooldown and step_diff_ratio >= min_step_ratio
        ):
            risk_state['trailing_stop_price'] = new_stop
            risk_state['last_trailing_update_time'] = now
            log_trading(
                f"🧷 更新追踪止损: {new_stop:.2f} | ATR {atr:.2f} | 盈利 {profit_ratio:.2%}"
            )

        # 触发平仓
        stop_price = risk_state.get('trailing_stop_price')
        if stop_price is None:
            return False, "追踪止损未初始化"

        if side == 'long' and current_price <= stop_price:
            # 平仓策略：支持全平或部分平仓
            close_all = cfg.get('close_all_on_hit', True)
            partial_ratio = float(cfg.get('partial_close_ratio', 0.5))
            min_amount = TRADE_CONFIG.get('min_amount', 0.01)
            size_to_close = pos['size'] if close_all else max(min_amount, round(pos['size'] * partial_ratio, 2))
            log_trading(f"🎯 追踪止盈触发(多): 价格 {current_price:.2f} ≤ 止损 {stop_price:.2f} | 平仓数量 {size_to_close:.2f}")
            exchange.create_market_order(
                TRADE_CONFIG['symbol'], 'sell', size_to_close,
                params={'reduceOnly': True, 'tag': '60bb4a8d3416BCDE'}
            )
            if close_all:
                log_success(f"✅ 全平多仓 {pos['size']:.2f} 张")
                # 重置状态（全平时）
                risk_state['trailing_stop_price'] = None
                risk_state['position_high_price'] = None
                risk_state['last_trailing_update_time'] = 0
            else:
                log_success(f"✅ 部分平多仓 {size_to_close:.2f} 张，继续追踪")
            return True, "追踪止盈完成"

        if side == 'short' and current_price >= stop_price:
            # 平仓策略：支持全平或部分平仓
            close_all = cfg.get('close_all_on_hit', True)
            partial_ratio = float(cfg.get('partial_close_ratio', 0.5))
            min_amount = TRADE_CONFIG.get('min_amount', 0.01)
            size_to_close = pos['size'] if close_all else max(min_amount, round(pos['size'] * partial_ratio, 2))
            log_trading(f"🎯 追踪止盈触发(空): 价格 {current_price:.2f} ≥ 止损 {stop_price:.2f} | 平仓数量 {size_to_close:.2f}")
            exchange.create_market_order(
                TRADE_CONFIG['symbol'], 'buy', size_to_close,
                params={'reduceOnly': True, 'tag': '60bb4a8d3416BCDE'}
            )
            if close_all:
                log_success(f"✅ 全平空仓 {pos['size']:.2f} 张")
                # 重置状态（全平时）
                risk_state['trailing_stop_price'] = None
                risk_state['position_low_price'] = None
                risk_state['last_trailing_update_time'] = 0
            else:
                log_success(f"✅ 部分平空仓 {size_to_close:.2f} 张，继续追踪")
            return True, "追踪止盈完成"

        return False, "继续持有，追踪止盈未触发"

    except Exception as e:
        log_error(f"追踪止盈异常: {e}")
        return False, f"错误: {e}"


def update_campaign_state(pos):
    """维护战役（campaign）状态：定位起始、累计bars"""
    try:
        global risk_state
        if not pos or pos.get('size', 0) <= 0:
            # 无持仓时重置
            risk_state['campaign'] = {'start_time': 0, 'bars_elapsed': 0, 'side': None, 'entry_price': None, 'mae': 0.0, 'mfe': 0.0, 'planned_R': None}
            return
        camp = risk_state.get('campaign', {})
        is_new = camp.get('side') != pos.get('side') or not camp.get('entry_price') or abs((camp.get('entry_price') or 0) - (pos.get('entry_price') or 0)) > 1e-8
        if is_new or camp.get('start_time', 0) == 0:
            risk_state['campaign'] = {
                'start_time': time.time(),
                'bars_elapsed': 0,
                'side': pos.get('side'),
                'entry_price': pos.get('entry_price'),
                'mae': 0.0,
                'mfe': 0.0,
                'planned_R': 1.0  # 预设R值骨架，可根据策略计算更新
            }
        else:
            # 每周期递增一次bars
            risk_state['campaign']['bars_elapsed'] = int(risk_state['campaign'].get('bars_elapsed', 0)) + 1
    except Exception as e:
        log_warning(f"更新战役状态失败: {e}")


def update_campaign_metrics(price_data):
    """基于当前价格更新MAE/MFE骨架度量"""
    try:
        pos = get_current_position()
        camp = risk_state.get('campaign', {})
        if not pos or not camp or camp.get('entry_price') in (None, 0):
            return
        entry = float(camp.get('entry_price'))
        current = float(price_data.get('price'))
        side = pos.get('side')
        if entry <= 0 or current is None:
            return
        if side == 'long':
            run_up = max(0.0, (current - entry) / entry)
            drawdown = max(0.0, (entry - current) / entry)
        else:
            run_up = max(0.0, (entry - current) / entry)
            drawdown = max(0.0, (current - entry) / entry)
        risk_state['campaign']['mfe'] = max(float(camp.get('mfe', 0.0) or 0.0), run_up)
        risk_state['campaign']['mae'] = max(float(camp.get('mae', 0.0) or 0.0), drawdown)
    except Exception as e:
        log_warning(f"更新战役度量失败: {e}")


def monitor_position_exits(price_data):
    """额外退出机制监控：时间止损与结构失效退出"""
    try:
        pos = get_current_position()
        if not pos or pos.get('size', 0) <= 0:
            return False, "无持仓"

        # 更新战役状态（bars累计）
        update_campaign_state(pos)
        camp = risk_state.get('campaign', {})

        # 基础数据
        current_price = price_data.get('price')
        entry = pos.get('entry_price', 0) or 0
        side = pos.get('side')
        if entry <= 0 or current_price is None:
            return False, "入场或现价缺失"

        # 选取动态或默认时间止损/追踪参数作为最小推进参考
        trailing_cfg = risk_state.get('dynamic_trailing_cfg') or TRADE_CONFIG.get('risk_management', {}).get('trailing_stop', {})
        effective_time_stop_cfg = risk_state.get('dynamic_time_stop_cfg') or TRADE_CONFIG['risk_management'].get('time_stop', {})
        min_prog = float(effective_time_stop_cfg.get('min_progress_ratio', trailing_cfg.get('activation_ratio', 0.004)))

        # 计算利润比例
        profit_ratio = (current_price - entry) / entry if side == 'long' else (entry - current_price) / entry

        # 更新战役MAE/MFE骨架度量
        update_campaign_metrics(price_data)

        # ⏳ 时间止损
        ts_cfg = risk_state.get('dynamic_time_stop_cfg') or TRADE_CONFIG['risk_management'].get('time_stop', {})
        if ts_cfg.get('enabled', False):
            window_bars = int(ts_cfg.get('window_bars', 3))
            if int(camp.get('bars_elapsed', 0)) >= window_bars and profit_ratio < float(min_prog):
                if TRADE_CONFIG.get('test_mode'):
                    log_info(f"⏳ 测试模式时间止损：窗口{window_bars}bars未达推进 {profit_ratio:.2%} < {min_prog:.2%}")
                else:
                    log_trading(f"⏳ 时间止损触发：窗口{window_bars}bars未达推进 {profit_ratio:.2%} < {min_prog:.2%}")
                    side_close = 'sell' if side == 'long' else 'buy'
                    exchange.create_market_order(
                        TRADE_CONFIG['symbol'], side_close, pos['size'],
                        params={'reduceOnly': True, 'tag': 'time_stop_exit'}
                    )
                    log_success("✅ 时间止损退出")
                # 重置战役与追踪状态
                risk_state['campaign'] = {'start_time': 0, 'bars_elapsed': 0, 'side': None, 'entry_price': None}
                risk_state['trailing_stop_price'] = None
                risk_state['position_high_price'] = None
                risk_state['position_low_price'] = None
                risk_state['last_trailing_update_time'] = 0
                return True, "时间止损"

        # 🧱 结构失效退出
        se_cfg = risk_state.get('dynamic_structural_exit_cfg') or TRADE_CONFIG['risk_management'].get('structural_exit', {})
        if se_cfg.get('enabled', False):
            basic_trend = (price_data.get('trend_analysis') or {}).get('basic_trend', {})
            direction = basic_trend.get('direction', '震荡整理')
            clarity = basic_trend.get('clarity', '不明确')
            stability = float(basic_trend.get('stability_score', 0) or 0)
            conflict = (side == 'long' and direction == '空头趋势') or (side == 'short' and direction == '多头趋势')
            threshold = float(se_cfg.get('stability_threshold', 50))
            require_conflict = bool(se_cfg.get('require_conflict', True))

            should_exit = (stability < threshold and clarity == '不明确') or (stability < threshold and (not require_conflict or conflict)) or (require_conflict and conflict and stability < threshold)

            if should_exit:
                if TRADE_CONFIG.get('test_mode'):
                    log_info(f"🧱 测试模式结构失效退出：方向{direction} 稳定性{stability:.1f}%")
                else:
                    log_trading(f"🧱 结构失效退出：方向{direction} 稳定性{stability:.1f}%")
                    side_close = 'sell' if side == 'long' else 'buy'
                    exchange.create_market_order(
                        TRADE_CONFIG['symbol'], side_close, pos['size'],
                        params={'reduceOnly': True, 'tag': 'structural_exit'}
                    )
                    log_success("✅ 结构失效退出完成")
                # 重置战役与追踪状态
                risk_state['campaign'] = {'start_time': 0, 'bars_elapsed': 0, 'side': None, 'entry_price': None}
                risk_state['trailing_stop_price'] = None
                risk_state['position_high_price'] = None
                risk_state['position_low_price'] = None
                risk_state['last_trailing_update_time'] = 0
                return True, "结构失效退出"

        return False, "未触发额外退出"
    except Exception as e:
        log_warning(f"额外退出监控异常: {e}")
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


def get_price_data():
    """获取当前价格与趋势数据（轻量包装）。
    统一为延迟队列复查提供数据结构，与主流程一致。
    """
    try:
        return get_btc_ohlcv_enhanced()
    except Exception as e:
        log_error(f"获取价格数据失败: {e}")
        return None


def check_delayed_signals():
    """
    检查延迟执行队列中的信号，对于符合条件的信号执行交易
    """
    if 'delayed_signals' not in globals() or not globals()['delayed_signals']:
        return
    
    current_time = time.time()
    executed_signals = []
    
    for i, delayed_signal in enumerate(globals()['delayed_signals']):
        # 检查信号是否过期（超过5分钟）
        if current_time - delayed_signal['timestamp'] > 300:  # 5分钟过期
            log_info(f"⏰ 延迟信号已过期: {delayed_signal['signal']} ({delayed_signal['delay_reason']})")
            executed_signals.append(i)
            continue
        
        # 获取当前市场数据重新检查趋势
        try:
            current_price_data = get_price_data()
            basic_trend = current_price_data['trend_analysis'].get('basic_trend', {})
            current_trend_direction = basic_trend.get('direction', '震荡整理')
            current_trend_stability = basic_trend.get('stability_score', 0)
            current_price_vs_ema12_pct = basic_trend.get('price_vs_ema12_pct', 0)
            long_term = current_price_data.get('long_term_analysis', {})
            long_market_structure = long_term.get('market_structure', 'N/A')
            long_bias = long_term.get('market_bias', '中性')
            long_bias_strength = float(long_term.get('bias_strength', 0) or 0)
            
            signal_type = delayed_signal['signal']
            confidence = delayed_signal.get('confidence', 'LOW')
            
            # 重新检查趋势确认条件
            confirmed = True
            reason = "趋势确认，执行延迟信号"

            # A. 晚入场保护：距离EMA12过远（非高置信度信号）
            if abs(current_price_vs_ema12_pct) > 2.0 and confidence != 'HIGH':
                confirmed = False
                reason = f"离EMA12过远({current_price_vs_ema12_pct:+.2f}%)"

            # B. 多周期一致性：1小时趋势需同向（非高置信度信号）
            if confirmed:
                try:
                    df_1h = get_1h_ohlcv_data()
                    if df_1h is not None and len(df_1h) >= 30:
                        hour_trend = get_market_trend(df_1h)
                        hour_dir = hour_trend.get('basic_trend', {}).get('direction', None)
                        if signal_type == 'BUY' and hour_dir != '多头趋势' and confidence != 'HIGH':
                            confirmed = False
                            reason = f"1小时趋势非多头({hour_dir})"
                        if signal_type == 'SELL' and hour_dir != '空头趋势' and confidence != 'HIGH':
                            confirmed = False
                            reason = f"1小时趋势非空头({hour_dir})"
                except Exception:
                    pass

            # C. 长周期过滤：顶部/底部区域与市场偏向（非高置信度信号）
            if confirmed:
                if signal_type == 'BUY':
                    if long_market_structure == '可能顶部区域':
                        confirmed = False
                        reason = "长周期提示可能顶部区域"
                    elif long_bias == '偏空' and long_bias_strength >= 40 and confidence != 'HIGH':
                        confirmed = False
                        reason = f"长周期偏空(强度{long_bias_strength:.1f}%)"
                elif signal_type == 'SELL':
                    if long_market_structure == '可能底部区域':
                        confirmed = False
                        reason = "长周期提示可能底部区域"
                    elif long_bias == '偏多' and long_bias_strength >= 40 and confidence != 'HIGH':
                        confirmed = False
                        reason = f"长周期偏多(强度{long_bias_strength:.1f}%)"
            
            # 1. 逆趋势信号需要趋势稳定性达到85%
            if (signal_type == 'BUY' and current_trend_direction == '空头趋势') or \
               (signal_type == 'SELL' and current_trend_direction == '多头趋势'):
                if current_trend_stability < 85:
                    confirmed = False
                    reason = f"逆趋势稳定性不足: {current_trend_stability:.1f}% < 85%"
            
            # 2. 顺趋势信号需要稳定性达到60%
            elif (signal_type == 'BUY' and current_trend_direction == '多头趋势') or \
                 (signal_type == 'SELL' and current_trend_direction == '空头趋势'):
                if current_trend_stability < 60:
                    confirmed = False
                    reason = f"顺趋势稳定性不足: {current_trend_stability:.1f}% < 60%"
            
            # 3. 震荡行情中的信号需要趋势明确
            elif current_trend_direction == '震荡整理':
                confirmed = False
                reason = "仍在震荡行情中"
            
            if confirmed:
                log_info(f"✅ 执行延迟信号: {signal_type} ({reason})")
                
                # 重新计算仓位（价格可能已变化）
                current_position = get_current_position()
                new_position_size = calculate_intelligent_position(
                    {
                        'signal': delayed_signal['signal'],
                        'confidence': delayed_signal['confidence'],
                        'reason': delayed_signal['reason']
                    },
                    current_price_data,
                    current_position
                )
                
                # 使用智能交易执行，自动计算与管理仓位与风控
                execute_intelligent_trade(
                    {
                        'signal': delayed_signal['signal'],
                        'confidence': delayed_signal['confidence'],
                        'reason': delayed_signal['reason'],
                        'risk_control': delayed_signal.get('risk_control', {})
                    },
                    current_price_data
                )
                
                executed_signals.append(i)
                
            else:
                log_info(f"⏳ 延迟信号仍需等待: {signal_type} - {reason}")
                
        except Exception as e:
            log_error(f"❌ 检查延迟信号时出错: {e}")
    
    # 移除已执行或过期的信号
    if executed_signals:
        # 从后往前删除，避免索引问题
        for i in sorted(executed_signals, reverse=True):
            if i < len(globals()['delayed_signals']):
                globals()['delayed_signals'].pop(i)
        
        log_info(f"📋 延迟执行队列更新，剩余信号: {len(globals()['delayed_signals'])}")


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

        # 🆕 根据余额动态计算基础仓位 - 优化仓位管理策略
        # 基础仓位比例根据信心程度动态调整，不再是固定5%
        base_position_ratios = {
            'HIGH': 0.15,  # 高信心：15%基础仓位
            'MEDIUM': 0.08,  # 中等信心：8%基础仓位  
            'LOW': 0.03    # 低信心：3%基础仓位
        }
        
        # 获取基础仓位比例
        base_position_ratio = base_position_ratios.get(signal_data['confidence'], 0.05)
        base_usdt = usdt_balance * base_position_ratio
        log_info(f"💰 可用USDT余额: {usdt_balance:.2f}, 动态计算基础仓位: {base_usdt:.2f} USDT ({base_position_ratio:.1%})")

        # 根据信心程度调整 - 优化信心倍数
        confidence_multipliers = {
            'HIGH': config['high_confidence_multiplier'],
            'MEDIUM': config['medium_confidence_multiplier'], 
            'LOW': config['low_confidence_multiplier']
        }
        confidence_multiplier = confidence_multipliers.get(signal_data['confidence'], 1.0)

        # 根据趋势强度调整 - 优化趋势权重
        trend = price_data['trend_analysis'].get('overall', '震荡整理')
        if trend in ['强势上涨', '强势下跌']:
            trend_multiplier = config['trend_strength_multiplier']
        elif trend in ['上涨趋势', '下跌趋势']:
            trend_multiplier = 1.1  # 普通趋势略微增加
        else:
            trend_multiplier = 0.9  # 震荡行情略微减少

        # 根据RSI状态精细化调整（不再是简单的0.7倍减仓）
        rsi = price_data['technical_data'].get('rsi', 50)
        if rsi > 80 or rsi < 20:  # 极端超买超卖区域
            rsi_multiplier = 0.6  # 大幅减仓
        elif rsi > 75 or rsi < 25:  # 一般超买超卖区域
            rsi_multiplier = 0.8  # 适度减仓
        elif 40 <= rsi <= 60:  # 中性区域
            rsi_multiplier = 1.1  # 略微增加仓位
        else:
            rsi_multiplier = 1.0  # 正常区域

        # 计算建议投入USDT金额
        suggested_usdt = base_usdt * confidence_multiplier * trend_multiplier * rsi_multiplier

        # 🆕 增加市场波动性调整因子
        volatility = price_data['technical_data'].get('atr_percent', 0.01)
        if volatility > 0.02:  # 高波动市场
            volatility_multiplier = 0.8  # 减仓20%
        elif volatility < 0.005:  # 低波动市场
            volatility_multiplier = 1.2  # 加仓20%
        else:
            volatility_multiplier = 1.0
            
        suggested_usdt = suggested_usdt * volatility_multiplier

        # 风险管理：不超过总资金的指定比例
        max_usdt = usdt_balance * config['max_position_ratio']
        final_usdt = min(suggested_usdt, max_usdt)
        
        # 🆕 确保最小仓位要求（至少覆盖手续费）
        min_usdt_needed = 2.0  # 最小2u仓位确保盈利潜力
        final_usdt = max(final_usdt, min_usdt_needed)

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
        df['sma_200'] = df['close'].rolling(window=200, min_periods=1).mean()  # 添加200周期均线

        # 指数移动平均线
        df['ema_20'] = df['close'].ewm(span=20).mean()
        df['ema_12'] = df['close'].ewm(span=12).mean()
        df['ema_36'] = df['close'].ewm(span=36).mean()
        df['ema_96'] = df['close'].ewm(span=96).mean()
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

        # 📏 ATR（Average True Range）- 用于稳定的追踪止盈
        # 使用Welles Wilder平滑的近似：EWMA(alpha=1/窗口)
        prev_close = df['close'].shift(1)
        tr1 = (df['high'] - df['low']).abs()
        tr2 = (df['high'] - prev_close).abs()
        tr3 = (df['low'] - prev_close).abs()
        df['tr'] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df['atr'] = df['tr'].ewm(alpha=1/14, adjust=False).mean()

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
    """判断市场趋势 - 增强版：添加基本趋势判断逻辑和趋势确认机制"""
    try:
        current_price = df['close'].iloc[-1]

        # 多时间框架趋势分析
        # 短线/中线改为EMA体系：12/36
        trend_short = "上涨" if current_price > df['ema_12'].iloc[-1] else "下跌"
        trend_medium = "上涨" if current_price > df['ema_36'].iloc[-1] else "下跌"

        # MACD趋势
        macd_trend = "bullish" if df['macd'].iloc[-1] > df['macd_signal'].iloc[-1] else "bearish"

        # 综合趋势判断
        if trend_short == "上涨" and trend_medium == "上涨":
            overall_trend = "强势上涨"
        elif trend_short == "下跌" and trend_medium == "下跌":
            overall_trend = "强势下跌"
        else:
            overall_trend = "震荡整理"

        # 🆕 基本趋势判断逻辑
        # 1. 均线上下判断
        # 统一到EMA：12/36（保留字段名以兼容下游引用）
        above_sma20 = current_price > df['ema_12'].iloc[-1]
        above_sma50 = current_price > df['ema_36'].iloc[-1]
        # 明确EMA命名，保持与SMA命名一致值以兼容
        above_ema12 = above_sma20
        above_ema36 = above_sma50
        
        # 2. 均线排列判断
        # 使用EMA排列替代SMA：快速线（12）与中线（36）的多空关系
        sma_bullish_alignment = df['ema_12'].iloc[-1] > df['ema_36'].iloc[-1]
        sma_bearish_alignment = df['ema_12'].iloc[-1] < df['ema_36'].iloc[-1]
        
        # 3. 趋势强度判断
        # 距离度量统一到EMA：12/36
        price_vs_sma20 = (current_price - df['ema_12'].iloc[-1]) / (df['ema_12'].iloc[-1] or 1) * 100
        price_vs_sma50 = (current_price - df['ema_36'].iloc[-1]) / (df['ema_36'].iloc[-1] or 1) * 100
        
        # 4. 趋势确认机制 - 检查最近3根K线的趋势一致性
        recent_trend_consistency = 0
        for i in range(1, 4):  # 检查最近3根K线
            if len(df) > i:
                price_prev = df['close'].iloc[-i-1]
                ema12_prev = df['ema_12'].iloc[-i-1]
                if (current_price > df['ema_12'].iloc[-1]) == (price_prev > ema12_prev):
                    recent_trend_consistency += 1
        
        # 5. 趋势稳定性评分 (0-100)
        trend_stability_score = (recent_trend_consistency / 3) * 100
        
        # 6. 基本趋势方向
        if above_sma20 and above_sma50:
            basic_trend_direction = "多头趋势"
            # 趋势强度考虑稳定性
            if price_vs_sma20 > 2 and price_vs_sma50 > 2 and trend_stability_score > 70:
                trend_strength = "强"
            else:
                trend_strength = "中等"
        elif not above_sma20 and not above_sma50:
            basic_trend_direction = "空头趋势"
            if price_vs_sma20 < -2 and price_vs_sma50 < -2 and trend_stability_score > 70:
                trend_strength = "强"
            else:
                trend_strength = "中等"
        else:
            basic_trend_direction = "震荡整理"
            trend_strength = "弱"

        # 7. 趋势明确性判断 - 加入稳定性要求
        trend_clarity = "明确" if (sma_bullish_alignment or sma_bearish_alignment) and abs(price_vs_sma20) > 1 and trend_stability_score > 60 else "不明确"

        return {
            'short_term': trend_short,
            'medium_term': trend_medium,
            'macd': macd_trend,
            'overall': overall_trend,
            'rsi_level': df['rsi'].iloc[-1],
            # 🆕 新增基本趋势判断字段
            'basic_trend': {
                'direction': basic_trend_direction,
                'strength': trend_strength,
                'clarity': trend_clarity,
                'above_sma20': above_sma20,
                'above_sma50': above_sma50,
                'above_ema12': above_ema12,
                'above_ema36': above_ema36,
                'sma_bullish_alignment': sma_bullish_alignment,
                'sma_bearish_alignment': sma_bearish_alignment,
                'price_vs_sma20_pct': price_vs_sma20,
                'price_vs_sma50_pct': price_vs_sma50,
                # 新增EMA字段，明确标注
                'price_vs_ema12_pct': price_vs_sma20,
                'price_vs_ema36_pct': price_vs_sma50,
                # 🆕 新增趋势稳定性指标
                'stability_score': trend_stability_score,
                'recent_consistency': recent_trend_consistency
            }
        }
    except Exception as e:
        log_error(f"趋势分析失败: {e}")
        return {}


def analyze_4h_long_term_trend():
    """分析4小时级别的长期趋势（周线和月线级别）用于识别底部和顶部"""
    try:
        # 获取4小时K线数据
        df_4h = get_4h_ohlcv_data()
        if df_4h is None or len(df_4h) < 50:
            return {}
        
        current_price = df_4h['close'].iloc[-1]
        
        # 长期趋势分析 - 基于更长周期的移动平均线
        weekly_trend = "上涨" if current_price > df_4h['sma_50'].iloc[-1] else "下跌"
        monthly_trend = "上涨" if current_price > df_4h['sma_200'].iloc[-1] else "下跌"
        
        # 长期均线排列判断
        long_term_bullish = df_4h['sma_50'].iloc[-1] > df_4h['sma_200'].iloc[-1]
        long_term_bearish = df_4h['sma_50'].iloc[-1] < df_4h['sma_200'].iloc[-1]
        
        # 价格相对于长期均线的位置
        price_vs_weekly = (current_price - df_4h['sma_50'].iloc[-1]) / df_4h['sma_50'].iloc[-1] * 100
        price_vs_monthly = (current_price - df_4h['sma_200'].iloc[-1]) / df_4h['sma_200'].iloc[-1] * 100
        
        # 底部识别逻辑
        is_potential_bottom = False
        bottom_reasons = []
        
        # 1. 价格接近或低于长期支撑位
        if current_price <= df_4h['sma_200'].iloc[-1] * 1.05:  # 价格在月线支撑附近
            is_potential_bottom = True
            bottom_reasons.append("价格接近月线支撑")
        
        # 2. RSI超卖区域
        if df_4h['rsi'].iloc[-1] < 30:
            is_potential_bottom = True
            bottom_reasons.append("RSI超卖")
        
        # 3. 成交量放大确认
        volume_ratio = df_4h['volume'].iloc[-1] / df_4h['volume'].rolling(20).mean().iloc[-1]
        if volume_ratio > 1.5 and current_price < df_4h['close'].iloc[-2]:  # 放量下跌
            is_potential_bottom = True
            bottom_reasons.append("放量下跌可能见底")
        
        # 顶部识别逻辑
        is_potential_top = False
        top_reasons = []
        
        # 1. 价格大幅高于长期均线
        if current_price >= df_4h['sma_200'].iloc[-1] * 1.20:  # 价格高于月线20%
            is_potential_top = True
            top_reasons.append("价格大幅偏离月线")
        
        # 2. RSI超买区域
        if df_4h['rsi'].iloc[-1] > 70:
            is_potential_top = True
            top_reasons.append("RSI超买")
        
        # 3. 成交量异常放大
        if volume_ratio > 2.0 and current_price > df_4h['close'].iloc[-2]:  # 放量上涨
            is_potential_top = True
            top_reasons.append("异常放量可能见顶")
        
        # 市场结构判断
        market_structure = "健康"
        if is_potential_bottom:
            market_structure = "可能底部区域"
        elif is_potential_top:
            market_structure = "可能顶部区域"
        elif long_term_bullish and weekly_trend == "上涨" and monthly_trend == "上涨":
            market_structure = "强势上涨趋势"
        elif long_term_bearish and weekly_trend == "下跌" and monthly_trend == "下跌":
            market_structure = "强势下跌趋势"
        else:
            market_structure = "震荡整理"
        
        # 🆕 大时间段整体数据分析 - 偏空偏多判断
        bias_analysis = analyze_market_bias(df_4h)
        
        return {
            'weekly_trend': weekly_trend,
            'monthly_trend': monthly_trend,
            'long_term_bullish': long_term_bullish,
            'long_term_bearish': long_term_bearish,
            'price_vs_weekly_pct': price_vs_weekly,
            'price_vs_monthly_pct': price_vs_monthly,
            'is_potential_bottom': is_potential_bottom,
            'is_potential_top': is_potential_top,
            'bottom_reasons': bottom_reasons,
            'top_reasons': top_reasons,
            'market_structure': market_structure,
            'volume_ratio': volume_ratio,
            # 🆕 新增大时间段分析结果
            'market_bias': bias_analysis.get('bias', '中性'),
            'bias_strength': bias_analysis.get('strength', 0),
            'bias_reasons': bias_analysis.get('reasons', []),
            'trend_consistency': bias_analysis.get('trend_consistency', 0)
        }
        
    except Exception as e:
        log_error(f"长期趋势分析失败: {e}")
        return {}


def analyze_market_bias(df):
    """
    大时间段整体数据分析 - 判断未来一段时间市场偏空偏多
    结合历史数据进行前后对比分析，识别市场结构变化
    """
    try:
        if len(df) < 100:  # 需要足够的数据进行历史分析
            return {'bias': '中性', 'strength': 0, 'reasons': ['数据不足'], 'trend_consistency': 0}
        
        current_price = df['close'].iloc[-1]
        bias_score = 0
        reasons = []
        
        # 1. 价格相对于历史区间的分析
        lookback_period = min(200, len(df))
        historical_high = df['high'].tail(lookback_period).max()
        historical_low = df['low'].tail(lookback_period).min()
        historical_mid = (historical_high + historical_low) / 2
        
        # 价格在历史区间中的位置
        price_position = (current_price - historical_low) / (historical_high - historical_low) * 100
        
        if price_position > 70:
            bias_score -= 15
            reasons.append(f"价格处于历史高位({price_position:.1f}%)")
        elif price_position < 30:
            bias_score += 15
            reasons.append(f"价格处于历史低位({price_position:.1f}%)")
        
        # 2. 均线系统分析
        # 短期均线 vs 长期均线
        sma20_vs_sma50 = df['sma_20'].iloc[-1] > df['sma_50'].iloc[-1]
        sma50_vs_sma200 = df['sma_50'].iloc[-1] > df['sma_200'].iloc[-1]
        
        if sma20_vs_sma50 and sma50_vs_sma200:
            bias_score += 20  # 多头排列
            reasons.append("均线多头排列")
        elif not sma20_vs_sma50 and not sma50_vs_sma200:
            bias_score -= 20  # 空头排列
            reasons.append("均线空头排列")
        
        # 3. 趋势一致性分析
        trend_consistency = 0
        
        # 检查最近20根K线的趋势一致性
        recent_trend_direction = []
        for i in range(1, 21):
            if i < len(df):
                price_change = df['close'].iloc[-i] - df['close'].iloc[-i-1] if i < len(df)-1 else 0
                recent_trend_direction.append(1 if price_change > 0 else -1 if price_change < 0 else 0)
        
        if recent_trend_direction:
            trend_consistency = sum(recent_trend_direction) / len(recent_trend_direction)
            if abs(trend_consistency) > 0.3:
                if trend_consistency > 0:
                    bias_score += 10
                    reasons.append("近期上涨趋势明确")
                else:
                    bias_score -= 10
                    reasons.append("近期下跌趋势明确")
        
        # 4. 成交量分析
        volume_ma20 = df['volume'].rolling(20).mean().iloc[-1]
        current_volume = df['volume'].iloc[-1]
        volume_ratio = current_volume / volume_ma20 if volume_ma20 > 0 else 1
        
        if volume_ratio > 1.5:
            # 放量上涨或下跌
            price_change_pct = (current_price - df['close'].iloc[-2]) / df['close'].iloc[-2] * 100
            if price_change_pct > 1:
                bias_score += 8
                reasons.append("放量上涨，多头动能强劲")
            elif price_change_pct < -1:
                bias_score -= 8
                reasons.append("放量下跌，空头动能强劲")
        
        # 5. RSI位置分析
        rsi_value = df['rsi'].iloc[-1]
        if rsi_value > 70:
            bias_score -= 12
            reasons.append(f"RSI超买({rsi_value:.1f})")
        elif rsi_value < 30:
            bias_score += 12
            reasons.append(f"RSI超卖({rsi_value:.1f})")
        
        # 6. MACD信号分析
        macd_histogram = df['macd_histogram'].iloc[-1]
        if macd_histogram > 0:
            bias_score += 8
            reasons.append("MACD柱状图转正")
        elif macd_histogram < 0:
            bias_score -= 8
            reasons.append("MACD柱状图转负")
        
        # 7. 支撑阻力分析
        support_levels = df['close'].rolling(20).min().iloc[-1]
        resistance_levels = df['close'].rolling(20).max().iloc[-1]
        
        distance_to_support = abs(current_price - support_levels) / current_price * 100
        distance_to_resistance = abs(current_price - resistance_levels) / current_price * 100
        
        if distance_to_support < 2:
            bias_score += 10
            reasons.append("接近强支撑位")
        elif distance_to_resistance < 2:
            bias_score -= 10
            reasons.append("接近强阻力位")
        
        # 综合判断
        # 规范化偏向强度到0-100（基于各项最大权重总和近似为83）
        max_score = 83.0
        bias_strength = min(100.0, abs(bias_score) / max_score * 100.0)
        
        if bias_score > 20:
            bias = "偏多"
        elif bias_score < -20:
            bias = "偏空"
        else:
            bias = "中性"
        
        # 如果没有明确理由，添加中性说明
        if not reasons:
            reasons.append("市场处于平衡状态")
        
        return {
            'bias': bias,
            'strength': bias_strength,
            'reasons': reasons,
            'trend_consistency': trend_consistency
        }
        
    except Exception as e:
        log_error(f"市场偏向分析失败: {e}")
        return {'bias': '中性', 'strength': 0, 'reasons': ['分析错误'], 'trend_consistency': 0}


def get_4h_ohlcv_data():
    """获取4小时K线数据用于大趋势分析"""
    try:
        # 获取4小时K线数据 - 使用300根K线，提高长周期均线稳定性（约7.5周）
        ohlcv = exchange.fetch_ohlcv(TRADE_CONFIG['symbol'], '4h', limit=300)
        
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        
        # 计算技术指标
        df = calculate_technical_indicators(df)

        return df
        
    except Exception as e:
        log_error(f"获取4小时K线数据失败: {e}")
        return None


def get_1h_ohlcv_data():
    """获取1小时K线数据用于中周期一致性过滤"""
    try:
        # 获取1小时K线数据 - 使用300根K线，确保SMA200可用
        ohlcv = exchange.fetch_ohlcv(TRADE_CONFIG['symbol'], '1h', limit=300)

        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')

        # 计算技术指标
        df = calculate_technical_indicators(df)

        return df
    except Exception as e:
        log_error(f"获取1小时K线数据失败: {e}")
        return None


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
        long_term_analysis = analyze_4h_long_term_trend()  # 使用4小时数据的大趋势分析

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
                'ema_20': current_data.get('ema_20', 0),
                'sma_50': current_data.get('sma_50', 0),
                'rsi': current_data.get('rsi', 0),
                'macd': current_data.get('macd', 0),
                'macd_signal': current_data.get('macd_signal', 0),
                'macd_histogram': current_data.get('macd_histogram', 0),
                'bb_upper': current_data.get('bb_upper', 0),
                'bb_lower': current_data.get('bb_lower', 0),
                'bb_position': current_data.get('bb_position', 0),
                'volume_ratio': current_data.get('volume_ratio', 0),
                'ATR': current_data.get('ATR', 0)
            },
            'trend_analysis': trend_analysis,
            'levels_analysis': levels_analysis,
            'long_term_analysis': long_term_analysis,
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

    # 🆕 获取基本趋势数据
    basic_trend = trend.get('basic_trend', {})
    # 🆕 获取长期趋势分析数据
    long_term = price_data.get('long_term_analysis', {})
    
    analysis_text = f"""
    【技术指标分析】
    📈 移动平均线:
    - 5周期: {safe_float(tech['sma_5']):.2f} | 价格相对: {(price_data['price'] - safe_float(tech['sma_5'])) / safe_float(tech['sma_5']) * 100:+.2f}%
    - 12周期EMA: {safe_float(tech.get('ema_12', 0)):.2f} | 价格相对: {(price_data['price'] - safe_float(tech.get('ema_12', 0))) / (safe_float(tech.get('ema_12', 0)) or 1) * 100:+.2f}%
    - 36周期EMA: {safe_float(tech.get('ema_36', 0)):.2f} | 价格相对: {(price_data['price'] - safe_float(tech.get('ema_36', 0))) / (safe_float(tech.get('ema_36', 0)) or 1) * 100:+.2f}%

    🎯 趋势分析:
    - 短期趋势: {trend.get('short_term', 'N/A')}
    - 中期趋势: {trend.get('medium_term', 'N/A')}
    - 整体趋势: {trend.get('overall', 'N/A')}
    - MACD方向: {trend.get('macd', 'N/A')}
    
    🎯 【基本趋势判断】:
    - 趋势方向: {basic_trend.get('direction', 'N/A')}
    - 趋势强度: {basic_trend.get('strength', 'N/A')}
    - 趋势明确性: {basic_trend.get('clarity', 'N/A')}
    - 趋势稳定性: {basic_trend.get('stability_score', 0):.1f}% ({basic_trend.get('recent_consistency', 0)}/3 K线一致)
    - 价格在快速均线(EMA12): {'上方' if basic_trend.get('above_ema12', False) else '下方'}
    - 价格在中线(EMA36): {'上方' if basic_trend.get('above_ema36', False) else '下方'}
    - 相对EMA12: {basic_trend.get('price_vs_ema12_pct', 0):+.2f}%
    - 相对EMA36: {basic_trend.get('price_vs_ema36_pct', 0):+.2f}%

    🎯 【长期趋势与市场结构分析】:
    - 周线趋势: {long_term.get('weekly_trend', 'N/A')}
    - 月线趋势: {long_term.get('monthly_trend', 'N/A')}
    - 长期均线排列: {'多头' if long_term.get('long_term_bullish', False) else '空头' if long_term.get('long_term_bearish', False) else '中性'}
    - 价格相对周线: {long_term.get('price_vs_weekly_pct', 0):+.2f}%
    - 价格相对月线: {long_term.get('price_vs_monthly_pct', 0):+.2f}%
    - 市场结构: {long_term.get('market_structure', 'N/A')}
    
    🎯 【大时间段整体分析 - 未来偏向判断】:
    - 市场偏向: {long_term.get('market_bias', 'N/A')} (强度: {long_term.get('bias_strength', 0):.1f}%)
    - 趋势一致性: {long_term.get('trend_consistency', 0):.2f}
    - 分析理由: {', '.join(long_term.get('bias_reasons', ['暂无']))}
    - 成交量比率: {long_term.get('volume_ratio', 0):.2f}x
    
    🎯 【底部顶部识别】:
    - 潜在底部: {'是' if long_term.get('is_potential_bottom', False) else '否'} {', '.join(long_term.get('bottom_reasons', []))}
    - 潜在顶部: {'是' if long_term.get('is_potential_top', False) else '否'} {', '.join(long_term.get('top_reasons', []))}

    📊 动量指标:
    - RSI: {safe_float(tech['rsi']):.2f} ({'超买' if safe_float(tech['rsi']) > 70 else '超卖' if safe_float(tech['rsi']) < 30 else '中性'})
    - MACD: {safe_float(tech['macd']):.4f}
    - 信号线: {safe_float(tech['macd_signal']):.4f}
    - ATR(波动率): {safe_float(tech.get('ATR', 0)):.2f}

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


def normalize_confidence(conf):
    """将置信度统一规范为'HIGH'|'MEDIUM'|'LOW'"""
    if conf is None:
        return "MEDIUM"
    if isinstance(conf, (int, float)):
        try:
            val = float(conf)
            if val >= 80:
                return "HIGH"
            if val >= 60:
                return "MEDIUM"
            return "LOW"
        except Exception:
            pass
    s = str(conf).strip().lower()
    mapping = {
        'high': 'HIGH', 'h': 'HIGH', '高': 'HIGH', '強': 'HIGH', '强': 'HIGH', '🔥': 'HIGH',
        'medium': 'MEDIUM', 'm': 'MEDIUM', '中': 'MEDIUM', '⚡': 'MEDIUM',
        'low': 'LOW', 'l': 'LOW', '低': 'LOW', '弱': 'LOW', '💡': 'LOW'
    }
    return mapping.get(s, "MEDIUM")


def create_fallback_signal(price_data):
    """创建备用交易信号"""
    return {
        "signal": "HOLD",
        "reason": "因技术分析暂时不可用，采取保守策略",
        "confidence": "LOW",
        "is_fallback": True
    }


def analyze_with_bailian(price_data):
    """使用阿里云百炼分析市场并生成交易信号（增强版）"""
    global risk_state

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
    你是一个专业的加密货币缠论分析师。擅长多时间周期分析和量化交易策略，专注于短线的高胜率机会。请基于以下BTC/USDT {TRADE_CONFIG['timeframe']}周期数据进行分析：

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
    7. **趋势结构认知**:
    - 以均线位置和结构稳定度理解趋势，不以均线交叉直接下指令
    - 价格突破关键支撑/阻力位作为结构变化的重要依据 

    【均线角色与使用原则】
    - EMA20 反映波段强弱；距离过大视为晚入场风险，用于噪音过滤与延迟执行。
    - EMA50 反映多空力量；与 EMA20 是否同向决定力量一致性，用于仓位调制。
    - EMA100 反映趋势强弱；与 EMA50 是否同向决定趋势可信度，用于门槛判定。
    - EMA200 反映牛熊；逆大级别（牛市做空/熊市做多）需更高确认与更保守风控。
    - 均线不直接产生交易信号，仅作为“噪音过滤 + 执行约束 + 风控联动”的依据。
    - 若出现贴线绕线（价格在短期均线附近反复穿越）且趋势稳定性不足，优先延迟或降仓。


    【当前技术状况分析】
    - 整体趋势: {price_data['trend_analysis'].get('overall', 'N/A')}
    - 短期趋势: {price_data['trend_analysis'].get('short_term', 'N/A')} 
    - RSI状态: {price_data['technical_data'].get('rsi', 0):.1f} ({'超买' if price_data['technical_data'].get('rsi', 0) > 70 else '超卖' if price_data['technical_data'].get('rsi', 0) < 30 else '中性'})
    - MACD方向: {price_data['trend_analysis'].get('macd', 'N/A')}
    
    【基本趋势判断 - 必须重点参考】
    - 基本趋势方向: {price_data['trend_analysis'].get('basic_trend', {}).get('direction', 'N/A')}
    - 趋势强度: {price_data['trend_analysis'].get('basic_trend', {}).get('strength', 'N/A')}
    - 趋势明确性: {price_data['trend_analysis'].get('basic_trend', {}).get('clarity', 'N/A')}
    - 价格相对EMA12: {price_data['trend_analysis'].get('basic_trend', {}).get('price_vs_ema12_pct', 0):+.2f}%
    - 价格相对EMA36: {price_data['trend_analysis'].get('basic_trend', {}).get('price_vs_ema36_pct', 0):+.2f}%

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

    【长期市场结构分析 - 新增要求】
    - **底部区域识别**: 当价格接近月线支撑、RSI超卖、成交量放大时，可能是底部区域，应谨慎做空，考虑分批建仓
    - **顶部区域识别**: 当价格大幅偏离月线、RSI超买、成交量异常放大时，可能是顶部区域，应谨慎做多，考虑减仓或止盈
    - **趋势延续**: 长期趋势明确时，短期回调可能是加仓机会而不是反转信号
    - **趋势反转**: 长期趋势与短期趋势出现明显背离时，需要警惕趋势反转的可能性

    【重要】请基于技术分析做出明确判断，避免因过度谨慎而错过趋势行情！
    【特别关注】必须结合长期市场结构分析，不要只看短期K线波动！

    【分析要求】
    基于以上分析，请给出明确的交易信号

    请用以下JSON格式回复：
    {{
        "signal": "BUY|SELL|HOLD",
        "reason": "简要分析理由(包含趋势判断和技术依据)",
        "confidence": "HIGH|MEDIUM|LOW",
        "risk_control": {{
            "trailing_stop": {{
                "atr_multiplier": 数值(可选),
                "activation_ratio": 数值(可选),
                "break_even_buffer_ratio": 数值(可选),
                "min_step_ratio": 数值(可选),
                "update_cooldown": 数值(秒, 可选),
                "aggressiveness": "aggressive|balanced|conservative"(可选)
            }},
            "noise_filter": {{
                "enabled": true|false(可选),
                "ema20_distance_pct_max": 数值(可选),
                "ema50_distance_pct_max": 数值(可选),
                "ema100_distance_pct_max": 数值(可选),
                "ema200_distance_pct_max": 数值(可选),
                "stability_min": 数值(可选),
                "alignment_required": true|false(可选),
                "regime": "trend|range|volatile"(可选)
            }},
            "execution_modulation": {{
                "size_multiplier_template": "aggressive|balanced|conservative"(可选),
                "trailing_template": "aggressive|balanced|conservative"(可选),
                "time_stop_template": "short|normal|long"(可选),
                "structural_exit_template": "strict|normal|loose"(可选)
            }}
        }}
    }}
    """

    try:
        response = bailian_client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system",
                 "content": f"你是一位专业的市场分析助手，专注于{TRADE_CONFIG['timeframe']}周期的趋势结构分析与风险边界建议。请结合K线形态与技术指标做出结构化判断，并严格遵循JSON格式，避免情绪化及非结构化输出。"},
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

        # 验证必需字段（去除固定止盈止损，改为仅需核心字段）
        required_fields = ['signal', 'reason', 'confidence']
        if not all(field in signal_data for field in required_fields):
            signal_data = create_fallback_signal(price_data)

        # 统一置信度格式
        signal_data['confidence'] = normalize_confidence(signal_data.get('confidence'))

        # 保存信号到历史记录
        signal_data['timestamp'] = price_data['timestamp']
        signal_history.append(signal_data)
        if len(signal_history) > 30:
            signal_history.pop(0)

        # 🆕 融合AI建议的追踪止盈参数（若提供），否则依据置信度动态调整
        try:
            rc = signal_data.get('risk_control', {}) or {}
            ts = rc.get('trailing_stop', {}) or {}

            def sf(v, default=None):
                try:
                    return float(v)
                except Exception:
                    return default

            dynamic_cfg = {}
            # 参数范围钳制
            def clamp(name, val):
                bounds = {
                    'atr_multiplier': (1.5, 5.0),
                    'activation_ratio': (0.001, 0.02),
                    'break_even_buffer_ratio': (0.0, 0.01),
                    'min_step_ratio': (0.0005, 0.01),
                    'update_cooldown': (30, 600)
                }
                if val is None:
                    return None
                lo, hi = bounds[name]
                try:
                    return max(lo, min(hi, val))
                except Exception:
                    return None

            if ts:
                dynamic_cfg = {
                    'atr_multiplier': clamp('atr_multiplier', sf(ts.get('atr_multiplier'), None)),
                    'activation_ratio': clamp('activation_ratio', sf(ts.get('activation_ratio'), None)),
                    'break_even_buffer_ratio': clamp('break_even_buffer_ratio', sf(ts.get('break_even_buffer_ratio'), None)),
                    'min_step_ratio': clamp('min_step_ratio', sf(ts.get('min_step_ratio'), None)),
                    'update_cooldown': int(clamp('update_cooldown', sf(ts.get('update_cooldown'), None))) if ts.get('update_cooldown') is not None else None,
                }
                # 清理掉None，保留有效值
                dynamic_cfg = {k: v for k, v in dynamic_cfg.items() if v is not None}

                # 基于aggressiveness提供默认模板
                aggr = (ts.get('aggressiveness') or '').lower()
                templates = {
                    'aggressive': {
                        'atr_multiplier': 2.0,
                        'activation_ratio': 0.003,
                        'break_even_buffer_ratio': 0.0008,
                        'min_step_ratio': 0.0015,
                        'update_cooldown': 90,
                    },
                    'balanced': {
                        'atr_multiplier': 2.5,
                        'activation_ratio': 0.004,
                        'break_even_buffer_ratio': 0.001,
                        'min_step_ratio': 0.002,
                        'update_cooldown': 120,
                    },
                    'conservative': {
                        'atr_multiplier': 3.0,
                        'activation_ratio': 0.005,
                        'break_even_buffer_ratio': 0.0015,
                        'min_step_ratio': 0.0025,
                        'update_cooldown': 150,
                    }
                }
                if aggr in templates:
                    for k, v in templates[aggr].items():
                        dynamic_cfg.setdefault(k, v)
                # 最终再进行一次范围钳制
                for k in list(dynamic_cfg.keys()):
                    dynamic_cfg[k] = clamp(k, dynamic_cfg[k]) if k != 'update_cooldown' else int(clamp('update_cooldown', dynamic_cfg[k]))
            else:
                # 若未提供，依据AI置信度设置模板
                conf = signal_data.get('confidence', 'MEDIUM')
                mapping = {
                    'HIGH': {
                        'atr_multiplier': 3.0,
                        'activation_ratio': 0.005,
                        'break_even_buffer_ratio': 0.0015,
                        'min_step_ratio': 0.0025,
                        'update_cooldown': 150,
                    },
                    'MEDIUM': {
                        'atr_multiplier': 2.5,
                        'activation_ratio': 0.004,
                        'break_even_buffer_ratio': 0.001,
                        'min_step_ratio': 0.002,
                        'update_cooldown': 120,
                    },
                    'LOW': {
                        'atr_multiplier': 2.0,
                        'activation_ratio': 0.003,
                        'break_even_buffer_ratio': 0.0008,
                        'min_step_ratio': 0.0015,
                        'update_cooldown': 90,
                    },
                }
                dynamic_cfg = mapping.get(conf, mapping['MEDIUM'])
                # 范围钳制
                for k in list(dynamic_cfg.keys()):
                    dynamic_cfg[k] = clamp(k, dynamic_cfg[k]) if k != 'update_cooldown' else int(clamp('update_cooldown', dynamic_cfg[k]))

            # 写入动态追踪参数供止盈函数使用
            if isinstance(dynamic_cfg, dict) and dynamic_cfg:
                risk_state['dynamic_trailing_cfg'] = dynamic_cfg
                log_info(f"🧪 动态追踪参数: {dynamic_cfg}")
        except Exception as e:
            log_warning(f"动态追踪参数处理失败: {e}")

        # 🆕 融合AI建议的噪音过滤与执行模板（若提供）
        try:
            rc = signal_data.get('risk_control', {}) or {}
            # 动态均线噪音过滤配置
            nf = rc.get('noise_filter', {}) or {}
            def sf(v, default=None):
                try:
                    return float(v)
                except Exception:
                    return default
            dynamic_ma_filter_cfg = {
                'ema20_distance_pct_max': sf(nf.get('ema20_distance_pct_max'), None),
                'ema50_distance_pct_max': sf(nf.get('ema50_distance_pct_max'), None),
                'ema100_distance_pct_max': sf(nf.get('ema100_distance_pct_max'), None),
                'ema200_distance_pct_max': sf(nf.get('ema200_distance_pct_max'), None),
                'stability_min': sf(nf.get('stability_min'), None),
                'alignment_required': bool(nf.get('alignment_required')) if nf.get('alignment_required') is not None else None,
                'enabled': bool(nf.get('enabled')) if nf.get('enabled') is not None else None,
                'regime': (nf.get('regime') or None)
            }
            dynamic_ma_filter_cfg = {k: v for k, v in dynamic_ma_filter_cfg.items() if v is not None}
            if dynamic_ma_filter_cfg:
                risk_state['dynamic_ma_filter_cfg'] = dynamic_ma_filter_cfg
                log_info(f"🧪 动态均线过滤参数: {dynamic_ma_filter_cfg}")

            # 执行调制模板：映射到时间止损与结构退出动态覆盖
            emod = rc.get('execution_modulation', {}) or {}
            ts_tpl = (emod.get('time_stop_template') or '').lower()
            se_tpl = (emod.get('structural_exit_template') or '').lower()
            ts_templates = {
                'short': {'window_bars': 2, 'min_progress_ratio': 0.003, 'close_all': True},
                'normal': {'window_bars': 3, 'min_progress_ratio': 0.004, 'close_all': True},
                'long': {'window_bars': 4, 'min_progress_ratio': 0.005, 'close_all': True},
            }
            se_templates = {
                'strict': {'stability_threshold': 60, 'require_conflict': False, 'enabled': True},
                'normal': {'stability_threshold': 50, 'require_conflict': True, 'enabled': True},
                'loose': {'stability_threshold': 40, 'require_conflict': True, 'enabled': True},
            }
            if ts_tpl in ts_templates:
                risk_state['dynamic_time_stop_cfg'] = ts_templates[ts_tpl]
                log_info(f"🧪 动态时间止损模板: {ts_tpl} → {ts_templates[ts_tpl]}")
            if se_tpl in se_templates:
                risk_state['dynamic_structural_exit_cfg'] = se_templates[se_tpl]
                log_info(f"🧪 动态结构退出模板: {se_tpl} → {se_templates[se_tpl]}")
        except Exception as e:
            log_warning(f"动态噪音过滤/执行模板处理失败: {e}")

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

    # 统一置信度格式，确保后续趋势过滤与仓位逻辑一致
    signal_data['confidence'] = normalize_confidence(signal_data.get('confidence'))

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

    # 🆕 趋势确认机制 - 防止反转前夕的错误交易
    def check_trend_confirmation(price_data, signal_data):
        """
        趋势确认检查：确保趋势信号稳定且一致
        返回 (confirmed, reason)
        """
        basic_trend = price_data['trend_analysis'].get('basic_trend', {})
        trend_direction = basic_trend.get('direction', '震荡整理')
        trend_clarity = basic_trend.get('clarity', '不明确')
        trend_stability = basic_trend.get('stability_score', 0)
        recent_consistency = basic_trend.get('recent_consistency', 0)
        
        signal_type = signal_data['signal']
        confidence = signal_data['confidence']
        
        # 1. 趋势稳定性检查
        if trend_stability < 60:  # 稳定性低于60%
            if confidence != 'HIGH':
                return False, f"趋势稳定性不足({trend_stability:.1f}%)，非高信心信号"
        
        # 2. 近期一致性检查
        if recent_consistency < 2:  # 最近3根K线中至少2根确认趋势
            if confidence != 'HIGH':
                return False, f"近期趋势一致性不足({recent_consistency}/3)，非高信心信号"
        
        # 3. 趋势方向确认
        if trend_direction == '震荡整理' and trend_clarity == '不明确':
            if confidence != 'HIGH':
                return False, "震荡行情中非高信心信号"
        
        # 4. 逆趋势信号额外确认
        if (signal_type == 'BUY' and trend_direction == '空头趋势') or \
           (signal_type == 'SELL' and trend_direction == '多头趋势'):
            # 逆趋势操作需要更高的确认标准
            if trend_stability < 75 or recent_consistency < 3:
                return False, f"逆趋势操作需要更高稳定性(≥75%)和完全一致性，当前稳定性:{trend_stability:.1f}%，一致性:{recent_consistency}/3"
        
        # 5. 顺趋势信号确认
        if (signal_type == 'BUY' and trend_direction == '多头趋势') or \
           (signal_type == 'SELL' and trend_direction == '空头趋势'):
            # 顺趋势操作可以放宽，但仍需基本确认
            if trend_stability < 40:
                return False, f"顺趋势但稳定性过低({trend_stability:.1f}%)"
        
        return True, "趋势确认通过"
    
    # 执行趋势确认检查
    if signal_data['signal'] != 'HOLD':
        confirmed, confirm_reason = check_trend_confirmation(price_data, signal_data)
        if not confirmed:
            log_warning(f"🔒 趋势确认失败: {confirm_reason}")
            return
        log_info(f"✅ 趋势确认通过: {confirm_reason}")

    # 🆕 趋势过滤检查 - 增强版：添加趋势稳定性检查和反转保护
    basic_trend = price_data['trend_analysis'].get('basic_trend', {})
    trend_direction = basic_trend.get('direction', '震荡整理')
    trend_clarity = basic_trend.get('clarity', '不明确')
    trend_stability = basic_trend.get('stability_score', 0)
    
    # 趋势过滤规则
    if signal_data['signal'] != 'HOLD':
        # 1. 趋势不明确时谨慎操作
        if trend_clarity == '不明确':
            if signal_data['confidence'] != 'HIGH':
                log_warning(f"🔒 趋势不明确，非高信心信号，跳过交易")
                return
        
        # 2. 趋势稳定性检查 - 新增
        if trend_stability < 50:  # 稳定性低于50%
            if signal_data['confidence'] != 'HIGH':
                log_warning(f"🔒 趋势稳定性不足({trend_stability:.1f}%)，非高信心信号，跳过交易")
                return
        
        # 3. 逆趋势操作需要高信心和趋势稳定性
        if (signal_data['signal'] == 'BUY' and trend_direction == '空头趋势') or \
           (signal_data['signal'] == 'SELL' and trend_direction == '多头趋势'):
            # 逆趋势操作需要更高的稳定性要求
            if signal_data['confidence'] != 'HIGH' or trend_stability < 70:
                log_warning(f"🔒 逆趋势操作需要高信心和趋势稳定性(≥70%)，当前信心: {signal_data['confidence']}, 稳定性: {trend_stability:.1f}%")
                return
        
        # 4. 顺趋势操作可以放宽要求，但仍需基本稳定性
        if (signal_data['signal'] == 'BUY' and trend_direction == '多头趋势') or \
           (signal_data['signal'] == 'SELL' and trend_direction == '空头趋势'):
            if trend_stability < 40:  # 顺趋势但稳定性太低
                log_warning(f"⚠️ 顺趋势但稳定性不足({trend_stability:.1f}%)，谨慎操作")
            else:
                log_info(f"✅ 顺趋势操作，趋势方向: {trend_direction}, 稳定性: {trend_stability:.1f}%")
    
    log_info(f"📊 基本趋势判断: {trend_direction} ({trend_clarity}), 稳定性: {trend_stability:.1f}%")

    current_position = get_current_position()

    # 🧹 均线噪音过滤：均线用于过滤噪音，不直接给出信号
    def is_noise_zone(price_data):
        basic_trend = price_data.get('trend_analysis', {}).get('basic_trend', {})
        dv_ema12 = abs(float(basic_trend.get('price_vs_ema12_pct', 0) or 0))
        dv_ema36 = abs(float(basic_trend.get('price_vs_ema36_pct', 0) or 0))
        cfg_static = TRADE_CONFIG.get('risk_management', {}).get('moving_average_filter', {})
        cfg_dynamic = risk_state.get('dynamic_ma_filter_cfg') or {}
        # 优先使用动态配置的启用开关，其次静态
        enabled = cfg_dynamic.get('enabled', cfg_static.get('enabled', False))
        if not enabled:
            return False, "均线噪音过滤未启用"
        # 动态键保持兼容（ema20/ema50），静态回退改为EMA12/EMA36配置
        thr_ema12 = float(cfg_dynamic.get('ema20_distance_pct_max', cfg_static.get('band_ema12_pct', 0.6)))
        thr_ema36 = float(cfg_dynamic.get('ema50_distance_pct_max', cfg_static.get('band_ema36_pct', 1.0)))
        within_ema12 = dv_ema12 <= thr_ema12
        within_ema36 = dv_ema36 <= thr_ema36
        noise = within_ema12 and within_ema36
        # 根据稳定性与趋势明确性叠加过滤（动态建议）
        stability_min = cfg_dynamic.get('stability_min')
        trend_clarity = basic_trend.get('clarity', '不明确')
        alignment_required = bool(cfg_dynamic.get('alignment_required')) if cfg_dynamic.get('alignment_required') is not None else False

        reason_core = f"EMA12距:{dv_ema12:.2f}%≤{thr_ema12:.2f}%, EMA36距:{dv_ema36:.2f}%≤{thr_ema36:.2f}%"
        extra_reasons = []
        if stability_min is not None:
            st = float(basic_trend.get('stability_score', 0) or 0)
            if st < stability_min:
                noise = True
                extra_reasons.append(f"稳定性不足({st:.1f}%<{stability_min:.1f}%)")
        if alignment_required and trend_clarity == '不明确':
            noise = True
            extra_reasons.append("趋势明确性不足")

        if noise:
            reason = f"价格处于噪音带 | {reason_core}"
            if extra_reasons:
                reason += " | " + ", ".join(extra_reasons)
        else:
            reason = f"价格脱离噪音带 | {reason_core}"
        return noise, reason
        return noise, reason

    if signal_data['signal'] != 'HOLD':
        noise, noise_reason = is_noise_zone(price_data)
        maf_cfg = TRADE_CONFIG.get('risk_management', {}).get('moving_average_filter', {})
        if noise:
            # 过滤非高置信度信号，或当配置要求时也可对所有信号过滤
            only_non_high = bool(maf_cfg.get('apply_to_non_high_confidence_only', True))
            if (only_non_high and signal_data['confidence'] != 'HIGH') or (not only_non_high):
                log_warning(f"🧹 均线噪音过滤: {noise_reason}，跳过交易")
                return

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
        
        # 计算所需保证金（修正：合约乘数应该在分子中）
        required_margin = (position_size * price_data['price'] * TRADE_CONFIG['contract_size']) / TRADE_CONFIG['leverage']
        
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

    # 🆕 延迟执行检查 - 防止在反转前夕交易
    def check_delay_execution(signal_data, price_data):
        """
        延迟执行检查：对于某些信号类型，等待额外确认
        返回 (should_execute, reason)
        """
        basic_trend = price_data['trend_analysis'].get('basic_trend', {})
        trend_direction = basic_trend.get('direction', '震荡整理')
        trend_stability = basic_trend.get('stability_score', 0)
        price_vs_ema12_pct = basic_trend.get('price_vs_ema12_pct', 0)
        long_term = price_data.get('long_term_analysis', {})
        long_market_structure = long_term.get('market_structure', 'N/A')
        long_bias = long_term.get('market_bias', '中性')
        long_bias_strength = float(long_term.get('bias_strength', 0) or 0)
        
        signal_type = signal_data['signal']
        confidence = signal_data['confidence']

        # 0. 晚入场保护：价格远离20EMA过多（±2%），降低非高置信度信号的执行
        if abs(price_vs_ema12_pct) > 2.0 and confidence != 'HIGH':
            return False, f"离EMA12过远({price_vs_ema12_pct:+.2f}%)，等待回调"

        # 0.1 多周期一致性：1小时趋势需同向（对非高置信度信号生效）
        hour_trend_dir = None
        try:
            df_1h = get_1h_ohlcv_data()
            if df_1h is not None and len(df_1h) >= 30:
                hour_trend = get_market_trend(df_1h)
                hour_trend_dir = hour_trend.get('basic_trend', {}).get('direction', None)
        except Exception:
            hour_trend_dir = None
        if hour_trend_dir:
            if signal_type == 'BUY' and hour_trend_dir != '多头趋势' and confidence != 'HIGH':
                return False, f"1小时趋势非多头({hour_trend_dir})，延迟执行"
            if signal_type == 'SELL' and hour_trend_dir != '空头趋势' and confidence != 'HIGH':
                return False, f"1小时趋势非空头({hour_trend_dir})，延迟执行"

        # 0.2 长周期过滤：顶部/底部区域与市场偏向（对非高置信度信号生效）
        if signal_type == 'BUY':
            if long_market_structure == '可能顶部区域':
                return False, "长周期提示可能顶部区域，暂缓做多"
            if long_bias == '偏空' and long_bias_strength >= 40 and confidence != 'HIGH':
                return False, f"长周期偏空(强度{long_bias_strength:.1f}%)，延迟做多"
        elif signal_type == 'SELL':
            if long_market_structure == '可能底部区域':
                return False, "长周期提示可能底部区域，暂缓做空"
            if long_bias == '偏多' and long_bias_strength >= 40 and confidence != 'HIGH':
                return False, f"长周期偏多(强度{long_bias_strength:.1f}%)，延迟做空"
        
        # 1. 逆趋势操作需要延迟执行（等待趋势确认）
        if (signal_type == 'BUY' and trend_direction == '空头趋势') or \
           (signal_type == 'SELL' and trend_direction == '多头趋势'):
            if trend_stability < 80:  # 逆趋势但稳定性不够高
                log_info(f"⏳ 逆趋势操作，等待趋势进一步确认 (稳定性: {trend_stability:.1f}%)")
                return False, "逆趋势操作需要更高稳定性确认"
        
        # 2. 低稳定性趋势中的操作需要延迟
        if trend_stability < 50 and confidence != 'HIGH':
            log_info(f"⏳ 低稳定性趋势，等待确认 (稳定性: {trend_stability:.1f}%)")
            return False, "低稳定性趋势需要额外确认"
        
        # 3. 震荡行情中的操作需要谨慎
        if trend_direction == '震荡整理' and confidence != 'HIGH':
            log_info("⏳ 震荡行情，等待趋势明确")
            return False, "震荡行情需要趋势明确"
        
        return True, "立即执行"
    
    # 执行延迟执行检查
    execute_now, delay_reason = check_delay_execution(signal_data, price_data)
    if not execute_now:
        log_warning(f"⏸️ 延迟执行: {delay_reason}")
        
        # 将信号加入延迟执行队列
        if 'delayed_signals' not in globals():
            globals()['delayed_signals'] = []
        
        delayed_signal = {
            'signal': signal_data['signal'],
            'confidence': signal_data['confidence'],
            'reason': signal_data['reason'],
            'price_data': price_data,
            'position_size': position_size,
            'timestamp': time.time(),
            'delay_reason': delay_reason
        }
        
        globals()['delayed_signals'].append(delayed_signal)
        log_info(f"📋 信号已加入延迟执行队列，当前队列长度: {len(globals()['delayed_signals'])}")
        
        # 检查是否有可以执行的延迟信号
        check_delayed_signals()
        return
    
    log_info("✅ 延迟执行检查通过，立即执行交易")

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

    # ⏳🧱 额外退出机制：时间止损与结构失效退出
    try:
        monitor_position_exits(price_data)
    except Exception as e:
        log_warning(f"退出机制监控异常: {e}")

    # 🎯 统一：ATR稳定追踪止盈监控
    try:
        auto_stop_profit_loss(price_data)
    except Exception as e:
        log_warning(f"追踪止盈监控异常: {e}")

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