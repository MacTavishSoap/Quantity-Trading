#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Bot 测试脚本
用于验证Telegram Bot配置和功能
"""

import os
import asyncio
from dotenv import load_dotenv
from telegram import Bot
from telegram.error import TelegramError
from datetime import datetime

# 加载环境变量
load_dotenv()

# Telegram配置
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
TELEGRAM_ENABLED = os.getenv('TELEGRAM_ENABLED', 'false').lower() == 'true'

def test_telegram_config():
    """测试Telegram配置"""
    print("🔍 检查Telegram配置...")
    
    if not TELEGRAM_ENABLED:
        print("❌ TELEGRAM_ENABLED 未启用")
        return False
    
    if not TELEGRAM_BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN 未配置")
        return False
    
    if not TELEGRAM_CHAT_ID:
        print("❌ TELEGRAM_CHAT_ID 未配置")
        return False
    
    print("✅ Telegram配置检查通过")
    print(f"   Bot Token: {TELEGRAM_BOT_TOKEN[:10]}...")
    print(f"   Chat ID: {TELEGRAM_CHAT_ID}")
    return True

async def test_bot_connection():
    """测试Bot连接"""
    print("\n🔗 测试Bot连接...")
    
    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        bot_info = await bot.get_me()
        print(f"✅ Bot连接成功")
        print(f"   Bot名称: {bot_info.first_name}")
        print(f"   Bot用户名: @{bot_info.username}")
        return bot
    except Exception as e:
        print(f"❌ Bot连接失败: {e}")
        return None

async def test_send_message(bot):
    """测试发送消息"""
    print("\n📤 测试发送消息...")
    
    test_message = f"""
🧪 <b>Telegram Bot 测试消息</b>

✅ 如果您收到此消息，说明配置正确！

🤖 <b>Bot信息:</b>
• Token: {TELEGRAM_BOT_TOKEN[:10]}...
• Chat ID: {TELEGRAM_CHAT_ID}

⏰ <b>测试时间:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

🎉 准备开始接收交易信号！
"""
    
    try:
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=test_message,
            parse_mode='HTML'
        )
        print("✅ 测试消息发送成功")
        return True
    except Exception as e:
        print(f"❌ 消息发送失败: {e}")
        return False

async def test_trading_signal_format():
    """测试交易信号格式"""
    print("\n📊 测试交易信号格式...")
    
    # 模拟交易信号数据
    signal_data = {
        'signal': 'BUY',
        'confidence': 85,
        'reasoning': '技术指标显示强烈买入信号，RSI超卖，MACD金叉'
    }
    
    price_data = {
        'price': 45000.50,
        'price_change': 2.5
    }
    
    position_size = 0.05
    
    signal_message = f"""
🎯 <b>交易信号</b>

📈 <b>信号:</b> {signal_data['signal']}
🎯 <b>置信度:</b> {signal_data['confidence']}%
💰 <b>仓位:</b> {position_size:.2f} 张

💡 <b>分析:</b>
{signal_data['reasoning']}

📊 <b>价格信息:</b>
• 当前价格: ${price_data['price']:,.2f}
• 价格变化: {price_data['price_change']:+.2f}%

⏰ <b>时间:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
    
    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=signal_message,
            parse_mode='HTML'
        )
        print("✅ 交易信号格式测试成功")
        return True
    except Exception as e:
        print(f"❌ 交易信号发送失败: {e}")
        return False

async def test_balance_format():
    """测试余额格式"""
    print("\n💰 测试余额格式...")
    
    # 模拟余额数据
    balance_info = {
        'usdt': 1000.50,
        'position_value': 500.25,
        'total': 1500.75
    }
    
    balance_message = f"""
💰 <b>账户余额</b>

💵 <b>可用USDT:</b> {balance_info['usdt']:,.2f}
📊 <b>持仓价值:</b> {balance_info['position_value']:,.2f} USDT
💎 <b>总资产:</b> {balance_info['total']:,.2f} USDT

⏰ <b>更新时间:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
    
    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=balance_message,
            parse_mode='HTML'
        )
        print("✅ 余额格式测试成功")
        return True
    except Exception as e:
        print(f"❌ 余额消息发送失败: {e}")
        return False

async def main():
    """主测试函数"""
    print("🚀 Telegram Bot 测试开始\n")
    
    # 1. 检查配置
    if not test_telegram_config():
        print("\n❌ 配置检查失败，请检查.env文件")
        return
    
    # 2. 测试连接
    bot = await test_bot_connection()
    if not bot:
        print("\n❌ Bot连接失败，请检查Token")
        return
    
    # 3. 测试基本消息
    if not await test_send_message(bot):
        print("\n❌ 消息发送失败，请检查Chat ID")
        return
    
    # 4. 测试交易信号格式
    await test_trading_signal_format()
    
    # 5. 测试余额格式
    await test_balance_format()
    
    print("\n🎉 所有测试完成！")
    print("如果您在Telegram中收到了测试消息，说明配置正确。")
    print("现在可以启动交易机器人开始接收实时播报了！")

if __name__ == "__main__":
    if not TELEGRAM_ENABLED:
        print("❌ Telegram功能未启用")
        print("请在.env文件中设置 TELEGRAM_ENABLED=true")
    else:
        asyncio.run(main())