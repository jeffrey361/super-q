# superQ

> A 股量化选股、推送和掘金仿真交易辅助工具。

---

## 简介

superQ 用本地 SQLite 保存行情和股票名称，运行多套选股策略后推送到飞书和微信 iLink，并可导出掘金 GM 信号。掘金下单依赖本机掘金客户端和 Python 3.10 环境。

---

## 快速开始

### 1. 安装依赖

主流程环境：

```powershell
.\.venv\Scripts\python.exe -m pip install akshare "pydantic-settings>=2.0" "python-dotenv>=1.0" "rich>=13.0" "pandas>=2.0" "requests>=2.31"
```

掘金下单环境使用 Python 3.10：

```powershell
.\.venv310\Scripts\python.exe -m pip install gm==3.0.183 python-dotenv pydantic-settings
```

### 2. 配置 `.env`

编辑 `.env`，至少确认这些配置：

```env
DB_PATH=data/sequoia_v2.db
MARKET_DATA_PROVIDER=auto
FEISHU_WEBHOOK_URL=你的飞书 Webhook
WECHAT_ILINK_ENABLED=true
WECHAT_ILINK_TARGET_USER_ID=你的微信 iLink 用户 ID
GM_ENABLED=true
GM_TOKEN=你的掘金 Token
GM_ACCOUNT_ID=你的掘金账户 ID
GM_SERV_ADDR=127.0.0.1:7001
GM_DRY_RUN=false
GM_BUY_VOLUME=100
GM_NEWS_HIGH_CONFIDENCE_BUY_VOLUME=150
NEWS_HIGH_CONFIDENCE_SCORE=80
```

不要把 `.env` 提交到代码仓库。

### 3. 微信 iLink 登录

首次使用或登录失效时运行：

```powershell
.\.venv\Scripts\python.exe .\wechat_login.py
```

扫码确认后，给 bot 发一条任意消息，让程序缓存 `contextToken`。

### 4. 一键运行

运行一次完整链路：

```powershell
.\.venv\Scripts\python.exe .\start.py
```

它会按顺序执行：

```text
main.py -> gm_order_once.py
```

含义：

- `main.py`：行情同步或读取本地数据、运行策略、飞书/微信推送、导出 `data/gm_trade_signal.json`
- `gm_order_once.py`：读取掘金信号、执行风控、提交仿真下单、保存账户快照

行情同步默认使用 `MARKET_DATA_PROVIDER=auto`，会按 `eastmoney -> sina`
顺序尝试，并统一写入 `date/open/high/low/close/volume/turnover` 字段。
如果只想使用某个源，可设置为 `eastmoney` 或 `sina`。

如果要指定 GM 使用的 Python：

```powershell
.\.venv\Scripts\python.exe .\start.py --gm-python .\.venv310\Scripts\python.exe
```

### 5. 常驻无人值守

每天固定时间运行：

```powershell
.\.venv\Scripts\python.exe .\start.py --daemon --time 14:45:00
```

建议无人值守前确认：

- 掘金客户端已登录并运行
- 本机掘金服务可访问，例如 `127.0.0.1:7001`
- 微信 iLink 登录态有效
- `.env` 中 `GM_DRY_RUN=false` 时确实允许提交仿真订单

## 掘金仿真交易规则

`main.py` 会把策略结果写入 `data/gm_trade_signal.json`。下单脚本读取这个文件后按股数下单，不再只按金额下单。

当前规则：

- 普通策略信号：买入 `GM_BUY_VOLUME` 股，默认 `100` 股。
- 策略信号叠加高可信新闻：买入 `GM_NEWS_HIGH_CONFIDENCE_BUY_VOLUME` 股，默认 `150` 股。
- 高可信新闻判断：新闻综合分大于等于 `NEWS_HIGH_CONFIDENCE_SCORE`，默认 `80`。
- 已持有同一股票：跳过买入，避免重复加仓。
- 反向信号：新闻策略中被风险新闻过滤的持仓股，卖出当前可用持仓。
- 黑名单、最大持仓数、每日下单次数、交易时间窗口仍会继续生效。

反向信号来自 `NewsConfirmStrategy` 的风险过滤结果。例如新闻命中“立案、调查、减持、预亏、问询函、监管函、退市、诉讼、商誉减值、解禁”等风险词时，该股票不会进入买入候选；如果账户里已有该股，GM 信号会生成卖出计划。

`gm_order_once.py` 会执行一次性下单：

- 买入：调用 GM `order_volume`，股数为 `100` 或 `150`。
- 卖出：调用 GM `order_volume`，股数为当前可用持仓。
- `GM_DRY_RUN=true` 时只生成计划，不提交订单。
- `GM_DRY_RUN=false` 时提交掘金仿真订单。

`gm_sim_strategy.py` 也支持同样的信号结构，适合放到掘金策略环境中定时执行。

## 单独运行

只跑策略和推送：

```powershell
python main.py
```

只跑掘金一键下单：

```powershell
.\.venv310\Scripts\python.exe .\gm_order_once.py
```

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

---

## 目录结构

```
superQ-v2/
├── start.py                   # 一键启动入口
├── main.py                    # 策略、推送、GM 信号导出
├── gm_order_once.py           # 掘金一键下单
├── gm_sim_strategy.py         # 掘金环境策略脚本
├── wechat_login.py            # 微信 iLink 登录
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── .gitignore
├── README.md
├── data/                      # 数据库、微信状态、GM 信号和快照
└── sequoia_x/
    ├── core/                  # 配置和日志
    ├── data/                  # 数据引擎
    ├── notify/                # 飞书、微信 iLink
    ├── strategy/              # 选股策略
    └── trade/                 # GM 信号和账户快照
```
