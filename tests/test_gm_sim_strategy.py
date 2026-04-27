"""掘金仿真交易脚本测试。"""

import json
from pathlib import Path

import gm_sim_strategy


def test_load_today_signal_returns_none_for_old_signal(tmp_path: Path) -> None:
    """仿真脚本不应执行非当日交易信号。"""
    signal_path = tmp_path / "gm_trade_signal.json"
    signal_path.write_text(
        json.dumps({"date": "2026-04-24", "symbols": ["SZSE.300054"]}),
        encoding="utf-8",
    )

    assert gm_sim_strategy.load_today_signal(str(signal_path), today="2026-04-25") is None


def test_build_orders_respects_dry_run_and_cash_per_stock() -> None:
    """仿真脚本应根据交易信号生成固定金额买入计划。"""
    signal = {
        "date": "2026-04-25",
        "symbols": ["SZSE.300054", "SZSE.000007"],
        "order_cash_per_stock": 12000,
        "dry_run": True,
    }

    orders = gm_sim_strategy.build_orders(signal)

    assert orders == [
        {"symbol": "SZSE.300054", "value": 12000.0, "dry_run": True},
        {"symbol": "SZSE.000007", "value": 12000.0, "dry_run": True},
    ]


def test_build_orders_uses_explicit_volume_buy_and_sell_orders() -> None:
    """新信号结构应按股数生成买入/卖出计划。"""
    signal = {
        "date": "2026-04-25",
        "dry_run": False,
        "orders": [
            {"symbol": "SZSE.300054", "side": "buy", "volume": 150, "reason": "news_high_confidence"},
            {"symbol": "SHSE.600900", "side": "sell", "volume": "all", "reason": "reverse_signal"},
        ],
    }

    orders = gm_sim_strategy.build_orders(signal)

    assert orders == [
        {
            "symbol": "SZSE.300054",
            "side": "buy",
            "volume": 150,
            "dry_run": False,
            "reason": "news_high_confidence",
        },
        {
            "symbol": "SHSE.600900",
            "side": "sell",
            "volume": "all",
            "dry_run": False,
            "reason": "reverse_signal",
        },
    ]


def test_build_account_snapshot_contains_cash_and_positions() -> None:
    """账户快照应包含余额、当日盈利、总资产、总市值和持仓明细。"""
    cash = {
        "account_id": "acct-1",
        "available": 88000.5,
        "nav": 120000.0,
        "balance": 119000.0,
        "pnl": 2000.0,
        "fpnl": 1500.0,
        "market_value": 32000.0,
        "order_frozen": 17518.0,
        "updated_at": "2026-04-25 14:50:00",
    }
    positions = [
        {
            "symbol": "SZSE.300054",
            "volume": 100,
            "available": 100,
            "vwap": 20.0,
            "price": 21.0,
            "market_value": 2100.0,
            "fpnl": 100.0,
        }
    ]

    snapshot = gm_sim_strategy.build_account_snapshot(
        cash=cash,
        positions=positions,
        unfinished_orders=[
            {
                "symbol": "SZSE.300054",
                "side": "buy",
                "volume": 100,
                "filled_volume": 0,
                "status": "pending",
            }
        ],
        orders=[
            {
                "symbol": "SZSE.300054",
                "side": "buy",
                "volume": 100,
                "filled_volume": 0,
                "status": "pending",
            }
        ],
        now="2026-04-25 14:51:00",
    )

    assert snapshot["captured_at"] == "2026-04-25 14:51:00"
    assert snapshot["cash"]["available"] == 88000.5
    assert snapshot["cash"]["nav"] == 120000.0
    assert snapshot["cash"]["day_pnl"] == 2000.0
    assert snapshot["cash"]["floating_pnl"] == 1500.0
    assert snapshot["cash"]["market_value"] == 32000.0
    assert snapshot["cash"]["order_frozen"] == 17518.0
    assert snapshot["positions"] == [
        {
            "symbol": "SZSE.300054",
            "volume": 100,
            "available": 100,
            "vwap": 20.0,
            "price": 21.0,
            "market_value": 2100.0,
            "floating_pnl": 100.0,
        }
    ]
    assert snapshot["unfinished_orders"] == [
        {
            "symbol": "SZSE.300054",
            "side": "buy",
            "volume": 100,
            "filled_volume": 0,
            "status": "pending",
            "created_at": "",
        }
    ]
    assert snapshot["orders"] == [
        {
            "symbol": "SZSE.300054",
            "side": "buy",
            "volume": 100,
            "filled_volume": 0,
            "status": "pending",
            "created_at": "",
        }
    ]


def test_save_account_snapshot_writes_json(tmp_path: Path) -> None:
    """账户快照应保存为 superQ 可读取的 JSON 文件。"""
    snapshot_path = tmp_path / "gm_account_snapshot.json"
    snapshot = {"cash": {"available": 1000.0}, "positions": []}

    gm_sim_strategy.save_account_snapshot(snapshot, str(snapshot_path))

    assert json.loads(snapshot_path.read_text(encoding="utf-8")) == snapshot


def test_filter_orders_applies_blacklist_position_limit_and_existing_positions() -> None:
    """风控应拦截黑名单、超过最大持仓和已持仓重复买入。"""
    plans = [
        {"symbol": "SZSE.000001", "value": 10000.0, "dry_run": False},
        {"symbol": "SZSE.000002", "value": 10000.0, "dry_run": False},
        {"symbol": "SZSE.000003", "value": 10000.0, "dry_run": False},
    ]
    positions = [{"symbol": "SZSE.000001", "volume": 100}]

    allowed, blocked = gm_sim_strategy.apply_risk_controls(
        plans,
        positions=positions,
        max_positions=2,
        blacklist={"SZSE.000002"},
        now="2026-04-27 10:00:00",
        trade_start_time="09:30:00",
        trade_end_time="15:00:00",
    )

    assert allowed == [{"symbol": "SZSE.000003", "value": 10000.0, "dry_run": False}]
    assert blocked == [
        {"symbol": "SZSE.000001", "reason": "already held"},
        {"symbol": "SZSE.000002", "reason": "blacklisted"},
    ]


def test_filter_orders_allows_sell_for_held_stock_and_blocks_sell_without_available_volume() -> None:
    """反向卖出只能卖已有可用持仓，且不受重复持仓买入拦截影响。"""
    plans = [
        {"symbol": "SZSE.300054", "side": "sell", "volume": "all", "dry_run": False},
        {"symbol": "SHSE.600900", "side": "sell", "volume": "all", "dry_run": False},
    ]
    positions = [
        {"symbol": "SZSE.300054", "volume": 200, "available": 100},
        {"symbol": "SHSE.600900", "volume": 100, "available": 0},
    ]

    allowed, blocked = gm_sim_strategy.apply_risk_controls(
        plans,
        positions=positions,
        now="2026-04-27 10:00:00",
        trade_start_time="09:30:00",
        trade_end_time="15:00:00",
    )

    assert allowed == [{"symbol": "SZSE.300054", "side": "sell", "volume": 100, "dry_run": False}]
    assert blocked == [{"symbol": "SHSE.600900", "reason": "no available position"}]


def test_filter_orders_blocks_symbols_with_unfinished_orders() -> None:
    """存在未结委托的股票不应重复交易。"""
    plans = [
        {"symbol": "SZSE.300054", "side": "buy", "volume": 100, "dry_run": False},
        {"symbol": "SHSE.600900", "side": "buy", "volume": 100, "dry_run": False},
    ]

    allowed, blocked = gm_sim_strategy.apply_risk_controls(
        plans,
        positions=[],
        unfinished_orders=[{"symbol": "SZSE.300054", "volume": 100, "status": "pending"}],
        now="2026-04-27 10:00:00",
        trade_start_time="09:30:00",
        trade_end_time="15:00:00",
    )

    assert allowed == [{"symbol": "SHSE.600900", "side": "buy", "volume": 100, "dry_run": False}]
    assert blocked == [{"symbol": "SZSE.300054", "reason": "unfinished order exists"}]


def test_filter_orders_caps_order_value_and_blocks_outside_trade_time() -> None:
    """风控应限制单票金额，并默认拦截非交易时间真实下单。"""
    plans = [{"symbol": "SZSE.300054", "value": 20000.0, "dry_run": False}]

    allowed, blocked = gm_sim_strategy.apply_risk_controls(
        plans,
        positions=[],
        max_order_value=12000,
        now="2026-04-27 20:00:00",
        trade_start_time="09:30:00",
        trade_end_time="15:00:00",
    )

    assert allowed == []
    assert blocked == [{"symbol": "SZSE.300054", "reason": "outside trade time"}]

    allowed, blocked = gm_sim_strategy.apply_risk_controls(
        plans,
        positions=[],
        max_order_value=12000,
        allow_non_trade_time=True,
        now="2026-04-27 20:00:00",
        trade_start_time="09:30:00",
        trade_end_time="15:00:00",
    )

    assert allowed == [{"symbol": "SZSE.300054", "value": 12000.0, "dry_run": False}]
    assert blocked == []


def test_filter_orders_applies_daily_order_limit() -> None:
    """风控应限制单次/每日最多提交的下单数量。"""
    plans = [
        {"symbol": "SZSE.000001", "value": 10000.0, "dry_run": False},
        {"symbol": "SZSE.000002", "value": 10000.0, "dry_run": False},
    ]

    allowed, blocked = gm_sim_strategy.apply_risk_controls(
        plans,
        positions=[],
        daily_order_limit=1,
        now="2026-04-27 10:00:00",
        trade_start_time="09:30:00",
        trade_end_time="15:00:00",
    )

    assert allowed == [{"symbol": "SZSE.000001", "value": 10000.0, "dry_run": False}]
    assert blocked == [{"symbol": "SZSE.000002", "reason": "daily order limit reached"}]
