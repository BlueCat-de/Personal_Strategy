---
description: "挖掘新因子（多 agent 并行 + IC 筛选 + 稳定性检验）"
---

# 因子挖掘：$ARGUMENTS

围绕 `$ARGUMENTS` 主题系统性挖掘新因子。

## 执行流程

1. **明确挖掘主题**：$ARGUMENTS（如"低波族"、"流动性"、"反转族"、"基本面质量"）

2. **factor-library 查重（"我们试过没？"，SOP §3.0 前置硬门）**——在生成任何新因子前先跑：
   ```bash
   .venv/Scripts/python.exe -m ashare_quant.research.factor_library lookup "$ARGUMENTS"
   ```
   读 `data/factorlib/factors.json`（228 因子带 verdict/why/血统）。**撞上死族（扩散/invest-quality 比率代理/Sloan 应计/asset-growth/动量）直接放弃或强制提出逃过已记录失败模式的变体**，不重挖。把命中结果作为每个候选的"前情提要"。

3. **多视角因子生成**（使用 Workflow 并行 agents）：
   - 每类一个 agent，给出因子 spec（名称、经济假设、公式、数据输入、方向、预期半衰期）
   - 要求从经济学/行为学出发，附 Ptrade 可部署性映射
   - 参考 `src/ashare_quant/research/factor_mining_workflow.js`

4. **实现候选因子**：
   - 在 `src/ashare_quant/research/cand_*.py` 模块中实现（复用 `cand_lib.build_shared`）
   - 价格类用 full-daily rolling；复杂统计用 per_date_stat
   - 参考 `src/ashare_quant/research/cand_lib.py` 的共享面板 API

5. **复杂度预算闸门（SOP §3.0，IC 之前）**——每个实现好的候选先过复杂度门，超阈值的剔除，**不进入 IC 筛选**：
   ```bash
   # 单因子：声明式（从 factors.json 读已知复杂度）
   .venv/Scripts/python.exe -m ashare_quant.research.factor_library gate <factor_name>
   # 或 AST 审计 compute() 函数（启发式交叉核对，fail-closed 挡住 xmath/Piotroski 复杂族）
   .venv/Scripts/python.exe -m ashare_quant.research.complexity audit src/ashare_quant/research/cand_<x>.py --func compute
   ```
   上限：自由参数 ≤4、原始字段 ≤3、AST 节点 ≤120；组合层 K_max=8、自由参数和 ≤16、Effective N ≥5。若候选池 > K_max×3，先用事前红旗（经济解释/可部署性）裁剪，**不靠 IC 裁池**。

6. **IC 筛选（train+val only）**：
   ```bash
   .venv/Scripts/python.exe -m ashare_quant.research.factor_probe
   ```
   - 按 mean IC / ICIR / 逐年命中率排序
   - **逐年命中率是最重要的筛选标准**（跨 14 年稳定 > 单期高 IC）
   - 剔除与新老因子相关 > 0.7 的

7. **稳定性分析**：
   ```bash
   .venv/Scripts/python.exe -m ashare_quant.research.factor_stability_analysis
   ```
   - 确认 train+val 逐年命中率 ≥ 0.70
   - 确认因子 IC 衰减半衰期与预期再平衡频率匹配

8. **回写因子库**：把本轮新因子的 verdict/metrics/复杂度写回 `data/factorlib/factors.json`（`FactorLibrary.add` + `save`），附 split 标记，供下一轮查重。

## 关键约束
- **所有数据字段必须在 Ptrade 可取**（无 SW 行业、无分析师、无宏观）
- **PIT 严格**：因子只用信号日及之前的数据
- **test 集全程保密**：只在最后揭盲一次

## 关键文件
- 因子探针：`src/ashare_quant/research/factor_probe.py`
- 稳定性分析：`src/ashare_quant/research/factor_stability_analysis.py`
- 共享面板：`src/ashare_quant/research/cand_lib.py`
- 挖掘 workflow 模板：`src/ashare_quant/research/factor_mining_workflow.js`
