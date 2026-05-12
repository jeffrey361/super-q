"""最终选股聚合器测试。"""

from super_q.strategy.final_selection import build_final_selection


def test_final_selection_ranks_sources_news_scores_and_limits_to_top_five() -> None:
    """最终选股应按综合评分排序，并最多保留 5 只股票。"""
    strategy_results = {
        "MaVolumeStrategy": ["000001", "000002", "000003", "000004", "000005", "000006"],
        "HighTightFlagStrategy": ["000006"],
        "TurtleTradeStrategy": ["000003", "000006"],
        "NewsConfirmStrategy": ["000002", "000006"],
    }
    news_scores = [
        {"symbol": "000002", "final_score": 80},
        {"symbol": "000006", "final_score": 70},
    ]

    selected = build_final_selection(
        strategy_results=strategy_results,
        news_scores=news_scores,
        reverse_symbols=[],
        max_symbols=5,
        min_score=0,
    )

    assert [item.symbol for item in selected] == [
        "000006",
        "000002",
        "000003",
        "000001",
        "000004",
    ]
    assert selected[0].score > selected[1].score
    assert selected[0].sources == [
        "MaVolumeStrategy",
        "HighTightFlagStrategy",
        "TurtleTradeStrategy",
        "NewsConfirmStrategy",
    ]


def test_final_selection_can_return_empty_when_scores_below_threshold() -> None:
    """最终选股低于总分门槛时应允许当天 0 只股票。"""
    selected = build_final_selection(
        strategy_results={"MaVolumeStrategy": ["000001"]},
        news_scores=[],
        reverse_symbols=[],
        max_symbols=5,
        min_score=60,
    )

    assert selected == []


def test_final_selection_excludes_reverse_risk_symbols() -> None:
    """新闻硬风险反向信号应从最终买入池剔除。"""
    selected = build_final_selection(
        strategy_results={
            "HighTightFlagStrategy": ["000001"],
            "NewsConfirmStrategy": ["000001"],
        },
        news_scores=[{"symbol": "000001", "final_score": 95}],
        reverse_symbols=["000001"],
        max_symbols=5,
        min_score=0,
    )

    assert selected == []


def test_final_selection_uses_a_share_score_for_ranking() -> None:
    """A 股增强分应参与最终排序，并保留在结果中。"""
    selected = build_final_selection(
        strategy_results={
            "MaVolumeStrategy": ["000001"],
            "TurtleTradeStrategy": ["000002"],
        },
        news_scores=[],
        reverse_symbols=[],
        max_symbols=5,
        min_score=0,
        a_share_scores={"000001": 0, "000002": 20},
        a_share_risk_flags={"000002": ["主力净流入"]},
    )

    assert [item.symbol for item in selected] == ["000002", "000001"]
    assert selected[0].a_share_score == 20
    assert selected[0].risk_flags == ["主力净流入"]


def test_final_selection_excludes_a_share_hard_risk_symbols() -> None:
    """A 股增强硬风险应直接剔除最终买入候选。"""
    selected = build_final_selection(
        strategy_results={
            "HighTightFlagStrategy": ["000001"],
            "MaVolumeStrategy": ["000002"],
        },
        news_scores=[],
        reverse_symbols=[],
        max_symbols=5,
        min_score=0,
        a_share_scores={"000001": 30, "000002": 0},
        a_share_hard_risk_symbols=["000001"],
    )

    assert [item.symbol for item in selected] == ["000002"]
