"""全策略最终选股聚合与排序。"""

from dataclasses import dataclass
from typing import Any


TECHNICAL_SOURCE_SCORES: dict[str, float] = {
    "HighTightFlagStrategy": 35,
    "LimitUpShakeoutStrategy": 30,
    "UptrendLimitDownStrategy": 25,
    "MaVolumeStrategy": 20,
    "TurtleTradeStrategy": 15,
    "RpsBreakoutStrategy": 15,
    "NewsConfirmStrategy": 0,
}
NEWS_SCORE_WEIGHT = 0.7
CONFLUENCE_BONUS = 10


@dataclass(frozen=True)
class FinalSelection:
    """最终入选股票及其综合评分。"""

    symbol: str
    score: float
    sources: list[str]
    news_score: float = 0.0
    a_share_score: float = 0.0
    risk_flags: list[str] | None = None


def build_final_selection(
    strategy_results: dict[str, list[str]],
    news_scores: list[dict[str, Any]],
    reverse_symbols: list[str],
    max_symbols: int,
    min_score: float,
    a_share_scores: dict[str, float] | None = None,
    a_share_risk_flags: dict[str, list[str]] | None = None,
    a_share_hard_risk_symbols: list[str] | None = None,
) -> list[FinalSelection]:
    """把各策略候选汇总为统一 Top N 买入池。"""
    source_by_symbol: dict[str, list[str]] = {}
    for strategy_name, symbols in strategy_results.items():
        for symbol in symbols:
            source_by_symbol.setdefault(symbol, []).append(strategy_name)

    risk_symbols = set(reverse_symbols)
    risk_symbols.update(a_share_hard_risk_symbols or [])
    a_share_scores = a_share_scores or {}
    a_share_risk_flags = a_share_risk_flags or {}
    news_score_by_symbol = {
        str(item.get("symbol")): _to_float(item.get("final_score"))
        for item in news_scores
        if item.get("symbol")
    }

    selections: list[FinalSelection] = []
    for symbol, sources in source_by_symbol.items():
        if symbol in risk_symbols:
            continue
        technical_score = sum(TECHNICAL_SOURCE_SCORES.get(source, 0) for source in sources)
        news_score = news_score_by_symbol.get(symbol, 0.0)
        a_share_score = _to_float(a_share_scores.get(symbol))
        confluence_score = max(0, len(sources) - 1) * CONFLUENCE_BONUS
        total_score = technical_score + news_score * NEWS_SCORE_WEIGHT + confluence_score + a_share_score
        if total_score < min_score:
            continue
        selections.append(
            FinalSelection(
                symbol=symbol,
                score=round(total_score, 2),
                sources=sources,
                news_score=news_score,
                a_share_score=a_share_score,
                risk_flags=a_share_risk_flags.get(symbol, []),
            )
        )

    selections.sort(key=lambda item: (-item.score, item.symbol))
    if max_symbols <= 0:
        return selections
    return selections[:max_symbols]


def final_selection_summary(selections: list[FinalSelection]) -> str:
    """生成一次性推送用的最终评分摘要。"""
    if not selections:
        return ""
    lines = ["最终选股评分："]
    for index, item in enumerate(selections, start=1):
        source_text = ",".join(item.sources)
        lines.append(
            f"{index}. {item.symbol} 综合分 {item.score:g} "
            f"新闻分 {item.news_score:g} A股增强 {item.a_share_score:g} 来源：{source_text}"
        )
        if item.risk_flags:
            lines.append(f"   风险/提示：{','.join(item.risk_flags)}")
    return "\n".join(lines)


def _to_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
