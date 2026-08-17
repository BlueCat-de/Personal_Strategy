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
   - 样本隔离：**train(2015-2021) / val(2022-2023) / test(2024+ 保密)** —— 现代市场切分（SOP §0.2，2026-08 重切；旧 2007-2013 已废弃）

2. **事前假设 + 复杂度预算（SOP §0.3，写代码前冻结入 git）**：
   - 经济假设（为什么有效？预期方向？预期半衰期？）
   - **复杂度预算**：K_max（默认 8）、N_max、Effective N 上限（≥5）、单因子复杂度上限（自由参数≤4 / 原始字段≤3 / AST 节点≤120）

3. **因子构造**：
   - 在 `src/ashare_quant/research/cand_*.py` 中实现因子
   - 截面变换（rank / 中性化）+ 时序变换（rolling / ewm）
   - PIT 审计（滚动窗口终止于信号日，不含未来）

4. **因子筛选（先过 §3.0 复杂度预算闸门，再跑 IC）**：
   - **查重**：`python -m ashare_quant.research.factor_library lookup "<主题>"` —— 撞死族（扩散/invest-quality/Sloan/asset-growth/动量）直接放弃
   - **复杂度门**：`python -m ashare_quant.research.factor_library gate <name>` 或 `complexity audit <file>` —— 超阈值不进入 IC
   - 然后才运行 `factor_probe.py` / `factor_stability_analysis.py` 计算 IC/ICIR/逐年命中率
   - 分位单调性检查；正交化（与新老因子相关性 < 0.7）；换手率评估

5. **因子组合**：
   - 默认等权（最稳健）
   - 检查因子对齐性（不能混合选股逻辑冲突的因子）
   - **核对复杂度预算**：`python -m ashare_quant.research.factor_library composite <因子...> --effective-n <值>` —— K≤K_max、自由参数和≤16、**Effective N ≥ 下限（默认5）**；超预算按 §3.0 减员，不得加因子对冲。前提：新因子须先 `FactorLibrary.add`+`save` 写回 factors.json（mine-factors 步骤 8），否则 composite 报 unknown factor

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
- **不没事前复杂度预算就开筛**（先 §0.3 冻结 K_max/N_max/Effective N 上限，§3.0 IC 前强制查重+裁剪）
- **不重复挖已证伪的死族**（扩散/invest-quality 比率代理/Sloan 应计/asset-growth/动量；先 `factor_library lookup` 查重）
