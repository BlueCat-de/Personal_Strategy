# BigQuant SDK 使用教程

来源：https://bigquant.com/wiki/doc/vac4qwmQr4

本文档整理自 BigQuant 官方 SDK 使用文档，并补充了本机环境验证记录。不要在本文档中写入真实 API Key、Token、用户名或密码。

## 概览

BigQuant SDK 是面向本地 IDE 开发的量化研究工具，可以在本地编写策略逻辑，同时调用 BigQuant 云端数据、回测和模拟交易能力。

核心模块：

- `dai`：云端数据查询，支持 SQL 查询 BigQuant 数据表。
- `bigtrader`：本地回测引擎，策略逻辑保留在本地。
- `papertrading`：查询和管理 BigQuant 平台上的模拟交易策略。
- `AIStudio`：云端计算，官方文档标注暂未开放。
- `FAI`：分布式计算，官方文档标注暂未开放或部分开放。

## 本机安装记录

BigQuant SDK 要求 Python 3.11。当前项目默认系统 Python 是 3.9，不适合安装完整 SDK，因此已单独创建 conda 环境：

```bash
conda create -n bigquant python=3.11 pip -y
conda activate bigquant
python -m pip install --upgrade pip setuptools wheel
python -m pip install 'bigquant[bigtrader]' -i https://pypi.bigquant.com/simple/
```

已验证版本：

```text
Python 3.11.15
bigquant==0.1.11
bigquant-core==0.1.14
bigtradercpp==0.1.22
```

验证命令：

```bash
conda run -n bigquant python -c "import sys, bigquant, bigquant_core, bigtradercpp; print(sys.version); print('bigquant ok')"
```

使用环境：

```bash
conda activate bigquant
```

## 快速安装

BigQuant SDK 支持 Windows、Linux 和 macOS，要求 Python 3.11。

全功能版：

```bash
pip install 'bigquant[all]' -i https://pypi.bigquant.com/simple/
```

仅数据查询：

```bash
pip install bigquant -i https://pypi.bigquant.com/simple/
```

数据查询 + 本地回测：

```bash
pip install 'bigquant[bigtrader]' -i https://pypi.bigquant.com/simple/
```

安装完成后验证：

```bash
bq --version
bq pkg list
```

## 登录认证

### API Key 认证

登录 BigQuant 平台，进入“我的 > API Keys > 创建”，获取 `AK.SK` 格式凭证。

命令行认证：

```bash
bq auth --apikey <AK.SK>
```

交互式认证：

```bash
bq auth configure
```

验证登录状态：

```bash
bq auth status
```

配置文件位置：

```text
~/.bigquant/config.json
```

### 用户名密码登录

```bash
bq auth login -u <用户名> -p <密码>
```

### 代码中初始化

```python
import bigquant

# 从 bq auth 自动生成的配置文件初始化
bigquant.init_from_config()

# 或者直接传入 AK/SK
bigquant.init(ak="YOUR_AK", sk="YOUR_SK")

# 或者使用 Token。Token 仅支持 HTTP，不支持 Arrow Flight 高速传输。
bigquant.init_from_token("YOUR_TOKEN")

# 或者使用用户名密码
bigquant.init_from_password("username", "password")

print(bigquant.whoami())
```

如果已经通过 `bq auth` 配置凭证，SDK 首次使用时会自动从配置文件加载，一般不需要在代码中显式调用 `init()`。

## Hello BigQuant

创建 `start.py`：

```python
from bigquant import dai
from bigquant import bigtrader


# 1. 提取数据。本地不存数据，通过 SQL 即取即用。
df = dai.query(
    "SELECT date, instrument, close FROM cn_stock_bar1d WHERE date >= '2024-01-01' LIMIT 10",
    filters={"date": ["2024-01-01", "2024-12-31"]},
).df()
print("数据获取成功：")
print(df.head())


# 2. 极简回测。策略逻辑保留在本地。
def initialize(context):
    context.set_commission(bigtrader.PerOrder(buy_cost=0.00005))
    context.asset = "000001.SZ"


def handle_data(context, data):
    if context.get_account_position(context.asset).amount == 0:
        context.order_target_percent(context.asset, 1.0)


performance = bigtrader.run(
    start_date="2024-01-01",
    end_date="2024-01-31",
    initialize=initialize,
    handle_data=handle_data,
)
print("回测完成，夏普比率:", performance.summary["sharp_ratio"])
```

运行：

```bash
conda activate bigquant
python start.py
```

## 数据查询：DAI

DAI 是 BigQuant SDK 的数据查询模块，通过 SQL 查询云端金融数据，支持 Arrow Flight 高性能传输。

### `cn_stock_bar1d` 字段口径

官方数据表地址：

```text
https://bigquant.com/data/datasources/cn_stock_bar1d
```

该表是股票后复权日行情。字段包括：

```text
instrument    证券代码
name          证券简称
adjust_factor 累计后复权因子
pre_close     昨收盘价（后复权）
open          开盘价（后复权）
close         收盘价（后复权）
high          最高价（后复权）
low           最低价（后复权）
volume        成交量，单位为股
deal_number   成交笔数
amount        成交金额
change_ratio  涨跌幅（后复权）
turn          换手率
upper_limit   涨停价
lower_limit   跌停价
date          日期
```

本项目当前离线数据使用接近前复权的价格口径，且 `volume` 使用“手”。因此接入 BigQuant 时不能直接使用原始 `open/high/low/close/volume`，需要做转换：

```text
qfq_price = hfq_price / latest_adjust_factor
volume_hand = volume_share / 100
turnover = turn
```

`bigquant_provider.py` 默认已经执行该转换：

```python
fetch_bigquant_daily_history_batch(
    ["000001", "600519"],
    start_date="2026-07-01",
    end_date="2026-07-06",
    adjust="qfq",
    volume_unit="hand",
)
```

已用 `000001` 和 `600519` 在 2026-07-01 至 2026-07-06 做过对比验证，转换后的 OHLC 与当前 Tencent/Sina 离线数据完全匹配，成交量仅有四舍五入误差。

### 基础查询

```python
from bigquant import dai

df = dai.query(
    """
    SELECT date, instrument, open, high, low, close, volume
    FROM cn_stock_bar1d
    WHERE date >= '2024-01-01' AND date <= '2024-03-31'
    """,
    filters={"date": ["2024-01-01", "2024-03-31"]},
).df()

print(df.head())
```

重要约束：查询有日期范围时，务必同时传 `filters` 参数，避免全表扫描。

### 结果格式转换

```python
from bigquant import dai

result = dai.query("SELECT * FROM cn_stock_bar1d LIMIT 100", full_db_scan=True)

df = result.df()          # pandas DataFrame
table = result.arrow()    # PyArrow Table
pl_df = result.pl()       # Polars DataFrame
rows = result.fetchall()  # Python list，每行为 dict

reader = result.fetch_arrow_reader(batch_size=10000)
for batch in reader:
    print(batch.num_rows)
```

### filters 分区过滤

```python
from bigquant import dai

df = dai.query(
    "SELECT * FROM cn_stock_bar1d WHERE close > 10",
    filters={"date": ["2024-01-01", "2024-12-31"]},
).df()

df = dai.query(
    "SELECT * FROM cn_stock_bar1d",
    filters={
        "date": ["2024-01-01", "2024-12-31"],
        "instrument": ["000001.SZ", "600519.SH"],
    },
).df()
```

### DataSource 读写

```python
import pandas as pd
from bigquant import dai

df = pd.DataFrame(
    {
        "date": ["2024-01-01", "2024-01-02"],
        "instrument": ["000001.SZ", "000001.SZ"],
        "my_signal": [1.0, -1.0],
    }
)

ds = dai.DataSource.write_bdb(
    data=df,
    id="my_signal_data",
    partitioning=["date"],
    overwrite=True,
)
print("写入成功，DataSource ID:", ds.id)

ds = dai.DataSource("my_signal_data")
df = ds.read_bdb(
    as_type=pd.DataFrame,
    partition_filter={"date": ("2024-01-01", "2024-01-31")},
    columns=["date", "instrument", "my_signal"],
)
```

写入自定义 DataSource 需要开通写入权限。

### 搜索和浏览数据表

```python
from bigquant import dai

results = dai.search_datasources("股票日线")
for item in results:
    docs = item.get("docs", {})
    print(item.get("id", ""), docs.get("cn_name", ""))

schema = dai.get_datasource_schema("cn_stock_bar1d")
print("表名:", schema["cn_name"])
for field in schema["fields"]:
    print(f"{field['name']:20} {field['type']:15} {field['description']}")

updates = dai.get_datasource_updates("cn_stock_bar1d")
for update in updates[:5]:
    print(update["created_at"], update.get("data", {}).get("rows"), "行")

my_ds = dai.list_datasources()
for ds in my_ds:
    print(ds.get("id"), ds.get("docs", {}).get("cn_name", ""))
```

## 本地回测：BigTrader

BigTrader 是 BigQuant SDK 的本地回测引擎，策略逻辑在本地运行。

### 基础回测

```python
from bigquant import bigtrader


def initialize(context):
    context.set_commission(bigtrader.PerOrder(buy_cost=0.0003, sell_cost=0.0003))
    context.asset = "000001.SZ"


def handle_data(context, data):
    position = context.get_account_position(context.asset)
    if position.amount == 0:
        context.order_target_percent(context.asset, 1.0)


performance = bigtrader.run(
    start_date="2024-01-01",
    end_date="2024-01-31",
    initialize=initialize,
    handle_data=handle_data,
    capital_base=1_000_000,
    benchmark="000300.SH",
)

print(performance.summary)
```

### 查看回测结果

```python
summary = performance.summary
print("夏普比率:", summary["sharp_ratio"])
print("最大回撤:", summary["max_drawdown"])
print("年化收益:", summary["annual_return_ratio"])
print("累计收益:", summary["return_ratio"])
print("胜率:", summary["win_ratio"])

raw_perf = performance.raw_perf
```

### 多资产策略

```python
from bigquant import bigtrader


def initialize(context):
    context.set_commission(bigtrader.PerOrder(buy_cost=0.0003, sell_cost=0.0003))
    context.stocks = ["000001.SZ", "000002.SZ", "600519.SH"]


def handle_data(context, data):
    for stock in context.stocks:
        context.order_target_percent(stock, 1.0 / len(context.stocks))


performance = bigtrader.run(
    start_date="2024-01-01",
    end_date="2024-01-31",
    initialize=initialize,
    handle_data=handle_data,
)
```

## 模拟交易：PaperTrading

PaperTrading 模块用于查询和管理 BigQuant 平台上的模拟交易策略。需要先在 BigQuant 平台上创建并运行策略。

### 获取策略列表

```python
from bigquant import papertrading

result = papertrading.list(page=1, size=20)
print(f"共 {result['count']} 个策略")
for item in result["items"]:
    status = "运行中" if item["status"] == 1 else "已停止"
    print(f"{item['id']} {item['strategy_name']} {status}")
```

### 获取策略详情

```python
from bigquant import papertrading

strategy = papertrading.get("your_strategy_id")
print(strategy.strategy_info)
```

### 查询持仓

```python
from bigquant import papertrading

strategy = papertrading.get("your_strategy_id")

latest = strategy.get_positions(order_by=["-trading_day"], page=1, size=1)
latest_day = latest["items"][0]["trading_day"]

positions = strategy.get_positions(
    constraints={"trading_day": latest_day},
    page=1,
    size=100,
)

for pos in positions["items"]:
    print(f"{pos['instrument']} 持仓: {pos['current_qty']} 盈亏: {pos['position_pnl']:.2f}")
```

### 查询已成交订单

```python
from bigquant import papertrading

strategy = papertrading.get("your_strategy_id")

latest = strategy.get_orders(order_by=["-trading_day"], page=1, size=1)
latest_day = latest["items"][0]["trading_day"]

orders = strategy.get_orders(
    constraints={"trading_day": latest_day},
    page=1,
    size=100,
)

for order in orders["items"]:
    direction = "买入" if str(order["direction"]) == "1" else "卖出"
    print(f"{order['instrument']} {direction} 成交量: {order['filled_qty']} 均价: {order['average_price']:.2f}")
```

### 查询待交易信号

```python
from bigquant import papertrading

strategy = papertrading.get("your_strategy_id")

latest = strategy.get_planned_orders(order_by=["-trading_day"], page=1, size=1)
latest_day = latest["items"][0]["trading_day"]

signals = strategy.get_planned_orders(
    constraints={"trading_day": latest_day},
    page=1,
    size=100,
)

for sig in signals["items"]:
    if sig.get("instrument") and sig.get("order_qty", 0) > 0:
        direction = "买入" if str(sig["direction"]) == "1" else "卖出"
        print(f"{sig['instrument']} {direction} 委托量: {sig['order_qty']} 委托价: {sig['order_price']:.2f}")
```

### 查询绩效

```python
from bigquant import papertrading

strategy = papertrading.get("your_strategy_id")
performances = strategy.get_performances()

if performances:
    latest = performances[-1]
    risk = latest[4] if isinstance(latest[4], dict) else {}
    print(f"交易日: {latest[0]}")
    print(f"累计收益: {latest[6]:.2%}")
    print(f"年化收益: {latest[3]:.2%}")
    print(f"最大回撤: {latest[2]:.2%}")
    print(f"夏普比率: {risk.get('sharpe', 'N/A')}")
```

### 获取分享策略列表

```python
from bigquant import papertrading

result = papertrading.list_shared(
    benchmark="000300.SH",
    page=1,
    size=20,
)

for item in result["items"]:
    print(f"{item.get('strategy_id')} {item.get('strategy_name')}")
```

### 删除策略

```python
from bigquant import papertrading

papertrading.unshare("your_strategy_id")
result = papertrading.delete("your_strategy_id")
print(f"已删除 {result.get('deleted', 0)} 个策略")
```

## AIStudio

官方文档标注暂未开放，SDK 接口仍在开发中。

## FAI 分布式计算

官方文档标注暂未开放或部分开放。FAI 需要 Python 3.11。

安装 FAI 依赖：

```bash
pip install 'bigquant[fai]' -i https://pypi.bigquant.com/simple/
```

## 常见问题

### 安装后找不到 `bq` 命令

在 conda 环境中优先使用：

```bash
conda activate bigquant
bq --version
```

如果仍找不到，检查当前环境的脚本路径：

```bash
which python
python -m pip show bigquant
```

### 查询报错 `full_db_scan=False but no filters provided`

需要提供 `filters` 参数，或者显式允许全表扫描：

```python
df = dai.query(sql, filters={"date": ["2024-01-01", "2024-12-31"]}).df()

df = dai.query(sql, full_db_scan=True).df()
```

优先使用 `filters`，全表扫描数据量大时较慢。

### Token 认证和 AK/SK 认证的区别

- AK/SK 认证支持全部功能，包括 Arrow Flight 高性能数据传输。
- Token 认证仅支持 HTTP 接口，不支持 Arrow Flight 直连模式。

### BigQuant AIStudio 网页端和本地 SDK 的区别

- 网页端通常是 `import dai`，本地 SDK 是 `from bigquant import dai`。
- 网页端无需初始化，本地需要通过 `bq auth` 配置凭证。
- 本地 `dai.query` 默认走 Arrow Flight 直连，速度更快。

### 如何退出登录

```bash
bq auth logout
```

## 项目内使用建议

本项目当前生产链路仍使用本地离线行情和私有策略目录。BigQuant SDK 建议先作为独立研究环境使用，不要直接替换正在运行的每日取数和飞书推送链路。

建议路径：

1. 在 `bigquant` conda 环境中验证 BigQuant 账号认证和 DAI 查询。
2. 用 DAI 拉取小样本数据，与 `data/offline/a_share_12m_tencent_sina` 的字段口径对比。
3. 单独写 BigQuant 研究脚本，不直接接入生产 daemon。
4. 确认数据字段、复权、停牌、涨跌停、ST、板块过滤都一致后，再考虑接入本项目策略流程。

敏感信息约束：

- 不要把真实 `AK.SK`、Token、用户名、密码写进代码或文档。
- 本地 API Key 应继续放在 `.env.local` 或 `~/.bigquant/config.json`。
- `.env.local` 已被 `.gitignore` 忽略，不要手动强制提交。
