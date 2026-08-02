---
description: "对指定策略运行健壮性 SOP 五维度扫描"
---

# 健壮性测试：$ARGUMENTS

对 `$ARGUMENTS` 策略执行 `docs/SOP_ROBUSTNESS_ANALYSIS.md` 的五维度扫描。

## 执行

健壮性脚本位于 `src/ashare_quant/research/` 下。先确认对应的脚本：

```bash
# v2 策略
.venv/Scripts/python.exe -m ashare_quant.research.composite_v2_robustness

# 通用（修改脚本中的 weights + factor 函数后运行）
.venv/Scripts/python.exe -m ashare_quant.research.composite_robustness
```

## 五个维度

| 维度 | 测试 | 通过标准 |
|---|---|---|
| 因子权重微扰 | ±50% 每个因子 | 最大敏感度 < 0.20 |
| 因子消融 | leave-one-out | 去掉任一因子 Sharpe 降幅 < 0.30 |
| 滑点敏感度 | 5/10/20/50 bp | 50bp 仍 Sharpe > 0 |
| 持仓数 | 6/8/10/12 | 全平（无尖峰） |
| 调仓频率/相位 | 月频 vs 双月频 vs 偏移 | 相位不敏感 |

## 注意

- 只在 train+val 上运行，**不碰 test**
- 预计算一次因子面板，多次扫描复用
- 结果输出到 `data/research/<strategy>_robustness/`
- SOP 全文：`docs/SOP_ROBUSTNESS_ANALYSIS.md`
