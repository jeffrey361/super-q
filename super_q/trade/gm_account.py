"""掘金账户快照读取。"""

import json
import sqlite3
from pathlib import Path
from typing import Any


class GmAccountSnapshotReader:
    """读取 gm_sim_strategy.py 导出的账户快照。"""

    def __init__(self, snapshot_path: str, db_path: str | None = None) -> None:
        self.snapshot_path = Path(snapshot_path)
        self.db_path = Path(db_path) if db_path else None

    def load(self) -> dict[str, Any] | None:
        if not self.snapshot_path.exists():
            return None
        try:
            return json.loads(self.snapshot_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def summary_text(self) -> str:
        snapshot = self.load()
        if not snapshot:
            return ""

        cash = snapshot.get("cash", {})
        stock_names = self._load_stock_names()
        lines = [
            "掘金账户快照",
            f"更新时间：{snapshot.get('captured_at', '')}",
            f"账户余额：{float(cash.get('available', 0) or 0):.2f}",
            f"当日盈利：{float(cash.get('day_pnl', 0) or 0):.2f}",
            f"浮动盈亏：{float(cash.get('floating_pnl', 0) or 0):.2f}",
            f"总资产：{float(cash.get('nav', 0) or 0):.2f}",
            f"持仓市值：{float(cash.get('market_value', 0) or 0):.2f}",
            f"委托冻结：{float(cash.get('order_frozen', 0) or 0):.2f}",
        ]

        positions = snapshot.get("positions", [])
        if positions:
            lines.append("持仓信息：")
            lines.append("代码 名称 现价 持仓 市值(CNY)")
            for item in positions:
                raw_symbol = str(item.get("symbol") or "")
                display_code = self._format_symbol(raw_symbol)
                name = self._stock_name(stock_names, raw_symbol, display_code)
                price = float(item.get("price", 0) or 0)
                market_value = float(item.get("market_value", 0) or 0)
                lines.append(
                    f"{display_code} {name} {price:.2f} {self._currency(display_code)} "
                    f"{item.get('volume')} {market_value:,.0f}"
                )
        else:
            lines.append("持仓信息：无")

        self._append_order_lines(
            lines,
            title="未结委托：",
            orders=snapshot.get("unfinished_orders", []),
            stock_names=stock_names,
        )
        self._append_order_lines(
            lines,
            title="委托流水：",
            orders=snapshot.get("orders", []),
            stock_names=stock_names,
        )

        return "\n".join(lines)

    def _append_order_lines(
        self,
        lines: list[str],
        title: str,
        orders: list[dict[str, Any]],
        stock_names: dict[str, str],
    ) -> None:
        if not orders:
            return
        lines.append(title)
        for item in orders[:10]:
            raw_symbol = str(item.get("symbol") or "")
            display_code = self._format_symbol(raw_symbol)
            name = self._stock_name(stock_names, raw_symbol, display_code)
            side = str(item.get("side") or "")
            volume = item.get("volume", 0)
            status = str(item.get("status") or "")
            lines.append(f"{display_code} {name} {side} {volume} {status}")

    def _load_stock_names(self) -> dict[str, str]:
        if not self.db_path or not self.db_path.exists():
            return {}
        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute("SELECT symbol, name FROM stock_names").fetchall()
        except sqlite3.Error:
            return {}
        return {str(symbol): str(name) for symbol, name in rows}

    def _stock_name(self, stock_names: dict[str, str], raw_symbol: str, display_code: str) -> str:
        for key in (raw_symbol, display_code, self._lookup_symbol_key(raw_symbol)):
            if key in stock_names:
                return stock_names[key]
        return "未知"

    def _lookup_symbol_key(self, symbol: str) -> str:
        if "." not in symbol:
            return symbol
        left, right = symbol.split(".", 1)
        if left in {"SHSE", "SZSE", "BJSE"}:
            return right
        return left

    def _format_symbol(self, symbol: str) -> str:
        if "." not in symbol:
            return symbol
        left, right = symbol.split(".", 1)
        exchange_suffix = {"SHSE": "SH", "SZSE": "SZ", "BJSE": "BJ"}
        if left in exchange_suffix:
            return f"{right}.{exchange_suffix[left]}"
        return symbol

    def _currency(self, display_code: str) -> str:
        if display_code.endswith(".HK"):
            return "HKD"
        return "CNY"
