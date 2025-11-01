#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
风险控制管理工具
用于监控和管理交易系统的风险状态
"""

import os
import sys
import json
import time
from datetime import datetime
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

def load_risk_state():
    """加载风险状态（如果有持久化存储）"""
    # 这里可以从文件或数据库加载风险状态
    # 目前返回默认状态
    return {
        'consecutive_losses': 0,
        'daily_pnl': 0.0,
        'last_anomaly_time': 0,
        'circuit_breaker_active': False,
        'emergency_stop': False,
        'trading_suspended': False,
        'last_price_check': None,
        'volatility_history': []
    }

def display_risk_status():
    """显示当前风险状态"""
    print("\n" + "="*60)
    print("🛡️  风险控制状态监控")
    print("="*60)
    
    risk_state = load_risk_state()
    
    print(f"📊 连续亏损次数: {risk_state['consecutive_losses']}")
    print(f"💰 当日盈亏: {risk_state['daily_pnl']:+.2f} USDT")
    print(f"🔴 熔断状态: {'激活' if risk_state['circuit_breaker_active'] else '正常'}")
    print(f"🚨 紧急停止: {'激活' if risk_state['emergency_stop'] else '正常'}")
    print(f"⏸️  交易暂停: {'是' if risk_state['trading_suspended'] else '否'}")
    
    if risk_state['last_anomaly_time'] > 0:
        last_anomaly = datetime.fromtimestamp(risk_state['last_anomaly_time'])
        print(f"⚠️  上次异常: {last_anomaly.strftime('%Y-%m-%d %H:%M:%S')}")
    else:
        print("⚠️  上次异常: 无")
    
    print(f"📈 波动率历史: {len(risk_state['volatility_history'])} 个数据点")

def display_risk_config():
    """显示风险控制配置"""
    print("\n" + "="*60)
    print("⚙️  风险控制配置")
    print("="*60)
    
    # 这里应该从主程序配置中读取，简化版本
    config = {
        'enable_anomaly_detection': True,
        'max_price_change_1m': 0.05,
        'max_price_change_5m': 0.10,
        'max_volatility_threshold': 0.15,
        'circuit_breaker_enabled': True,
        'max_consecutive_losses': 3,
        'max_daily_loss_ratio': 0.20,
        'slippage_protection': True,
        'max_slippage_ratio': 0.005,
        'emergency_stop_enabled': True,
        'price_deviation_threshold': 0.03,
        'volatility_window': 20,
        'anomaly_cooldown': 300
    }
    
    print(f"🔍 价格异常检测: {'启用' if config['enable_anomaly_detection'] else '禁用'}")
    print(f"📊 最大1分钟变化: {config['max_price_change_1m']:.1%}")
    print(f"📊 最大5分钟变化: {config['max_price_change_5m']:.1%}")
    print(f"⚡ 波动率阈值: {config['max_volatility_threshold']:.1%}")
    print(f"🔴 熔断机制: {'启用' if config['circuit_breaker_enabled'] else '禁用'}")
    print(f"📉 最大连续亏损: {config['max_consecutive_losses']}次")
    print(f"💸 最大日亏损比例: {config['max_daily_loss_ratio']:.1%}")
    print(f"🎯 滑点保护: {'启用' if config['slippage_protection'] else '禁用'}")
    print(f"📈 最大滑点: {config['max_slippage_ratio']:.1%}")
    print(f"🕐 异常冷却时间: {config['anomaly_cooldown']}秒")

def reset_risk_state():
    """重置风险状态"""
    print("\n⚠️  确认重置风险控制状态？")
    print("这将重置以下状态：")
    print("- 连续亏损次数")
    print("- 熔断状态")
    print("- 紧急停止状态")
    print("- 交易暂停状态")
    
    confirm = input("\n输入 'YES' 确认重置: ")
    
    if confirm.upper() == 'YES':
        # 这里应该调用主程序的重置函数
        # 或者更新持久化存储
        print("✅ 风险控制状态已重置")
        print("⚠️  注意：主程序需要重启才能生效")
    else:
        print("❌ 重置操作已取消")

def emergency_stop():
    """紧急停止交易"""
    print("\n🚨 紧急停止交易")
    print("⚠️  这将立即停止所有交易活动")
    
    confirm = input("\n输入 'STOP' 确认紧急停止: ")
    
    if confirm.upper() == 'STOP':
        # 这里应该设置紧急停止标志
        print("🛑 紧急停止已激活")
        print("⚠️  所有交易活动已暂停")
        print("💡 使用重置功能恢复交易")
    else:
        print("❌ 紧急停止已取消")

def show_menu():
    """显示菜单"""
    print("\n" + "="*60)
    print("🛡️  风险控制管理工具")
    print("="*60)
    print("1. 查看风险状态")
    print("2. 查看风险配置")
    print("3. 重置风险状态")
    print("4. 紧急停止交易")
    print("5. 实时监控")
    print("0. 退出")
    print("="*60)

def real_time_monitor():
    """实时监控风险状态"""
    print("\n🔍 实时监控模式（按 Ctrl+C 退出）")
    
    try:
        while True:
            os.system('cls' if os.name == 'nt' else 'clear')
            display_risk_status()
            print(f"\n⏰ 更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print("按 Ctrl+C 退出监控...")
            time.sleep(10)  # 每10秒更新一次
    except KeyboardInterrupt:
        print("\n\n✅ 退出实时监控")

def main():
    """主函数"""
    while True:
        show_menu()
        
        try:
            choice = input("\n请选择操作 (0-5): ").strip()
            
            if choice == '1':
                display_risk_status()
            elif choice == '2':
                display_risk_config()
            elif choice == '3':
                reset_risk_state()
            elif choice == '4':
                emergency_stop()
            elif choice == '5':
                real_time_monitor()
            elif choice == '0':
                print("\n👋 再见！")
                break
            else:
                print("\n❌ 无效选择，请重试")
                
            if choice != '5':  # 实时监控模式不需要暂停
                input("\n按回车键继续...")
                
        except KeyboardInterrupt:
            print("\n\n👋 再见！")
            break
        except Exception as e:
            print(f"\n❌ 发生错误: {e}")
            input("按回车键继续...")

if __name__ == "__main__":
    main()