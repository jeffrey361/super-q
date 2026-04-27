"""掘金账户快照读取测试。"""

import json
import sqlite3
from pathlib import Path

from sequoia_x.trade.gm_account import GmAccountSnapshotReader


def test_reader_loads_account_snapshot_summary(tmp_path: Path) -> None:
    """superQ 应能读取掘金账户余额、盈利、总资产和持仓摘要。"""
    snapshot_path = tmp_path / "gm_account_snapshot.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "captured_at": "2026-04-25 14:51:00",
                "cash": {
                    "available": 88000.5,
                    "nav": 120000.0,
                    "day_pnl": 2000.0,
                    "floating_pnl": 1500.0,
                    "market_value": 32000.0,
                    "order_frozen": 17518.0,
                },
                "positions": [
                    {
                        "symbol": "SZSE.300054",
                        "volume": 100,
                        "available": 100,
                        "market_value": 2100.0,
                        "floating_pnl": 100.0,
                    }
                ],
                "unfinished_orders": [
                    {"symbol": "SZSE.300054", "side": "buy", "volume": 100, "status": "pending"}
                ],
                "orders": [
                    {"symbol": "SZSE.000001", "side": "buy", "volume": 100, "status": "filled"}
                ],
            }
        ),
        encoding="utf-8",
    )

    summary = GmAccountSnapshotReader(str(snapshot_path)).summary_text()

    assert "账户余额：88000.50" in summary
    assert "当日盈利：2000.00" in summary
    assert "总资产：120000.00" in summary
    assert "委托冻结：17518.00" in summary
    assert "代码 名称 现价 持仓 市值(CNY)" in summary
    assert "300054.SZ 未知 0.00 CNY 100 2,100" in summary
    assert "未结委托：" in summary
    assert "300054.SZ 未知 buy 100 pending" in summary
    assert "委托流水：" in summary
    assert "000001.SZ 未知 buy 100 filled" in summary


def test_reader_adds_position_names_and_formats_codes(tmp_path: Path) -> None:
    """持仓摘要应带股票名称，并按常见后缀格式展示代码。"""
    snapshot_path = tmp_path / "gm_account_snapshot.json"
    db_path = tmp_path / "sequoia.db"
    snapshot_path.write_text(
        json.dumps(
            {
                "captured_at": "2026-04-25 14:51:00",
                "cash": {"available": 88000.5, "nav": 120000.0},
                "positions": [
                    {
                        "symbol": "SZSE.300054",
                        "volume": 100,
                        "available": 100,
                        "market_value": 2100.0,
                        "floating_pnl": 100.0,
                    },
                    {
                        "symbol": "SHSE.600900",
                        "volume": 200,
                        "available": 200,
                        "market_value": 5000.0,
                        "floating_pnl": -50.0,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE stock_names (symbol TEXT PRIMARY KEY, name TEXT NOT NULL, keywords TEXT, updated_at TEXT)"
        )
        conn.executemany(
            "INSERT INTO stock_names (symbol, name, keywords, updated_at) VALUES (?, ?, ?, ?)",
            [
                ("300054", "鼎龙股份", "鼎龙股份,300054", "2026-04-25"),
                ("600900", "长江电力", "长江电力,600900", "2026-04-25"),
            ],
        )

    summary = GmAccountSnapshotReader(str(snapshot_path), db_path=str(db_path)).summary_text()

    assert "代码 名称 现价 持仓 市值(CNY)" in summary
    assert "300054.SZ 鼎龙股份 0.00 CNY 100 2,100" in summary
    assert "600900.SH 长江电力 0.00 CNY 200 5,000" in summary


def test_reader_formats_hk_position_table_row(tmp_path: Path) -> None:
    """港股持仓应保留 HK 后缀并展示 HKD 现价。"""
    snapshot_path = tmp_path / "gm_account_snapshot.json"
    db_path = tmp_path / "sequoia.db"
    snapshot_path.write_text(
        json.dumps(
            {
                "captured_at": "2026-04-25 14:51:00",
                "cash": {"available": 88000.5, "nav": 120000.0},
                "positions": [
                    {
                        "symbol": "09988.HK",
                        "volume": 100,
                        "available": 100,
                        "price": 131.8,
                        "market_value": 12126,
                        "floating_pnl": 100.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE stock_names (symbol TEXT PRIMARY KEY, name TEXT NOT NULL, keywords TEXT, updated_at TEXT)"
        )
        conn.execute(
            "INSERT INTO stock_names (symbol, name, keywords, updated_at) VALUES (?, ?, ?, ?)",
            ("09988.HK", "阿里巴巴", "阿里巴巴,09988.HK", "2026-04-25"),
        )

    summary = GmAccountSnapshotReader(str(snapshot_path), db_path=str(db_path)).summary_text()

    assert "09988.HK 阿里巴巴 131.80 HKD 100 12,126" in summary


def test_reader_returns_empty_summary_when_snapshot_missing(tmp_path: Path) -> None:
    """快照文件不存在时不应影响主流程。"""
    assert GmAccountSnapshotReader(str(tmp_path / "missing.json")).summary_text() == ""
