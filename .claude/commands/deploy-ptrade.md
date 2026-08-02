---
description: "将本地策略迁移到 Ptrade 实盘/模拟盘"
---

# Ptrade 迁移：$ARGUMENTS

将 `$ARGUMENTS` 策略从本地回测迁移到 Ptrade，按 `docs/SOP_PTRADE_MIGRATION.md` 执行。

## 执行步骤

1. **确认本地策略文件**：`src/ashare_quant/strategies/$ARGUMENTS.py`
2. **确认因子与 Ptrade 字段映射**（每个因子输入必须 Ptrade 可取）：
   - 价格类 → `get_history`（close/open/high/low/volume/money）
   - 估值类 → `get_fundamentals('valuation')`（pe_ttm/pb/dv_ttm/total_value/turnover_rate）
   - 财务类 → `get_fundamentals('growth_ability'/'profit_ability')`
   - **禁止**：SW 行业、分析师数据、宏观、Ptrade 不可取字段
3. **创建 Ptrade 适配器**：`deploy/ptrade/$ARGUMENTS.py`
4. **确认 8 层容灾全部继承**：
   - 信号失败重试（不 raise）
   - 停机错过信号检测
   - desired_shares 净值漂移重算
   - order try/except + limit_price(±0.5% 缓冲)
   - strategy_version 校验
   - _status_map 容错
   - 估值 pickle 缓存（100MB 滚动清理）
   - missed signal + timing reseed
5. **关键 Ptrade 差异处理**：
   - `set_commission/slippage/limit_mode` 仅回测可用（交易模式忽略 → 账户设置里配）
   - `order()` 必须传 `limit_price`（否则低流动性票快照失败）
   - numpy.str_ → `_u()` 转换
   - 禁 os 模块
   - 月频 vs 双月频 `_is_signal_day` 调整
6. **写单元测试**：`tests/test_ptrade_adapter_$ARGUMENTS.py`
7. **部署清单**：
   - Ptrade 创建策略（普通股票、分钟级）
   - 粘贴代码
   - 确认无需上传额外文件（除 tushare_token 版需要）
   - 短区间回测验证 → 扩展全期

## 关键文件
- SOP 全文：`docs/SOP_PTRADE_MIGRATION.md`
- 参考适配器：`deploy/ptrade/composite_alpha_v2.py`
- 单元测试参考：`tests/test_ptrade_adapter_v2.py`
