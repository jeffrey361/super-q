# 项目目录整理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将辅助脚本、零散文档和日志目录统一归类，同时保持核心运行入口稳定。

**Architecture:** 根目录保留工程元数据、部署文件和核心 Python 入口。`scripts/` 承载辅助脚本，`docs/notes/` 承载项目资料，`logs/` 承载根目录日志占位。所有被移动文件的引用点同步更新并通过聚焦测试验证。

**Tech Stack:** Python 3.10+、PowerShell、Docker Compose、pytest。

---

### Task 1: 创建归类目录并移动文件

**Files:**
- Create: `scripts/`
- Create: `docs/notes/`
- Create: `logs/.gitkeep`
- Create: `integrations/legacy/`
- Move: `start_superq.ps1` -> `scripts/start_superq.ps1`
- Move: `stop_superq.ps1` -> `scripts/stop_superq.ps1`
- Move: `start_superq.bat` -> `scripts/start_superq.bat`
- Move: `stop_superq.bat` -> `scripts/stop_superq.bat`
- Move: `wechat_login.py` -> `scripts/wechat_login.py`
- Move: `推荐.md` -> `docs/notes/推荐.md`
- Move: `pending_features.md` -> `docs/notes/pending_features.md`
- Move: `template.md` -> `docs/notes/template.md`
- Move: `strategy_push_preview.txt` -> `docs/notes/strategy_push_preview.txt`
- Move: `weixin.py` -> `integrations/legacy/weixin.py`

- [ ] **Step 1: 创建目录**

Run: `New-Item -ItemType Directory -Force -Path scripts,docs/notes,logs`

Expected: 三个目录存在。

- [ ] **Step 2: 移动辅助脚本和资料文件**

Run: `Move-Item` 将上述文件移入目标目录。

Expected: 根目录不再包含这些辅助文件，目标目录包含对应文件。

- [ ] **Step 3: 创建日志目录占位文件**

Run: `New-Item -ItemType File -Force -Path logs/.gitkeep`

Expected: `logs/.gitkeep` 存在。

### Task 2: 更新脚本、Docker 和测试引用

**Files:**
- Modify: `scripts/start_superq.ps1`
- Modify: `scripts/stop_superq.ps1`
- Modify: `Dockerfile`
- Modify: `docker-compose.yml`
- Modify: `tests/test_windows_scripts.py`

- [ ] **Step 1: 修正 PowerShell 项目根目录解析**

将 `scripts/start_superq.ps1` 和 `scripts/stop_superq.ps1` 的 `$Root` 改为脚本目录的上级目录。

Expected: 从 `scripts/` 双击或命令行运行时仍使用项目根目录作为工作目录。

- [ ] **Step 2: 修正容器内微信登录脚本路径**

`Dockerfile` 复制 `scripts/` 目录，`docker-compose.yml` 的 `wechat-login` 命令改为 `python scripts/wechat_login.py`。

Expected: Docker 工具 profile 仍能找到微信登录脚本。

- [ ] **Step 3: 更新 Windows 脚本测试路径**

`tests/test_windows_scripts.py` 从 `scripts/` 读取 `.ps1` 和 `.bat` 文件。

Expected: 测试检查移动后的脚本内容。

### Task 3: 更新文档和忽略规则

**Files:**
- Modify: `README.md`
- Modify: `.gitignore`

- [ ] **Step 1: 更新 README 运行命令和目录结构**

将 Windows 启停脚本路径改为 `scripts/...`，微信登录命令改为 `.\scripts\wechat_login.py`，目录结构加入 `scripts/`、`docs/notes/`、`logs/`。

Expected: README 展示的命令与实际文件位置一致。

- [ ] **Step 2: 完善 `.gitignore`**

补充 `logs/`、`*.pid`、`network_pull.*.log`、常见缓存和运行产物规则，并保留 `logs/.gitkeep`。

Expected: 新生成日志和缓存默认不会污染工作区。

### Task 4: 验证

**Files:**
- Test: `tests/test_windows_scripts.py`

- [ ] **Step 1: 运行聚焦测试**

Run: `python -m pytest tests/test_windows_scripts.py -q`

Expected: Windows 脚本路径相关测试通过。

- [ ] **Step 2: 检查状态和引用**

Run: `rg "wechat_login.py|start_superq|stop_superq|strategy_push_preview|pending_features|template.md|推荐.md" -n README.md Dockerfile docker-compose.yml tests scripts docs`

Expected: 引用路径都指向移动后的目录。
