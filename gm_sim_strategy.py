# coding=utf-8
"""掘金量化仿真交易策略脚本。"""

import json
import os
from datetime import date
from datetime import datetime
from datetime import time as datetime_time
from pathlib import Path
from typing import Any

try:
    from gm.api import *  # type: ignore # noqa: F403
except ImportError:
    MODE_LIVE = "live"
    MODE_BACKTEST = "backtest"
    ADJUST_PREV = "prev"
    OrderSide_Buy = 1
    OrderSide_Sell = 2
    OrderType_Market = 2
    PositionEffect_Open = 1
    PositionEffect_Close = 2

    def schedule(*args, **kwargs):  # type: ignore[no-untyped-def]
        return None

    def order_value(*args, **kwargs):  # type: ignore[no-untyped-def]
        return None

    def order_volume(*args, **kwargs):  # type: ignore[no-untyped-def]
        return None

    def get_cash(account_id=None):  # type: ignore[no-untyped-def]
        return {}

    def get_position(account_id=None):  # type: ignore[no-untyped-def]
        return []

    def get_unfinished_orders(account_id=None):  # type: ignore[no-untyped-def]
        return []

    def get_orders(account_id=None):  # type: ignore[no-untyped-def]
        return []

    def run(*args, **kwargs):  # type: ignore[no-untyped-def]
        return None


SIGNAL_PATH = os.getenv("GM_SIGNAL_PATH", "data/gm_trade_signal.json")
ACCOUNT_SNAPSHOT_PATH = os.getenv("GM_ACCOUNT_SNAPSHOT_PATH", "data/gm_account_snapshot.json")
GM_ACCOUNT_ID = os.getenv("GM_ACCOUNT_ID") or None
GM_TOKEN = os.getenv("GM_TOKEN", "")
GM_STRATEGY_ID = os.getenv("GM_STRATEGY_ID", "")
GM_TRADE_TIME = os.getenv("GM_TRADE_TIME", "14:50:00")
GM_MAX_POSITIONS = int(os.getenv("GM_MAX_POSITIONS", "5") or "5")


def load_today_signal(signal_path: str = SIGNAL_PATH, today: str | None = None) -> dict[str, Any] | None:
    signal_file = Path(signal_path)
    if not signal_file.exists():
        return None

    payload = json.loads(signal_file.read_text(encoding="utf-8"))
    today_str = today or date.today().strftime("%Y-%m-%d")
    if payload.get("date") != today_str:
        return None
    return payload


def build_orders(signal: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(signal.get("orders"), list):
        dry_run = bool(signal.get("dry_run", True))
        plans = []
        for item in signal["orders"]:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "")
            if not symbol:
                continue
            plans.append({
                "symbol": symbol,
                "side": str(item.get("side") or "buy").lower(),
                "volume": item.get("volume", 0),
                "dry_run": dry_run,
                "reason": str(item.get("reason") or ""),
            })
        return plans

    order_cash = float(signal.get("order_cash_per_stock", 10000))
    dry_run = bool(signal.get("dry_run", True))
    return [
        {"symbol": symbol, "value": order_cash, "dry_run": dry_run}
        for symbol in signal.get("symbols", [])
    ]


def parse_symbol_set(value: str | set[str] | list[str] | tuple[str, ...] | None) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {item.strip() for item in value.split(",") if item.strip()}
    return {str(item).strip() for item in value if str(item).strip()}


def is_within_trade_time(
    now: str | datetime | None = None,
    trade_start_time: str = "09:30:00",
    trade_end_time: str = "15:00:00",
) -> bool:
    current = now
    if current is None:
        current_dt = datetime.now()
    elif isinstance(current, str):
        current_dt = datetime.fromisoformat(current)
    else:
        current_dt = current
    start = datetime_time.fromisoformat(trade_start_time)
    end = datetime_time.fromisoformat(trade_end_time)
    return start <= current_dt.time() <= end


def apply_risk_controls(
    plans: list[dict[str, Any]],
    positions: list[dict[str, Any]],
    unfinished_orders: list[dict[str, Any]] | None = None,
    max_positions: int = 5,
    blacklist: str | set[str] | list[str] | tuple[str, ...] | None = None,
    daily_order_limit: int = 0,
    submitted_today: int = 0,
    max_order_value: float = 0,
    allow_non_trade_time: bool = False,
    now: str | datetime | None = None,
    trade_start_time: str = "09:30:00",
    trade_end_time: str = "15:00:00",
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    blocked: list[dict[str, str]] = []
    allowed: list[dict[str, Any]] = []
    blacklist_set = parse_symbol_set(blacklist)
    unfinished_symbols = {
        str(item.get("symbol") or "")
        for item in (unfinished_orders or [])
        if item.get("symbol")
    }
    available_by_symbol = {
        str(item.get("symbol") or ""): _to_int(item.get("available", item.get("volume")))
        for item in positions
        if item.get("symbol")
    }
    held_symbols = {
        str(item.get("symbol") or "")
        for item in positions
        if _to_int(item.get("volume")) > 0 and item.get("symbol")
    }
    current_position_count = len(held_symbols)
    trade_time_ok = allow_non_trade_time or is_within_trade_time(
        now=now,
        trade_start_time=trade_start_time,
        trade_end_time=trade_end_time,
    )

    for plan in plans:
        symbol = str(plan.get("symbol") or "")
        side = str(plan.get("side") or "buy").lower()
        if not trade_time_ok and not bool(plan.get("dry_run", True)):
            blocked.append({"symbol": symbol, "reason": "outside trade time"})
            continue
        if symbol in blacklist_set:
            blocked.append({"symbol": symbol, "reason": "blacklisted"})
            continue
        if symbol in unfinished_symbols:
            blocked.append({"symbol": symbol, "reason": "unfinished order exists"})
            continue
        if side == "sell":
            available_volume = available_by_symbol.get(symbol, 0)
            sell_volume = available_volume if plan.get("volume") == "all" else _to_int(plan.get("volume"))
            if available_volume <= 0 or sell_volume <= 0:
                blocked.append({"symbol": symbol, "reason": "no available position"})
                continue
            next_plan = dict(plan)
            next_plan["side"] = "sell"
            next_plan["volume"] = min(sell_volume, available_volume)
            next_plan.pop("reason", None)
            allowed.append(next_plan)
            continue
        if symbol in held_symbols:
            blocked.append({"symbol": symbol, "reason": "already held"})
            continue
        if daily_order_limit and submitted_today + len(allowed) >= daily_order_limit:
            blocked.append({"symbol": symbol, "reason": "daily order limit reached"})
            continue
        if max_positions and current_position_count + len(allowed) >= max_positions:
            blocked.append({"symbol": symbol, "reason": "max positions reached"})
            continue

        next_plan = dict(plan)
        if max_order_value and _to_float(next_plan.get("value")) > max_order_value:
            next_plan["value"] = float(max_order_value)
        allowed.append(next_plan)

    return allowed, blocked


def _to_float(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _to_int(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def build_account_snapshot(
    cash: dict[str, Any],
    positions: list[dict[str, Any]],
    unfinished_orders: list[dict[str, Any]] | None = None,
    orders: list[dict[str, Any]] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    return {
        "captured_at": now or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "cash": {
            "account_id": cash.get("account_id", ""),
            "available": _to_float(cash.get("available")),
            "nav": _to_float(cash.get("nav")),
            "balance": _to_float(cash.get("balance")),
            "day_pnl": _to_float(cash.get("pnl")),
            "floating_pnl": _to_float(cash.get("fpnl")),
            "market_value": _to_float(cash.get("market_value")),
            "frozen": _to_float(cash.get("frozen")),
            "order_frozen": _to_float(cash.get("order_frozen")),
            "updated_at": str(cash.get("updated_at", "")),
        },
        "positions": [
            {
                "symbol": item.get("symbol", ""),
                "volume": _to_int(item.get("volume")),
                "available": _to_int(item.get("available")),
                "vwap": _to_float(item.get("vwap")),
                "price": _to_float(item.get("price")),
                "market_value": _to_float(item.get("market_value")),
                "floating_pnl": _to_float(item.get("fpnl")),
            }
            for item in positions
        ],
        "unfinished_orders": [_normalize_order(item) for item in (unfinished_orders or [])],
        "orders": [_normalize_order(item) for item in (orders or [])],
    }


def _normalize_order(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": item.get("symbol", ""),
        "side": item.get("side", ""),
        "volume": _to_int(item.get("volume")),
        "filled_volume": _to_int(item.get("filled_volume", item.get("filled_vwap", 0))),
        "status": str(item.get("status", "")),
        "created_at": str(item.get("created_at", "")),
    }


def save_account_snapshot(snapshot: dict[str, Any], snapshot_path: str = ACCOUNT_SNAPSHOT_PATH) -> None:
    path = Path(snapshot_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")


def export_account_snapshot(account_id: str | None = GM_ACCOUNT_ID) -> None:
    try:
        cash = get_cash(account_id=account_id)  # noqa: F405
        positions = get_position(account_id=account_id)  # noqa: F405
        unfinished_orders = get_unfinished_orders(account_id=account_id)  # noqa: F405
        orders = get_orders(account_id=account_id)  # noqa: F405
        save_account_snapshot(build_account_snapshot(
            cash or {},
            positions or [],
            unfinished_orders=unfinished_orders or [],
            orders=orders or [],
        ))
    except Exception as exc:
        print(f"导出掘金账户快照失败：{exc}")


def init(context) -> None:  # type: ignore[no-untyped-def]
    schedule(schedule_func=algo, date_rule="1d", time_rule=GM_TRADE_TIME)  # noqa: F405


def algo(context) -> None:  # type: ignore[no-untyped-def]
    export_account_snapshot()
    signal = load_today_signal()
    if not signal:
        print("未发现当日掘金交易信号，跳过")
        return

    positions = get_position(account_id=GM_ACCOUNT_ID) or []  # noqa: F405
    unfinished_orders = get_unfinished_orders(account_id=GM_ACCOUNT_ID) or []  # noqa: F405
    plans, blocked = apply_risk_controls(
        build_orders(signal),
        positions=positions,
        unfinished_orders=unfinished_orders,
        max_positions=GM_MAX_POSITIONS,
        allow_non_trade_time=True,
    )
    for item in blocked:
        print(f"交易计划已拦截：{item['symbol']} {item['reason']}")

    for plan in plans:
        side = str(plan.get("side") or "buy").lower()
        if plan["dry_run"]:
            if "volume" in plan:
                print(f"DRY_RUN {side} 计划：{plan['symbol']} {plan['volume']} 股")
            else:
                print(f"DRY_RUN 买入计划：{plan['symbol']} 金额 {plan['value']}")
            continue
        if "volume" in plan:
            order_volume(  # noqa: F405
                symbol=plan["symbol"],
                volume=plan["volume"],
                side=OrderSide_Sell if side == "sell" else OrderSide_Buy,  # noqa: F405
                order_type=OrderType_Market,  # noqa: F405
                position_effect=PositionEffect_Close if side == "sell" else PositionEffect_Open,  # noqa: F405
            )
            continue
        order_value(  # noqa: F405
            symbol=plan["symbol"],
            value=plan["value"],
            side=OrderSide_Buy,  # noqa: F405
            order_type=OrderType_Market,  # noqa: F405
            position_effect=PositionEffect_Open,  # noqa: F405
        )
    export_account_snapshot()


if __name__ == "__main__":
    run(  # noqa: F405
        strategy_id=GM_STRATEGY_ID,
        filename="gm_sim_strategy.py",
        mode=MODE_LIVE,  # noqa: F405
        token=GM_TOKEN,
        backtest_start_time="2020-11-01 08:00:00",
        backtest_end_time="2020-11-10 16:00:00",
        backtest_adjust=ADJUST_PREV,  # noqa: F405
        backtest_initial_cash=10000000,
        backtest_commission_ratio=0.0001,
        backtest_slippage_ratio=0.0001,
    )
