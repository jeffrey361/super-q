"""掘金量化仿真交易信号导出。"""

import json
from datetime import date
from pathlib import Path
from typing import Callable

from super_q.core.config import Settings
from super_q.core.logger import get_logger
from super_q.trade.symbols import to_gm_symbol

logger = get_logger(__name__)


class GmSignalExporter:
    """把 superQ 选股结果导出给掘金策略脚本读取。"""

    def __init__(self, settings: Settings, today: Callable[[], str] | None = None) -> None:
        self.settings = settings
        self.today = today or (lambda: date.today().strftime("%Y-%m-%d"))

    def export(
        self,
        symbols: list[str],
        strategy_name: str,
        news_scores: list[dict[str, object]] | None = None,
        reverse_symbols: list[str] | None = None,
    ) -> bool:
        if not self.settings.gm_enabled:
            return False

        gm_symbols = [to_gm_symbol(symbol) for symbol in symbols[: self.settings.gm_max_positions]]
        score_by_symbol = {
            str(item.get("symbol")): item
            for item in (news_scores or [])
            if item.get("symbol")
        }
        high_confidence_score = self.settings.news_high_confidence_score
        orders = []
        for symbol, gm_symbol in zip(symbols[: self.settings.gm_max_positions], gm_symbols, strict=False):
            score = score_by_symbol.get(symbol)
            final_score = _to_float(score.get("final_score")) if score else 0.0
            high_confidence = bool(score) and final_score >= high_confidence_score
            orders.append({
                "symbol": gm_symbol,
                "side": "buy",
                "volume": (
                    self.settings.gm_news_high_confidence_buy_volume
                    if high_confidence
                    else self.settings.gm_buy_volume
                ),
                "reason": "news_high_confidence" if high_confidence else "strategy",
            })

        for symbol in reverse_symbols or []:
            orders.append({
                "symbol": to_gm_symbol(symbol),
                "side": "sell",
                "volume": "all",
                "reason": "reverse_signal",
            })

        payload = {
            "date": self.today(),
            "strategy": strategy_name,
            "symbols": gm_symbols,
            "order_cash_per_stock": self.settings.gm_order_cash_per_stock,
            "orders": orders,
            "max_positions": self.settings.gm_max_positions,
            "dry_run": self.settings.gm_dry_run,
        }

        signal_path = Path(self.settings.gm_signal_path)
        signal_path.parent.mkdir(parents=True, exist_ok=True)
        signal_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"掘金交易信号已导出：{signal_path}，共 {len(gm_symbols)} 只股票")
        return True


def _to_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
