import numpy as np
import pandas as pd
import math

class MarketNoiseFilter:
    """
    市场噪音过滤器 (Market Noise Filter)
    
    基于统计学和混沌理论 (Chaos Theory) 的特征工程，
    用于识别市场是否处于"随机游走"或"无序震荡"状态。
    
    核心指标:
    1. Kaufman Efficiency Ratio (ER): 考夫曼效率系数
    2. Choppiness Index (CI): 波动性指数 (基于分形维数)
    3. Volatility Regime: 波动率体制分析
    """
    
    def __init__(self, lookback=14):
        self.lookback = lookback
        self.thresholds = {
            'choppiness_high': 61.8, # 高于此值表示强烈的震荡/噪音
            'choppiness_low': 38.2,  # 低于此值表示强烈的趋势
            'er_low': 0.3,           # 效率系数低于此值表示噪音
            'vol_z_high': 2.5        # 波动率异常高
        }
        # 历史状态缓存 (最近12次, 约3小时)
        self.history = []
        self.max_history = 12

    def calculate_efficiency_ratio(self, close_prices, period=None):
        """
        计算考夫曼效率系数 (ER)
        ER = Direction / Volatility
        ER 接近 1: 单边趋势 (信号质量高)
        ER 接近 0: 杂乱震荡 (噪音大)
        """
        if period is None: period = self.lookback
        
        if len(close_prices) < period + 1:
            return 0.5
            
        # 价格变化 (Direction)
        change = abs(close_prices.iloc[-1] - close_prices.iloc[-period - 1])
        
        # 波动总和 (Volatility)
        # diff() 计算每一步的变化，abs() 取绝对值，rolling().sum() 求和
        volatility = close_prices.diff().abs().rolling(window=period).sum().iloc[-1]
        
        if volatility == 0:
            return 0
            
        return change / volatility

    def calculate_choppiness_index(self, df, period=None):
        """
        计算波动性指数 (Choppiness Index)
        公式: 100 * LOG10( SUM(ATR(1), n) / (MaxHigh(n) - MinLow(n)) ) / LOG10(n)
        
        CI > 61.8: 市场处于盘整/噪音状态
        CI < 38.2: 市场处于趋势状态
        """
        if period is None: period = self.lookback
        
        if len(df) < period + 1:
            return 50.0
            
        # 1. 计算 True Range (ATR(1))
        # TR = max(high-low, abs(high-prev_close), abs(low-prev_close))
        high = df['high']
        low = df['low']
        close = df['close']
        prev_close = close.shift(1)
        
        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        # 2. 计算周期内的 TR 之和
        sum_tr = true_range.rolling(window=period).sum().iloc[-1]
        
        # 3. 计算周期内的 Range (最高 - 最低)
        max_high = high.rolling(window=period).max().iloc[-1]
        min_low = low.rolling(window=period).min().iloc[-1]
        range_hl = max_high - min_low
        
        if range_hl == 0:
            return 50.0
            
        # 4. 计算 CI
        try:
            ci = 100 * np.log10(sum_tr / range_hl) / np.log10(period)
            return ci
        except:
            return 50.0

    def analyze(self, df):
        """
        综合分析当前市场的噪音水平 (结合历史状态)
        """
        close = df['close']
        
        # 1. 计算基础指标
        er = self.calculate_efficiency_ratio(close)
        ci = self.calculate_choppiness_index(df)
        
        # 2. 计算波动率 Z-Score
        returns = close.pct_change()
        curr_vol = returns.rolling(window=self.lookback).std().iloc[-1]
        long_vol = returns.rolling(window=self.lookback * 5).std().iloc[-1] 
        
        vol_ratio = 0
        if long_vol > 0:
            vol_ratio = curr_vol / long_vol
            
        # 3. 瞬时状态判定
        current_state = 'NEUTRAL' 
        reasons = []
        
        # A. CI 判定
        if ci > self.thresholds['choppiness_high']:
            current_state = 'RANGING'
        elif ci < self.thresholds['choppiness_low']:
            current_state = 'TRENDING'
            
        # B. ER 修正
        if er < self.thresholds['er_low'] and current_state != 'TRENDING':
            current_state = 'RANGING'
        
        # C. 混乱判定
        if vol_ratio > 2.0 and er < 0.4:
            current_state = 'CHAOTIC'

        # --- 4. 历史状态平滑 (Time-Series Smoothing) ---
        # 存入历史记录
        self.history.append({
            'state': current_state,
            'ci': ci,
            'er': er,
            'vol': vol_ratio
        })
        if len(self.history) > self.max_history:
            self.history.pop(0)
            
        # 统计历史状态
        state_counts = {'RANGING': 0, 'TRENDING': 0, 'CHAOTIC': 0, 'NEUTRAL': 0}
        avg_ci = 0
        
        for h in self.history:
            state_counts[h['state']] += 1
            avg_ci += h['ci']
        
        avg_ci /= len(self.history)
        
        # 最终决策逻辑 (基于历史投票)
        final_state = current_state
        
        # 如果过去大部分时间都是震荡，哪怕现在偶尔趋势也不信
        if state_counts['RANGING'] + state_counts['CHAOTIC'] >= len(self.history) * 0.6:
            if current_state == 'NEUTRAL':
                final_state = 'RANGING'
                reasons.append(f"历史惯性(震荡{state_counts['RANGING']}次)")
        
        # 如果长期是趋势，突然变成震荡，可能是短暂回调，保持趋势判定
        if state_counts['TRENDING'] >= len(self.history) * 0.7:
            if current_state == 'RANGING':
                final_state = 'TRENDING' # 视为旗形整理，仍是趋势
                reasons.append(f"趋势中继(趋势{state_counts['TRENDING']}次)")

        # 生成原因描述
        if final_state == 'RANGING':
            reasons.append(f"CI高(Avg:{avg_ci:.1f})")
        elif final_state == 'TRENDING':
            reasons.append(f"CI低(Avg:{avg_ci:.1f})")
        elif final_state == 'CHAOTIC':
            reasons.append(f"波动异常(Vol:{vol_ratio:.1f})")
            
        noise_score = 0
        if final_state in ['RANGING', 'CHAOTIC']:
            noise_score = 60 + (20 if final_state == 'CHAOTIC' else 0)

        return {
            'state': final_state, 
            'is_noisy': (final_state in ['RANGING', 'CHAOTIC']),
            'noise_score': noise_score,
            'features': {
                'efficiency_ratio': er,
                'choppiness_index': ci,
                'volatility_ratio': vol_ratio,
                'avg_ci': avg_ci
            },
            'reason': ", ".join(reasons) if reasons else "市场平稳"
        }

if __name__ == "__main__":
    # 简单测试
    print("测试噪音过滤器...")
    # 创建模拟数据: 正弦波 (有规律) + 随机噪音
    x = np.linspace(0, 100, 200)
    prices = 100 + 10 * np.sin(x/5) + np.random.normal(0, 2, 200) # 趋势 + 噪音
    
    df = pd.DataFrame({
        'high': prices + 1,
        'low': prices - 1,
        'close': prices,
        'open': prices # 简化
    })
    
    nf = MarketNoiseFilter()
    result = nf.analyze(df)
    print(f"分析结果: {result}")
