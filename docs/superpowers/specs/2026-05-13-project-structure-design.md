# 项目目录整理设计

## 目标

以中度整理方式降低根目录噪音，同时保持主要运行入口稳定。根目录继续保留标准工程文件、部署文件和核心入口脚本，辅助脚本、零散文档和运行日志按用途归类。

## 目录边界

根目录保留 `main.py`、`start.py`、`gm_order_once.py`、`gm_sim_strategy.py`，避免影响 README、Docker、测试和用户已有运行习惯。

新增 `scripts/` 存放 Windows 启停脚本和微信 iLink 登录辅助脚本。新增 `docs/notes/` 存放推荐、待办、模板、推送预览等项目资料。新增 `logs/` 作为根目录日志归档位置，并保留 `.gitkeep`。

历史遗留的独立微信平台适配器不参与当前 `super_q` 包运行，归档到 `integrations/legacy/`，保留源码但避免占用根目录。

## 引用更新

移动脚本后同步更新 README、Dockerfile、docker-compose 和测试路径。PowerShell 脚本从 `scripts/` 运行时，需要把项目根目录解析为脚本目录的上级目录，确保仍能找到 `.venv`、`data` 和 `start.py`。

## 风险控制

不删除已跟踪的 `data/` 状态文件，不移动核心入口脚本，不改变 Python 包结构。`.gitignore` 补充日志、缓存、运行产物和本地环境忽略规则，减少后续根目录再次堆积生成物。
