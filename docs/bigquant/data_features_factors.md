# BigQuant 因子库字段分类清单

- 来源页面：`https://bigquant.com/doc/data_features.html`
- 本地 HTML：`docs/bigquant/data_features.html`
- 生成时间：2026-07-09 14:31:44 +0800
- 基础字段数：2
- 因子字段/模板数：186

> 说明：本文按 BigQuant 文档中的分类整理。包含 `$i` 的字段是因子模板，需按同一行的 `$i 取值` 生成具体字段名，例如 `close_$i` 在 `$i=0` 时为 `close_0`。


## 如何获取这些因子

BigQuant 文档给出的因子库访问接口是：

```python
D.features(
    instruments,
    start_date='2005-01-01',
    end_date=None,
    fields=['close_0', 'close_1'],
    groupped_by_instrument=False,
    frequency='daily',
)
```

### 参数说明

| 参数 | 类型/示例 | 说明 |
| --- | --- | --- |
| `instruments` | `['000001.SZA']` | 证券代码列表。文档示例使用 `000001.SZA`。 |
| `start_date` | `'2017-01-01'` | 开始日期。 |
| `end_date` | `'2017-01-07'` / `None` | 结束日期；为 `None` 时由接口默认处理。 |
| `fields` | `['close_1', 'return_20']` | 需要读取的字段列表。字段名见下方分类表。 |
| `groupped_by_instrument` | `False` / `True` | 是否按 `instrument` 分组返回；`False` 返回 `DataFrame`，`True` 返回按证券代码分组的 `dict`。注意文档参数名拼写为 `groupped_by_instrument`。 |
| `frequency` | `'daily'` | 数据周期。文档写明传入 `'daily'` 或 `'minute'`，但目前功能上只支持 `'daily'`。 |

### 返回结果

- `groupped_by_instrument=False`：返回一个 `DataFrame`，通常包含 `date`、`instrument` 以及请求的因子字段。
- `groupped_by_instrument=True`：返回一个 `dict`，按 `instrument` 分组。

### 基础示例

```python
# 获取平安银行 2017-01-01 到 2017-01-07 的前一交易日收盘价因子
df = D.features(
    instruments=['000001.SZA'],
    start_date='2017-01-01',
    end_date='2017-01-07',
    fields=['close_1'],
)
```

### 多股票、多因子示例

```python
fields = [
    'close_0',
    'return_20',
    'rank_return_20',
    'market_cap_0',
    'pe_ttm_0',
    'ta_rsi_14_0',
    'volatility_60_0',
]

df = D.features(
    instruments=['000001.SZA', '000002.SZA'],
    start_date='2024-01-01',
    end_date='2024-12-31',
    fields=fields,
    groupped_by_instrument=False,
    frequency='daily',
)
```

### `$i` 模板字段如何展开

下方分类表中包含 `$i` 的字段不是直接请求字段，需要先替换为具体取值：

```text
close_$i        + i=0   => close_0
return_$i       + i=20  => return_20
ta_rsi_$i_0     + i=14  => ta_rsi_14_0
volatility_$i_0 + i=60  => volatility_60_0
```

例如请求 20 日收益和 60 日波动率：

```python
df = D.features(
    instruments=['000001.SZA'],
    start_date='2024-01-01',
    end_date='2024-12-31',
    fields=['return_20', 'rank_return_20', 'volatility_60_0', 'rank_volatility_60_0'],
)
```

### 与当前项目 BigQuant SDK 的关系

本页文档描述的是因子库接口 `D.features`。当前项目主要使用 BigQuant DAI / BigTrader 链路；如果运行环境中没有注入全局 `D` 对象，需要在 BigQuant 官方 Notebook / 支持 `D.features` 的运行环境中执行，或再确认新版 SDK 是否提供等价 DAI 数据源。

## 分类目录

- [数据字段](#数据字段)：2 项
- [基本信息](#基本信息)：14 项
- [量价因子](#量价因子)：15 项
- [换手率因子](#换手率因子)：4 项
- [估值因子](#估值因子)：17 项
- [资金流](#资金流)：13 项
- [财务因子](#财务因子)：68 项
- [股东因子](#股东因子)：8 项
- [技术分析因子](#技术分析因子)：25 项
- [波动率](#波动率)：4 项
- [BETA值](#beta值)：18 项

## 数据字段

共 2 项。

| 字段名 | 描述 | 适用市场 |
| --- | --- | --- |
| date | 交易日期 | A股,基金,期货,港股,美股 |
| instrument | 证券代码 | A股,基金,期货,港股,美股 |

## 基本信息

共 14 项。

| 字段名 | 描述 | 适用市场 |
| --- | --- | --- |
| list_days_0 | 已经上市的天数，按自然日计算 | A股,港股,美股 |
| list_board_0 | 上市板，主板：1，中小企业板：2，创业板：3 | A股 |
| company_found_date_0 | 公司成立天数 | A股 |
| st_status_0 | ST状态：0：正常股票，1：ST，2： * ST，11：暂停上市 | A股 |
| industry_sw_level1_0 | 申万一级行业类别 | A股 |
| industry_sw_level2_0 | 申万二级行业类别 | A股 |
| industry_sw_level3_0 | 申万三级行业类别 | A股 |
| in_sse50_0 | 是否属于上证50指数成份 | A股 |
| in_csi300_0 | 是否属于沪深300指数成份 | A股 |
| in_csi500_0 | 是否属于中证500指数成份 | A股 |
| in_csi800_0 | 是否属于中证800指数成份 | A股 |
| in_sse180_0 | 是否属于上证180指数成份 | A股 |
| in_csi100_0 | 是否属于中证100指数成份 | A股 |
| in_szse100_0 | 是否属于深证100指数成份 | A股 |

## 量价因子

共 15 项。

| 字段名 | $i 取值 | 描述 | 适用市场 |
| --- | --- | --- | --- |
| open_$i | [0 .. 20] | 第前i个交易日的开盘价，当天为0 | A股,基金,期货,港股,美股 |
| high_$i | [0 .. 20] | 第前i个交易日的最高价 | A股,基金,期货,港股,美股 |
| low_$i | [0 .. 20] | 第前i个交易日的最低价 | A股,基金,期货,港股,美股 |
| volume_$i | [0 .. 20] | 第前i个交易日的交易量 | A股,基金,期货,港股,美股 |
| adjust_factor_$i | [0 .. 20] | 第前i个交易日的复权因子 | A股,基金,港股,美股 |
| deal_number_$i | [0 .. 20] | 第前i个交易日的成交笔数 | A股 |
| price_limit_status_$i | [0 .. 20] | 第前i个交易日的股价在收盘时的涨跌停状态，1表示跌停，2表示未涨跌停，3则表示涨停 | A股,基金 |
| close_$i | [0 .. 120] | 第前i个交易日的收盘价 | A股,基金,期货,港股,美股 |
| daily_return_$i | [0 .. 20]; 30, 40, 50, 60, 70, 80, 90, 120, 150, 180, 210, 240, 270, 300, 330, 360 | 第前i个交易日的收益，=close_i/close_(i+1) | A股,基金,期货,港股,美股 |
| return_$i | [0 .. 20]; 30, 40, 50, 60, 70, 80, 90, 120, 150, 180, 210, 240, 270, 300, 330, 360 | 过去i个交易日的收益，=close_0/close_(i+1) | A股,基金,期货,港股,美股 |
| rank_return_$i | [0 .. 20]; 30, 40, 50, 60, 70, 80, 90, 120, 150, 180, 210, 240, 270, 300, 330, 360 | 过去i个交易日的收益 (return_i) 排名，=从小到大排名序号/总数 | A股,基金,期货,港股,美股 |
| amount_$i | [0 .. 120] | 第前i个交易日的交易额 | A股,基金,期货,港股,美股 |
| rank_amount_$i | [0 .. 120] | 第前i个交易日的交易额百分比排名 | A股,基金,期货,港股,美股 |
| avg_amount_$i | [0 .. 20]; 30, 40, 50, 60, 70, 80, 90, 120, 150, 180, 210, 240, 270, 300, 330, 360 | 过去i个交易日的平均交易额，0表示今日 | A股,基金,期货,港股,美股 |
| rank_avg_amount_$i | [0 .. 20]; 30, 40, 50, 60, 70, 80, 90, 120, 150, 180, 210, 240, 270, 300, 330, 360 | 过去i个交易日的平均交易额，百分比排名 | A股,基金,期货,港股,美股 |

## 换手率因子

共 4 项。

| 字段名 | $i 取值 | 描述 | 适用市场 |
| --- | --- | --- | --- |
| turn_$i | [0 .. 20] | 第前i个交易日的换手率 | A股,基金,港股,美股 |
| rank_turn_$i | [0 .. 20] | 过去i个交易日的换手率 (turn_i) 排名，=从小到大排名序号/总数 | A股,基金,港股,美股 |
| avg_turn_$i | [0 .. 20]; 30, 40, 50, 60, 70, 80, 90, 120, 150, 180, 210, 240, 270, 300, 330, 360 | 过去i个交易日的平均换手率，0表示今日 | A股,基金,港股,美股 |
| rank_avg_turn_$i | [0 .. 20]; 30, 40, 50, 60, 70, 80, 90, 120, 150, 180, 210, 240, 270, 300, 330, 360 | 过去i个交易日的平均换手率排名，=从小到大排名序号/总数 | A股,基金,港股,美股 |

## 估值因子

共 17 项。

| 字段名 | 描述 | 适用市场 |
| --- | --- | --- |
| market_cap_0 | 总市值 | A股,港股,美股 |
| rank_market_cap_0 | 总市值，升序百分比排名 | A股,港股,美股 |
| market_cap_float_0 | 流通市值 | A股,港股,美股 |
| rank_market_cap_float_0 | 流通市值，升序百分比排名 | A股,港股,美股 |
| pe_ttm_0 | 市盈率 (TTM) | A股,港股,美股 |
| rank_pe_ttm_0 | 市盈率 (TTM)，升序百分比排名 | A股,港股,美股 |
| pe_lyr_0 | 市盈率 (LYR) | A股,港股,美股 |
| rank_pe_lyr_0 | 市盈率 (LYR)，升序百分比排名 | A股,港股,美股 |
| pb_lf_0 | 市净率 (LF) | A股 |
| rank_pb_lf_0 | 市净率 (LF)，升序百分比排名 | A股 |
| pb_mrq_0 | 市净率 (MRQ) | 港股,美股 |
| rank_pb_mrq_0 | 市净率 (MRQ)，升序百分比排名 | 港股,美股 |
| ps_ttm_0 | 市销率 (TTM) | A股,港股,美股 |
| rank_ps_ttm_0 | 市销率 (TTM)，升序百分比排名 | A股,港股,美股 |
| west_netprofit_ftm_0 | 一致预测净利润（未来12个月） | A股 |
| west_eps_ftm_0 | 一致预测每股收益（未来12个月） | A股 |
| west_avgcps_ftm_0 | 一致预测每股现金流（未来12个月） | A股 |

## 资金流

共 13 项。

| 字段名 | $i 取值 | 描述 | 适用市场 |
| --- | --- | --- | --- |
| mf_net_amount_$i | [0 .. 20] | 第前i个交易日净主动买入额，= 买入金额 - 卖出金额 (包括超大单、大单、中单或小单) | A股 |
| avg_mf_net_amount_$i | [0 .. 20] | 过去i个交易日平均净主动买入额 | A股 |
| rank_avg_mf_net_amount_$i | [0 .. 20] | 过去i个交易日平均净主动买入额排名 | A股 |
| mf_net_amount_main_0 |  | 主力净流入净额 | A股 |
| mf_net_pct_main_0 |  | 主力净流入占比 | A股 |
| mf_net_amount_xl_0 |  | 超大单净流入净额 | A股 |
| mf_net_pct_xl_0 |  | 超大单净流入占比 | A股 |
| mf_net_amount_l_0 |  | 大单净流入净额 | A股 |
| mf_net_pct_l_0 |  | 大单净流入占比 | A股 |
| mf_net_amount_m_0 |  | 中单净流入净额 | A股 |
| mf_net_pct_m_0 |  | 中单净流入占比 | A股 |
| mf_net_amount_s_0 |  | 小单净流入净额 | A股 |
| mf_net_pct_s_0 |  | 小单净流入占比 | A股 |

## 财务因子

共 68 项。

| 字段名 | 描述 | 适用市场 |
| --- | --- | --- |
| fs_publish_date_0 | 最近财报发布距今天数，按自然日计算，当天为0 | A股 |
| fs_quarter_year_0 | 财报对应的年份 | A股 |
| fs_quarter_index_0 | 财报对应的季度，取值 1/2/3/4，1表示第一季度，以此类推 | A股 |
| fs_net_profit_0 | 归属母公司股东的净利润 | A股 |
| fs_net_profit_ttm_0 | 归属母公司股东的净利润 (TTM) | A股 |
| fs_net_profit_yoy_0 | 归属母公司股东的净利润同比增长率 | A股 |
| rank_fs_net_profit_yoy_0 | 归属母公司股东的净利润同比增长率，升序百分比排名 | A股 |
| fs_net_profit_qoq_0 | 归属母公司股东的净利润单季度环比增长率 | A股 |
| rank_fs_net_profit_qoq_0 | 归属母公司股东的净利润单季度环比增长率，升序百分比排名 | A股 |
| fs_deducted_profit_0 | 扣除非经常性损益后的净利润 | A股 |
| fs_deducted_profit_ttm_0 | 扣除非经常性损益后的净利润 (TTM) | A股 |
| fs_roe_0 | 净资产收益率 | A股 |
| rank_fs_roe_0 | 净资产收益率，升序百分比排名 | A股 |
| fs_roe_ttm_0 | 净资产收益率 (TTM) | A股 |
| rank_fs_roe_ttm_0 | 净资产收益率 (TTM)，升序百分比排名 | A股 |
| fs_roa_0 | 总资产报酬率 | A股 |
| rank_fs_roa_0 | 总资产报酬率，升序百分比排名 | A股 |
| fs_roa_ttm_0 | 总资产报酬率 (TTM) | A股 |
| rank_fs_roa_ttm_0 | 总资产报酬率 (TTM)，升序百分比排名 | A股 |
| fs_gross_profit_margin_0 | 销售毛利率 | A股 |
| fs_gross_profit_margin_ttm_0 | 销售毛利率 (TTM) | A股 |
| fs_net_profit_margin_0 | 销售净利率 | A股 |
| fs_net_profit_margin_ttm_0 | 销售净利率 (TTM) | A股 |
| fs_operating_revenue_0 | 营业收入 | A股 |
| fs_operating_revenue_ttm_0 | 营业收入 (TTM) | A股 |
| fs_operating_revenue_yoy_0 | 营业收入同比增长率 | A股 |
| rank_fs_operating_revenue_yoy_0 | 营业收入同比增长率，升序百分比排名 | A股 |
| fs_operating_revenue_qoq_0 | 营业收入单季度环比增长率 | A股 |
| rank_fs_operating_revenue_qoq_0 | 营业收入单季度环比增长率，升序百分比排名 | A股 |
| fs_free_cash_flow_0 | 企业自由现金流 | A股 |
| fs_net_cash_flow_0 | 经营活动产生的现金流量净额 | A股 |
| fs_net_cash_flow_ttm_0 | 经营活动现金净流量 (TTM) | A股 |
| fs_eps_0 | 每股收益 | A股 |
| rank_fs_eps_0 | 每股收益，升序百分比排名 | A股 |
| fs_eps_yoy_0 | 每股收益同比增长率 | A股 |
| rank_fs_eps_yoy_0 | 每股收益同比增长率，升序百分比排名 | A股 |
| fs_bps_0 | 每股净资产 | A股 |
| rank_fs_bps_0 | 每股净资产，升序百分比排名 | A股 |
| fs_current_assets_0 | 流动资产 | A股 |
| fs_non_current_assets_0 | 非流动资产 | A股 |
| fs_current_liabilities_0 | 流动负债 | A股 |
| fs_non_current_liabilities_0 | 非流动负债 | A股 |
| fs_cash_ratio_0 | 现金比率 | A股 |
| rank_fs_cash_ratio_0 | 现金比率，升序百分比排名 | A股 |
| fs_common_equity_0 | 普通股权益总额 | A股 |
| fs_cash_equivalents_0 | 货币资金 | A股 |
| fs_account_receivable_0 | 应收账款 | A股 |
| fs_fixed_assets_0 | 固定资产 | A股 |
| fs_proj_matl_0 | 工程物资 | A股 |
| fs_construction_in_process_0 | 在建工程 | A股 |
| fs_fixed_assets_disp_0 | 固定资产清理 | A股 |
| fs_account_payable_0 | 应付账款 | A股 |
| fs_total_liability_0 | 负债合计 | A股 |
| fs_paicl_up_capital_0 | 实收资本（或股本） | A股 |
| fs_capital_reserves_0 | 资本公积金 | A股 |
| fs_surplus_reserves_0 | 盈余公积金 | A股 |
| fs_undistributed_profit_0 | 未分配利润 | A股 |
| fs_eqy_belongto_parcomsh_0 | 归属母公司股东的权益 | A股 |
| fs_total_equity_0 | 所有者权益合计 | A股 |
| fs_gross_revenues_0 | 营业总收入 | A股 |
| fs_total_operating_costs_0 | 营业总成本 | A股 |
| fs_selling_expenses_0 | 销售费用 | A股 |
| fs_financial_expenses_0 | 财务费用 | A股 |
| fs_general_expenses_0 | 管理费用 | A股 |
| fs_operating_profit_0 | 营业利润 | A股 |
| fs_total_profit_0 | 利润总额 | A股 |
| fs_income_tax_0 | 所得税 | A股 |
| fs_net_income_0 | 净利润 | A股 |

## 股东因子

共 8 项。

| 字段名 | 描述 | 适用市场 |
| --- | --- | --- |
| sh_holder_avg_pct_0 | 户均持股比例 | A股 |
| rank_sh_holder_avg_pct_0 | 户均持股比例，升序百分比排名 | A股 |
| sh_holder_avg_pct_3m_chng_0 | 户均持股比例季度增长率 | A股 |
| rank_sh_holder_avg_pct_3m_chng_0 | 户均持股比例季度增长率，升序百分比排名 | A股 |
| sh_holder_avg_pct_6m_chng_0 | 户均持股比例半年增长率 | A股 |
| rank_sh_holder_avg_pct_6m_chng_0 | 户均持股比例半年增长率，升序百分比排名 | A股 |
| sh_holder_num_0 | 股东户数 | A股 |
| rank_sh_holder_num_0 | 股东户数，升序百分比排名 | A股 |

## 技术分析因子

共 25 项。

| 字段名 | $i 取值 | 描述 | 适用市场 |
| --- | --- | --- | --- |
| ta_sma_$i_0 | 5, 10, 20, 30, 60 | 收盘价的i日简单移动平均值 | A股,基金,期货,港股,美股 |
| ta_ema_$i_0 | 5, 10, 20, 30, 60 | 收盘价的i日指数移动平均值 | A股,基金,期货,港股,美股 |
| ta_wma_$i_0 | 5, 10, 20, 30, 60 | 收盘价的i日加权移动平均值 | A股,基金,期货,港股,美股 |
| ta_ad_0 |  | 收集派发指标 | A股,基金,期货,港股,美股 |
| ta_aroon_down_$i_0 | 14, 28 | 阿隆指标aroondown，timeperiod=i | A股,基金,期货,港股,美股 |
| ta_aroon_up_$i_0 | 14, 28 | 阿隆指标aroonup，timeperiod=i | A股,基金,期货,港股,美股 |
| ta_aroonosc_$i_0 | 14, 28 | AROONOSC指标，timeperiod=i | A股,基金,期货,港股,美股 |
| ta_atr_$i_0 | 14, 28 | ATR指标，timeperiod=i | A股,基金,期货,港股,美股 |
| ta_bbands_upperband_$i_0 | 14, 28 | BBANDS指标，timeperiod=i | A股,基金,期货,港股,美股 |
| ta_bbands_middleband_$i_0 | 14, 28 | BBANDS指标，timeperiod=i | A股,基金,期货,港股,美股 |
| ta_bbands_lowerband_$i_0 | 14, 28 | BBANDS指标，timeperiod=i | A股,基金,期货,港股,美股 |
| ta_adx_$i_0 | 14, 28 | ADX指标，timeperiod=i | A股,基金,期货,港股,美股 |
| ta_cci_$i_0 | 14, 28 | CCI指标，timeperiod=i | A股,基金,期货,港股,美股 |
| ta_macd_macd_12_26_9_0 |  | MACD | A股,基金,期货,港股,美股 |
| ta_macd_macdsignal_12_26_9_0 |  | MACD | A股,基金,期货,港股,美股 |
| ta_macd_macdhist_12_26_9_0 |  | MACD | A股,基金,期货,港股,美股 |
| ta_obv_0 |  | OBV指标 | A股,基金,期货,港股,美股 |
| ta_stoch_slowk_5_3_0_3_0_0 |  | STOCH (KDJ) 指标K值 | A股,基金,期货,港股,美股 |
| ta_stoch_slowd_5_3_0_3_0_0 |  | STOCH (KDJ) 指标D值 | A股,基金,期货,港股,美股 |
| ta_mfi_$i_0 | 14, 28 | MFI指标，timeperiod=i | A股,基金,期货,港股,美股 |
| ta_rsi_$i_0 | 14, 28 | RSI指标，timeperiod=i | A股,基金,期货,港股,美股 |
| ta_trix_$i_0 | 14, 28 | TRIX指标，timeperiod=i | A股,基金,期货,港股,美股 |
| ta_sar_0 |  | SAR指标 | A股,基金,期货,港股,美股 |
| ta_mom_$i_0 | 10, 20, 30, 60 | MOM指标，timperiod=i | A股,基金,期货,港股,美股 |
| ta_willr_$i_0 | 14, 28 | WILLR指标，timeperiod=i | A股,基金,期货,港股,美股 |

## 波动率

共 4 项。

| 字段名 | $i 取值 | 描述 | 适用市场 |
| --- | --- | --- | --- |
| swing_volatility_$i_0 | 5, 10, 30, 60, 120, 240 | 振幅波动率，timeperiod=i | A股,基金,期货,港股,美股 |
| rank_swing_volatility_$i_0 | 5, 10, 30, 60, 120, 240 | 振幅波动率，timeperiod=i，升序百分比排名 | A股,基金,期货,港股,美股 |
| volatility_$i_0 | 5, 10, 30, 60, 120, 240 | 波动率，timeperiod=i | A股,基金,期货,港股,美股 |
| rank_volatility_$i_0 | 5, 10, 30, 60, 120, 240 | 波动率，timeperiod=i，升序百分比排名 | A股,基金,期货,港股,美股 |

## BETA值

共 18 项。

| 字段名 | $i 取值 | 描述 | 适用市场 |
| --- | --- | --- | --- |
| beta_sse50_$i_0 | 5, 10, 30, 60, 90, 120, 180 | BETA值(上证50)，timeperiod=i | A股 |
| rank_beta_sse50_$i_0 | 5, 10, 30, 60, 90, 120, 180 | BETA值(上证50)，timeperiod=i，升序百分比排名 | A股 |
| beta_csi300_$i_0 | 5, 10, 30, 60, 90, 120, 180 | BETA值(沪深300)，timeperiod=i | A股 |
| rank_beta_csi300_$i_0 | 5, 10, 30, 60, 90, 120, 180 | BETA值(沪深300)，timeperiod=i，升序百分比排名 | A股 |
| beta_csi500_$i_0 | 5, 10, 30, 60, 90, 120, 180 | BETA值(中证500)，timeperiod=i | A股 |
| rank_beta_csi500_$i_0 | 5, 10, 30, 60, 90, 120, 180 | BETA值(中证500)，timeperiod=i，升序百分比排名 | A股 |
| beta_csi800_$i_0 | 5, 10, 30, 60, 90, 120, 180 | BETA值(中证800)，timeperiod=i | A股 |
| rank_beta_csi800_$i_0 | 5, 10, 30, 60, 90, 120, 180 | BETA值(中证800)，timeperiod=i，升序百分比排名 | A股 |
| beta_sse180_$i_0 | 5, 10, 30, 60, 90, 120, 180 | BETA值(上证180)，timeperiod=i | A股 |
| rank_beta_sse180_$i_0 | 5, 10, 30, 60, 90, 120, 180 | BETA值(上证180)，timeperiod=i，升序百分比排名 | A股 |
| beta_csi100_$i_0 | 5, 10, 30, 60, 90, 120, 180 | BETA值(中证100)，timeperiod=i | A股 |
| rank_beta_csi100_$i_0 | 5, 10, 30, 60, 90, 120, 180 | BETA值(中证100)，timeperiod=i，升序百分比排名 | A股 |
| beta_szzs_$i_0 | 5, 10, 30, 60, 90, 120, 180 | BETA值(上证综指)，timeperiod=i | A股 |
| rank_beta_szzs_$i_0 | 5, 10, 30, 60, 90, 120, 180 | BETA值(上证综指)，timeperiod=i，升序百分比排名 | A股 |
| beta_gem_$i_0 | 5, 10, 30, 60, 90, 120, 180 | BETA值(创业板)，timeperiod=i | A股 |
| rank_beta_gem_$i_0 | 5, 10, 30, 60, 90, 120, 180 | BETA值(创业板)，timeperiod=i，升序百分比排名 | A股 |
| beta_industry_$i_0 | 5, 10, 30, 60, 90, 120, 180 | BETA值(所在行业)，timeperiod=i | A股 |
| rank_beta_industry_$i_0 | 5, 10, 30, 60, 90, 120, 180 | BETA值(所在行业)，timeperiod=i，升序百分比排名 | A股 |
