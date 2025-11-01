#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
获取Telegram Chat ID的辅助脚本
"""

import requests
import json

# 您的Bot Token
BOT_TOKEN = "8593405195:AAHjfJ9MsHH2NKKKMAE3UcX0Wj5Zjblbfso"

def get_chat_id():
    """获取Chat ID"""
    print("🔍 正在获取Chat ID...")
    print("请确保您已经：")
    print("1. 在Telegram中找到了您的Bot (@poasy_bot)")
    print("2. 点击了START按钮")
    print("3. 发送了至少一条消息给Bot")
    print()
    
    # 调用Telegram API获取更新
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    
    try:
        response = requests.get(url)
        data = response.json()
        
        if data['ok'] and data['result']:
            print("✅ 找到以下对话：")
            print()
            
            chat_ids = set()
            for update in data['result']:
                if 'message' in update:
                    chat = update['message']['chat']
                    chat_id = chat['id']
                    chat_type = chat['type']
                    
                    if chat_type == 'private':
                        first_name = chat.get('first_name', '')
                        last_name = chat.get('last_name', '')
                        username = chat.get('username', '')
                        
                        print(f"📱 私聊对话:")
                        print(f"   Chat ID: {chat_id}")
                        print(f"   姓名: {first_name} {last_name}".strip())
                        if username:
                            print(f"   用户名: @{username}")
                        print()
                        
                        chat_ids.add(chat_id)
                    
                    elif chat_type in ['group', 'supergroup']:
                        title = chat.get('title', '')
                        print(f"👥 群组对话:")
                        print(f"   Chat ID: {chat_id}")
                        print(f"   群组名: {title}")
                        print()
                        
                        chat_ids.add(chat_id)
            
            if chat_ids:
                print("🎯 请选择一个Chat ID并更新到.env文件中的TELEGRAM_CHAT_ID")
                print("💡 通常选择您个人的私聊Chat ID")
                return list(chat_ids)
            else:
                print("❌ 没有找到任何对话")
                return []
        else:
            print("❌ 获取更新失败")
            print(f"错误信息: {data}")
            return []
            
    except Exception as e:
        print(f"❌ 请求失败: {e}")
        return []

def update_env_file(chat_id):
    """更新.env文件中的Chat ID"""
    try:
        # 读取现有的.env文件
        with open('.env', 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 替换Chat ID
        updated_content = content.replace('your_chat_id_here', str(chat_id))
        
        # 写回文件
        with open('.env', 'w', encoding='utf-8') as f:
            f.write(updated_content)
        
        print(f"✅ 已更新.env文件，Chat ID设置为: {chat_id}")
        return True
    except Exception as e:
        print(f"❌ 更新.env文件失败: {e}")
        return False

if __name__ == "__main__":
    print("🤖 Telegram Chat ID 获取工具")
    print("=" * 40)
    
    chat_ids = get_chat_id()
    
    if chat_ids:
        if len(chat_ids) == 1:
            chat_id = chat_ids[0]
            print(f"🎯 自动选择Chat ID: {chat_id}")
            if update_env_file(chat_id):
                print("\n🎉 配置完成！现在可以运行测试脚本了：")
                print("python test_telegram.py")
        else:
            print("\n请手动选择一个Chat ID并更新到.env文件中")
            print("将 'your_chat_id_here' 替换为您选择的Chat ID")
    else:
        print("\n请确保：")
        print("1. Bot Token正确")
        print("2. 已经与Bot开始对话")
        print("3. 网络连接正常")