# 🛡️ 风险控制系统使用说明

## 概述

本交易系统已集成完整的风险控制机制，专门用于防范"黑天鹅"事件和"插针"等极端市场情况。

此外，系统支持基于 ATR 与 AI 建议融合的“追踪止盈”机制，可在趋势行情中更好地放宽盈利空间，在震荡或不确定环境下适度收紧止盈保护。

## 🔧 风险控制功能

### 1. 价格异常检测 (Price Anomaly Detection)
- **功能**: 实时监控价格变化，识别异常波动
- **保护**: 防范插针、闪崩等极端价格变动
- **参数**:
  - `max_price_change_1m`: 1分钟最大价格变化 (默认5%)
  - `max_price_change_5m`: 5分钟最大价格变化 (默认10%)
  - `price_deviation_threshold`: 价格偏离阈值 (默认3%)

### 2. 波动率保护 (Volatility Protection)
- **功能**: 监控市场波动率，在极端波动时暂停交易
- **保护**: 防范高波动率环境下的异常损失
- **参数**:
  - `max_volatility_threshold`: 最大波动率阈值 (默认15%)
  - `volatility_window`: 波动率计算窗口 (默认20个周期)

### 3. 熔断机制 (Circuit Breaker)
- **功能**: 在连续亏损或日亏损过大时自动停止交易
- **保护**: 防范连续错误决策导致的大额损失
- **参数**:
  - `max_consecutive_losses`: 最大连续亏损次数 (默认3次)
  - `max_daily_loss_ratio`: 最大日亏损比例 (默认20%)

### 4. 滑点保护 (Slippage Protection)
- **功能**: 限制市价单的最大滑点
- **保护**: 防范极端市场条件下的过度滑点
- **参数**:
  - `max_slippage_ratio`: 最大滑点比例 (默认0.5%)

### 5. 紧急停止机制 (Emergency Stop)
- **功能**: 手动或自动紧急停止所有交易
- **保护**: 在检测到系统异常时立即停止交易

### 6. 追踪止盈（ATR + AI融合）
- **功能**: 结合 ATR（平均真实波动范围）与 AI 的策略建议，动态设定追踪止盈的参数，使止盈轨迹既稳健又具有策略自适应性。
- **参数来源**:
  1) 全局默认：`TRADE_CONFIG['risk_management']['trailing_stop']`
  2) AI建议（优先级更高）：在分析阶段，AI可返回 `risk_control.trailing_stop` 字段（可选），系统将写入 `risk_state['dynamic_trailing_cfg']` 并优先使用。
- **可用参数**:
  - `atr_multiplier`：ATR倍数，决定止损轨迹距离（推荐范围 1.5–5.0）
  - `activation_ratio`：激活盈利比例，达到后启动追踪（推荐范围 0.1%–2%）
  - `break_even_buffer_ratio`：保本缓冲比例，首段保护至保本上/下方（推荐范围 0–1%）
  - `min_step_ratio`：最小步进比例，满足后更新止损（推荐范围 0.05%–1%）
  - `update_cooldown`：更新冷却秒数，避免频繁抖动（推荐范围 30–600 秒）
  - `aggressiveness`：`aggressive|balanced|conservative`（可选），用于给出风格倾向模板
- **边界钳制**：系统会对上述参数进行范围校验与钳制，超出范围的值将被自动限制在合理区间内。

#### AI 输出示例
```json
{
  "signal": "BUY",
  "reason": "……",
  "confidence": "HIGH",
  "risk_control": {
    "trailing_stop": {
      "atr_multiplier": 2.8,
      "activation_ratio": 0.004,
      "break_even_buffer_ratio": 0.0012,
      "min_step_ratio": 0.002,
      "update_cooldown": 120,
      "aggressiveness": "balanced"
    }
  }
}
```

#### 运行机制简述
- 无持仓时，追踪状态会重置。
- 达到激活盈利阈值后，按 ATR 倍数与保本缓冲计算候选止损，仅沿趋势方向移动；满足最小步进与冷却条件后更新。
- 价格触及追踪止损即平仓（reduceOnly），并重置追踪状态。

## ⚙️ 配置说明

风险控制参数在 `Quantitytrading.py` 的 `TRADE_CONFIG['risk_management']` 中配置：

```python
'risk_management': {
    'enable_anomaly_detection': True,      # 启用价格异常检测
    'max_price_change_1m': 0.05,          # 1分钟最大变化5%
    'max_price_change_5m': 0.10,          # 5分钟最大变化10%
    'max_volatility_threshold': 0.15,     # 波动率阈值15%
    'circuit_breaker_enabled': True,      # 启用熔断机制
    'max_consecutive_losses': 3,          # 最大连续亏损3次
    'max_daily_loss_ratio': 0.20,         # 最大日亏损20%
    'slippage_protection': True,          # 启用滑点保护
    'max_slippage_ratio': 0.005,          # 最大滑点0.5%
    'emergency_stop_enabled': True,       # 启用紧急停止
    'price_deviation_threshold': 0.03,    # 价格偏离阈值3%
    'volatility_window': 20,              # 波动率窗口20周期
    'anomaly_cooldown': 300               # 异常冷却时间5分钟
    'trailing_stop': {
        'atr_multiplier': 2.5,
        'activation_ratio': 0.004,
        'break_even_buffer_ratio': 0.001,
        'min_step_ratio': 0.002,
        'update_cooldown': 120,
        'close_all_on_hit': True
    }
}
```

## 🔍 风险监控工具

### 使用风险管理工具
运行独立的风险监控工具：
```bash
python risk_manager.py
```

### 功能菜单
1. **查看风险状态** - 显示当前风险控制状态
2. **查看风险配置** - 显示风险控制参数配置
3. **重置风险状态** - 手动重置风险控制状态
4. **紧急停止交易** - 立即停止所有交易活动
5. **实时监控** - 实时监控风险状态变化

## 📊 风险状态说明

### 风险状态指标
- **连续亏损次数**: 当前连续亏损的交易次数
- **当日盈亏**: 当日累计盈亏金额
- **熔断状态**: 是否触发熔断机制
- **紧急停止**: 是否处于紧急停止状态
- **交易暂停**: 是否暂停交易
- **上次异常时间**: 最近一次检测到异常的时间

### 状态重置
当触发风险控制机制后，可以通过以下方式重置：
1. 使用 `risk_manager.py` 工具手动重置
2. 重启主程序（会自动重置部分状态）
3. 等待冷却时间自动恢复

## ⚠️ 重要提醒

1. **参数调整**: 根据交易策略和风险承受能力调整参数
2. **监控频率**: 建议定期检查风险状态
3. **异常处理**: 触发风险控制后，应分析原因再决定是否继续交易
4. **备份策略**: 建议设置多层风险控制，不依赖单一机制

## 🚨 紧急情况处理

### 如果系统触发风险控制：
1. **不要慌张** - 系统正在保护您的资金
2. **检查原因** - 使用监控工具查看触发原因
3. **分析市场** - 确认是否为正常的市场波动
4. **谨慎重置** - 确认安全后再重置风险状态

### 联系支持
如遇到系统异常或需要技术支持，请保留日志文件并联系技术支持。

---

**注意**: 风险控制系统是为了保护资金安全，但不能完全消除交易风险。请合理设置参数并谨慎交易。