# superQ

> A 股量化选股、新闻确认、推送提醒和掘金 GM 仿真交易辅助工具。

superQ 基于开源项目 [Sequoia-X](https://github.com/sngyai/Sequoia-X) 二次开发。感谢原项目作者和贡献者提供的基础框架与开源贡献。

## 功能概览

- 本地 SQLite 行情库：支持 AkShare 增量同步和本地数据回放。
- 多策略选股：均线量能、海龟突破、RPS、强势整理、涨停回踩等策略统一汇总。
- 最终选股评分：把技术策略、新闻确认、A 股增强洞察和风险信号统一排序。
- 新闻确认策略：支持本地缓存新闻、定向 SearXNG 搜索、风险新闻过滤。
- A 股增强洞察：可选接入资金流、热度、题材和公告风险，失败时自动降级。
- 权限品种过滤：同步前可排除科创板、北交所、新三板、可转债、退市整理和 ST / *ST。
- 推送通知：支持飞书机器人和微信 iLink。
- 掘金 GM：导出买入/卖出信号，并可执行一次性仿真下单。
- Windows 常驻：提供启动和停止脚本，适合定时无人值守。

## 开始之前

请准备：

- Windows 或 Linux/macOS 环境。
- Python 3.10 及以上，主流程建议使用项目内 `.venv`。
- 如果使用掘金下单，另准备 Python 3.10 环境和本机掘金客户端。
- 如果使用微信 iLink，先完成扫码登录。
- 如果使用新闻定向搜索，准备可访问的 SearXNG 服务。

本项目仅用于研究和辅助决策，不构成任何投资建议。实盘交易前请自行确认账户权限、交易风险和券商/交易所规则。

## 安装

主流程环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install akshare "pydantic-settings>=2.0" "python-dotenv>=1.0" "rich>=13.0" "pandas>=2.0" "requests>=2.31"
```

开发和测试依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install "pytest>=8.0" "hypothesis>=6.100" "pytest-mock>=3.12"
```

掘金下单环境使用 Python 3.10：

```powershell
python -m venv .venv310
.\.venv310\Scripts\python.exe -m pip install gm==3.0.183 python-dotenv pydantic-settings
```

## 配置

复制示例配置：

```powershell
Copy-Item .env.example .env
```

然后编辑 `.env`。不要把 `.env` 提交到代码仓库。

常用配置：

```env
DB_PATH=data/sequoia_v2.db
START_DATE=2024-01-01
SYNC_MARKET_DATA=true
MARKET_DATA_PROVIDER=auto
SYNC_EXCLUDE_QUALIFIED_MARKETS=true
FEISHU_WEBHOOK_URL=你的飞书 Webhook

NEWS_TARGETED_SEARCH_ENABLED=false
A_SHARE_INSIGHT_ENABLED=false

WECHAT_ILINK_ENABLED=false
GM_ENABLED=false
GM_DRY_RUN=true
```

### 行情同步

- `SYNC_MARKET_DATA=true`：运行时先同步行情。
- `SYNC_MARKET_DATA=false`：跳过联网同步，直接使用本地数据库。
- `MARKET_DATA_PROVIDER=auto`：按 `eastmoney -> sina` 尝试。
- `MARKET_DATA_PROVIDER=eastmoney` 或 `sina`：固定使用单一行情源。
- `SYNC_EXCLUDE_QUALIFIED_MARKETS=true`：同步列表排除科创板、北交所、新三板、可转债、退市整理和 ST / *ST。这个开关只影响后续增量同步，不会删除本地已有数据。

### 新闻确认

- `NEWS_TARGETED_SEARCH_ENABLED=false`：只使用本地缓存和已有新闻数据，速度更稳定。
- `NEWS_TARGETED_SEARCH_ENABLED=true`：对候选股逐只请求 SearXNG，网络慢时会拖慢主流程。
- `NEWS_TARGETED_SEARCH_LIMIT`：每只股票最多取多少条定向搜索结果。

### A 股增强洞察

- `A_SHARE_INSIGHT_ENABLED=false`：关闭资金流、热度、题材和公告风险外部接口。
- `A_SHARE_INSIGHT_ENABLED=true`：最终选股前刷新候选股洞察数据。
- `A_SHARE_INSIGHT_CACHE_HOURS`：洞察缓存小时数。
- `A_SHARE_INSIGHT_HARD_RISK_EXCLUDE=true`：命中硬风险的股票不进入最终买入池。

如果运行时出现 AkShare、东财或代理超时，优先关闭：

```env
A_SHARE_INSIGHT_ENABLED=false
NEWS_TARGETED_SEARCH_ENABLED=false
```

### 推送与交易

- `FEISHU_WEBHOOK_URL`：飞书机器人地址。
- `WECHAT_ILINK_ENABLED=true`：启用微信 iLink 推送。
- `GM_ENABLED=true`：启用 GM 信号导出。
- `GM_DRY_RUN=true`：只生成计划，不提交下单。
- `GM_DRY_RUN=false`：允许提交掘金仿真订单。

## 运行

运行一次完整链路：

```powershell
.\.venv\Scripts\python.exe .\start.py
```

它会按顺序执行：

```text
main.py -> gm_order_once.py
```

只跑策略和推送：

```powershell
.\.venv\Scripts\python.exe .\main.py
```

只跑掘金一键下单：

```powershell
.\.venv310\Scripts\python.exe .\gm_order_once.py
```

指定 GM 使用的 Python：

```powershell
.\.venv\Scripts\python.exe .\start.py --gm-python .\.venv310\Scripts\python.exe
```

常驻无人值守：

```powershell
.\.venv\Scripts\python.exe .\start.py --daemon --time 14:45:00
```

## Windows 启停脚本

启动常驻任务：

```powershell
.\scripts\start_superq.ps1
```

或双击：

```text
scripts\start_superq.bat
```

停止常驻任务和掘金客户端：

```powershell
.\scripts\stop_superq.ps1
```

或双击：

```text
scripts\stop_superq.bat
```

运行日志默认写入：

```text
data/superq_daemon.out.log
data/superq_daemon.err.log
```

如需调整常驻运行时间：

```powershell
$env:START_RUN_TIME="14:50:00"
.\scripts\start_superq.ps1
```

## 微信 iLink 登录

首次使用或登录失效时运行：

```powershell
.\.venv\Scripts\python.exe .\scripts\wechat_login.py
```

扫码确认后，给 bot 发一条任意消息，让程序缓存 `contextToken`。

## 掘金仿真交易规则

`main.py` 会把最终信号写入 `data/gm_trade_signal.json`。下单脚本读取这个文件后按股数下单。

- 普通策略信号：买入 `GM_BUY_VOLUME` 股，默认 `100` 股。
- 策略信号叠加高可信新闻：买入 `GM_NEWS_HIGH_CONFIDENCE_BUY_VOLUME` 股，默认 `200` 股。
- 高可信新闻判断：新闻综合分大于等于 `NEWS_HIGH_CONFIDENCE_SCORE`，默认 `80`。
- 已持有同一股票：跳过买入，避免重复加仓。
- 反向信号：新闻策略中被风险新闻过滤的持仓股，卖出当前可用持仓。
- 黑名单、最大持仓数、每日下单次数、交易时间窗口仍会继续生效。

`gm_order_once.py` 会执行一次性下单：

- 买入：调用 GM `order_volume`。
- 卖出：调用 GM `order_volume`，股数为当前可用持仓。
- `GM_DRY_RUN=true` 时只生成计划，不提交订单。
- `GM_DRY_RUN=false` 时提交掘金仿真订单。

`gm_sim_strategy.py` 也支持同样的信号结构，适合放到掘金策略环境中定时执行。

## Docker

Docker 适合跑策略、飞书/微信推送和信号导出：

```powershell
docker compose run --rm app
```

常驻模式：

```powershell
docker compose --profile daemon up daemon
```

GM 下单容器是可选尝试：

```powershell
docker compose --profile gm run --rm gm-order
```

掘金客户端仍需要在 Windows 宿主机运行。容器内连接宿主机时通常使用：

```env
GM_SERV_ADDR=host.docker.internal:7001
```

如果 GM SDK 在 Linux 容器中不可用，就继续用 Windows 本机 `.venv310` 运行 `gm_order_once.py`。

## 测试

运行全部测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

常用聚焦测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_data_engine.py tests/test_main.py -q
```

## 故障排查

### 运行看起来卡住

常见原因是外部网络接口串行等待超时。优先关闭外部增强：

```env
A_SHARE_INSIGHT_ENABLED=false
NEWS_TARGETED_SEARCH_ENABLED=false
```

如果行情同步慢，可临时跳过同步：

```env
SYNC_MARKET_DATA=false
```

### 飞书或微信没有推送

检查：

- `FEISHU_WEBHOOK_URL` 是否有效。
- 微信 iLink 是否完成登录。
- `WECHAT_ILINK_ENABLED` 和 `WECHAT_ILINK_TARGET_USER_ID` 是否配置。
- 当天是否有最终选股结果。

### GM 没有下单

检查：

- 掘金客户端是否启动并登录。
- `GM_SERV_ADDR` 是否可访问。
- `GM_DRY_RUN` 是否仍为 `true`。
- 是否达到最大持仓、黑名单、每日下单次数或交易时间限制。

## 目录结构

```text
superQ/
├── start.py                   # 一键启动入口
├── main.py                    # 策略、推送、GM 信号导出
├── gm_order_once.py           # 掘金一键下单
├── gm_sim_strategy.py         # 掘金环境策略脚本
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── .env.example
├── LICENSE
├── NOTICE.md
├── README.md
├── data/                      # 数据库、状态、GM 信号和快照
├── logs/                      # 根目录日志归档占位
├── scripts/                   # Windows 启停和辅助工具脚本
│   ├── start_superq.ps1
│   ├── stop_superq.ps1
│   ├── start_superq.bat
│   ├── stop_superq.bat
│   └── wechat_login.py
├── integrations/
│   └── legacy/                # 历史集成适配器归档
├── docs/
│   ├── notes/                 # 项目资料、模板和推送预览
│   └── superpowers/           # 设计和实施计划
└── super_q/
    ├── core/                  # 配置和日志
    ├── data/                  # 数据引擎和 A 股洞察
    ├── notify/                # 飞书、微信 iLink
    ├── strategy/              # 选股策略
    └── trade/                 # GM 信号和账户快照
```

## 开源许可与鸣谢

本项目使用 MIT License，详见 [LICENSE](LICENSE)。

本项目基于 [sngyai/Sequoia-X](https://github.com/sngyai/Sequoia-X) 二次开发，感谢原作者和贡献者的开源工作。更多说明见 [NOTICE.md](NOTICE.md)。

如果你继续分发或二次开发本项目，请保留许可证文本、版权声明和对上游项目的鸣谢。
