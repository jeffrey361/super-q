# A 股增强分析 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 FinceptTerminal 中适合当前项目的 A 股资金、热度、题材、风险能力接入最终选股评分和新闻确认策略。

**Architecture:** 新增 `AShareInsightService` 作为 AkShare 适配层和 SQLite 缓存层；策略层只消费标准化后的 `AShareInsight` 和 `AShareScore`。最终评分聚合器接收增强分和硬风险列表；新闻确认策略通过注入的洞察服务补充概念关键词和风险标记。

**Tech Stack:** Python 3.10+、AkShare、SQLite、pandas、pytest、pydantic-settings。

---

### Task 1: A 股洞察服务

**Files:**

- Create: `super_q/data/a_share_insight.py`
- Test: `tests/test_a_share_insight.py`

- [ ] **Step 1: Write failing tests**

覆盖服务能标准化资金、热度、题材、风险，并在外部接口失败时返回中性结果。

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m pytest tests/test_a_share_insight.py -q`

Expected: 因 `super_q.data.a_share_insight` 不存在而失败。

- [ ] **Step 3: Implement service**

实现内容：

- `AShareInsight`
- `AShareScore`
- `AShareInsightService`
- `refresh_symbols()`
- `get_symbol_insight()`
- `get_symbol_keywords()`
- `score_symbol()`
- SQLite 表 `a_share_insights`

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_a_share_insight.py -q`

Expected: PASS。

### Task 2: 最终评分接入

**Files:**

- Modify: `super_q/strategy/final_selection.py`
- Test: `tests/test_final_selection.py`

- [ ] **Step 1: Write failing tests**

新增测试：

- A 股增强分参与排序。
- A 股硬风险股票被最终买入池排除。
- `FinalSelection` 输出包含 `a_share_score` 和 `risk_flags`。

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m pytest tests/test_final_selection.py -q`

Expected: 因新参数和字段不存在而失败。

- [ ] **Step 3: Implement scoring changes**

`build_final_selection()` 新增可选参数：

- `a_share_scores`
- `a_share_risk_flags`
- `a_share_hard_risk_symbols`

总分加入 `a_share_score`。

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_final_selection.py -q`

Expected: PASS。

### Task 3: 新闻确认接入

**Files:**

- Modify: `super_q/strategy/news_confirm.py`
- Test: `tests/test_strategy.py`

- [ ] **Step 1: Write failing tests**

新增测试：

- 注入洞察服务后，概念关键词可用于新闻主题识别。
- 洞察服务硬风险会把候选股放入 `rejected_scores`。

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m pytest tests/test_strategy.py -q`

Expected: 因 `insight_service` 参数和风险合并逻辑不存在而失败。

- [ ] **Step 3: Implement strategy integration**

`NewsConfirmStrategy.__init__()` 增加 `insight_service` 可选参数。

`run()` 在加载关键词后调用洞察服务：

- 补充 `theme_keywords`
- 合并风险标记
- 硬风险直接进入拒绝列表

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_strategy.py -q`

Expected: PASS。

### Task 4: 主流程和配置接入

**Files:**

- Modify: `super_q/core/config.py`
- Modify: `main.py`
- Modify: `README.md`
- Test: `tests/test_main.py`

- [ ] **Step 1: Write failing tests**

新增测试：

- 主流程启用 A 股洞察服务时，会把增强分传给最终评分。
- 硬风险股票不会进入最终推送。

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m pytest tests/test_main.py -q`

Expected: 因配置和主流程接入缺失而失败。

- [ ] **Step 3: Implement main integration**

新增配置：

- `a_share_insight_enabled`
- `a_share_insight_cache_hours`
- `a_share_insight_score_weight`
- `a_share_insight_hard_risk_exclude`
- `a_share_insight_request_timeout_seconds`

`main.py` 在所有策略结果收集后，对候选股调用 `AShareInsightService.refresh_symbols()`，再将分数和风险传入 `build_final_selection()`。

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_main.py -q`

Expected: PASS。

### Task 5: 全量验证

**Files:**

- No production changes unless verification reveals issues.

- [ ] **Step 1: Run focused tests**

Run: `python -m pytest tests/test_a_share_insight.py tests/test_final_selection.py tests/test_strategy.py tests/test_main.py -q`

Expected: PASS。

- [ ] **Step 2: Run full tests**

Run: `python -m pytest -q`

Expected: PASS。
