# 47 个有效策略原始还原审计

## 口径

本审计只针对当前批量回测中的 47 个有效策略，不包含已剔除的固定单票择时策略。

“已还原”表示本地适配脚本已经按原始源码的核心信号规则实现；“部分还原”表示信号或交易规则能还原一部分，但资产、平台数据或 A 股限制使结果不等同原策略；“代理复现”表示当前只能用相近的本地因子或动量逻辑替代；“受限还原”表示原始策略依赖做空、分钟级或非 A 股机制，和当前个人 A 股 long-only 周频约束冲突。

## 本轮已做的还原

- 交易执行层从默认下一交易日收盘价改为下一交易日开盘价近似执行，匹配多数 BigQuant/QuantConnect 原始脚本中的开盘调仓设定；仍使用日线数据、T+1、100 股整数手和完整手续费。
- `strategy-quantconnect-15-stock-selection-strategy-based-on-fundamental-factors` 原先误用 `value_quality` 因子代理，已改回原始源码中的 21/63/126 日等权动量，每周持有最强 1 只。
- `strategy-quantconnect-245-tech-momentum-winner-rotation` 当前已经匹配原始 21/63/126 日等权动量逻辑。

## 本轮验证结果

- 新结果目录：`data/backtests/jqdata_12m_realistic_tplus1_weekly_open_restore_45k_no_single_symbol`
- 47 个策略全部成功运行。
- `run_config.json` 已记录 `execution_price_field=open`。
- 首日权益和现金均为 45,000 元。
- 所有实际成交股数均为 100 股整数倍。
- 所有有成交策略均产生佣金、印花税和过户费记录。
- 实际持仓中没有创业板、科创板、北交所或 ST/*ST 标的。

本轮 Top 5：

| 排名 | 策略 | 收益率 | 年化收益 | Sharpe | 最大回撤 | 期末权益 |
|---:|---|---:|---:|---:|---:|---:|
| 1 | `strategy-bigquant-multi-factor-17ed5d18` | 19.41% | 20.29% | 1.20 | -7.01% | 53,735.12 |
| 2 | `strategy-bigquant-value-quality-838db701` | 9.49% | 9.90% | 0.70 | -10.23% | 49,271.70 |
| 3 | `strategy-bigquant-value-quality-d983e306` | 8.14% | 8.49% | 0.66 | -8.86% | 48,662.10 |
| 4 | `strategy-quantconnect-312-high-roe-large-cap-quality-basket` | 2.49% | 2.60% | 0.23 | -16.07% | 46,122.07 |
| 5 | `strategy-github-factor-akshare-backtrader-122d1777` | 0.94% | 0.98% | 0.14 | -9.65% | 45,423.18 |

`strategy-quantconnect-15-stock-selection-strategy-based-on-fundamental-factors` 在还原为原始动量逻辑后，收益从上一版代理口径约 +3.20% 变为 -70.36%。这说明代理口径会显著改变策略结论，后续排名应优先参考还原状态，而不是只看收益率。

## 逐策略状态

| 策略 | 来源 | 状态 | 依据 | 后续处理 |
|---|---|---|---|---|
| `strategy-bigquant-multi-factor-17ed5d18` | BigQuant | 代理复现 | 原始依赖私有 BigQuant 表 `user_factor_5ba350b6011211efb1ef322b4793acaa` | 只能还原交易规则；选股 score 需拿到私有因子表或重建因子 |
| `strategy-github-momentum-akshare-strategy-6-4d59d07e` | GitHub | 代理复现 | 原始文件是 Backtrader 框架/抽象类，不是完整 alpha 策略 | 需回到原仓库寻找具体子策略 |
| `strategy-bigquant-value-quality-d983e306` | BigQuant | 代理复现 | 原始依赖私有 BigQuant 表 `user_factor_0b2cbf4b4f62567657e89f1af9abaab2` | 只能还原交易规则；选股 score 需拿到私有因子表或重建因子 |
| `strategy-quantconnect-312-high-roe-large-cap-quality-basket` | QuantConnect | 代理复现 | 原始依赖 QuantConnect Fundamental: ROE、market cap、IPO date、dollar volume | 需接入 JQData 财务因子后才能还原 |
| `strategy-github-factor-akshare-backtrader-122d1777` | GitHub | 代理复现 | 原始文件是 broker/data handler，不是完整 alpha 策略 | 需回到原仓库寻找具体子策略 |
| `strategy-quantconnect-15-stock-selection-strategy-based-on-fundamental-factors` | QuantConnect | 已还原 | 原始为 21/63/126 日等权动量，每周持有最强 1 只 | 已从 `value_quality` 代理改为 `momentum_rotation(21,63,126)` |
| `strategy-github-momentum-akshare-strategy-4-6d1be012` | GitHub | 代理复现 | 原始文件是 Backtrader 框架/抽象类，不是完整 alpha 策略 | 需回到原仓库寻找具体子策略 |
| `strategy-quantconnect-411-short-volatility-overbought-mean-reversion-shorts` | QuantConnect | 受限还原 | 原始策略做空高波动/高 Connors RSI 标的 | 当前 A 股 long-only 不能还原空头收益 |
| `strategy-quantconnect-345-large-cap-us-momentum-rotation-strategy` | QuantConnect | 代理复现 | 原始依赖 market cap 宇宙筛选和 QuantConnect 动量指标 | 需接入 JQData 市值并重建大盘股池 |
| `strategy-quantconnect-322-calendar-month-seasonality-long-short-equity` | QuantConnect | 受限还原 | 原始包含多空季节性组合 | 当前 A 股 long-only 不能还原空头腿 |
| `strategy-bigquant-value-quality-838db701` | BigQuant | 代理复现 | 原始依赖私有 BigQuant 表 `user_factor_1118aa6141b38bf8e52c230ee78c7a7a` | 只能还原交易规则；选股 score 需拿到私有因子表或重建因子 |
| `strategy-bigquant-value-quality-0e936728` | BigQuant | 代理复现 | 原始依赖私有 BigQuant 表 `user_factor_a4868f250438acfc8678de63b978ca94` | 只能还原交易规则；选股 score 需拿到私有因子表或重建因子 |
| `strategy-bigquant-etf-allocation-cfe15ced` | BigQuant | 部分还原 | 原始 BigQuant 模块可见，但仍依赖平台数据和模块 | 交易规则可还原；平台数据需逐项替换 |
| `strategy-quantconnect-446-long-only-liquid-equities-tail-risk-optimized` | QuantConnect | 代理复现 | 原始依赖 QuantConnect Universe/Alpha/Portfolio 框架 | 需重建选股、alpha 和组合优化模型 |
| `strategy-bigquant-machine-learning-78e9289a` | BigQuant | 代理复现 | 原始依赖私有 BigQuant 表 `user_factor_eff532f6f58811ee8d87aec62d52c705` | 只能还原交易规则；选股 score 需拿到私有因子表或重建因子 |
| `strategy-github-momentum-akshare-strategy-2-d279963f` | GitHub | 代理复现 | 原始文件是 Backtrader 框架/抽象类，不是完整 alpha 策略 | 需回到原仓库寻找具体子策略 |
| `strategy-bigquant-value-quality-0b37aada` | BigQuant | 代理复现 | 原始依赖私有 BigQuant 表 `user_factor_ac37a5ae3d4cb7399f1149fe16721958` | 只能还原交易规则；选股 score 需拿到私有因子表或重建因子 |
| `strategy-bigquant-convertible-bond-4c3ad03a` | BigQuant | 部分还原 | 原始 BigQuant 模块可见，但当前数据集是股票 OHLCV，不是可转债全字段 | 需补可转债转股溢价率、余额、评级等字段 |
| `strategy-github-momentum-akshare-strategy-3-a469bb70` | GitHub | 代理复现 | 原始文件是 Backtrader 框架/抽象类，不是完整 alpha 策略 | 需回到原仓库寻找具体子策略 |
| `strategy-bigquant-machine-learning-66ad9020` | BigQuant | 部分还原 | 原始 BigQuant 模块可见，但 ML 特征和训练数据不完整 | 需恢复训练样本、标签和模型参数 |
| `strategy-bigquant-multi-factor-1487f796` | BigQuant | 代理复现 | 原始依赖私有 BigQuant 表 `user_formydream_48f019f0` | 只能还原交易规则；选股 score 需拿到私有因子表或重建因子 |
| `strategy-github-pairs-trading-akshare-samuelstrategy-45a0e383` | GitHub | 部分还原 | 原始配对交易 z-score 逻辑完整，但原策略需要多空交易 | 当前只适合 long-only 降级测试；完整还原需要融资融券/做空假设 |
| `strategy-bigquant-multi-factor-84bbfa4a` | BigQuant | 代理复现 | 原始依赖私有 BigQuant 表 `user_formydream_1511e6e8` | 只能还原交易规则；选股 score 需拿到私有因子表或重建因子 |
| `strategy-quantconnect-123-high-conviction-mega-cap-rotation-vs-qqq` | QuantConnect | 代理复现 | 原始是随机森林预测相对 QQQ 超额收益 | 需实现训练集、标签、基准和模型推断 |
| `strategy-github-momentum-akshare-strategy-7-f24b82bb` | GitHub | 代理复现 | 原始文件是 Backtrader 框架/抽象类，不是完整 alpha 策略 | 需回到原仓库寻找具体子策略 |
| `strategy-bigquant-intraday-fec2d524` | BigQuant | 代理复现 | 原始依赖私有 BigQuant 表 `user_data_bq3xsdcp_7a60b646` | 只能还原交易规则；选股 score 需拿到私有因子表或重建因子 |
| `strategy-github-factor-akshare-strategy-1-dd9ef2fe` | GitHub | 代理复现 | 原始文件是分钟级数据/技术框架，不是完整日线 alpha 策略 | 当前不做分钟交易，无法严格还原 |
| `strategy-bigquant-machine-learning-8e128b0e` | BigQuant | 代理复现 | 原始依赖私有 BigQuant 表 `user_factor_a9c08602f4c311ee9c42a61996343845` | 只能还原交易规则；选股 score 需拿到私有因子表或重建因子 |
| `strategy-bigquant-machine-learning-3556fc10` | BigQuant | 代理复现 | 原始依赖私有 BigQuant 表 `user_factor_0c26192ef58c11eead9c3afa1a581da0` | 只能还原交易规则；选股 score 需拿到私有因子表或重建因子 |
| `strategy-bigquant-multi-factor-57395713` | BigQuant | 代理复现 | 原始依赖私有 BigQuant 表 `user_factor_sfgsxysnxfhs` | 只能还原交易规则；选股 score 需拿到私有因子表或重建因子 |
| `strategy-bigquant-machine-learning-f4da1f55` | BigQuant | 部分还原 | 原始 BigQuant 模块可见，但 ML 特征和训练数据不完整 | 需恢复训练样本、标签和模型参数 |
| `strategy-quantconnect-78-kelly-criterion-sma-crossover` | QuantConnect | 部分还原 | 原始为 IBM/SHY 小时级 Kelly + SMA 切换 | 当前用日线 A 股/避险资产映射，不能等同原策略 |
| `strategy-github-momentum-akshare-37009-strategy-873c0e65` | GitHub | 代理复现 | 原始文件不是完整可执行 alpha 策略 | 需回到原仓库寻找具体子策略 |
| `strategy-github-momentum-akshare-strategy-1-aafc00f7` | GitHub | 代理复现 | 原始文件是 Backtrader 框架/抽象类，不是完整 alpha 策略 | 需回到原仓库寻找具体子策略 |
| `strategy-bigquant-machine-learning-6e32deba` | BigQuant | 代理复现 | 原始依赖私有 BigQuant 表 `user_factor_08e9e7aef55011eeb96a227d79b4aa92` | 只能还原交易规则；选股 score 需拿到私有因子表或重建因子 |
| `strategy-bigquant-multi-factor-4ab5026b` | BigQuant | 代理复现 | 原始依赖私有 BigQuant 表 `user_data_licmh5905` | 只能还原交易规则；选股 score 需拿到私有因子表或重建因子 |
| `strategy-bigquant-machine-learning-0c898229` | BigQuant | 代理复现 | 原始依赖私有 BigQuant 表 `user_factor_a3ba7cbcf58711eea7670a53dd8e4409` | 只能还原交易规则；选股 score 需拿到私有因子表或重建因子 |
| `strategy-bigquant-intraday-fea64753` | BigQuant | 代理复现 | 原始依赖私有 BigQuant 表 `user_data_cgmm_n01` | 只能还原交易规则；选股 score 需拿到私有因子表或重建因子 |
| `strategy-bigquant-intraday-4707990b` | BigQuant | 代理复现 | 原始依赖私有 BigQuant 表 `user_data_bq5iqmhg4ea5305f` | 只能还原交易规则；选股 score 需拿到私有因子表或重建因子 |
| `strategy-bigquant-multi-factor-4fdc3f4c` | BigQuant | 代理复现 | 原始依赖私有 BigQuant 表 `user_data_zhjif6753` | 只能还原交易规则；选股 score 需拿到私有因子表或重建因子 |
| `strategy-github-momentum-akshare-strategy-53ee0543` | GitHub | 代理复现 | 原始文件是 Backtrader 框架/抽象类，不是完整 alpha 策略 | 需回到原仓库寻找具体子策略 |
| `strategy-quantconnect-245-tech-momentum-winner-rotation` | QuantConnect | 已还原 | 原始为 21/63/126 日等权动量，每周持有最强 1 只 | 当前适配已匹配核心信号 |
| `strategy-bigquant-machine-learning-5c8ec388` | BigQuant | 代理复现 | 原始依赖私有 BigQuant 表 `user_data_lanyue3316` | 只能还原交易规则；选股 score 需拿到私有因子表或重建因子 |
| `strategy-bigquant-etf-allocation-f5287950` | BigQuant | 部分还原 | 原始 BigQuant 模块可见，但策略原始标的是纳斯达克/ETF 择时 | 需要明确 A 股替代资产映射，不能直接等同 |
| `strategy-bigquant-multi-factor-2b57336e` | BigQuant | 代理复现 | 原始依赖私有 BigQuant 表 `user_factor_94c95d839dda9f7c` | 只能还原交易规则；选股 score 需拿到私有因子表或重建因子 |
| `strategy-github-factor-akshare-strategy-7b3684a0` | GitHub | 代理复现 | 原始文件是框架/数据处理/抽象类，不是完整 alpha 策略 | 需回到原仓库寻找具体子策略 |
| `strategy-github-momentum-akshare-strategy-5-f7f3f98d` | GitHub | 代理复现 | 原始文件是 Backtrader 框架/抽象类，不是完整 alpha 策略 | 需回到原仓库寻找具体子策略 |

## 结论

严格意义上，当前 47 个策略里只有少数 QuantConnect 动量策略具备完整可还原的公开源码。BigQuant 策略的问题不是撮合层，而是核心选股分数大多来自私有 `user_factor` 或 `user_data` 表；没有这些表时，只能做交易层还原和本地因子代理。GitHub 中大量文件是框架源码或抽象类，不是具体 alpha 策略，因此不能把当前代理回测结果视为原始策略表现。
