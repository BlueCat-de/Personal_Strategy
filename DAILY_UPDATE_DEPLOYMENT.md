# 每日闭市后自动取数部署说明

本文档用于部署 `update_offline_a_share_daily.py`，让电脑在不关机的情况下每天闭市后自动更新：

```text
/Users/bytedance/cqm/Personal_Strategy/data/offline/a_share_12m_tencent_sina
```

## 1. 单次手动运行

建议先用 dry-run 验证：

```bash
cd /Users/bytedance/cqm/Personal_Strategy

python3 update_offline_a_share_daily.py \
  --output-dir /Users/bytedance/cqm/Personal_Strategy/data/offline/a_share_12m_tencent_sina \
  --data-sources tencent,sina \
  --limit 20 \
  --dry-run
```

正式执行一次：

```bash
cd /Users/bytedance/cqm/Personal_Strategy

python3 update_offline_a_share_daily.py \
  --output-dir /Users/bytedance/cqm/Personal_Strategy/data/offline/a_share_12m_tencent_sina \
  --data-sources tencent,sina
```

默认行为：

- 非交易日自动跳过；
- 每只股票异常后重试 2 次；
- 使用锁文件防止并发执行；
- 日志写入 `logs/offline_daily_update.log`；
- 默认从 `.feishu_webhook` 读取飞书 bot webhook，正式运行后推送成功/跳过/失败摘要；
- 更新 `symbols/*.csv`、`prices_long.csv` 和 `manifest.json`。

## 2. 前台常驻调度模式

每天 `16:30` 执行一次：

```bash
cd /Users/bytedance/cqm/Personal_Strategy

python3 update_offline_a_share_daily.py \
  --daemon \
  --run-at 16:30 \
  --output-dir /Users/bytedance/cqm/Personal_Strategy/data/offline/a_share_12m_tencent_sina \
  --data-sources tencent,sina \
  --log-file /Users/bytedance/cqm/Personal_Strategy/logs/offline_daily_update.log
```

## 3. 后台持续运行

使用 `nohup` 后台运行：

```bash
cd /Users/bytedance/cqm/Personal_Strategy

mkdir -p logs run

nohup python3 update_offline_a_share_daily.py \
  --daemon \
  --run-at 16:30 \
  --output-dir /Users/bytedance/cqm/Personal_Strategy/data/offline/a_share_12m_tencent_sina \
  --data-sources tencent,sina \
  --log-file /Users/bytedance/cqm/Personal_Strategy/logs/offline_daily_update.log \
  > /Users/bytedance/cqm/Personal_Strategy/logs/offline_daily_update.nohup.log 2>&1 &

echo $! > /Users/bytedance/cqm/Personal_Strategy/run/offline_daily_update.pid
```

查看进程：

```bash
ps -p "$(cat /Users/bytedance/cqm/Personal_Strategy/run/offline_daily_update.pid)" -o pid,etime,command
```

查看日志：

```bash
tail -f /Users/bytedance/cqm/Personal_Strategy/logs/offline_daily_update.log
```

停止进程：

```bash
kill "$(cat /Users/bytedance/cqm/Personal_Strategy/run/offline_daily_update.pid)"
```

## 4. macOS 开机自启动

推荐使用 `launchd`。先确认 Python 路径：

```bash
which python3
```

假设输出是 `/usr/bin/python3`，创建 LaunchAgent：

```bash
mkdir -p ~/Library/LaunchAgents

cat > ~/Library/LaunchAgents/com.personal.strategy.daily-update.plist <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.personal.strategy.daily-update</string>

  <key>WorkingDirectory</key>
  <string>/Users/bytedance/cqm/Personal_Strategy</string>

  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/Users/bytedance/cqm/Personal_Strategy/update_offline_a_share_daily.py</string>
    <string>--daemon</string>
    <string>--run-at</string>
    <string>16:30</string>
    <string>--output-dir</string>
    <string>/Users/bytedance/cqm/Personal_Strategy/data/offline/a_share_12m_tencent_sina</string>
    <string>--data-sources</string>
    <string>tencent,sina</string>
    <string>--log-file</string>
    <string>/Users/bytedance/cqm/Personal_Strategy/logs/offline_daily_update.log</string>
  </array>

  <key>RunAtLoad</key>
  <true/>

  <key>KeepAlive</key>
  <true/>

  <key>StandardOutPath</key>
  <string>/Users/bytedance/cqm/Personal_Strategy/logs/offline_daily_update.launchd.out.log</string>

  <key>StandardErrorPath</key>
  <string>/Users/bytedance/cqm/Personal_Strategy/logs/offline_daily_update.launchd.err.log</string>
</dict>
</plist>
PLIST
```

如果 `which python3` 不是 `/usr/bin/python3`，把 plist 里的第一段 `<string>/usr/bin/python3</string>` 改成实际路径。

加载并启动：

```bash
launchctl unload ~/Library/LaunchAgents/com.personal.strategy.daily-update.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.personal.strategy.daily-update.plist
launchctl start com.personal.strategy.daily-update
```

查看状态：

```bash
launchctl list | grep com.personal.strategy.daily-update
tail -f /Users/bytedance/cqm/Personal_Strategy/logs/offline_daily_update.log
```

停止并取消自启动：

```bash
launchctl stop com.personal.strategy.daily-update
launchctl unload ~/Library/LaunchAgents/com.personal.strategy.daily-update.plist
```

## 5. 飞书通知和告警配置

脚本默认开启飞书通知，并从本地文件读取 webhook：

```text
/Users/bytedance/cqm/Personal_Strategy/.feishu_webhook
```

该文件已加入 `.gitignore`，不要提交到远端仓库。文件支持多个机器人 webhook：每行一个，或用逗号分隔。脚本会向所有 webhook 推送相同信息。

正式运行时，以下状态都会推送到飞书：

- 成功更新；
- 非交易日跳过；
- 已有任务运行导致跳过；
- 更新失败。

dry-run 默认不推送，避免测试刷屏。如需测试飞书通知，可显式增加：

```bash
--feishu-notify-dry-run
```

禁用飞书通知：

```bash
--no-feishu-notify
```

使用其他 webhook 文件：

```bash
--feishu-webhook-file /path/to/.feishu_webhook
```

推送内容示例：

```text
A股离线数据每日更新：成功
日期：20260703
目录：/Users/bytedance/cqm/Personal_Strategy/data/offline/a_share_12m_tencent_sina
新增：0，刷新：3046，无数据：0，失败：0
失败率：0.00%
prices_long：2025-01-06 至 2026-07-03
覆盖股票数：3046
总行数：xxxxxx
```

### 可选 shell 告警

脚本支持 `--alert-command`。失败时会执行该 shell 命令，并注入环境变量：

```text
UPDATE_STATUS
UPDATE_MESSAGE
UPDATE_OUTPUT_DIR
```

macOS 本地通知示例：

```bash
python3 update_offline_a_share_daily.py \
  --daemon \
  --run-at 16:30 \
  --output-dir /Users/bytedance/cqm/Personal_Strategy/data/offline/a_share_12m_tencent_sina \
  --data-sources tencent,sina \
  --alert-command 'osascript -e "display notification \"$UPDATE_MESSAGE\" with title \"A股数据更新失败\""'
```

自有 Webhook 示例：

```bash
--alert-command 'curl -sS -X POST "$WEBHOOK_URL" -H "Content-Type: application/json" -d "{\"text\":\"$UPDATE_MESSAGE\"}"'
```

## 6. 健壮性评估

| 检查项 | 当前实现 |
|---|---|
| 交易日判断 | 默认开启。周末直接跳过；工作日用 `000001` 当天日线 bar 判断是否真实交易日。 |
| 网络异常 | 数据源按 `tencent,sina` fallback；单票异常默认重试 2 次；异常记录到日志。 |
| 防并发 | 使用 `fcntl` 文件锁，默认锁文件为 `<output-dir>/.daily_update.lock`。同一时间只允许一个取数任务写数据；策略任务读取前也会检查该锁。 |
| 防重复 | 取数 daemon 写入 `run/offline_daily_update_scheduler_state.json`，策略 daemon 写入 `run/daily_strategy_scheduler_state.json`；进程在执行时间后重启时不会重复跑当天任务。 |
| 日志 | 同时输出到 stdout 和 `logs/offline_daily_update.log`。`launchd` 另有 stdout/stderr 日志。 |
| 错误告警 | 内置飞书通知，成功/跳过/失败都会推送；另支持 `--alert-command` 作为失败兜底。 |
| 数据一致性 | 单票文件按 `date,symbol` 去重；保留股票代码前导零；`universe.csv`、单票 CSV、`prices_long.csv`、`manifest.json` 均采用临时文件写入后原子替换。 |
| 非交易日 | 默认跳过，不会重建数据。可用 `--no-skip-non-trading-day` 强制运行。 |

## 7. 改进建议

- 当前交易日判断依赖 `000001` 日线可取到数据；如果数据源整体异常，可能被判断为非交易日。更严格的生产方案可接入官方交易日历或维护本地交易日历文件。
- 飞书通知依赖本地 `.feishu_webhook` 文件；如果 webhook 失效，脚本会记录错误但不会泄露 URL。
- 如果希望少量股票失败也导致飞书失败通知，可降低 `--max-failed-ratio`。
- `prices_long.csv` 每次全量重建，稳定但耗时。数据规模继续扩大后，可以改成分区存储或增量合并。
- 建议每周额外做一次 `--refresh-universe`，更新退市、ST 状态和新股过滤。

## 8. 每日策略检查后台部署

取数任务建议在 `16:30` 执行，策略任务建议在 `16:50` 开始检查。策略脚本会先检查：

```text
data/offline/a_share_12m_tencent_sina/prices_long.csv
```

的最新日期是否等于当天日期。策略读取行情前还会检查取数任务的 `.daily_update.lock`，只有取数锁释放且今日数据已经更新，才会运行最新策略；如果 `16:50` 时取数还没结束，后台任务会每 5 分钟重试一次，默认等到 `20:30`。超过截止时间仍未取得今日数据或取数锁仍未释放时，才会跳过并发送飞书说明，避免基于旧数据或半成品数据生成交易信号。

策略 daemon 会把已执行日期写入：

```text
run/daily_strategy_scheduler_state.json
```

即使 `16:50` 后重启 daemon，也不会重复执行当天策略或重复推送。

取数 daemon 同样会把已执行日期写入：

```text
run/offline_daily_update_scheduler_state.json
```

即使 `16:30` 后重启 daemon，也不会重复拉取当天全市场数据。

策略端默认 `--trading-day-check weekday`：周末直接跳过，工作日等待数据到截止时间。这样避免额外网络检查失败时，把真实交易日误判成非交易日。若希望工作日节假日更早跳过，可改为 `--trading-day-check auto`。

单次手动运行：

```bash
cd /Users/bytedance/cqm/Personal_Strategy

python3 run_daily_strategy_signal.py \
  --data-dir /Users/bytedance/cqm/Personal_Strategy/data/offline/a_share_12m_tencent_sina \
  --output-base-dir /Users/bytedance/cqm/Personal_Strategy/data/backtests/daily_strategy_signals \
  --warmup-start-date 20250106 \
  --start-date 20250705 \
  --initial-cash 100000
```

指定历史交易日验证：

```bash
python3 run_daily_strategy_signal.py \
  --end-date 20260703 \
  --data-dir /Users/bytedance/cqm/Personal_Strategy/data/offline/a_share_12m_tencent_sina \
  --output-base-dir /Users/bytedance/cqm/Personal_Strategy/data/backtests/daily_strategy_signals_test_real \
  --no-feishu-notify
```

后台常驻运行：

```bash
cd /Users/bytedance/cqm/Personal_Strategy
mkdir -p logs run

nohup python3 run_daily_strategy_signal.py \
  --daemon \
  --run-at 16:50 \
  --data-ready-retry-seconds 300 \
  --data-ready-deadline 20:30 \
  --data-lock-wait-seconds 1800 \
  --data-dir /Users/bytedance/cqm/Personal_Strategy/data/offline/a_share_12m_tencent_sina \
  --output-base-dir /Users/bytedance/cqm/Personal_Strategy/data/backtests/daily_strategy_signals \
  --warmup-start-date 20250106 \
  --start-date 20250705 \
  --initial-cash 100000 \
  --log-file /Users/bytedance/cqm/Personal_Strategy/logs/daily_strategy_signal.log \
  > /Users/bytedance/cqm/Personal_Strategy/logs/daily_strategy_signal.nohup.log 2>&1 &

echo $! > /Users/bytedance/cqm/Personal_Strategy/run/daily_strategy_signal.pid
```

查看策略任务：

```bash
ps -p "$(cat /Users/bytedance/cqm/Personal_Strategy/run/daily_strategy_signal.pid)" -o pid,etime,command
tail -f /Users/bytedance/cqm/Personal_Strategy/logs/daily_strategy_signal.log
```

停止策略任务：

```bash
kill "$(cat /Users/bytedance/cqm/Personal_Strategy/run/daily_strategy_signal.pid)"
```

macOS 开机自启动配置：

```bash
mkdir -p ~/Library/LaunchAgents

cat > ~/Library/LaunchAgents/com.personal.strategy.daily-signal.plist <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.personal.strategy.daily-signal</string>

  <key>WorkingDirectory</key>
  <string>/Users/bytedance/cqm/Personal_Strategy</string>

  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/Users/bytedance/cqm/Personal_Strategy/run_daily_strategy_signal.py</string>
    <string>--daemon</string>
    <string>--run-at</string>
    <string>16:50</string>
    <string>--data-ready-retry-seconds</string>
    <string>300</string>
    <string>--data-ready-deadline</string>
    <string>20:30</string>
    <string>--data-lock-wait-seconds</string>
    <string>1800</string>
    <string>--data-dir</string>
    <string>/Users/bytedance/cqm/Personal_Strategy/data/offline/a_share_12m_tencent_sina</string>
    <string>--output-base-dir</string>
    <string>/Users/bytedance/cqm/Personal_Strategy/data/backtests/daily_strategy_signals</string>
    <string>--warmup-start-date</string>
    <string>20250106</string>
    <string>--start-date</string>
    <string>20250705</string>
    <string>--initial-cash</string>
    <string>100000</string>
    <string>--log-file</string>
    <string>/Users/bytedance/cqm/Personal_Strategy/logs/daily_strategy_signal.log</string>
  </array>

  <key>RunAtLoad</key>
  <true/>

  <key>KeepAlive</key>
  <true/>

  <key>StandardOutPath</key>
  <string>/Users/bytedance/cqm/Personal_Strategy/logs/daily_strategy_signal.launchd.out.log</string>

  <key>StandardErrorPath</key>
  <string>/Users/bytedance/cqm/Personal_Strategy/logs/daily_strategy_signal.launchd.err.log</string>
</dict>
</plist>
PLIST
```

加载并启动：

```bash
launchctl unload ~/Library/LaunchAgents/com.personal.strategy.daily-signal.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.personal.strategy.daily-signal.plist
launchctl start com.personal.strategy.daily-signal
```

策略飞书消息包括：

- 是否成功运行；
- 是否因为今日数据未更新而跳过；
- 当前目标持仓；
- 当日持仓调整记录；
- 假设本金 10 万、从本地 live state 启用日开始计数的当日收益率和累计收益率；
- 如果无调整，会明确说明“维持当前目标持仓，不主动调仓”。
