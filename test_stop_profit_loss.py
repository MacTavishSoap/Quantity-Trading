#!/usr/bin/env python3
"""
测试 ATR 追踪止盈功能（新接口）
 - 适配 Quantitytrading.auto_stop_profit_loss(price_data)
 - 打桩 exchange 与 get_current_position
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import importlib

Quantitytrading = importlib.import_module('Quantitytrading')


class FakeExchange:
    def __init__(self):
        self.orders = []

    def create_market_order(self, symbol, side, size, params=None):
        self.orders.append({
            'symbol': symbol,
            'side': side,
            'size': float(size),
            'params': params or {}
        })


def reset_state():
    # 重置追踪状态
    Quantitytrading.risk_state['trailing_stop_price'] = None
    Quantitytrading.risk_state['position_high_price'] = None
    Quantitytrading.risk_state['position_low_price'] = None
    Quantitytrading.risk_state['last_trailing_update_time'] = 0
    Quantitytrading.risk_state['dynamic_trailing_cfg'] = None
    # 注入虚拟交易所
    Quantitytrading.exchange = FakeExchange()
    # 设定最小下单量
    Quantitytrading.TRADE_CONFIG['min_amount'] = 0.001


def make_price_data(price, high=None, low=None):
    return {
        'price': float(price),
        'high': float(high) if high is not None else float(price),
        'low': float(low) if low is not None else float(price),
        # 不提供 full_data['atr']，促使使用高低差回退
        'full_data': None
    }


def test_no_activation():
    print("\n📋 用例1: 未达到激活阈值，不应启动追踪")
    reset_state()
    # 持仓：多头，入场价 100，仓位 2.0
    Quantitytrading.get_current_position = lambda: {
        'side': 'long', 'size': 2.0, 'entry_price': 100.0
    }
    # 当前涨幅 0.2% < activation_ratio 0.4%
    price_data = make_price_data(100.2, 100.21, 100.19)
    result, msg = Quantitytrading.auto_stop_profit_loss(price_data)
    print(f"结果: {result}, 信息: {msg}")
    assert result is False
    assert Quantitytrading.risk_state['trailing_stop_price'] is None
    assert len(Quantitytrading.exchange.orders) == 0


def test_activation_and_update():
    print("\n📋 用例2: 达到激活阈值，应初始化并更新追踪价")
    reset_state()
    Quantitytrading.get_current_position = lambda: {
        'side': 'long', 'size': 2.0, 'entry_price': 100.0
    }
    # 当前涨幅 1% >= 0.4% 激活
    price_data = make_price_data(101.0, 101.2, 100.8)
    result, msg = Quantitytrading.auto_stop_profit_loss(price_data)
    print(f"结果: {result}, 信息: {msg}")
    assert Quantitytrading.risk_state['trailing_stop_price'] is not None
    assert len(Quantitytrading.exchange.orders) == 0


def test_trigger_full_close():
    print("\n📋 用例3: 触及追踪止损，触发全平")
    reset_state()
    Quantitytrading.get_current_position = lambda: {
        'side': 'long', 'size': 2.0, 'entry_price': 100.0
    }
    # 激活并更新追踪价
    price_data_up = make_price_data(101.0, 101.2, 100.8)
    Quantitytrading.auto_stop_profit_loss(price_data_up)
    stop = Quantitytrading.risk_state['trailing_stop_price']
    assert stop is not None
    # 价格跌破止损，触发
    price_data_hit = make_price_data(stop - 0.01, stop, stop - 0.02)
    result, msg = Quantitytrading.auto_stop_profit_loss(price_data_hit)
    print(f"结果: {result}, 信息: {msg}")
    assert result is True
    assert len(Quantitytrading.exchange.orders) == 1
    order = Quantitytrading.exchange.orders[0]
    assert order['side'] == 'sell'
    assert abs(order['size'] - 2.0) < 1e-6
    # 全平后应重置追踪状态
    assert Quantitytrading.risk_state['trailing_stop_price'] is None


def test_trigger_partial_close():
    print("\n📋 用例4: 触及止损，部分平仓并继续追踪")
    reset_state()
    # 注入动态配置：触发不全平，部分平仓 50%
    Quantitytrading.risk_state['dynamic_trailing_cfg'] = {
        'activation_ratio': 0.001,  # 降低激活门槛，便于测试
        'atr_multiplier': 2.0,
        'break_even_buffer_ratio': 0.0,
        'min_step_ratio': 0.0,
        'update_cooldown': 0,
        'close_all_on_hit': False,
        'partial_close_ratio': 0.5
    }
    Quantitytrading.get_current_position = lambda: {
        'side': 'long', 'size': 2.0, 'entry_price': 100.0
    }
    # 激活并更新追踪价
    Quantitytrading.auto_stop_profit_loss(make_price_data(100.2, 100.21, 100.19))
    stop = Quantitytrading.risk_state['trailing_stop_price']
    assert stop is not None
    # 价格跌破止损，触发部分平仓
    result, msg = Quantitytrading.auto_stop_profit_loss(make_price_data(stop - 0.01, stop, stop - 0.02))
    print(f"结果: {result}, 信息: {msg}")
    assert result is True
    assert len(Quantitytrading.exchange.orders) == 1
    order = Quantitytrading.exchange.orders[0]
    assert order['side'] == 'sell'
    # 只平一半
    assert abs(order['size'] - 1.0) < 1e-6
    # 继续追踪（不重置）
    assert Quantitytrading.risk_state['trailing_stop_price'] is not None


if __name__ == "__main__":
    print("🧪 开始测试 ATR 追踪止盈功能...")
    test_no_activation()
    test_activation_and_update()
    test_trigger_full_close()
    test_trigger_partial_close()
    print("\n🎉 测试完成!")