"""主程序入口属性测试。"""

import sys
from unittest.mock import patch

import pytest
from hypothesis import given, settings as h_settings
from hypothesis import strategies as st

# 预先导入 main 模块，避免在 @given 循环中重复导入
import main as main_module
from sequoia_x.core.config import Settings


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

    assert feishu_cls.return_value.send.called
    assert wechat_cls.return_value.send.called
    assert gm_exporter_cls.return_value.export.called
    assert feishu_cls.return_value.send.call_args.kwargs["account_summary"] == "账户余额：88000.50"
    assert wechat_cls.return_value.send.call_args.kwargs["account_summary"] == "账户余额：88000.50"


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


def test_main_exports_news_scores_and_reverse_symbols_for_gm() -> None:
    """主流程应把新闻高可信分数和风险反向信号传给 GM 导出器。"""
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
    assert gm_exporter_cls.return_value.export.call_args.kwargs["reverse_symbols"] == ["600900"]


def test_main_exports_reverse_signal_even_without_selected_symbols() -> None:
    """新闻策略只有风险反向信号时也应导出 GM 卖出信号。"""
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

    gm_exporter_cls.return_value.export.assert_called_once()
    assert gm_exporter_cls.return_value.export.call_args.kwargs["symbols"] == []
    assert gm_exporter_cls.return_value.export.call_args.kwargs["reverse_symbols"] == ["600900"]
