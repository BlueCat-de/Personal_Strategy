---
description: "挖掘新因子（多 agent 并行 + IC 筛选 + 稳定性检验）"
---

# 因子挖掘：$ARGUMENTS

围绕 `$ARGUMENTS` 主题系统性挖掘新因子。

## 执行流程

1. **明确挖掘主题**：$ARGUMENTS（如"低波族"、"流动性"、"反转族"、"基本面质量"）

2. **多视角因子生成**（使用 Workflow 并行 agents）：
   - 每类一个 agent，给出因子 spec（名称、经济假设、公式、数据输入、方向、预期半衰期）
   - 要求从经济学/行为学出发，附 Ptrade 可部署性映射
   - 参考 `src/ashare_quant/research/factor_mining_workflow.js`

3. **实现候选因子**：
   - 在 `src/ashare_quant/research/cand_*.py` 模块中实现（复用 `cand_lib.build_shared`）
   - 价格类用 full-daily rolling；复杂统计用 per_date_stat
   - 参考 `src/ashare_quant/research/cand_lib.py` 的共享面板 API

4. **IC 筛选（train+val only）**：
   ```bash
   .venv/Scripts/python.exe -m ashare_quant.research.factor_probe
   ```
   - 按 mean IC / ICIR / 逐年命中率排序
   - **逐年命中率是最重要的筛选标准**（跨 14 年稳定 > 单期高 IC）
   - 剔除与新老因子相关 > 0.7 的

5. **稳定性分析**：
   ```bash
   .venv/Scripts/python.exe -m ashare_quant.research.factor_stability_analysis
   ```
   - 确认 train+val 逐年命中率 ≥ 0.70
   - 确认因子 IC 衰减半衰期与预期再平衡频率匹配

## 关键约束
- **所有数据字段必须在 Ptrade 可取**（无 SW 行业、无分析师、无宏观）
- **PIT 严格**：因子只用信号日及之前的数据
- **test 集全程保密**：只在最后揭盲一次

## 关键文件
- 因子探针：`src/ashare_quant/research/factor_probe.py`
- 稳定性分析：`src/ashare_quant/research/factor_stability_analysis.py`
- 共享面板：`src/ashare_quant/research/cand_lib.py`
- 挖掘 workflow 模板：`src/ashare_quant/research/factor_mining_workflow.js`
