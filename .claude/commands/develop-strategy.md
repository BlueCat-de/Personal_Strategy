---
description: "按 SOP 系统性开发一个新因子策略"
---

# 策略开发：$ARGUMENTS

按 `docs/SOP_STRATEGY_DEVELOPMENT.md` 的 9 步流程执行策略开发。

## 执行步骤

1. **确认目标和约束**
   - 要开发什么类型的策略？（因子选股 / 择时 / 轮动）
   - 目标：穿越熊牛 / 降低回撤 / 提高夏普 / 其他
   - 约束：本地 Tushare 数据 + Ptrade 可部署（无 SW 行业、无分析师数据）
   - 样本隔离：train(2007-2013) / val(2014-2020) / test(2021-2026 保密)

2. **事前假设**：写下经济假设（为什么有效？预期方向？预期半衰期？）

3. **因子构造**：
   - 在 `src/ashare_quant/research/cand_*.py` 中实现因子
   - 截面变换（rank / 中性化）+ 时序变换（rolling / ewm）
   - PIT 审计（滚动窗口终止于信号日，不含未来）

4. **因子筛选**：
   - 运行 `factor_probe.py` 或 `factor_stability_analysis.py` 计算 IC/ICIR/逐年命中率
   - 分位单调性检查
   - 正交化（与新老因子的相关性 < 0.7）
   - 换手率评估

5. **因子组合**：
   - 默认等权（最稳健）
   - 检查因子对齐性（不能混合选股逻辑冲突的因子）
   - 检查有效因子数（Effective N）

6. **组合构建**：
   - Top-N 等权选股（默认 N=8）
   - 市值中性化（不使用行业）
   - 频率与 IC 半衰期匹配

7. **train+val 回测** + 健壮性 SOP + 对抗审查

8. **test 揭盲**（一次性，跑完不回头调）

9. **evaluation pipeline** 全面评测

## 关键文件
- SOP 全文：`docs/SOP_STRATEGY_DEVELOPMENT.md`
- 因子探针：`src/ashare_quant/research/factor_probe.py`
- 稳定性分析：`src/ashare_quant/research/factor_stability_analysis.py`
- 共享面板：`src/ashare_quant/research/cand_lib.py`
- 回测引擎：`src/ashare_quant/backtest.py`
- 评测 pipeline：`src/ashare_quant/evaluation/pipeline.py`

## 反模式（避免）
- 不先看 test 再调参
- 不用边际消融选因子（用逐年 IC 稳定性）
- 不混合选股逻辑冲突的因子
- 不忽略换手成本
