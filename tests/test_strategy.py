"""策略引擎属性测试。"""

import tempfile
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
from hypothesis import given, settings as h_settings
from hypothesis import strategies as st

from super_q.core.config import Settings
from super_q.data.engine import DataEngine
from super_q.strategy.ma_volume import MaVolumeStrategy
from super_q.strategy.news_confirm import NewsConfirmStrategy
from super_q.strategy.rps_breakout import RpsBreakoutStrategy
from super_q.strategy.turtle_trade import TurtleTradeStrategy


class _FixedStrategy:
    def __init__(self, selected: list[str]) -> None:
        self._selected = selected

    def run(self) -> list[str]:
        return self._selected


class _FakeInsight:
    def __init__(
        self,
        theme_keywords: list[str] | None = None,
        risk_flags: list[str] | None = None,
        hard_risk: bool = False,
    ) -> None:
        self.theme_keywords = theme_keywords or []
        self.risk_flags = risk_flags or []
        self.hard_risk = hard_risk


class _FakeInsightService:
    def __init__(
        self,
        keywords: dict[str, list[str]] | None = None,
        insights: dict[str, _FakeInsight] | None = None,
    ) -> None:
        self.keywords = keywords or {}
        self.insights = insights or {}

    def get_symbol_keywords(self, symbols: list[str]) -> dict[str, list[str]]:
        return {
            symbol: self.keywords[symbol]
            for symbol in symbols
            if symbol in self.keywords
        }

    def get_symbol_insight(self, symbol: str):
        return self.insights.get(symbol, _FakeInsight())


def _insert_daily_rows(
    engine: DataEngine,
    rows: list[tuple[str, str, float, float, float, float, float, float]],
) -> None:
    with sqlite3.connect(engine.db_path) as conn:
        conn.executemany(
            """
            INSERT INTO stock_daily
                (symbol, date, open, high, low, close, volume, turnover)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()


@pytest.fixture(autouse=True)
def _disable_external_news_search(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEWS_TARGETED_SEARCH_ENABLED", "false")
    monkeypatch.setenv("NEWS_SEARXNG_URL", "")


# Feature: superQ-v2, Property 9: 策略 run() 返回值类型正确
@given(
    symbols=st.lists(
        st.text(min_size=6, max_size=6, alphabet="0123456789"),
        min_size=0, max_size=3, unique=True,
    )
)
@h_settings(max_examples=30, deadline=None)
def test_strategy_run_returns_list_of_str(symbols: list[str]) -> None:
    """属性 9：run() 应返回 list[str]，每个元素为非空字符串。"""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        settings = Settings(
            db_path=str(Path(tmp_dir) / "test.db"),
            start_date="2024-01-01",
            feishu_webhook_url="https://example.com/hook",
        )
        engine = DataEngine(settings)

        with patch.object(engine, "get_all_symbols", return_value=symbols):
            with patch.object(engine, "get_ohlcv", return_value=pd.DataFrame()):
                strategy = MaVolumeStrategy(engine=engine, settings=settings)
                result = strategy.run()

    assert isinstance(result, list)
    assert all(isinstance(s, str) and len(s) > 0 for s in result)


def test_turtle_strategy_skips_symbols_without_latest_market_date() -> None:
    """海龟策略不应把同步失败或停牌导致的旧日期突破混入今日信号。"""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        settings = Settings(
            db_path=str(Path(tmp_dir) / "test.db"),
            start_date="2024-01-01",
            feishu_webhook_url="https://example.com/hook",
            turtle_breakout_days=20,
            turtle_min_turnover=100_000_000,
            turtle_min_daily_gain=0.0,
        )
        engine = DataEngine(settings)
        rows = []
        old_dates = pd.date_range("2026-03-25", periods=21, freq="D")
        for index, day in enumerate(old_dates):
            close = 10.0 + index * 0.1
            high = close + 0.2
            if index == len(old_dates) - 1:
                close = 20.0
                high = 20.2
            rows.append(
                (
                    "300001",
                    day.strftime("%Y-%m-%d"),
                    close - 0.5,
                    high,
                    close - 1,
                    close,
                    10_000,
                    200_000_000,
                )
            )
        rows.append(("300002", "2026-04-27", 10.0, 10.5, 9.5, 10.1, 10_000, 50_000_000))
        _insert_daily_rows(engine, rows)

        assert TurtleTradeStrategy(engine=engine, settings=settings).run() == []


def test_rps_breakout_strategy_uses_configured_strict_filters() -> None:
    """RPS 策略应支持用更高 RPS、近高点比例和成交额过滤收紧候选池。"""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        settings = Settings(
            db_path=str(Path(tmp_dir) / "test.db"),
            start_date="2024-01-01",
            feishu_webhook_url="https://example.com/hook",
            rps_period=120,
            rps_threshold=95,
            rps_near_high_ratio=0.98,
            rps_min_turnover=200_000_000,
        )
        engine = DataEngine(settings)
        rows = []
        dates = pd.date_range("2025-12-28", periods=121, freq="D")
        latest_closes = {
            "000001": 150.0,
            "000002": 130.0,
            "000003": 120.0,
        }
        for number in range(1, 21):
            symbol = f"{number:06d}"
            latest_close = latest_closes.get(symbol, 80.0 + number)
            latest_high = (
                150.0
                if symbol == "000001"
                else 144.0
                if symbol == "000002"
                else latest_close + 2
            )
            for index, day in enumerate(dates):
                if index == len(dates) - 1:
                    close = latest_close
                    open_price = close - 2
                    high = latest_high
                    turnover = 300_000_000
                else:
                    close = 100.0
                    open_price = 99.5
                    high = 101.0
                    turnover = 120_000_000
                rows.append(
                    (
                        symbol,
                        day.strftime("%Y-%m-%d"),
                        open_price,
                        high,
                        close - 1,
                        close,
                        10_000,
                        turnover,
                    )
                )
        _insert_daily_rows(engine, rows)

        assert RpsBreakoutStrategy(engine=engine, settings=settings).run() == ["000001"]


def test_news_confirm_strategy_keeps_technical_stock_with_positive_news() -> None:
    """新闻确认策略应保留有正面相关新闻的技术候选股。"""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        settings = Settings(
            db_path=str(Path(tmp_dir) / "test.db"),
            start_date="2024-01-01",
            feishu_webhook_url="https://example.com/hook",
        )
        engine = DataEngine(settings)
        strategy = NewsConfirmStrategy(
            engine=engine,
            settings=settings,
            technical_strategies=[_FixedStrategy(["300054", "000007"])],
            news_fetcher=lambda: [
                {"title": "鼎龙股份机器人材料业务获得政策催化", "content": "机器人产业链持续升温"},
            ],
            symbol_keywords={"300054": ["鼎龙股份", "机器人"], "000007": ["全新好"]},
        )

        assert strategy.run() == ["300054"]


def test_news_confirm_strategy_filters_risk_news() -> None:
    """候选股出现立案、减持等风险新闻时应被过滤。"""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        settings = Settings(
            db_path=str(Path(tmp_dir) / "test.db"),
            start_date="2024-01-01",
            feishu_webhook_url="https://example.com/hook",
        )
        engine = DataEngine(settings)
        strategy = NewsConfirmStrategy(
            engine=engine,
            settings=settings,
            technical_strategies=[_FixedStrategy(["300054"])],
            news_fetcher=lambda: [
                {"title": "鼎龙股份机器人业务受关注", "content": "机器人产业链持续升温"},
                {"title": "鼎龙股份股东拟减持", "content": "公司公告股东拟减持股份"},
            ],
            symbol_keywords={"300054": ["鼎龙股份", "机器人"]},
        )

        assert strategy.run() == []


def test_news_confirm_strategy_uses_cached_news_from_last_7_days() -> None:
    """新闻确认策略应缓存新闻，并只使用近 7 天内的新闻。"""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        settings = Settings(
            db_path=str(Path(tmp_dir) / "test.db"),
            start_date="2024-01-01",
            feishu_webhook_url="https://example.com/hook",
            news_lookback_days=7,
        )
        engine = DataEngine(settings)
        strategy = NewsConfirmStrategy(
            engine=engine,
            settings=settings,
            technical_strategies=[_FixedStrategy(["300054", "000007"])],
            news_fetcher=lambda: [
                {
                    "source": "test",
                    "title": "鼎龙股份机器人材料业务获得政策催化",
                    "content": "机器人产业链持续升温",
                    "published_at": "2026-04-24 10:00:00",
                    "url": "https://example.com/a",
                },
                {
                    "source": "test",
                    "title": "全新好历史旧闻",
                    "content": "旧闻不应参与近 7 天策略",
                    "published_at": "2026-04-10 10:00:00",
                    "url": "https://example.com/b",
                },
            ],
            symbol_keywords={"300054": ["鼎龙股份", "机器人"], "000007": ["全新好"]},
            now=lambda: pd.Timestamp("2026-04-25 10:00:00"),
        )

        assert strategy.run() == ["300054"]
        with sqlite3.connect(engine.db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM news_items").fetchone()[0] == 2

        strategy_again = NewsConfirmStrategy(
            engine=engine,
            settings=settings,
            technical_strategies=[_FixedStrategy(["300054"])],
            news_fetcher=lambda: [],
            symbol_keywords={"300054": ["鼎龙股份", "机器人"]},
            now=lambda: pd.Timestamp("2026-04-25 10:00:00"),
        )
        assert strategy_again.run() == ["300054"]


def test_news_confirm_strategy_uses_local_stock_name_mapping() -> None:
    """新闻确认策略应优先使用本地 stock_names 映射表，不再依赖远端名称接口。"""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        settings = Settings(
            db_path=str(Path(tmp_dir) / "test.db"),
            start_date="2024-01-01",
            feishu_webhook_url="https://example.com/hook",
        )
        engine = DataEngine(settings)
        strategy = NewsConfirmStrategy(
            engine=engine,
            settings=settings,
            technical_strategies=[_FixedStrategy(["300054"])],
            news_fetcher=lambda: [
                {
                    "source": "test",
                    "title": "鼎龙股份机器人材料业务获得政策催化",
                    "content": "机器人产业链持续升温",
                    "published_at": "2026-04-24 10:00:00",
                },
            ],
            now=lambda: pd.Timestamp("2026-04-25 10:00:00"),
        )
        strategy.upsert_stock_names({"300054": "鼎龙股份"})

        with patch("akshare.stock_info_a_code_name", side_effect=AssertionError("不应拉远端映射")):
            assert strategy.run() == ["300054"]


def test_news_confirm_strategy_scores_events_and_sorts_candidates() -> None:
    """新闻确认策略应识别事件、题材热度并按综合分排序。"""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        settings = Settings(
            db_path=str(Path(tmp_dir) / "test.db"),
            start_date="2024-01-01",
            feishu_webhook_url="https://example.com/hook",
        )
        engine = DataEngine(settings)
        strategy = NewsConfirmStrategy(
            engine=engine,
            settings=settings,
            technical_strategies=[_FixedStrategy(["300054", "000333"])],
            news_fetcher=lambda: [
                {"title": "鼎龙股份机器人材料订单增长", "content": "机器人产业链政策催化"},
                {"title": "鼎龙股份半导体材料国产替代加速", "content": "订单持续增长"},
                {"title": "美的集团家电业务稳定", "content": "分红保持稳定"},
            ],
            symbol_keywords={
                "300054": ["鼎龙股份", "机器人", "半导体"],
                "000333": ["美的集团", "家电"],
            },
        )

        assert strategy.run() == ["300054", "000333"]
        scores = strategy.last_scores
        assert scores[0]["symbol"] == "300054"
        assert scores[0]["final_score"] > scores[1]["final_score"]
        assert "订单" in scores[0]["events"]
        assert "机器人" in scores[0]["themes"]


def test_news_confirm_strategy_uses_alias_and_concept_tables() -> None:
    """新闻关联应支持股票别名和概念题材表。"""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        settings = Settings(
            db_path=str(Path(tmp_dir) / "test.db"),
            start_date="2024-01-01",
            feishu_webhook_url="https://example.com/hook",
        )
        engine = DataEngine(settings)
        strategy = NewsConfirmStrategy(
            engine=engine,
            settings=settings,
            technical_strategies=[_FixedStrategy(["300054"])],
            news_fetcher=lambda: [
                {"title": "打印耗材龙头受益国产替代", "content": "半导体材料板块热度提升"},
            ],
            now=lambda: pd.Timestamp("2026-04-25 10:00:00"),
        )
        strategy.upsert_stock_names({"300054": "鼎龙股份"})
        strategy.upsert_stock_aliases({"300054": ["打印耗材龙头"]})
        strategy.upsert_stock_concepts({"300054": ["半导体材料"]})

        assert strategy.run() == ["300054"]
        assert "半导体材料" in strategy.last_scores[0]["themes"]


def test_news_confirm_strategy_records_risk_reject_reason() -> None:
    """风险新闻过滤应记录过滤原因，方便推送和排障。"""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        settings = Settings(
            db_path=str(Path(tmp_dir) / "test.db"),
            start_date="2024-01-01",
            feishu_webhook_url="https://example.com/hook",
        )
        engine = DataEngine(settings)
        strategy = NewsConfirmStrategy(
            engine=engine,
            settings=settings,
            technical_strategies=[_FixedStrategy(["300054"])],
            news_fetcher=lambda: [
                {"title": "鼎龙股份股东减持", "content": "减持计划公告"},
            ],
            symbol_keywords={"300054": ["鼎龙股份"]},
        )

        assert strategy.run() == []
        assert strategy.rejected_scores[0]["symbol"] == "300054"
        assert "减持" in strategy.rejected_scores[0]["reject_reason"]


def test_news_confirm_strategy_uses_targeted_news_fetcher_for_candidates() -> None:
    """技术候选股可通过定向新闻补充获得催化剂确认。"""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        settings = Settings(
            db_path=str(Path(tmp_dir) / "test.db"),
            start_date="2024-01-01",
            feishu_webhook_url="https://example.com/hook",
        )
        engine = DataEngine(settings)

        def targeted_fetcher(symbol: str, keywords: list[str]) -> list[dict[str, str]]:
            assert symbol == "300054"
            assert "鼎龙股份" in keywords
            return [
                {
                    "source": "东方财富",
                    "title": "鼎龙股份半导体材料订单增长",
                    "content": "国产替代加速，订单持续增长",
                    "published_at": "2026-04-24 10:00:00",
                }
            ]

        strategy = NewsConfirmStrategy(
            engine=engine,
            settings=settings,
            technical_strategies=[_FixedStrategy(["300054"])],
            news_fetcher=lambda: [],
            targeted_news_fetcher=targeted_fetcher,
            symbol_keywords={"300054": ["鼎龙股份", "半导体材料"]},
            now=lambda: pd.Timestamp("2026-04-25 10:00:00"),
        )

        assert strategy.run() == ["300054"]
        assert strategy.last_scores[0]["matched_news"][0]["source"] == "东方财富"


def test_news_confirm_strategy_does_not_match_by_theme_only() -> None:
    """新闻只命中题材词但没有股票名称、代码或别名时，不应关联到候选股。"""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        settings = Settings(
            db_path=str(Path(tmp_dir) / "test.db"),
            start_date="2024-01-01",
            feishu_webhook_url="https://example.com/hook",
        )
        engine = DataEngine(settings)
        strategy = NewsConfirmStrategy(
            engine=engine,
            settings=settings,
            technical_strategies=[_FixedStrategy(["300054"])],
            news_fetcher=lambda: [
                {"title": "机器人产业政策持续加码", "content": "机器人产业链景气度提升"},
            ],
            symbol_keywords={"300054": ["鼎龙股份", "机器人"]},
        )

        assert strategy.run() == []


def test_news_confirm_strategy_passes_searxng_basic_auth() -> None:
    """SearXNG 开启 Basic Auth 后，定向搜索请求应携带用户名密码。"""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        settings = Settings(
            db_path=str(Path(tmp_dir) / "test.db"),
            start_date="2024-01-01",
            feishu_webhook_url="https://example.com/hook",
            news_targeted_search_enabled=True,
            news_searxng_url="http://searxng.example",
            news_searxng_username="superq",
            news_searxng_password="secret",
            news_targeted_search_limit=1,
        )
        engine = DataEngine(settings)
        strategy = NewsConfirmStrategy(
            engine=engine,
            settings=settings,
            technical_strategies=[_FixedStrategy(["300054"])],
            news_fetcher=lambda: [],
            symbol_keywords={"300054": ["鼎龙股份"]},
            now=lambda: pd.Timestamp("2026-04-25 10:00:00"),
        )

        class _Response:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, list[dict[str, str]]]:
                return {"results": []}

        with patch("requests.get", return_value=_Response()) as get:
            strategy.run()

        assert get.call_args.kwargs["auth"] == ("superq", "secret")


def test_news_confirm_strategy_soft_risk_reduces_score_without_hard_reject() -> None:
    """软风险只扣分并保留风险提示，不应像硬风险一样直接生成反向信号。"""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        settings = Settings(
            db_path=str(Path(tmp_dir) / "test.db"),
            start_date="2024-01-01",
            feishu_webhook_url="https://example.com/hook",
        )
        engine = DataEngine(settings)
        strategy = NewsConfirmStrategy(
            engine=engine,
            settings=settings,
            technical_strategies=[_FixedStrategy(["300054"])],
            news_fetcher=lambda: [
                {
                    "title": "鼎龙股份收到问询函后订单继续增长",
                    "content": "半导体材料订单增长，国产替代趋势延续",
                },
            ],
            symbol_keywords={"300054": ["鼎龙股份", "半导体材料"]},
        )

        assert strategy.run() == ["300054"]
        assert strategy.rejected_scores == []
        assert "问询函" in strategy.last_scores[0]["soft_risks"]


def test_news_confirm_strategy_cleans_old_cached_news() -> None:
    """新闻缓存应按保留天数清理旧新闻。"""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        settings = Settings(
            db_path=str(Path(tmp_dir) / "test.db"),
            start_date="2024-01-01",
            feishu_webhook_url="https://example.com/hook",
            news_cache_retention_days=30,
        )
        engine = DataEngine(settings)
        strategy = NewsConfirmStrategy(
            engine=engine,
            settings=settings,
            technical_strategies=[_FixedStrategy(["300054"])],
            news_fetcher=lambda: [
                {
                    "title": "鼎龙股份机器人材料业务获得政策催化",
                    "published_at": "2026-04-24 10:00:00",
                },
                {
                    "title": "鼎龙股份很久以前的旧新闻",
                    "published_at": "2026-01-01 10:00:00",
                },
            ],
            symbol_keywords={"300054": ["鼎龙股份"]},
            now=lambda: pd.Timestamp("2026-04-25 10:00:00"),
        )

        assert strategy.run() == ["300054"]
        with sqlite3.connect(engine.db_path) as conn:
            titles = [row[0] for row in conn.execute("SELECT title FROM news_items").fetchall()]
        assert titles == ["鼎龙股份机器人材料业务获得政策催化"]


def test_news_confirm_strategy_builds_push_summary() -> None:
    """新闻策略应生成可推送的评分、事件和命中新闻摘要。"""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        settings = Settings(
            db_path=str(Path(tmp_dir) / "test.db"),
            start_date="2024-01-01",
            feishu_webhook_url="https://example.com/hook",
        )
        engine = DataEngine(settings)
        strategy = NewsConfirmStrategy(
            engine=engine,
            settings=settings,
            technical_strategies=[_FixedStrategy(["300054"])],
            news_fetcher=lambda: [
                {"title": "鼎龙股份机器人材料订单增长", "content": "机器人产业链政策催化"},
            ],
            symbol_keywords={"300054": ["鼎龙股份", "机器人"]},
        )

        assert strategy.run() == ["300054"]
        summary = strategy.news_summary_text()
        assert "新闻确认：" in summary
        assert "300054" in summary
        assert "综合分" in summary
        assert "鼎龙股份机器人材料订单增长" in summary


def test_news_confirm_strategy_uses_a_share_insight_theme_keywords() -> None:
    """新闻确认应使用 A 股洞察服务提供的题材关键词增强事件识别。"""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        settings = Settings(
            db_path=str(Path(tmp_dir) / "test.db"),
            start_date="2024-01-01",
            feishu_webhook_url="https://example.com/hook",
            news_score_threshold=0,
        )
        engine = DataEngine(settings)
        strategy = NewsConfirmStrategy(
            engine=engine,
            settings=settings,
            technical_strategies=[_FixedStrategy(["300054"])],
            news_fetcher=lambda: [
                {"title": "鼎龙股份订单增长", "content": "半导体材料国产替代加速"},
            ],
            symbol_keywords={"300054": ["鼎龙股份"]},
            insight_service=_FakeInsightService(
                keywords={"300054": ["半导体材料", "国产替代"]}
            ),
        )

        assert strategy.run() == ["300054"]
        assert "半导体材料" in strategy.last_scores[0]["themes"]


def test_news_confirm_strategy_rejects_a_share_insight_hard_risk() -> None:
    """A 股洞察硬风险应让新闻候选进入拒绝列表。"""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        settings = Settings(
            db_path=str(Path(tmp_dir) / "test.db"),
            start_date="2024-01-01",
            feishu_webhook_url="https://example.com/hook",
            news_score_threshold=0,
        )
        engine = DataEngine(settings)
        strategy = NewsConfirmStrategy(
            engine=engine,
            settings=settings,
            technical_strategies=[_FixedStrategy(["300054"])],
            news_fetcher=lambda: [
                {"title": "鼎龙股份订单增长", "content": "机器人材料业务增长"},
            ],
            symbol_keywords={"300054": ["鼎龙股份"]},
            insight_service=_FakeInsightService(
                insights={"300054": _FakeInsight(risk_flags=["立案"], hard_risk=True)}
            ),
        )

        assert strategy.run() == []
        assert strategy.gm_reverse_symbols() == ["300054"]
        assert strategy.rejected_scores[0]["reject_reason"] == "立案"
