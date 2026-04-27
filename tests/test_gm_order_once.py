"""掘金一键下单脚本测试。"""

import json
from pathlib import Path

import gm_order_once


def test_run_once_loads_signal_orders_and_saves_snapshot(tmp_path: Path) -> None:
    """一键脚本应读取当日信号、按风控下单并保存账户快照。"""
    signal_path = tmp_path / "gm_trade_signal.json"
    snapshot_path = tmp_path / "gm_account_snapshot.json"
    signal_path.write_text(
        json.dumps(
            {
                "date": "2026-04-25",
                "symbols": ["SZSE.300054"],
                "order_cash_per_stock": 10000,
                "dry_run": False,
            }
        ),
        encoding="utf-8",
    )
    calls = []

    result = gm_order_once.run_once(
        signal_path=str(signal_path),
        snapshot_path=str(snapshot_path),
        order_log_path=str(tmp_path / "gm_order_log.json"),
        account_id="acct-1",
        today="2026-04-25",
        now="2026-04-25 10:00:00",
        allow_non_trade_time=False,
        order_func=lambda **kwargs: calls.append(kwargs) or [{"status": 10}],
        cash_func=lambda account_id=None: {
            "account_id": account_id,
            "available": 90000,
            "nav": 100000,
            "order_frozen": 10000,
        },
        position_func=lambda account_id=None: [],
    )

    assert result["submitted"] == [{"symbol": "SZSE.300054", "value": 10000.0}]
    assert result["blocked"] == []
    assert calls[0]["symbol"] == "SZSE.300054"
    assert calls[0]["value"] == 10000.0
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert snapshot["cash"]["order_frozen"] == 10000.0
    order_log = json.loads((tmp_path / "gm_order_log.json").read_text(encoding="utf-8"))
    assert order_log[0]["symbol"] == "SZSE.300054"


def test_run_once_submits_volume_buy_and_sell_orders(tmp_path: Path) -> None:
    """一键脚本应按新订单结构用股数买入，并对反向信号卖出可用持仓。"""
    signal_path = tmp_path / "gm_trade_signal.json"
    snapshot_path = tmp_path / "gm_account_snapshot.json"
    signal_path.write_text(
        json.dumps(
            {
                "date": "2026-04-25",
                "dry_run": False,
                "orders": [
                    {"symbol": "SZSE.300054", "side": "buy", "volume": 150},
                    {"symbol": "SHSE.600900", "side": "sell", "volume": "all"},
                ],
            }
        ),
        encoding="utf-8",
    )
    calls = []

    result = gm_order_once.run_once(
        signal_path=str(signal_path),
        snapshot_path=str(snapshot_path),
        order_log_path=str(tmp_path / "gm_order_log.json"),
        account_id="acct-1",
        today="2026-04-25",
        now="2026-04-25 10:00:00",
        order_volume_func=lambda **kwargs: calls.append(kwargs) or [{"status": 10}],
        cash_func=lambda account_id=None: {"account_id": account_id, "available": 90000, "nav": 100000},
        position_func=lambda account_id=None: [
            {"symbol": "SHSE.600900", "volume": 200, "available": 100}
        ],
    )

    assert result["submitted"] == [
        {"symbol": "SZSE.300054", "side": "buy", "volume": 150},
        {"symbol": "SHSE.600900", "side": "sell", "volume": 100},
    ]
    assert calls[0]["symbol"] == "SZSE.300054"
    assert calls[0]["volume"] == 150
    assert calls[1]["symbol"] == "SHSE.600900"
    assert calls[1]["volume"] == 100


def test_run_once_blocks_symbol_with_unfinished_order(tmp_path: Path) -> None:
    """已有未结委托时，一键脚本不应重复交易同一股票。"""
    signal_path = tmp_path / "gm_trade_signal.json"
    snapshot_path = tmp_path / "gm_account_snapshot.json"
    signal_path.write_text(
        json.dumps(
            {
                "date": "2026-04-25",
                "dry_run": False,
                "orders": [{"symbol": "SZSE.300054", "side": "buy", "volume": 100}],
            }
        ),
        encoding="utf-8",
    )
    calls = []

    result = gm_order_once.run_once(
        signal_path=str(signal_path),
        snapshot_path=str(snapshot_path),
        order_log_path=str(tmp_path / "gm_order_log.json"),
        account_id="acct-1",
        today="2026-04-25",
        now="2026-04-25 10:00:00",
        order_volume_func=lambda **kwargs: calls.append(kwargs),
        cash_func=lambda account_id=None: {"account_id": account_id, "available": 90000, "nav": 100000},
        position_func=lambda account_id=None: [],
        unfinished_orders_func=lambda account_id=None: [{"symbol": "SZSE.300054", "volume": 100}],
        orders_func=lambda account_id=None: [],
        sleep_func=lambda seconds: None,
    )

    assert result["submitted"] == []
    assert result["blocked"] == [{"symbol": "SZSE.300054", "reason": "unfinished order exists"}]
    assert calls == []


def test_run_once_supports_gm_order_query_functions_without_account_id(tmp_path: Path) -> None:
    """GM SDK 的 get_unfinished_orders/get_orders 不接收 account_id 参数。"""
    signal_path = tmp_path / "gm_trade_signal.json"
    snapshot_path = tmp_path / "gm_account_snapshot.json"
    signal_path.write_text(
        json.dumps(
            {
                "date": "2026-04-25",
                "dry_run": False,
                "orders": [{"symbol": "SZSE.300054", "side": "buy", "volume": 100}],
            }
        ),
        encoding="utf-8",
    )

    def unfinished_orders_func():
        return []

    def orders_func():
        return []
    positions = [[], [{"symbol": "SZSE.300054", "volume": 100}]]

    result = gm_order_once.run_once(
        signal_path=str(signal_path),
        snapshot_path=str(snapshot_path),
        order_log_path=str(tmp_path / "gm_order_log.json"),
        account_id="acct-1",
        today="2026-04-25",
        now="2026-04-25 10:00:00",
        order_volume_func=lambda **kwargs: [{"status": 10}],
        cash_func=lambda account_id=None: {"account_id": account_id, "available": 90000, "nav": 100000},
        position_func=lambda account_id=None: positions.pop(0) if positions else [],
        unfinished_orders_func=unfinished_orders_func,
        orders_func=orders_func,
        snapshot_poll_seconds=0,
        sleep_func=lambda seconds: None,
    )

    assert result["submitted"] == [{"symbol": "SZSE.300054", "side": "buy", "volume": 100}]


def test_run_once_polls_account_snapshot_after_submitting_orders(tmp_path: Path) -> None:
    """下单后 GM 账户查询可能延迟刷新，应轮询到持仓变化后再写最终快照。"""
    signal_path = tmp_path / "gm_trade_signal.json"
    snapshot_path = tmp_path / "gm_account_snapshot.json"
    signal_path.write_text(
        json.dumps(
            {
                "date": "2026-04-25",
                "dry_run": False,
                "orders": [{"symbol": "SZSE.300054", "side": "buy", "volume": 100}],
            }
        ),
        encoding="utf-8",
    )
    position_results = [
        [],
        [],
        [{"symbol": "SZSE.300054", "volume": 100, "available": 100}],
    ]

    result = gm_order_once.run_once(
        signal_path=str(signal_path),
        snapshot_path=str(snapshot_path),
        order_log_path=str(tmp_path / "gm_order_log.json"),
        account_id="acct-1",
        today="2026-04-25",
        now="2026-04-25 10:00:00",
        order_volume_func=lambda **kwargs: [{"status": 10}],
        cash_func=lambda account_id=None: {"account_id": account_id, "available": 90000, "nav": 100000},
        position_func=lambda account_id=None: position_results.pop(0) if position_results else [],
        unfinished_orders_func=lambda account_id=None: [],
        orders_func=lambda account_id=None: [],
        snapshot_poll_seconds=2,
        final_snapshot_wait_seconds=60,
        sleep_func=lambda seconds: None,
    )

    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert result["submitted"] == [{"symbol": "SZSE.300054", "side": "buy", "volume": 100}]
    assert snapshot["positions"] == [
        {
            "symbol": "SZSE.300054",
            "volume": 100,
            "available": 100,
            "vwap": 0.0,
            "price": 0.0,
            "market_value": 0.0,
            "floating_pnl": 0.0,
        }
    ]


def test_run_once_waits_then_saves_orders_in_final_snapshot(tmp_path: Path) -> None:
    """提交交易后应等待配置秒数，再把持仓、未结委托和委托流水写入最终快照。"""
    signal_path = tmp_path / "gm_trade_signal.json"
    snapshot_path = tmp_path / "gm_account_snapshot.json"
    signal_path.write_text(
        json.dumps(
            {
                "date": "2026-04-25",
                "dry_run": False,
                "orders": [{"symbol": "SZSE.300054", "side": "buy", "volume": 100}],
            }
        ),
        encoding="utf-8",
    )
    slept = []
    unfinished_results = [
        [],
        [{"symbol": "SZSE.300054", "volume": 100, "status": "pending"}],
    ]
    position_results = [
        [],
        [{"symbol": "SZSE.300054", "volume": 100}],
    ]

    gm_order_once.run_once(
        signal_path=str(signal_path),
        snapshot_path=str(snapshot_path),
        order_log_path=str(tmp_path / "gm_order_log.json"),
        account_id="acct-1",
        today="2026-04-25",
        now="2026-04-25 10:00:00",
        order_volume_func=lambda **kwargs: [{"status": 10}],
        cash_func=lambda account_id=None: {"account_id": account_id, "available": 90000, "nav": 100000},
        position_func=lambda account_id=None: (
            position_results.pop(0)
            if position_results
            else [{"symbol": "SZSE.300054", "volume": 100}]
        ),
        unfinished_orders_func=lambda account_id=None: (
            unfinished_results.pop(0)
            if unfinished_results
            else [{"symbol": "SZSE.300054", "volume": 100, "status": "pending"}]
        ),
        orders_func=lambda account_id=None: [{"symbol": "SZSE.300054", "volume": 100, "status": "filled"}],
        snapshot_poll_seconds=0,
        final_snapshot_wait_seconds=60,
        sleep_func=lambda seconds: slept.append(seconds),
    )

    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert 60 in slept
    assert snapshot["unfinished_orders"][0]["symbol"] == "SZSE.300054"
    assert snapshot["orders"][0]["symbol"] == "SZSE.300054"


def test_run_once_does_not_submit_blocked_orders(tmp_path: Path) -> None:
    """一键脚本遇到黑名单股票时不应提交下单。"""
    signal_path = tmp_path / "gm_trade_signal.json"
    snapshot_path = tmp_path / "gm_account_snapshot.json"
    signal_path.write_text(
        json.dumps(
            {
                "date": "2026-04-25",
                "symbols": ["SZSE.300054"],
                "order_cash_per_stock": 10000,
                "dry_run": False,
            }
        ),
        encoding="utf-8",
    )
    calls = []

    result = gm_order_once.run_once(
        signal_path=str(signal_path),
        snapshot_path=str(snapshot_path),
        today="2026-04-25",
        now="2026-04-25 10:00:00",
        blacklist={"SZSE.300054"},
        order_func=lambda **kwargs: calls.append(kwargs),
        cash_func=lambda account_id=None: {"available": 100000, "nav": 100000},
        position_func=lambda account_id=None: [],
    )

    assert result["submitted"] == []
    assert result["blocked"] == [{"symbol": "SZSE.300054", "reason": "blacklisted"}]
    assert calls == []


def test_run_once_counts_existing_order_log_for_daily_limit(tmp_path: Path) -> None:
    """一键脚本应根据本地下单日志执行每日下单次数限制。"""
    signal_path = tmp_path / "gm_trade_signal.json"
    snapshot_path = tmp_path / "gm_account_snapshot.json"
    order_log_path = tmp_path / "gm_order_log.json"
    signal_path.write_text(
        json.dumps(
            {
                "date": "2026-04-25",
                "symbols": ["SZSE.300054"],
                "order_cash_per_stock": 10000,
                "dry_run": False,
            }
        ),
        encoding="utf-8",
    )
    order_log_path.write_text(
        json.dumps([{"date": "2026-04-25", "symbol": "SZSE.000001", "value": 10000}]),
        encoding="utf-8",
    )
    calls = []

    result = gm_order_once.run_once(
        signal_path=str(signal_path),
        snapshot_path=str(snapshot_path),
        order_log_path=str(order_log_path),
        today="2026-04-25",
        now="2026-04-25 10:00:00",
        daily_order_limit=1,
        order_func=lambda **kwargs: calls.append(kwargs),
        cash_func=lambda account_id=None: {"available": 100000, "nav": 100000},
        position_func=lambda account_id=None: [],
    )

    assert result["submitted"] == []
    assert result["blocked"] == [{"symbol": "SZSE.300054", "reason": "daily order limit reached"}]
    assert calls == []
