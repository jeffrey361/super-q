"""主程序入口属性测试。"""

import sys
from unittest.mock import patch

import pytest
from hypothesis import given, settings as h_settings
from hypothesis import strategies as st

# 预先导入 main 模块，避免在 @given 循环中重复导入
import main as main_module
from super_q.core.config import Settings


# Feature: superQ-v2, Property 13: 主程序异常以非零退出码终止
@given(error_msg=st.text(min_size=1, max_size=100))
@h_settings(max_examples=30, deadline=None)
def test_main_exits_nonzero_on_exception(error_msg: str) -> None:
    """属性 13：main() 中任意未捕获异常应导致 sys.exit(1)。"""
    # patch main 模块中直接引用的 get_settings
    with patch.object(main_module, "get_settings", side_effect=RuntimeError(error_msg)):
        with pytest.raises(SystemExit) as exc_info:
            main_module.main()
        assert exc_info.value.code != 0


def test_main_sends_wechat_notification_for_selected_symbols() -> None:
    """主流程有选股结果时应同时尝试微信 iLink 推送并导出掘金信号。"""
    settings = Settings(
        db_path="data/test.db",
        start_date="2024-01-01",
        feishu_webhook_url="https://example.com/hook",
        wechat_ilink_enabled=True,
        wechat_ilink_target_user_id="abc@im.wechat",
        final_selection_min_score=0,
    )

    class _Strategy:
        webhook_key = "test"

        def run(self) -> list[str]:
            return ["300054"]

    class _WeekendDate:
        @classmethod
        def today(cls):
            class _Today:
                def weekday(self) -> int:
                    return 5

            return _Today()

    with (
        patch.object(main_module, "get_settings", return_value=settings),
        patch.object(main_module, "DataEngine") as engine_cls,
        patch.object(main_module, "date", _WeekendDate),
        patch.object(main_module, "MaVolumeStrategy", return_value=_Strategy()),
        patch.object(main_module, "TurtleTradeStrategy", return_value=_Strategy()),
        patch.object(main_module, "HighTightFlagStrategy", return_value=_Strategy()),
        patch.object(main_module, "LimitUpShakeoutStrategy", return_value=_Strategy()),
        patch.object(main_module, "UptrendLimitDownStrategy", return_value=_Strategy()),
        patch.object(main_module, "RpsBreakoutStrategy", return_value=_Strategy()),
        patch.object(main_module, "NewsConfirmStrategy", return_value=_Strategy()),
        patch.object(main_module, "FeishuNotifier") as feishu_cls,
        patch.object(main_module, "WechatIlinkNotifier") as wechat_cls,
        patch.object(main_module, "GmSignalExporter") as gm_exporter_cls,
        patch.object(main_module, "GmAccountSnapshotReader") as gm_account_reader_cls,
    ):
        engine_cls.return_value = object()
        gm_account_reader_cls.return_value.summary_text.return_value = "账户余额：88000.50"

        main_module.main()

    feishu_cls.return_value.send.assert_called_once()
    wechat_cls.return_value.send.assert_called_once()
    gm_exporter_cls.return_value.export.assert_called_once()
    assert feishu_cls.return_value.send.call_args.kwargs["account_summary"] == "账户余额：88000.50"
    assert wechat_cls.return_value.send.call_args.kwargs["account_summary"] == "账户余额：88000.50"


def test_main_uses_final_selection_limit_for_notification_and_gm_export() -> None:
    """通知和 GM 都只使用最终选股 Top N。"""
    settings = Settings(
        db_path="data/test.db",
        start_date="2024-01-01",
        feishu_webhook_url="https://example.com/hook",
        gm_enabled=True,
        final_selection_max_symbols=2,
        final_selection_min_score=0,
    )

    class MaVolumeStrategy:
        webhook_key = "test"

        def run(self) -> list[str]:
            return ["300054", "000007", "600900"]

    class _EmptyStrategy:
        webhook_key = "test"

        def run(self) -> list[str]:
            return []

    class _WeekendDate:
        @classmethod
        def today(cls):
            class _Today:
                def weekday(self) -> int:
                    return 5

            return _Today()

    with (
        patch.object(main_module, "get_settings", return_value=settings),
        patch.object(main_module, "DataEngine") as engine_cls,
        patch.object(main_module, "date", _WeekendDate),
        patch.object(main_module, "MaVolumeStrategy", return_value=MaVolumeStrategy()),
        patch.object(main_module, "TurtleTradeStrategy", return_value=_EmptyStrategy()),
        patch.object(main_module, "HighTightFlagStrategy", return_value=_EmptyStrategy()),
        patch.object(main_module, "LimitUpShakeoutStrategy", return_value=_EmptyStrategy()),
        patch.object(main_module, "UptrendLimitDownStrategy", return_value=_EmptyStrategy()),
        patch.object(main_module, "RpsBreakoutStrategy", return_value=_EmptyStrategy()),
        patch.object(main_module, "NewsConfirmStrategy", return_value=_EmptyStrategy()),
        patch.object(main_module, "FeishuNotifier") as feishu_cls,
        patch.object(main_module, "WechatIlinkNotifier") as wechat_cls,
        patch.object(main_module, "GmSignalExporter") as gm_exporter_cls,
        patch.object(main_module, "GmAccountSnapshotReader") as gm_account_reader_cls,
    ):
        engine_cls.return_value = object()
        gm_account_reader_cls.return_value.summary_text.return_value = ""

        main_module.main()

    assert feishu_cls.return_value.send.call_args.kwargs["symbols"] == ["000007", "300054"]
    assert wechat_cls.return_value.send.call_args.kwargs["symbols"] == ["000007", "300054"]
    assert gm_exporter_cls.return_value.export.call_args.kwargs["symbols"] == [
        "000007",
        "300054",
    ]


def test_main_sends_one_final_top_selection_and_exports_gm_once() -> None:
    """主流程应汇总所有策略后只推送一次，并只导出最终 Top 5 给 GM。"""
    settings = Settings(
        db_path="data/test.db",
        start_date="2024-01-01",
        feishu_webhook_url="https://example.com/hook",
        gm_enabled=True,
        final_selection_max_symbols=5,
        final_selection_min_score=0,
    )

    class _Strategy:
        webhook_key = "test"

        def __init__(self, symbols: list[str]) -> None:
            self.symbols = symbols

        def run(self) -> list[str]:
            return self.symbols

    class MaVolumeStrategy(_Strategy):
        pass

    class TurtleTradeStrategy(_Strategy):
        pass

    class HighTightFlagStrategy(_Strategy):
        pass

    class LimitUpShakeoutStrategy(_Strategy):
        pass

    class UptrendLimitDownStrategy(_Strategy):
        pass

    class RpsBreakoutStrategy(_Strategy):
        pass

    class NewsConfirmStrategy(_Strategy):
        webhook_key = "news_confirm"

        def gm_news_scores(self):
            return [
                {"symbol": "000006", "final_score": 95},
                {"symbol": "000002", "final_score": 80},
            ]

        def gm_reverse_symbols(self):
            return []

    class _WeekendDate:
        @classmethod
        def today(cls):
            class _Today:
                def weekday(self) -> int:
                    return 5

            return _Today()

    with (
        patch.object(main_module, "get_settings", return_value=settings),
        patch.object(main_module, "DataEngine") as engine_cls,
        patch.object(main_module, "date", _WeekendDate),
        patch.object(
            main_module,
            "MaVolumeStrategy",
            return_value=MaVolumeStrategy(
                ["000001", "000002", "000003", "000004", "000005", "000006"]
            ),
        ),
        patch.object(
            main_module,
            "TurtleTradeStrategy",
            return_value=TurtleTradeStrategy(["000003"]),
        ),
        patch.object(
            main_module,
            "HighTightFlagStrategy",
            return_value=HighTightFlagStrategy(["000006"]),
        ),
        patch.object(
            main_module,
            "LimitUpShakeoutStrategy",
            return_value=LimitUpShakeoutStrategy([]),
        ),
        patch.object(
            main_module,
            "UptrendLimitDownStrategy",
            return_value=UptrendLimitDownStrategy([]),
        ),
        patch.object(main_module, "RpsBreakoutStrategy", return_value=RpsBreakoutStrategy([])),
        patch.object(
            main_module,
            "NewsConfirmStrategy",
            return_value=NewsConfirmStrategy(["000002", "000006"]),
        ),
        patch.object(main_module, "FeishuNotifier") as feishu_cls,
        patch.object(main_module, "WechatIlinkNotifier") as wechat_cls,
        patch.object(main_module, "GmSignalExporter") as gm_exporter_cls,
        patch.object(main_module, "GmAccountSnapshotReader") as gm_account_reader_cls,
    ):
        engine_cls.return_value = object()
        gm_account_reader_cls.return_value.summary_text.return_value = ""

        main_module.main()

    feishu_cls.return_value.send.assert_called_once()
    wechat_cls.return_value.send.assert_called_once()
    gm_exporter_cls.return_value.export.assert_called_once()
    assert feishu_cls.return_value.send.call_args.kwargs["strategy_name"] == "DailyTopSelection"
    assert feishu_cls.return_value.send.call_args.kwargs["symbols"] == [
        "000006",
        "000002",
        "000003",
        "000001",
        "000004",
    ]
    assert gm_exporter_cls.return_value.export.call_args.kwargs["symbols"] == [
        "000006",
        "000002",
        "000003",
        "000001",
        "000004",
    ]


def test_main_skips_notifications_and_gm_when_final_selection_is_empty() -> None:
    """最终选股为空时，当天不推送也不导出 GM 信号。"""
    settings = Settings(
        db_path="data/test.db",
        start_date="2024-01-01",
        feishu_webhook_url="https://example.com/hook",
        gm_enabled=True,
        final_selection_min_score=999,
    )

    class _Strategy:
        webhook_key = "test"

        def run(self) -> list[str]:
            return ["000001"]

    class _WeekendDate:
        @classmethod
        def today(cls):
            class _Today:
                def weekday(self) -> int:
                    return 5

            return _Today()

    with (
        patch.object(main_module, "get_settings", return_value=settings),
        patch.object(main_module, "DataEngine") as engine_cls,
        patch.object(main_module, "date", _WeekendDate),
        patch.object(main_module, "MaVolumeStrategy", return_value=_Strategy()),
        patch.object(main_module, "TurtleTradeStrategy", return_value=_Strategy()),
        patch.object(main_module, "HighTightFlagStrategy", return_value=_Strategy()),
        patch.object(main_module, "LimitUpShakeoutStrategy", return_value=_Strategy()),
        patch.object(main_module, "UptrendLimitDownStrategy", return_value=_Strategy()),
        patch.object(main_module, "RpsBreakoutStrategy", return_value=_Strategy()),
        patch.object(main_module, "NewsConfirmStrategy", return_value=_Strategy()),
        patch.object(main_module, "FeishuNotifier") as feishu_cls,
        patch.object(main_module, "WechatIlinkNotifier") as wechat_cls,
        patch.object(main_module, "GmSignalExporter") as gm_exporter_cls,
        patch.object(main_module, "GmAccountSnapshotReader") as gm_account_reader_cls,
    ):
        engine_cls.return_value = object()
        gm_account_reader_cls.return_value.summary_text.return_value = ""

        main_module.main()

    feishu_cls.return_value.send.assert_not_called()
    wechat_cls.return_value.send.assert_not_called()
    gm_exporter_cls.return_value.export.assert_not_called()


def test_main_skips_market_sync_on_weekday_when_disabled() -> None:
    """交易日也应支持通过配置跳过行情同步，直接使用本地数据跑策略。"""
    settings = Settings(
        db_path="data/test.db",
        start_date="2024-01-01",
        feishu_webhook_url="https://example.com/hook",
        sync_market_data=False,
    )

    class _EmptyStrategy:
        webhook_key = "test"

        def run(self) -> list[str]:
            return []

    class _WeekdayDate:
        @classmethod
        def today(cls):
            class _Today:
                def weekday(self) -> int:
                    return 0

            return _Today()

    with (
        patch.object(main_module, "get_settings", return_value=settings),
        patch.object(main_module, "DataEngine") as engine_cls,
        patch.object(main_module, "date", _WeekdayDate),
        patch.object(main_module, "MaVolumeStrategy", return_value=_EmptyStrategy()),
        patch.object(main_module, "TurtleTradeStrategy", return_value=_EmptyStrategy()),
        patch.object(main_module, "HighTightFlagStrategy", return_value=_EmptyStrategy()),
        patch.object(main_module, "LimitUpShakeoutStrategy", return_value=_EmptyStrategy()),
        patch.object(main_module, "UptrendLimitDownStrategy", return_value=_EmptyStrategy()),
        patch.object(main_module, "RpsBreakoutStrategy", return_value=_EmptyStrategy()),
        patch.object(main_module, "NewsConfirmStrategy", return_value=_EmptyStrategy()),
        patch.object(main_module, "FeishuNotifier"),
        patch.object(main_module, "WechatIlinkNotifier"),
        patch.object(main_module, "GmSignalExporter"),
        patch.object(main_module, "GmAccountSnapshotReader") as gm_account_reader_cls,
    ):
        engine_cls.return_value.get_all_symbols.return_value = ["300054"]
        gm_account_reader_cls.return_value.summary_text.return_value = ""

        main_module.main()

    engine_cls.return_value.get_all_symbols.assert_not_called()
    engine_cls.return_value.sync_all.assert_not_called()


def test_main_exports_news_scores_for_final_gm_selection() -> None:
    """主流程应把新闻高可信分数传给最终 GM 导出器。"""
    settings = Settings(
        db_path="data/test.db",
        start_date="2024-01-01",
        feishu_webhook_url="https://example.com/hook",
        gm_enabled=True,
        final_selection_min_score=0,
    )

    class _EmptyStrategy:
        webhook_key = "test"

        def run(self) -> list[str]:
            return []

    class _NewsStrategy:
        webhook_key = "news"

        def run(self) -> list[str]:
            return ["300054"]

        def news_summary_text(self) -> str:
            return "新闻摘要"

        def gm_news_scores(self):
            return [{"symbol": "300054", "final_score": 90}]

        def gm_reverse_symbols(self):
            return ["600900"]

    class _WeekendDate:
        @classmethod
        def today(cls):
            class _Today:
                def weekday(self) -> int:
                    return 5

            return _Today()

    with (
        patch.object(main_module, "get_settings", return_value=settings),
        patch.object(main_module, "DataEngine") as engine_cls,
        patch.object(main_module, "date", _WeekendDate),
        patch.object(main_module, "MaVolumeStrategy", return_value=_EmptyStrategy()),
        patch.object(main_module, "TurtleTradeStrategy", return_value=_EmptyStrategy()),
        patch.object(main_module, "HighTightFlagStrategy", return_value=_EmptyStrategy()),
        patch.object(main_module, "LimitUpShakeoutStrategy", return_value=_EmptyStrategy()),
        patch.object(main_module, "UptrendLimitDownStrategy", return_value=_EmptyStrategy()),
        patch.object(main_module, "RpsBreakoutStrategy", return_value=_EmptyStrategy()),
        patch.object(main_module, "NewsConfirmStrategy", return_value=_NewsStrategy()),
        patch.object(main_module, "FeishuNotifier"),
        patch.object(main_module, "WechatIlinkNotifier"),
        patch.object(main_module, "GmSignalExporter") as gm_exporter_cls,
        patch.object(main_module, "GmAccountSnapshotReader") as gm_account_reader_cls,
    ):
        engine_cls.return_value = object()
        gm_account_reader_cls.return_value.summary_text.return_value = ""

        main_module.main()

    gm_exporter_cls.return_value.export.assert_called_once()
    assert gm_exporter_cls.return_value.export.call_args.kwargs["news_scores"] == [
        {"symbol": "300054", "final_score": 90}
    ]
    assert gm_exporter_cls.return_value.export.call_args.kwargs["reverse_symbols"] == []


def test_main_skips_gm_when_only_reverse_signal_without_final_selection() -> None:
    """只有风险反向信号但没有最终买入股时，不应导出 GM 信号。"""
    settings = Settings(
        db_path="data/test.db",
        start_date="2024-01-01",
        feishu_webhook_url="https://example.com/hook",
        gm_enabled=True,
    )

    class _EmptyStrategy:
        webhook_key = "test"

        def run(self) -> list[str]:
            return []

    class _ReverseOnlyStrategy:
        webhook_key = "news"

        def run(self) -> list[str]:
            return []

        def gm_reverse_symbols(self):
            return ["600900"]

    class _WeekendDate:
        @classmethod
        def today(cls):
            class _Today:
                def weekday(self) -> int:
                    return 5

            return _Today()

    with (
        patch.object(main_module, "get_settings", return_value=settings),
        patch.object(main_module, "DataEngine") as engine_cls,
        patch.object(main_module, "date", _WeekendDate),
        patch.object(main_module, "MaVolumeStrategy", return_value=_EmptyStrategy()),
        patch.object(main_module, "TurtleTradeStrategy", return_value=_EmptyStrategy()),
        patch.object(main_module, "HighTightFlagStrategy", return_value=_EmptyStrategy()),
        patch.object(main_module, "LimitUpShakeoutStrategy", return_value=_EmptyStrategy()),
        patch.object(main_module, "UptrendLimitDownStrategy", return_value=_EmptyStrategy()),
        patch.object(main_module, "RpsBreakoutStrategy", return_value=_EmptyStrategy()),
        patch.object(main_module, "NewsConfirmStrategy", return_value=_ReverseOnlyStrategy()),
        patch.object(main_module, "FeishuNotifier"),
        patch.object(main_module, "WechatIlinkNotifier"),
        patch.object(main_module, "GmSignalExporter") as gm_exporter_cls,
        patch.object(main_module, "GmAccountSnapshotReader") as gm_account_reader_cls,
    ):
        engine_cls.return_value = object()
        gm_account_reader_cls.return_value.summary_text.return_value = ""

        main_module.main()

    gm_exporter_cls.return_value.export.assert_not_called()


def test_main_uses_a_share_insight_scores_for_final_selection() -> None:
    """主流程启用 A 股洞察后，应把增强分传入最终选股排序。"""
    settings = Settings(
        db_path="data/test.db",
        start_date="2024-01-01",
        feishu_webhook_url="https://example.com/hook",
        final_selection_min_score=0,
        a_share_insight_enabled=True,
    )

    class _Strategy:
        webhook_key = "test"

        def __init__(self, symbols: list[str]) -> None:
            self.symbols = symbols

        def run(self) -> list[str]:
            return self.symbols

    class _Insight:
        def __init__(self, total_score: float, hard_risk: bool = False) -> None:
            self.total_score = total_score
            self.hard_risk = hard_risk
            self.risk_flags = []

    class _WeekendDate:
        @classmethod
        def today(cls):
            class _Today:
                def weekday(self) -> int:
                    return 5

            return _Today()

    with (
        patch.object(main_module, "get_settings", return_value=settings),
        patch.object(main_module, "DataEngine") as engine_cls,
        patch.object(main_module, "date", _WeekendDate),
        patch.object(main_module, "MaVolumeStrategy", return_value=_Strategy(["000001"])),
        patch.object(main_module, "TurtleTradeStrategy", return_value=_Strategy(["000002"])),
        patch.object(main_module, "HighTightFlagStrategy", return_value=_Strategy([])),
        patch.object(main_module, "LimitUpShakeoutStrategy", return_value=_Strategy([])),
        patch.object(main_module, "UptrendLimitDownStrategy", return_value=_Strategy([])),
        patch.object(main_module, "RpsBreakoutStrategy", return_value=_Strategy([])),
        patch.object(main_module, "NewsConfirmStrategy", return_value=_Strategy([])),
        patch.object(main_module, "AShareInsightService", create=True) as insight_cls,
        patch.object(main_module, "FeishuNotifier") as feishu_cls,
        patch.object(main_module, "WechatIlinkNotifier") as wechat_cls,
        patch.object(main_module, "GmSignalExporter") as gm_exporter_cls,
        patch.object(main_module, "GmAccountSnapshotReader") as gm_account_reader_cls,
    ):
        engine_cls.return_value = object()
        gm_account_reader_cls.return_value.summary_text.return_value = ""
        insight_cls.return_value.refresh_symbols.return_value = {
            "000001": _Insight(0),
            "000002": _Insight(20),
        }

        main_module.main()

    assert feishu_cls.return_value.send.call_args.kwargs["symbols"] == ["000002", "000001"]
    assert wechat_cls.return_value.send.call_args.kwargs["symbols"] == ["000002", "000001"]
    assert gm_exporter_cls.return_value.export.call_args.kwargs["symbols"] == ["000002", "000001"]


def test_main_excludes_a_share_insight_hard_risk_from_final_selection() -> None:
    """A 股洞察硬风险股票不应进入最终推送和 GM 买入信号。"""
    settings = Settings(
        db_path="data/test.db",
        start_date="2024-01-01",
        feishu_webhook_url="https://example.com/hook",
        final_selection_min_score=0,
        a_share_insight_enabled=True,
    )

    class _Strategy:
        webhook_key = "test"

        def __init__(self, symbols: list[str]) -> None:
            self.symbols = symbols

        def run(self) -> list[str]:
            return self.symbols

    class _Insight:
        def __init__(
            self,
            total_score: float,
            risk_flags: list[str] | None = None,
            hard_risk: bool = False,
        ) -> None:
            self.total_score = total_score
            self.risk_flags = risk_flags or []
            self.hard_risk = hard_risk

    class _WeekendDate:
        @classmethod
        def today(cls):
            class _Today:
                def weekday(self) -> int:
                    return 5

            return _Today()

    with (
        patch.object(main_module, "get_settings", return_value=settings),
        patch.object(main_module, "DataEngine") as engine_cls,
        patch.object(main_module, "date", _WeekendDate),
        patch.object(main_module, "MaVolumeStrategy", return_value=_Strategy(["000001", "000002"])),
        patch.object(main_module, "TurtleTradeStrategy", return_value=_Strategy([])),
        patch.object(main_module, "HighTightFlagStrategy", return_value=_Strategy([])),
        patch.object(main_module, "LimitUpShakeoutStrategy", return_value=_Strategy([])),
        patch.object(main_module, "UptrendLimitDownStrategy", return_value=_Strategy([])),
        patch.object(main_module, "RpsBreakoutStrategy", return_value=_Strategy([])),
        patch.object(main_module, "NewsConfirmStrategy", return_value=_Strategy([])),
        patch.object(main_module, "AShareInsightService", create=True) as insight_cls,
        patch.object(main_module, "FeishuNotifier") as feishu_cls,
        patch.object(main_module, "WechatIlinkNotifier") as wechat_cls,
        patch.object(main_module, "GmSignalExporter") as gm_exporter_cls,
        patch.object(main_module, "GmAccountSnapshotReader") as gm_account_reader_cls,
    ):
        engine_cls.return_value = object()
        gm_account_reader_cls.return_value.summary_text.return_value = ""
        insight_cls.return_value.refresh_symbols.return_value = {
            "000001": _Insight(30, risk_flags=["立案"], hard_risk=True),
            "000002": _Insight(0),
        }

        main_module.main()

    assert feishu_cls.return_value.send.call_args.kwargs["symbols"] == ["000002"]
    assert wechat_cls.return_value.send.call_args.kwargs["symbols"] == ["000002"]
    assert gm_exporter_cls.return_value.export.call_args.kwargs["symbols"] == ["000002"]
