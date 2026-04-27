"""掘金量化仿真交易接入测试。"""

import json
from pathlib import Path

import pytest

from sequoia_x.core.config import Settings
from sequoia_x.trade.gm_signal import GmSignalExporter
from sequoia_x.trade.symbols import to_gm_symbol


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        ("000001", "SZSE.000001"),
        ("300054", "SZSE.300054"),
        ("600519", "SHSE.600519"),
        ("688001", "SHSE.688001"),
        ("430047", "BJSE.430047"),
        ("830799", "BJSE.830799"),
    ],
)
def test_to_gm_symbol_converts_a_share_codes(symbol: str, expected: str) -> None:
    """A 股 6 位代码应转换为掘金交易所代码。"""
    assert to_gm_symbol(symbol) == expected


def test_to_gm_symbol_rejects_invalid_code() -> None:
    """非法股票代码应显式报错，避免下单到错误标的。"""
    with pytest.raises(ValueError):
        to_gm_symbol("ABC001")


def test_gm_signal_exporter_writes_today_signal_when_enabled(tmp_path: Path) -> None:
    """启用掘金时，应把选股结果导出为仿真交易脚本可读的 JSON 信号。"""
    signal_path = tmp_path / "gm_trade_signal.json"
    settings = Settings(
        db_path=str(tmp_path / "test.db"),
        start_date="2024-01-01",
        feishu_webhook_url="https://example.com/hook",
        gm_enabled=True,
        gm_signal_path=str(signal_path),
        gm_order_cash_per_stock=12000,
        gm_max_positions=2,
        gm_dry_run=True,
        gm_buy_volume=100,
        gm_news_high_confidence_buy_volume=150,
        news_high_confidence_score=80,
    )

    exporter = GmSignalExporter(settings=settings, today=lambda: "2026-04-25")
    exported = exporter.export(
        ["300054", "000007", "600519"],
        "NewsConfirmStrategy",
        news_scores=[
            {"symbol": "300054", "final_score": 90},
            {"symbol": "000007", "final_score": 50},
        ],
        reverse_symbols=["600519"],
    )

    assert exported is True
    payload = json.loads(signal_path.read_text(encoding="utf-8"))
    assert payload == {
        "date": "2026-04-25",
        "strategy": "NewsConfirmStrategy",
        "symbols": ["SZSE.300054", "SZSE.000007"],
        "order_cash_per_stock": 12000,
        "orders": [
            {"symbol": "SZSE.300054", "side": "buy", "volume": 150, "reason": "news_high_confidence"},
            {"symbol": "SZSE.000007", "side": "buy", "volume": 100, "reason": "strategy"},
            {"symbol": "SHSE.600519", "side": "sell", "volume": "all", "reason": "reverse_signal"},
        ],
        "max_positions": 2,
        "dry_run": True,
    }


def test_gm_signal_exporter_noops_when_disabled(tmp_path: Path) -> None:
    """未启用掘金时，不应生成交易信号文件。"""
    signal_path = tmp_path / "gm_trade_signal.json"
    settings = Settings(
        db_path=str(tmp_path / "test.db"),
        start_date="2024-01-01",
        feishu_webhook_url="https://example.com/hook",
        gm_enabled=False,
        gm_signal_path=str(signal_path),
    )

    exported = GmSignalExporter(settings=settings).export(["300054"], "TestStrategy")

    assert exported is False
    assert not signal_path.exists()
