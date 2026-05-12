# coding=utf-8
"""读取掘金信号并执行一次仿真下单。"""

import json
import os
import time
from datetime import date
from pathlib import Path
from typing import Any, Callable
import inspect

from dotenv import load_dotenv

import gm_sim_strategy
from super_q.core.config import Settings

try:
    from gm.api import (  # type: ignore
        OrderSide_Buy,
        OrderSide_Sell,
        OrderType_Market,
        PositionEffect_Close,
        PositionEffect_Open,
        get_cash,
        get_orders,
        get_position,
        get_unfinished_orders,
        order_volume,
        order_value,
        set_serv_addr,
        set_token,
    )
except ImportError:
    OrderSide_Buy = 1
    OrderSide_Sell = 2
    OrderType_Market = 2
    PositionEffect_Close = 2
    PositionEffect_Open = 1

    def set_token(token: str) -> None:
        return None

    def set_serv_addr(addr: str) -> None:
        return None

    def order_value(**kwargs: Any) -> list[dict[str, Any]]:
        return []

    def order_volume(**kwargs: Any) -> list[dict[str, Any]]:
        return []

    def get_cash(account_id: str | None = None) -> dict[str, Any]:
        return {}

    def get_position(account_id: str | None = None) -> list[dict[str, Any]]:
        return []

    def get_unfinished_orders(account_id: str | None = None) -> list[dict[str, Any]]:
        return []

    def get_orders(account_id: str | None = None) -> list[dict[str, Any]]:
        return []


OrderFunc = Callable[..., Any]
CashFunc = Callable[..., dict[str, Any]]
PositionFunc = Callable[..., list[dict[str, Any]]]
OrdersFunc = Callable[..., list[dict[str, Any]]]
SleepFunc = Callable[[float], None]


def run_once(
    signal_path: str,
    snapshot_path: str,
    order_log_path: str = "data/gm_order_log.json",
    account_id: str = "",
    today: str | None = None,
    now: str | None = None,
    max_positions: int = 5,
    blacklist: str | set[str] | list[str] | tuple[str, ...] | None = None,
    daily_order_limit: int = 0,
    max_order_value: float = 0,
    allow_non_trade_time: bool = False,
    trade_start_time: str = "09:30:00",
    trade_end_time: str = "15:00:00",
    order_func: OrderFunc = order_value,
    order_volume_func: OrderFunc = order_volume,
    cash_func: CashFunc = get_cash,
    position_func: PositionFunc = get_position,
    unfinished_orders_func: OrdersFunc = get_unfinished_orders,
    orders_func: OrdersFunc = get_orders,
    snapshot_poll_seconds: int = 10,
    final_snapshot_wait_seconds: int = 0,
    sleep_func: SleepFunc = time.sleep,
) -> dict[str, Any]:
    today_str = today or date.today().strftime("%Y-%m-%d")
    signal = gm_sim_strategy.load_today_signal(signal_path, today=today)
    positions = position_func(account_id=account_id or None) or []
    cash = cash_func(account_id=account_id or None) or {}
    unfinished_orders = call_order_query(unfinished_orders_func, account_id=account_id) or []
    orders = call_order_query(orders_func, account_id=account_id) or []
    gm_sim_strategy.save_account_snapshot(
        gm_sim_strategy.build_account_snapshot(
            cash,
            positions,
            unfinished_orders=unfinished_orders,
            orders=orders,
        ),
        snapshot_path,
    )
    if not signal:
        return {"submitted": [], "blocked": [], "message": "no today signal"}

    plans = gm_sim_strategy.build_orders(signal)
    order_log = load_order_log(order_log_path)
    submitted_today = sum(1 for item in order_log if item.get("date") == today_str)
    allowed, blocked = gm_sim_strategy.apply_risk_controls(
        plans,
        positions=positions,
        unfinished_orders=unfinished_orders,
        max_positions=max_positions,
        blacklist=blacklist,
        daily_order_limit=daily_order_limit,
        submitted_today=submitted_today,
        max_order_value=max_order_value,
        allow_non_trade_time=allow_non_trade_time,
        now=now,
        trade_start_time=trade_start_time,
        trade_end_time=trade_end_time,
    )

    submitted = []
    for plan in allowed:
        side = str(plan.get("side") or "buy").lower()
        if plan["dry_run"]:
            if "volume" in plan:
                submitted.append({
                    "symbol": plan["symbol"],
                    "side": side,
                    "volume": plan["volume"],
                    "dry_run": True,
                })
            else:
                submitted.append({"symbol": plan["symbol"], "value": plan["value"], "dry_run": True})
            continue
        if "volume" in plan:
            order_volume_func(
                symbol=plan["symbol"],
                volume=plan["volume"],
                side=OrderSide_Sell if side == "sell" else OrderSide_Buy,
                order_type=OrderType_Market,
                position_effect=PositionEffect_Close if side == "sell" else PositionEffect_Open,
                price=0,
                account=account_id,
            )
            submitted.append({"symbol": plan["symbol"], "side": side, "volume": plan["volume"]})
            order_log.append({
                "date": today_str,
                "symbol": plan["symbol"],
                "side": side,
                "volume": plan["volume"],
            })
            continue
        order_func(
            symbol=plan["symbol"],
            value=plan["value"],
            side=OrderSide_Buy,
            order_type=OrderType_Market,
            position_effect=PositionEffect_Open,
            price=0,
            account=account_id,
        )
        submitted.append({"symbol": plan["symbol"], "value": plan["value"]})
        order_log.append({"date": today_str, "symbol": plan["symbol"], "value": plan["value"]})

    if submitted and final_snapshot_wait_seconds > 0:
        sleep_func(final_snapshot_wait_seconds)

    cash, positions, unfinished_orders, orders = poll_account_snapshot(
        account_id=account_id,
        cash_func=cash_func,
        position_func=position_func,
        unfinished_orders_func=unfinished_orders_func,
        orders_func=orders_func,
        submitted=submitted,
        poll_seconds=snapshot_poll_seconds,
        sleep_func=sleep_func,
    )
    gm_sim_strategy.save_account_snapshot(
        gm_sim_strategy.build_account_snapshot(
            cash,
            positions,
            unfinished_orders=unfinished_orders,
            orders=orders,
        ),
        snapshot_path,
    )
    save_order_log(order_log, order_log_path)
    return {"submitted": submitted, "blocked": blocked}


def poll_account_snapshot(
    account_id: str = "",
    cash_func: CashFunc = get_cash,
    position_func: PositionFunc = get_position,
    unfinished_orders_func: OrdersFunc = get_unfinished_orders,
    orders_func: OrdersFunc = get_orders,
    submitted: list[dict[str, Any]] | None = None,
    poll_seconds: int = 10,
    sleep_func: SleepFunc = time.sleep,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    submitted = submitted or []
    expected_buys = {
        str(item.get("symbol") or "")
        for item in submitted
        if str(item.get("side") or "buy").lower() == "buy"
    }
    attempts = max(1, int(poll_seconds) + 1)
    cash: dict[str, Any] = {}
    positions: list[dict[str, Any]] = []
    unfinished_orders: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []

    for attempt in range(attempts):
        cash = cash_func(account_id=account_id or None) or {}
        positions = position_func(account_id=account_id or None) or []
        unfinished_orders = call_order_query(unfinished_orders_func, account_id=account_id) or []
        orders = call_order_query(orders_func, account_id=account_id) or []
        held = {
            str(item.get("symbol") or "")
            for item in positions
            if gm_sim_strategy._to_int(item.get("volume")) > 0
        }
        if not expected_buys or expected_buys.issubset(held):
            break
        if attempt < attempts - 1:
            sleep_func(1)

    return cash, positions, unfinished_orders, orders


def call_order_query(func: OrdersFunc, account_id: str = "") -> list[dict[str, Any]]:
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        signature = None
    if signature and "account_id" in signature.parameters:
        return func(account_id=account_id or None)
    return func()


def load_order_log(order_log_path: str) -> list[dict[str, Any]]:
    path = Path(order_log_path)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return payload if isinstance(payload, list) else []


def save_order_log(order_log: list[dict[str, Any]], order_log_path: str) -> None:
    path = Path(order_log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(order_log, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    load_dotenv(".env", override=True)
    settings = Settings()
    if settings.gm_token:
        set_token(settings.gm_token)
    serv_addr = settings.gm_serv_addr or os.getenv("GM_SERV_ADDR", "")
    if serv_addr:
        set_serv_addr(serv_addr)

    result = run_once(
        signal_path=settings.gm_signal_path,
        snapshot_path=settings.gm_account_snapshot_path,
        order_log_path=settings.gm_order_log_path,
        account_id=settings.gm_account_id,
        max_positions=settings.gm_max_positions,
        blacklist=settings.gm_blacklist,
        daily_order_limit=settings.gm_daily_order_limit,
        max_order_value=settings.gm_max_order_value,
        allow_non_trade_time=settings.gm_allow_non_trade_time,
        trade_start_time=settings.gm_trade_start_time,
        trade_end_time=settings.gm_trade_end_time,
        snapshot_poll_seconds=settings.gm_account_snapshot_poll_seconds,
        final_snapshot_wait_seconds=settings.gm_final_snapshot_wait_seconds,
    )
    print(f"SUBMITTED={result['submitted']}")
    print(f"BLOCKED={result['blocked']}")


if __name__ == "__main__":
    main()
