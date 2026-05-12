# A 股增强分析接入设计

## 背景

当前项目已经具备 A 股日线同步、技术策略、新闻确认、最终选股聚合、飞书/微信推送和 GM 信号导出。`FinceptTerminal-main` 中的 A 股能力覆盖很广，但直接迁移完整脚本会引入大量当前用不到的接口和维护成本。

本设计采用轻量方案：只提取最终评分和新闻确认真正需要的 A 股数据能力，封装为当前项目内部的稳定接口。

## 目标

1. 增强最终选股评分，让最终 Top N 不只依赖技术策略和新闻分，也参考资金、热度、题材、风险。
2. 增强 `NewsConfirmStrategy`，让新闻匹配可以结合本地概念/行业关键词，并对公告类风险更敏感。
3. 所有外部数据失败时必须降级为中性结果，不中断主流程。
4. 保持现有策略接口和 GM 信号结构兼容。

## 非目标

1. 不迁移 Fincept 的 Qt 界面。
2. 不整批复制 `akshare_stocks_*.py` 的 400 多个 endpoint。
3. 第一阶段不接入 BaoStock/CNINFO PDF 下载和全文解析流水线。
4. 不改变现有日线行情表 `stock_daily` 的结构。

## 架构

新增模块 `super_q/data/a_share_insight.py`，提供 `AShareInsightService`。

服务职责：

- 从 AkShare 拉取轻量 A 股增强数据。
- 将结果标准化为项目内部结构。
- 写入 SQLite 缓存，减少重复请求。
- 对策略层暴露稳定方法，避免策略直接依赖 AkShare 原始字段。

建议核心方法：

- `get_symbol_insight(symbol: str) -> AShareInsight`
- `get_symbol_keywords(symbols: list[str]) -> dict[str, list[str]]`
- `score_symbol(symbol: str) -> AShareScore`
- `refresh_symbols(symbols: list[str]) -> dict[str, AShareInsight]`

## 数据维度

第一阶段接入四类高价值数据：

1. 资金流
   - 个股资金流、主力资金流或近似可用接口。
   - 正向净流入加分，明显净流出扣分。

2. 热度
   - 热股榜、人气榜、热关键词。
   - 命中候选股或候选股题材时加分。

3. 题材/行业
   - 个股概念、行业板块、概念板块成分。
   - 写入现有 `stock_concepts` 表，供新闻匹配使用。

4. 风险
   - 减持、质押、解禁、业绩预亏、问询/监管/诉讼/退市相关公告或数据。
   - 硬风险进入反向信号或新闻硬过滤；软风险扣分。

## SQLite 缓存

新增缓存表，不影响现有表：

- `a_share_insights`
  - `symbol`
  - `snapshot_date`
  - `money_flow_score`
  - `heat_score`
  - `theme_score`
  - `risk_score`
  - `total_score`
  - `risk_flags`
  - `theme_keywords`
  - `raw_json`
  - `updated_at`

缓存策略：

- 同一交易日同一股票优先使用缓存。
- 可通过配置控制是否启用增强分析。
- 外部接口失败时返回空洞察，分数为 0，风险为空。

## 最终评分接入

`FinalSelection` 增加 `a_share_score` 和 `risk_flags`。

总分公式保持简单：

```text
总分 = 技术分 + 新闻分 * 0.7 + 共振分 + A股增强分
```

其中 `A股增强分` 建议范围为 `-30` 到 `30`：

- 资金流：`-12` 到 `12`
- 热度：`0` 到 `8`
- 题材：`0` 到 `8`
- 风险：`-30` 到 `0`

若命中硬风险，最终选股直接排除，并传递给 GM 反向信号逻辑。

## 新闻确认接入

`NewsConfirmStrategy` 增强两处：

1. 在候选股评分前调用 `AShareInsightService.get_symbol_keywords()`，把概念、行业、别名写入 `stock_concepts` 或临时关键词映射。
2. 在单股新闻评分中合并风险标记：
   - 硬风险：进入 `rejected_scores`，并可生成反向信号。
   - 软风险：降低新闻最终分。

新闻匹配规则仍要求股票名称、代码、别名或公司相关关键词命中；仅命中泛题材词不能单独构成匹配。

## 配置

新增配置项：

- `a_share_insight_enabled: bool = True`
- `a_share_insight_cache_hours: int = 8`
- `a_share_insight_score_weight: float = 1.0`
- `a_share_insight_hard_risk_exclude: bool = True`
- `a_share_insight_request_timeout_seconds: float = 10.0`

## 错误处理

- AkShare 接口字段变化：记录 warning，返回中性洞察。
- 网络超时：重试少量次数后降级。
- 单只股票失败：不影响其他股票。
- 缓存写入失败：不中断策略，仅记录日志。

## 测试范围

新增单元测试覆盖：

1. A 股洞察服务在 AkShare 成功时能标准化资金、热度、题材、风险。
2. AkShare 失败时返回中性洞察，不抛出到主流程。
3. 最终选股能把 `a_share_score` 纳入排序。
4. 硬风险股票会从最终选股排除。
5. 新闻确认能使用洞察服务提供的概念关键词。
6. 新闻确认不会只因为泛题材词命中就误匹配股票。

## 实施顺序

1. 新增 A 股洞察数据结构、缓存表和服务。
2. 为服务补测试，使用 mock 的 AkShare 返回值。
3. 接入最终选股评分。
4. 接入新闻确认策略关键词和风险增强。
5. 更新 README 配置说明。
6. 运行测试并修复兼容问题。
