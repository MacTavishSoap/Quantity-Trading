#!/usr/bin/env python3
"""
测试自动止盈止损功能
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from Quantitytrading import auto_stop_profit_loss, log_info

def test_auto_stop_profit_loss():
    """测试自动止盈止损功能"""
    print("🧪 开始测试自动止盈止损功能...")
    
    # 模拟测试场景
    test_cases = [
        {
            'name': '多头止盈触发',
            'current_price': 51000,
            'expected': True,
            'description': '价格高于止盈价，应该触发止盈'
        },
        {
            'name': '多头止损触发', 
            'current_price': 48000,
            'expected': True,
            'description': '价格低于止损价，应该触发止损'
        },
        {
            'name': '价格在区间内',
            'current_price': 49500,
            'expected': False,
            'description': '价格在止盈止损之间，不应该触发'
        }
    ]
    
    for i, test_case in enumerate(test_cases, 1):
        print(f"\n📋 测试用例 {i}: {test_case['name']}")
        print(f"   - 描述: {test_case['description']}")
        print(f"   - 当前价格: {test_case['current_price']}")
        
        try:
            result, message = auto_stop_profit_loss(test_case['current_price'])
            print(f"   - 结果: {result} ({message})")
            print(f"   - 预期: {test_case['expected']}")
            
            if result == test_case['expected']:
                print("   ✅ 测试通过!")
            else:
                print("   ❌ 测试失败!")
                
        except Exception as e:
            print(f"   ❌ 测试异常: {e}")
    
    print("\n🎉 测试完成!")

if __name__ == "__main__":
    test_auto_stop_profit_loss()