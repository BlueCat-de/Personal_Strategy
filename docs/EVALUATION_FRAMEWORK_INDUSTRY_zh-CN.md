# 量化策略系统性评测:业界方法学全景报告

> **一句话定位**:在量化投资中,最难的从来不是"预测",而是"验证"(validation)。一份未经审计的回测在运行之前就已经被系统性高估了——幸存者偏差、未来函数、样本内拟合、被忽略的成本、多重检验,全部单向偏乐观,绝不悲观。本报告系统梳理业界严肃机构在投入真实资金前如何评测一个策略,涵盖从数据基础到生命周期管理的完整链条。

> 本报告通过并行检索 arxiv / SSRN / 业界量化博客与教科书整理而成,仅作研究与教育用途,不构成投资建议。

## 引言:核心理念 —— 验证才是难题

业界一致结论(Bailey & Lopez de Prado;Campbell Harvey;Lopez de Prado《Advances in Financial Machine Learning》)可浓缩为六条:

1. **未经审计的回测天生偏乐观**。幸存者、未来函数、样本内拟合、忽略成本,全部单向偏移。
2. **判断回测最重要的变量,恰恰是研究者最不记录的:到底试了多少个策略**。同样的夏普比率,试 1 次和试 1000 次含义完全不同。
3. **即便经过不多的搜索,教科书 t>2.0 的门槛在统计上已站不住脚**。业界把发现门槛提到 t≥3.0。
4. **信息泄漏是机器学习回测的隐形杀手**。标准 k-fold 在金融数据上结构性失效。
5. **量化界有自己的"可复现性危机"**:大多数已发表因子、大多数样本内漂亮的策略都是伪发现。
6. **生命周期门控顺序**:研究域 → 泄漏防护交叉验证 → 隔离样本外回测 → 纸面交易 → 小规模实盘试点 → 扩规模。

参考:[9 Mistakes Quants Make that Cause Backtests to Lie](https://easylanguagemastery.com/building-strategies/9-mistakes-quants-make-cause-backtests-lie/) | [Key Takeaways from López de Prado's AFML](https://abouttrading.substack.com/p/my-key-takeways-from-maros-lopez) | [A Backtesting Protocol in the Era of Machine Learning](https://www.marti.ai/quant/2018/12/09/backtesting-protocol.html)

---

## 一、数据与信息基础(一切评测的前提)

### 1.1 幸存者偏差与点在时间数据
- **它是什么**:构建历史股票池时,必须基于"当时的真实成员"(point-in-time membership),即包含已退市、被并购、破产的标的,而不是用"今天的成分股"去套用到过去。
- **为什么重要**:仅用现存公司回测会系统性高估收益。业界经验值:幸存者偏差单独一项每年膨胀约 **3–5%**,足以把一个盈亏平衡的策略包装成"赢家"。机制是——回测时只看到活下来的公司,跌到归零的样本被静默删除了。
- **业界怎么处理**:使用 point-in-time(PIT)数据库(如 Compustat "as-reported"、IBES 快照),按每个时点的真实可得成员重建宇宙,专门保留退市/并购记录。

### 1.2 公司行动处理与未来函数
- **它是什么**:分红、拆股、合股、并购等公司行动会造成价格序列不连续。两类处理:**回填调整**(adjusted price)与**原始价 + 现金簿**(raw price,事件发生时按因子调持仓、分红进现金)。
- **为什么重要**:未来函数(look-ahead bias)最隐蔽的来源之一。若回测用了**调整后收盘价**,未来的分红/拆股信息会反过来修改过去的价格点——你在 t 时刻"看到"的价格里已包含 t 时刻不可能知道的调整。此外**回测与实盘的公司行动处理时点往往不一致**(如 QuantConnect 回测午夜处理、实盘数据早晨才到),制造意外。
- **业界怎么处理**:(a) 特征用原始价 + 行情,持仓按事件即时调整,分红进现金簿;(b) 若必须用调整价,只用**事件发生时点之前已知**的调整(前向调整而非回填),审计每个特征时间戳;(c) 公司行动后重置并重新预热指标。
- 参考:[How corporate actions are handled in a backtest (QuantConnect)](https://www.quantconnect.com/docs/v2/writing-algorithms/securities/asset-classes/us-equity/corporate-actions) | [LSEG Corporate Actions](https://developers.lseg.com/en/article-catalog/article/workspace-corporate-actions-content-set-guide)

### 1.3 研究域 vs 交易域隔离
- **它是什么**:在数据/时段上建立防火墙——研究域用于想法生成与模型开发,**隔离的样本外交易域**(quarantined OOS)仅用于决定性回测、纸面交易门槛、实盘试点。
- **为什么重要**:一旦最终样本外测试被任何研究迭代"碰过",它就被污染了。研究者反复迭代直到样本外切片通过,本质上它已不是样本外。
- **业界怎么处理**:预先冻结交易域;记录并最小化对其访问次数(每次访问都抬高 MinBTL)。
- 参考:[The Dangers of Backtesting](https://portfoliooptimizationbook.com/book/8.3-dangers-backtesting.html)

---

## 二、统计显著性、多重检验与过拟合(区分真伪技能)

核心问题:研究者试了成百上千个配置,只报告最好的那个——标准推断被系统性高估。

### 2.1 多重检验陷阱与 t≥3.0 门槛
- **它是什么**:把发现门槛从 t>2.0 上调,补偿"同时检验了大量假设"。校正家族:**Bonferroni/Holm**(控制族错误率 FWER,最严格)、**BHY**(控制错误发现率 FDR,更有检验力)。
- **为什么重要**:Harvey-Liu-Zhu 编目了 **316 个已发表截面因子**("因子动物园"),t>2.0 下"至少一个假阳性"概率几乎为 100%。今日新因子应通过 **t≥3.0**,且随因子数量增长还在抬升(预测 2030s 升至 ~3.4)。
- **业界怎么处理**:主张金融应用用 **BHY/FDR**;把校正后的显著性**转换为最低盈利门槛**(例:T=240、波动率 10%、300 次试验、5% 显著性下,单检验需月收益 0.365%,BHY 下需 0.621%)。
- 参考:[...and the Cross-Section of Expected Returns (Harvey-Liu-Zhu, NBER)](https://www.nber.org/system/files/working_papers/w20592/w20592.pdf) | [Backtesting (Harvey & Liu, CME)](https://www.cmegroup.com/content/dam/cmegroup/education/files/backtesting.pdf)

### 2.2 缩水夏普比率 DSR / 概率夏普 PSR / MinTRL
- **PSR**:真实夏普超过基准的概率,引入偏度、超额峰度,不假设正态。
- **DSR**:在 PSR 基础上把基准替换为 **N 次独立试验后期望的最大夏普** SR\*。当真实夏普为 0、N=1000 时,**期望最大夏普 ≈ 3.26**——纯靠运气。
- **为什么重要**:仅约 **7 次试验**就能造出样本内 SR>1.0、而样本外真实 SR=0 的两年回测。一个回测 SR=1.92 看似 PSR=0.99,但 DSR 可能坍塌到 **0.82**(低于 0.95 门槛)而被正确否决。另外"夏普打五折"是错的——折扣非线性:SR<0.4 通常需砍 >50%,SR>1.0 只需 ≤25%(Haircut Sharpe Ratio)。
- **业界怎么处理**:报告 **DSR 而非原始夏普**;用 Haircut Sharpe 替代武断"五折";用 MinTRL 回答"必须纸面/实盘多久才能信任这个夏普"。关键输入是有效独立试验数 **N = ρ̂ + (1−ρ̂)·M**。
- 参考:[The Deflated Sharpe Ratio (Bailey & López de Prado)](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf) | [QuantDare 讲解](https://quantdare.com/deflated-sharpe-ratio-how-to-avoid-been-fooled-by-randomness/)

### 2.3 回测过拟合概率 PBO(CSCV)
- **它是什么**:**组合对称交叉验证 CSCV** 把 N 个策略收益切成 2N 个等长块,构造全部 C(2N,N) 个对称样本内/样本外划分(典型 2N=16,共 12,870 组),对每组算"样本内最优"在样本外的排名 r,logit=ln(r/(1−r));**PBO = logit<0 的比例**。无需分布假设。
- **为什么重要**:PBO 直接回答"模型选择流程是否过拟合"。接近 1 = 严重过拟合;>0.5 意味选择流程"反预测"——选中的策略大概率样本外跑输中位数。
- **业界怎么处理**:把 PBO 作为纸面交易前的**准入/否决闸门**。
- 参考:[The Probability of Backtest Overfitting (Bailey-Borwein-López de Prado-Zhu)](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf) | [PBO explained (Balaena Quant)](https://medium.com/balaena-quant-insights/the-probability-of-backtest-overfitting-pbo-9ba0ac7fb456)

### 2.4 事前经济假设与试验计数
- **它是什么**:跑任何回测前先书面陈述经济假设(pre-registration),并记录所有试过的策略数量。
- **为什么重要**:试验计数是 DSR、PBO、SPA 的共同输入。不记录或丢弃的回测会静默膨胀夏普,事后无法补救——"不报告试验数量,本身就是一种欺诈"。
- **业界怎么处理**:维护"试验日志";用 White Reality Check / Hansen SPA(自助法数据窥探检验)考虑全部被测模型后检验"最优模型是否真优于基准"。

> **统计显著性层摘要表**

| 指标 | 纠正什么 | 关键阈值/经验值 |
|---|---|---|
| DSR | 试验数 N + 非正态 + 样本长 | 应 ≥0.95;SR=1.92 可塌到 0.82 |
| PSR | 非正态(偏度/峰度) | 概率形式,应 ≥0.95 |
| Haircut Sharpe | 多重检验(非线性折扣) | SR<0.4 砍 >50%,SR>1.0 砍 ≤25% |
| t-门槛 | 因子动物园 | t≥3.0(且逐年抬升) |
| PBO | 模型选择过拟合 | 应 <0.5;接近 1 = 严重过拟合 |
| MinTRL | 样本过短 | 实盘/纸面需积累到此长度 |

---

## 三、信号与因子质量评测

### 3.1 IC、ICIR 与 IC 衰减/半衰期
- **信息系数 IC**:因子值与前瞻收益的截面相关。业界默认 **Spearman 秩 IC**。经验值:均值 IC **0.05–0.15 典型,>0.10 较强,>0.20 罕见**;实盘 IC 通常只有回测一半。
- **ICIR**(= 均值 IC / IC 标准差):衡量预测力**稳定性**。均值 IC 高但剧烈波动的因子不可交易。
- **IC 衰减/半衰期**:IC 随预测期延长指数衰减。**动量衰减快、价值/质量衰减慢**——半衰期决定最优再平衡频率与容量。
- 参考:[Real Factor Alpha (PyQuant News)](https://www.pyquantnews.com/free-python-resources/real-factor-alpha-how-to-measure-it-with-information-coefficient-and-alphalens-in-python) | [Turnover-Adjusted IR (arXiv:2105.10306)](https://arxiv.org/pdf/2105.10306) | [Alphalens](https://alphalens.ml4trading.io/notebooks/overview.html)

### 3.2 分位单调性与多空价差
按因子排序分桶(如十等分),检查(a)最高减最低分位价差、(b)是否单调。真因子产生干净持续的多空价差;非单调(中间桶最优)暗示噪声或异常值驱动。还能暴露收益是否集中于不流动微盘股(可交易性红灯)。

### 3.3 换手率与成本调整后的信息比率
单边换手率/平均持有期;**换手率调整 IR** 把均值 IC、IC 波动、IC 衰减整合进单一成本感知指标。高 alpha 配"疯狂换手率"不可交易——成本后才能看清排名。

### 3.4 行业/风格中性化
评分前剥离偶然风险敞口(行业内排序、减行业均值、回归剔除市场/规模/价值/动量/波动)。不中性化,"因子"可能是伪装的行业押注——原始债务股权比排序本质是做多金融/做空医疗。
- 参考:[Sector Neutralization (QuantRocket)](https://www.quantrocket.com/blog/sector-neutralization/)

### 3.5 因子拥挤
用持仓类与收益类信号衡量多少人在交易同一信号(估值价差、收益离散度、截面相关性、因子 ETF 成交量)。拥挤加速 alpha 衰减并预测尾部风险。机械因子(动量、反转)拥挤快且可预测;判断型(价值、质量)拥挤弥散。
- 参考:[Not All Factors Crowd Equally (arXiv:2512.11913)](https://arxiv.org/html/2512.11913v1)

---

## 四、业绩归因与风险分解

### 4.1 收益归因(Brinson)
把超额收益拆为三块:**配置效应** (w_p−w_b)·R_b、**选股效应** w_b·(R_p−R_b)、**交互效应**。回答"超额收益来自行业配置还是选股"。

### 4.2 风险归因(MCR / CR / TCAR)
- **边际风险贡献 MCR** = (Σw)_i / σ_p;**风险贡献 CR** = w_i·MCR_i,各持仓风险占比**加总=100%**。
- 1000 只中可能 5 只就吃掉约 25% 的主动风险。按信念而非名义规模下注;TCAR 精确定位吞噬风险预算的少数持仓。
- 参考:[Risk Attribution (x·σ·ρ)](https://tsgperformance.com/wp-content/uploads/2021/11/Risk-Attribution-x-sigma-rho.pdf) | [Causeway's Risk Lens](https://www.causewaycap.com/insight/under-the-manager-microscope-causeways-risk-lens/)

### 4.3 因子 vs 特异性风险分解 & 残差 Alpha
σ_p² = σ_factor² + σ_specific²。真选股者应显示**特异性风险 > 因子风险**。对 Fama-French/Carhart 回归,截距 alpha 与残差是剔除已知因子后的剩余——**"alpha 通常是隐藏的 beta"**,多数表面 alpha 控制规模/价值/动量后消失。因子模型 **R² 高 = 警告**。
- 参考:[Factor Analysis (Addepar)](https://addepar.com/assets/research-papers/addepar-factor-analysis.pdf) | [Brinson and Factors: A Unified Framework (Hentschel)](https://www.ludgerhentschel.com/PDFs/Hentschel%20'24b.pdf)

### 4.4 统计风险模型 vs 基本面风险模型
当 PCA 统计风险模型预测的跟踪误差显著高于命名因子基本面模型时,组合暴露了基本面模型未命名的"隐藏/过渡"因子——历史上常先于多空经理的困难期出现。
- 参考:[More than Just a Second Risk Number (Axioma via CAIA)](https://caia.org/sites/default/files/05_more-than-just-a-rsik_9-14-17_1.pdf)

---

## 五、选股技能分解(把收益拆到选股/规模/择时/运气)

### 5.1 主动管理基本定律与传递系数
**扩展基本定律 IR = TC × IC × √breadth**:IC 是预测技能,breadth 是独立预测数,**传递系数 TC = 预测主动收益与实际主动权重的相关**(−1~1,无约束时=1)。真实约束(禁做空、行业限制、换手/主动风险上限)常把 TC 压到远低于 1,于是高研究 IC 仍可能产生平庸实盘收益。诊断 TC 告诉你该修研究还是修组合构建。

### 5.2 胜率 vs 回报比(Van Loon 分解)
**恒等式 IC = 1.6 × [胜率 − 1/(1+回报比)]**(肥尾下常数降至 ~1.4):把胜率(选股)与回报比(下注规模/信念)对 IC 的贡献代数分离。
- **关键洞察**:不一定要超 50% 正确率。Man Group 与 CFA 研究发现**回报比与实现收益的相关性强于胜率**。30% 胜率配高回报比(~2.6)仍可产生强 IR;索罗斯"大满贯"胜率不到 30%,Renaissance 的 Medalion 胜率约 51% + 微小边际 + 极大广度。CFA 研究显示仅约 18% 的组合决策是净增值的,多数价值毁灭来自择时/活动而非选股。
- 参考:[Investing in Skill (Man Group)](https://www.man.com/insights/investing-in-skill) | [Manager Selection: The Power of Payoff (CFA)](https://rpc.cfainstitute.org/blogs/enterprising-investor/2022/manager-selection-the-power-of-payoff) | [Dispersion and Alpha Conversion (Morgan Stanley)](https://www.morganstanley.com/im/publication/insights/articles/dispersion-and-alpha-conversion.pdf)

### 5.3 横截面离散度(机会集)
离散度是"广度"的可观测代理:低离散度环境下即使有才能的经理也拉不开差距。实务:向高离散度行业(科技、医疗)倾斜活动,低离散度行业(公用事业、必需消费)趋于中性。

### 5.4 个股/行业贡献分析
按个股/行业分解实现盈亏与 IC,暴露集中风险(alpha 来自 2 只还是 200 只?),并分离选股、规模、择时技能。

---

## 六、交易成本、市场冲击、容量与执行

高夏普但低容量的策略在机构规模上接近一文不值。

### 6.1 执行落差、冲击模型与换手拖累
- **执行落差 Implementation Shortfall (IS)**:决策时点纸面组合与实际执行后组合的价值差 = 佣金 + 价差 + 冲击 + 时点 + 机会成本。
- **平方根冲击法则**:I(Q) = σ·Y·(Q/V)^α,α≈0.5——跨年代、资产类别、场所最稳健的微观结构经验规律。订单大 4 倍,每股成本约 2 倍。
- **临时 vs 永久冲击(Almgren-Chriss)**:永久冲击 ∝ γQ² 不回撤;临时冲击 ∝ ηv 随交易速度变化、事后回撤。**只算临时冲击的成本模型系统性低估真实成本**——"隐藏滑点"是实盘落后回测的主因。
- **换手率成本拖累**:年收益拖累 = 杠杆 × 换手率 × 交易日 × 每笔成本(bps)。
- **参与率 vs ADV**:策略成交量占每只票均量的百分比。超过百分之几就开始踩自己脚印。
- 参考:[A Brief History of Implementation Shortfall (QuantBrokers)](https://www.quantitativebrokers.com/blog/a-brief-history-of-implementation-shortfall) | [Market Impact Models (QuantRocket L28)](https://www.quantrocket.com/codeload/quant-finance-lectures/quant_finance_lectures/Lecture28-Market-Impact-Models.ipynb.html) | [Almgren-Chriss Framework](https://medium.com/@ibrahimlanre1890/trading-execution-algorithms-the-almgren-chriss-framework-56717dd650ce)

### 6.2 策略容量
AUM 到达"交易成本+冲击完全侵蚀预期 alpha"的水平即为容量上限。经验带:大盘 ~50–100 亿美元、中盘 ~10–30 亿、小盘常 <5 亿。容量必须**非线性**建模。**任何业绩指标旁都应报告容量调整后夏普,而非小资金峰值夏普**——HFT/日内可报夏普 7–10 但只能吸收几百万美元。
- 参考:[Portfolio Capacity Analysis](https://static1.squarespace.com/static/5e8b4232216b4d02baafbc9e/t/5e8f23fbb2200c6ef6e4bf38/1586439163875/Portfolio+Capacity+Analysis.pdf)

### 6.3 执行场所与微观结构 ★
- **它是什么**:真实执行需在**明场所(lit venues)与暗池(dark pools)**间路由,由智能订单路由(SOR)在降低冲击与逆向选择风险间权衡,还要考虑 maker-taker 费率。
- **为什么重要**:最佳执行(best execution)是受托义务。回测通常假设中价/收盘成交,完全忽略场所碎片化与路由——单向偏乐观。研究发现**准入限制更严的暗池信息泄漏更少、逆向选择更低**。
- **业界怎么处理**:用**成交后成本分析(TCA)**相对到达价(arrival mid)和区间 VWAP(bps)度量真实成本。
- 参考:[Market Microstructure 2026 (Quantt)](https://www.quantt.co.uk/resources/market-microstructure-guide) | [Dark Pools vs Lit Markets: SOR (Quod Financial)](https://www.quodfinancial.com/dark-pools-vs-lit-markets-how-sor-navigates-liquidity-fragmentation/) | [Differential Access to Dark Markets (Brugler 2025)](https://www.sciencedirect.com/science/article/pii/S0304405X25000947)

### 6.4 监管与做空约束 ★
- **它是什么**:做空受 **Reg SHO** 约束——**Rule 201 替代性上涨规则**、**定位要求(locate)**、**结算(close-out)**、**禁止裸卖空**,外加**融券费(borrow fee)**随券稀缺度飙升。
- **为什么重要**:做空约束是单向摩擦,**使"卖空被高估股"这类异常策略盈利能力大幅下降**,并侵蚀净容量。高**做空利用率(short utilization)**同时预示 alpha 下降与逼空崩溃风险(双重杀手)。
- **业界怎么处理**:回测中建模融券费与做空可行集(剔除无法定位标的),监控空头腿做空利用率。
- 参考:[Key Points About Regulation SHO (SEC)](https://www.sec.gov/investor/pubs/regsho.htm) | [Borrow Fees (IBKR)](https://www.interactivebrokers.com/campus/traders-insight/securities/short-selling/the-risks-of-shorting-series-part-ii-borrow-fees/) | [Short Sale Constraints and Overpricing (NBER)](https://www.nber.org/reporter/spring05/short-sale-constraints-and-overpricing)

### 6.5 税收效率 ★
- **它是什么**:对应税投资者,**税收拖累(tax drag)**是真实成本。短期资本利得(<1 年)按普通税率(美国最高 37%),长期享优惠(0/15/20%)。换手率越高,实现短期利得越多。
- **为什么重要**:换手率直接驱动短期利得实现——**即便微小 alpha 边际在高换手下也会被税收吃掉**。
- **业界怎么处理**:税收感知构建(延迟/避免短期利得、税损收割);**报告税后 alpha(after-tax alpha)**。AQR、Parametric 等把税收集成到构建层面。
- 参考:[Tax-Aware Investing (AQR)](https://www.aqr.com/Learning-Center/Tax-Aware-Investing) | [Long-Short with Tax-Managed Portfolios (Parametric)](https://www.parametricportfolio.com/blog/long-short-equity-strategy-with-tax-managed-portfolios)

> ★ = 基于文献缺口补充的维度

---

## 七、组合构建、优化与约束

### 7.1 均值方差优化的不稳定性
Michaud (1989) 的"**误差最大化器**"诊断:MVO 求协方差矩阵的逆,**最大化输入误差的影响**——对 μ 的误差远比对 Σ 致命,产出极端角点解,样本外常跑输朴素 1/N 等权。
- **业界怎么处理**:用**避免估计 μ**的方法(风险平价、最小方差、HRP 层次风险平价);Black-Litterman 反向优化;**Ledoit-Wolf 收缩**;优化前检查**协方差条件数**(接近奇异即报警)。
- 参考:[Drawbacks of MVO](https://portfoliooptimizationbook.com/book/7.5-MVP-drawbacks.html) | [Risk Parity - Covariance Shrinkage (SKFolio)](https://skfolio.org/auto_examples/risk_budgeting/plot_3_risk_parity_ledoit_wolf.html)

### 7.2 真实约束集与有效资产数
真实交易台硬约束:杠杆 ‖w‖₁≤2、换手上限、基数约束 ‖w‖₀≤K、单票/行业上下界、beta 中性、现金中性、跟踪误差 TEV 约束。**非约束最优永远不被交易;约束才是毛 alpha 转净 alpha 的地方**。
- **有效资产数 Effective N = exp(风险/权重贡献熵)**:等权不代表等风险。用 **Herfindahl 集中度 ‖w‖₂²** 检测角点解。
- 参考:[Portfolio Constraints](https://portfoliooptimizationbook.com/book/6.2-portfolio-constraints.html)

### 7.3 再平衡成本权衡
再平衡不是免费午餐:**月度日历再平衡的换手率是偏差带(±2%)触发的 2–4 倍**,换来的偏差控制微乎其微。**唯一有效指标是净成本收益**;成本越高,容忍带应越宽。
- 参考:[Rebalancing a multi-asset portfolio (Wellington)](https://www.wellington.com/en/insights/rebalancing-a-multi-asset-portfolio)

---

## 八、样本外验证、前向测试与参数稳定性

### 8.1 泄漏防护交叉验证
标准 k-fold 在金融上**结构性失效**:(a) 打乱摧毁时序(未来泄漏进训练);(b) 金融标签路径依赖(带事件窗口),即便按时序划分仍跨边界泄漏。
- **业界处理**:**带清洗与禁运的 k 折(Purged K-Fold CV + Embargo)**——清除标签事件窗口与测试折重叠的训练观测;每个测试折后加禁运缓冲(≥最大特征回看期)。**组合清洗交叉验证 CPCV** 用 N 组 k 测试组组合,从单一历史合成 φ 条以上相互独立、尊重时序的回测路径,配套平均标签唯一性/样本加权。
- **本仓库实现**:`research/walk_forward.py` 的 `walk_forward_folds(..., embargo_days=21)` 产出带 embargo 间隔的 train/test 折；`evaluation/significance.py` 的 `pbo_cscv(returns_matrix)` 实现真 CSCV（接 N 策略收益矩阵）；切分边界统一在 `research/splits.py`。注：旧 `probability_of_backtest_overfitting` 是**单策略 block-bootstrap 代理**（非真 CSCV），保留为 `pbo_block_bootstrap` 别名。
- 参考:[Cross Validation in Finance (QuantInsti)](https://blog.quantinsti.com/cross-validation-embargo-purging-combinatorial/) | [Combinatorial Purged CV (QuantBeckman)](https://www.quantbeckman.com/p/with-code-combinatorial-purged-cross)

### 8.2 前向分析与参数稳定性 ★
- **前向分析 Walk-Forward**:滚动/锚定窗口内样本内优化、紧随其后的样本外测试、汇总样本外为一条净值曲线。**关键诊断是参数稳定性**:若最优参数在窗口间剧烈跳跃(如 12 → 47 → 23),即便汇总样本外盈亏为正,"边际"也是曲线拟合。
- **本仓库实现**:`research/walk_forward.py` 的 `walk_forward_stability(raw_perf, benchmark, test_years, step_years)` 把已产出的净值曲线切成滚动 OOS 窗口，逐窗算 Sharpe/IR 并聚合（均值/标准差/最差窗/**衰减斜率**/IR>0 占比/`systematic_decay` 判定）；CLI `python -m ashare_quant.research.walk_forward --raw-perf ...`，或评测管线 `--walk-forward`。实证（v2，2026-08）：前半段 Sharpe 1.38 → 后半段 0.59，`systematic_decay=true`——单段 test 衰减被证实为系统性。
- **参数敏感性**:**"选高原不选孤峰"(prefer plateaus over peaks)**——选稳定区域中心而非单个过优化点;孤峰周围全是差邻居 = 过拟合红旗。用**参数稳健性评分**(邻近参数集的业绩方差)量化。
- 参考:[Walk Forward Optimization (Build Alpha)](https://www.buildalpha.com/walk-forward-optimization/) | [Robustness Testing Guide](https://www.buildalpha.com/robustness-testing-guide/) | [Red Flag Detection via Parameter Sensitivity](https://medium.com/@kryptera/red-flag-strategy-detection-using-parameter-sensitivity-analysis-09b7e62a2521)

### 8.3 三障碍标记与元标记(ML)
- **三障碍 Triple-Barrier**:用波动率缩放的上止盈/下止损/垂直时限三道栅栏决定标签 {+1,−1,0},对齐真实出场逻辑、降低标签噪声。
- **元标记 Meta-Labeling**:主模型定方向,副模型预测主信号成功概率并输出下注概率。把"方向"与"下注规模"解耦,经验上提升精度/夏普/回撤而不牺牲方向边际。
- 参考:[Does Meta-Labeling Add to Signal Efficacy? (Hudson & Thames)](https://hudsonthames.org/does-meta-labeling-add-to-signal-efficacy-triple-barrier-method/)

### 8.4 生命周期门控序列
研究域 → 泄漏防护 CV → 隔离样本外回测 → 纸面交易 → 小规模实盘试点 → 扩规模。López de Prado 格言:"**回测不是研究工具**"——研究未完成前不要回测,每多一次回测都抬高 MinBTL 并膨胀 PBO。

---

## 九、风险调整收益、回撤、尾部与压力测试

### 9.1 夏普之外的风险调整指标

| 指标 | 改进了什么 | 陷阱 |
|---|---|---|
| **Sortino**(下行偏差) | 只惩罚下行波动 | 少数大跌+多次小涨时分母可能夸大 |
| **Omega**(阈值上下概率加权比) | 不假设正态、捕获全分布含尾部 | 对阈值选择敏感 |
| **Calmar**(CAGR/最大回撤) | 每单位最差损失回报 | 高度依赖窗口;>1 可接受 |

### 9.2 回撤路径与 Ulcer 指数
最大回撤只是单点;**水下净值曲线与回撤持续期**才反映"资金被困亏损多久"——这才是融资压力/赎回导致策略崩溃的真实机制。**Ulcer 指数 = √(所有时点回撤百分比平方均值)**:同时惩罚深度与持续期。
- 参考:[Calmar Ratio and Ulcer Index (Wallible)](https://www.wallible.com/en/blog/2025-09-22-calmar-ratio-ulcer-index/)

### 9.3 尾部风险 CVaR/ES
**CVaR/预期尾部损失 ES**:损失超过 VaR 分位时的期望损失。**VaR 非一致风险测度**(不满足次可加性,组合 VaR 可高于成分);CVaR 一致。关键:高斯下 VaR/CVaR 都坍缩为 σ 倍数——**尾部测度仅在非正态时才增加信息**。

### 9.4 压力测试
- **历史情景回放**:2008、2020 新冠、互联网泡沫、1987 黑色星期一。
- **反向压力测试 Reverse Stress Testing**:从预设不可接受损失(组合跌 20%、融资违约)倒推引发它的市场状态——暴露没想到的失败模式。
- **危机相关性破裂**:压力下多样化资产相关性趋向 1,分散化在最需要时蒸发。**任何可信压力测试都必须重估压力期相关性**,而非复用平静期协方差。

### 9.5 区制检测与结构性断裂 ★
- **它是什么**:同一指数在不同区制(regime)下"像四个不同资产"。按趋势(200 日均线)×波动率(20 日滚动标准差)可分四态,熊/动荡态最差(−0.089%/日)。方法:**Hamilton 马尔可夫区制切换**、**变点检测(CUSUM、Chow、SADF)**。
- **为什么重要**:大量"它突然失效了"的失败,是策略"在无人察觉时被拟合到了某个区制"。卖波动策略可数年稳健收租、一次区制切换就被抹平。**结构性断裂(永久)不同于会回归的区制切换**——区分两者是最难也最有价值的判断之一。
- **业界怎么处理**:区制感知下注(不利区制减仓/关停);跨策略类型分散;用**夏普稳定性比率(各子窗口平均夏普/夏普标准差)**衡量跨区制一致性。
- 参考:[Regimes and Breaks (OpenAlgo)](https://openalgo.in/quant/regimes-and-breaks) | [Hamilton (2005) Regime-Switching](https://econweb.ucsd.edu/~jhamilto/palgrav1.pdf) | [Sharpe Stability Ratio (Macrosynergy)](https://macrosynergy.com/research/the-sharpe-stability-ratio-of-trading-strategies/)

---

## 十、Alpha 衰减、容量、拥挤与生命周期管理

衰减、容量、拥挤是同一机制的三副面孔——成功信号吸引资金与参与者,抬高冲击、侵蚀边际。

### 10.1 半衰期与衰减项结构
- **信息/IC 半衰期**:AR(1) 模型 T_½ = ln(0.5)/ln(φ)。经验值:动量 ~3 月、投资 ~1 月、质量 ~4–5 月、价值 ~3–4 月、低波 ~5–6 月。
- **Alpha 衰减项结构**:机构数据显示新仓增量 alpha 第 1 月最高、2–3 月衰减、约第 6 月统计上为零。大盘/被广泛关注的名字衰减更快。**实盘衰减项结构随时间缩短是领先警报(即便头条夏普仍好看)**。
- 参考:[How Often Should We Rebalance Equity Factor Portfolios? (Quantpedia)](https://quantpedia.com/how-often-should-we-rebalance-equity-factor-portfolios/) | [Alpha Decay (Di Mascio et al.)](https://jhfinance.web.unc.edu/wp-content/uploads/sites/12369/2016/02/Alpha-Decay.pdf)

### 10.2 拥挤指标矩阵
生产系统监控:交易失衡/共交易强度(动量组合再平衡流已占总订单流 ~1–2% 且上升)、估值/因子价差压缩、做空利用率、经理间业绩离散度收敛、PCA 信号有效维度(低维="独立 alpha"其实是同一拥挤下注)。
- 参考:[Zooming in on Equity Factor Crowding (CFM)](https://www.cfm.com/zooming-in-on-equity-factor-crowding/) | [Crowding Reassessment (Acadian)](https://www.acadian-asset.com/investment-insights/systematic-methods/misplaced-anxiety-a-reassessment-of-crowding-in-systematic-investing)

### 10.3 与现有组合的相关性 / 增量维度 ★
候选策略能否进入组合,取决于是否**提升有效正交驱动因子数(signal dimensionality)**。高原始相关性意味着只增风险、不带来边际 alpha。**增量 alpha 测试本质是维度测试**。需先对各策略做**波动率目标化(vol-targeting)**再做相关性/边际风险评估,否则高波动策略仅因波动率就主导排名。

### 10.4 退役机制
机制化、非相机抉择:各策略波动率目标化后,按 3/12/24 月滚动风险调整收益排名,施加**策略动量叠加**(均线之上增持、之下减持/退役)。对策略电池本身施加 24 月趋势叠加表现更优。
- 参考:[Multi-Strategy Management (Quantpedia)](https://quantpedia.com/multi-strategy-management-for-your-portfolio/)

---

## 十一、模型风险与治理 ★

- **它是什么**:把每个量化模型当作有生命周期、需独立验证和治理的资产。**模型风险 = 因模型输出有缺陷或被误用而导致负面结果的可能性**。监管机构(美联储/OCC)**SR 11-7**(2011,2026 修订为 SR 26-2)把模型验证制度化。
- **业界怎么处理(SR 11-7 三支柱)**:(1) **概念合理性**(审查模型设计与开发证据);(2) **持续监控**(过程验证 + 基准对比);(3) **结果分析(含回测)**(预测与实际对比)。外加**有效挑战(Effective Challenge)**——独立技术方的批判性分析;维护**模型清单**、完整文档、明确角色责任。
- 参考:[SR 11-7 Guidance (Federal Reserve)](http://www.federalreserve.gov/boarddocs/srletters/2011/sr1107.pdf) | [SR 11-7 Explained (Modelop)](https://www.modelop.com/ai-governance/ai-regulations-standards/sr-11-7) | [From SR 11-7 to SR 26-2 (Moody's)](https://www.moodys.com/web/en/us/insights/banking/from-sr117-to-sr262-managing-model-risk-when-models-dont-stand-still.html)

---

## 业界策略系统性评测清单

> 严肃机构在批准一个策略投入真实资金前,按以下分组逐项核验。★ = 文献缺口补充项。

**A. 数据与信息基础**
- [ ] 用点在时间(PIT)数据重建宇宙,含退市/并购/破产标的(无幸存者偏差)
- [ ] 公司行动按时点处理;审计特征无未来函数(look-ahead)
- [ ] 研究域与交易域严格隔离;记录对样本外域的访问次数
- [ ] 数据质量与相关性经过严格评估

**B. 统计显著性与过拟合**
- [ ] 记录全部试验计数 N(含丢弃的),计算有效独立试验数
- [ ] 报告 DSR ≥0.95(或 Haircut Sharpe),而非原始夏普
- [ ] 因子发现通过 t≥3.0(Bonferroni/Holm/BHY 校正)
- [ ] PBO <0.5(CSCV 检验未严重过拟合)
- [ ] 有事前书面经济假设;样本长度 ≥ MinTRL

**C. 信号/因子质量**
- [ ] Spearman 秩 IC 显著为正;ICIR(IC 的 t 统计)稳定,非仅均值高
- [ ] IC 衰减/半衰期与再平衡频率匹配
- [ ] 分位单调、多空价差干净;收益非集中于微盘股
- [ ] 行业/风格中性化后 alpha 仍存
- [ ] 换手率调整后 IR 仍可接受(成本后排名)

**D. 业绩归因与风险分解**
- [ ] Brinson 收益归因(配置 vs 选股)明确
- [ ] 因子回归残差 alpha 显著(非隐藏 beta);因子模型 R² 合理
- [ ] 主动风险中特异性 > 因子(确认是选股而非因子择时)
- [ ] TCAR 按持仓加总=100%,定位风险集中持仓
- [ ] 统计 vs 基本面风险价差无异常(无未命名因子暴露)

**E. 选股技能**
- [ ] 扩展基本定律 IR = TC×IC×√breadth 各项可解释;TC 未被约束压垮
- [ ] 胜率/回报比分解(Van Loon);价值非来自少数"大满贯"
- [ ] 横截面离散度(机会集)充足;按行业离散度分配活动
- [ ] "什么都不做"诊断:选股 vs 规模 vs 择时/活动分离

**F. 成本、冲击、容量与执行**
- [ ] 执行落差(IS)建模含永久冲击(非仅临时);平方根冲击法则
- [ ] 换手率成本拖累已计入;参与率 <ADV 几个百分点
- [ ] 报告容量上限(非线性),并列出"容量调整后夏普"
- [ ] ★ 真实执行场所/SOR/TCA 验证最佳执行(明场所+暗池路由)
- [ ] ★ 做空约束建模:融券费、Reg SHO 定位/Rule 201、做空可行集
- [ ] ★ 对应税投资者报告税后 alpha;换手率税收拖累已计入

**G. 组合构建与优化**
- [ ] 优化器样本外跑赢 1/N 等权与最小方差(非自家样本内前沿)
- [ ] 协方差矩阵条件数正常;已用 Ledoit-Wolf 收缩或 HRP
- [ ] 有效资产数合理(非"名字分散、风险集中");无角点解
- [ ] 约束集(杠杆/换手/基数/中性/TEV)已列明,毛→净 alpha 可追溯
- [ ] 再平衡为净成本最优(偏差带 vs 月度,非免费午餐)

**H. 样本外验证与参数稳定性**
- [ ] 交叉验证带清洗(purging)+ 禁运(embargo)或 CPCV,无泄漏
- [ ] 前向分析汇总样本外为正;**最优参数跨窗口稳定**
- [ ] ★ 参数敏感性:"选高原不选孤峰",参数稳健性评分达标
- [ ] ML 标签用三障碍 + 元标记;特征重要性 OOS 且控制相关性
- [ ] 通过生命周期门控序列(纸面交易 → 小规模实盘 → 扩规模)

**I. 风险调整、回撤、尾部与压力**
- [ ] 夏普之外报告 Sortino/Omega/Calmar;周期一致、同类比较
- [ ] 回撤路径 + Ulcer 指数 + 持续期(非仅最大回撤单点)
- [ ] 尾部用 CVaR/ES(非 VaR);确认收益非正态后尾部测度有信息增量
- [ ] 历史情景回放 + 反向压力测试 + 危机相关性破裂重估
- [ ] ★ 区制/结构性断裂检测(HMM/CUSUM);不利区制有减仓机制;夏普稳定性比率高

**J. 衰减、容量、拥挤与组合契合**
- [ ] 半衰期已知;衰减项结构未被监测到缩短
- [ ] 拥挤仪表盘(共交易强度/估值价差/做空利用率/经理离散度)无红灯
- [ ] ★ 与现有组合相关性低、提升有效维度(增量 alpha 测试通过)
- [ ] 各策略波动率目标化后排名;有机械动量叠加退役机制
- [ ] 部署后持续监控上述全部指标,衰减即触发退役

**K. ★ 模型风险与治理**
- [ ] 模型有独立验证(概念合理性 + 持续监控 + 结果分析/回测)
- [ ] 有"有效挑战"——独立技术方批判性审查假设与局限
- [ ] 维护模型清单、完整文档、明确角色责任;供应商模型同标准

---

> **终极提醒**:所有 DSR/PSR/PBO/MinTRL 这类指标,最有价值的用途是告诉你**何时不要信任一个回测**——这比任何单一"好"数字都重要。唯一真正的样本外是实盘交易。本清单的目标不是让某个策略"通过",而是让虚假发现的策略**无法通过**。
