"""A 股增强洞察服务测试。"""

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import Mock

import pandas as pd

from super_q.core.config import Settings
from super_q.data.a_share_insight import AShareInsightService
from super_q.data.engine import DataEngine


def _make_engine() -> DataEngine:
    tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    settings = Settings(
        db_path=str(Path(tmp_dir.name) / "test.db"),
        start_date="2024-01-01",
        feishu_webhook_url="https://example.com/hook",
        a_share_insight_enabled=True,
    )
    engine = DataEngine(settings)
    engine._tmp_dir = tmp_dir  # type: ignore[attr-defined]
    return engine


def test_a_share_insight_scores_money_heat_theme_and_risk() -> None:
    """洞察服务应把资金、热度、题材、风险标准化为稳定分数。"""
    engine = _make_engine()
    ak = Mock()
    ak.stock_individual_fund_flow.return_value = pd.DataFrame(
        [{"日期": "2026-05-05", "主力净流入-净额": 120_000_000}]
    )
    ak.stock_hot_rank_latest_em.return_value = pd.DataFrame(
        [{"代码": "300054", "排名": 12}, {"代码": "000001", "排名": 120}]
    )
    ak.stock_individual_info_em.return_value = pd.DataFrame(
        [
            {"item": "股票简称", "value": "鼎龙股份"},
            {"item": "行业", "value": "半导体材料"},
        ]
    )
    ak.stock_zh_a_disclosure_report_cninfo.return_value = pd.DataFrame(
        [{"公告标题": "鼎龙股份获得机构调研"}]
    )
    ak.stock_restricted_release_detail_em.return_value = pd.DataFrame()
    ak.stock_gpzy_pledge_ratio_detail_em.return_value = pd.DataFrame()

    service = AShareInsightService(engine=engine, settings=engine.settings, ak_client=ak)
    insight = service.get_symbol_insight("300054", force_refresh=True)

    assert insight.symbol == "300054"
    assert insight.money_flow_score > 0
    assert insight.heat_score > 0
    assert "半导体材料" in insight.theme_keywords
    assert insight.hard_risk is False
    assert insight.total_score > 0

    with sqlite3.connect(engine.db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM a_share_insights").fetchone()[0]
    assert count == 1


def test_a_share_insight_marks_hard_risk_from_disclosure() -> None:
    """公告命中硬风险词时应产生硬风险和负向分数。"""
    engine = _make_engine()
    ak = Mock()
    ak.stock_individual_fund_flow.return_value = pd.DataFrame()
    ak.stock_hot_rank_latest_em.return_value = pd.DataFrame()
    ak.stock_individual_info_em.return_value = pd.DataFrame(
        [{"item": "股票简称", "value": "风险股份"}]
    )
    ak.stock_zh_a_disclosure_report_cninfo.return_value = pd.DataFrame(
        [{"公告标题": "风险股份收到监管立案调查公告"}]
    )
    ak.stock_restricted_release_detail_em.return_value = pd.DataFrame()
    ak.stock_gpzy_pledge_ratio_detail_em.return_value = pd.DataFrame()

    service = AShareInsightService(engine=engine, settings=engine.settings, ak_client=ak)
    insight = service.get_symbol_insight("000001", force_refresh=True)

    assert insight.hard_risk is True
    assert any("立案" in flag or "调查" in flag for flag in insight.risk_flags)
    assert insight.total_score < 0


def test_a_share_insight_returns_neutral_when_akshare_fails() -> None:
    """外部接口失败时应返回中性洞察，不中断主流程。"""
    engine = _make_engine()
    ak = Mock()
    ak.stock_individual_fund_flow.side_effect = RuntimeError("网络失败")
    ak.stock_hot_rank_latest_em.side_effect = RuntimeError("网络失败")
    ak.stock_individual_info_em.side_effect = RuntimeError("网络失败")
    ak.stock_zh_a_disclosure_report_cninfo.side_effect = RuntimeError("网络失败")
    ak.stock_restricted_release_detail_em.side_effect = RuntimeError("网络失败")
    ak.stock_gpzy_pledge_ratio_detail_em.side_effect = RuntimeError("网络失败")

    service = AShareInsightService(engine=engine, settings=engine.settings, ak_client=ak)
    insight = service.get_symbol_insight("300054", force_refresh=True)

    assert insight.total_score == 0
    assert insight.risk_flags == []
    assert insight.theme_keywords == []


def test_a_share_insight_get_symbol_keywords_uses_theme_keywords() -> None:
    """服务应能为新闻策略提供股票题材关键词。"""
    engine = _make_engine()
    service = AShareInsightService(engine=engine, settings=engine.settings)
    service._write_cache(
        service._neutral_insight(
            "300054",
            theme_keywords=["半导体材料", "机器人材料"],
        )
    )

    assert service.get_symbol_keywords(["300054"]) == {
        "300054": ["半导体材料", "机器人材料"]
    }


def test_a_share_insight_calls_theme_api_with_timeout() -> None:
    """题材接口应传入配置超时，避免代理或远端异常时拖慢主流程。"""
    engine = _make_engine()
    object.__setattr__(engine.settings, "a_share_insight_request_timeout_seconds", 3.5)
    ak = Mock()
    ak.stock_individual_fund_flow.return_value = pd.DataFrame()
    ak.stock_hot_rank_latest_em.return_value = pd.DataFrame()
    ak.stock_individual_info_em.return_value = pd.DataFrame(
        [{"item": "行业", "value": "半导体材料"}]
    )
    ak.stock_zh_a_disclosure_report_cninfo.return_value = pd.DataFrame()
    ak.stock_restricted_release_detail_em.return_value = pd.DataFrame()
    ak.stock_gpzy_pledge_ratio_detail_em.return_value = pd.DataFrame()

    service = AShareInsightService(engine=engine, settings=engine.settings, ak_client=ak)
    insight = service.get_symbol_insight("300054", force_refresh=True)

    assert "半导体材料" in insight.theme_keywords
    ak.stock_individual_info_em.assert_called_once_with(symbol="300054", timeout=3.5)


def test_a_share_insight_risk_apis_match_current_akshare_signatures() -> None:
    """限售解禁和股权质押接口不应传 symbol 参数，并仍能按股票代码过滤风险。"""
    engine = _make_engine()
    ak = Mock()
    ak.stock_individual_fund_flow.return_value = pd.DataFrame()
    ak.stock_hot_rank_latest_em.return_value = pd.DataFrame()
    ak.stock_individual_info_em.return_value = pd.DataFrame()
    ak.stock_zh_a_disclosure_report_cninfo.return_value = pd.DataFrame()
    ak.stock_restricted_release_detail_em.return_value = pd.DataFrame(
        [{"代码": "000402", "股票简称": "金融街", "解禁类型": "首发限售解禁"}]
    )
    ak.stock_gpzy_pledge_ratio_detail_em.return_value = pd.DataFrame(
        [{"证券代码": "000402", "证券简称": "金融街", "质押比例": "35%"}]
    )

    service = AShareInsightService(engine=engine, settings=engine.settings, ak_client=ak)
    insight = service.get_symbol_insight("000402", force_refresh=True)

    ak.stock_restricted_release_detail_em.assert_called_once_with()
    ak.stock_gpzy_pledge_ratio_detail_em.assert_called_once_with()
    assert "解禁" in insight.risk_flags
    assert "质押" in insight.risk_flags
