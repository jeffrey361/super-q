"""superQ-X V2 主程序入口。

调度顺序：初始化配置 → 初始化日志 → 数据同步 → 策略执行 → 结果推送。
"""

import sys
from dotenv import load_dotenv
load_dotenv()

from datetime import date

import socket
socket.setdefaulttimeout(10.0)

from super_q.core.windows_compat import patch_slow_platform_machine
patch_slow_platform_machine()

from super_q.core.config import get_settings
from super_q.core.logger import get_logger
from super_q.data.a_share_insight import AShareInsightService
from super_q.data.engine import DataEngine
from super_q.notify.feishu import FeishuNotifier
from super_q.notify.wechat_ilink import WechatIlinkNotifier
from super_q.strategy.base import BaseStrategy
from super_q.strategy.final_selection import build_final_selection, final_selection_summary
from super_q.strategy.high_tight_flag import HighTightFlagStrategy
from super_q.strategy.limit_up_shakeout import LimitUpShakeoutStrategy
from super_q.strategy.ma_volume import MaVolumeStrategy
from super_q.strategy.news_confirm import NewsConfirmStrategy
from super_q.strategy.turtle_trade import TurtleTradeStrategy
from super_q.strategy.uptrend_limit_down import UptrendLimitDownStrategy
from super_q.strategy.rps_breakout import RpsBreakoutStrategy
from super_q.trade.gm_account import GmAccountSnapshotReader
from super_q.trade.gm_signal import GmSignalExporter


def main() -> None:
    """
    主调度函数，按顺序执行完整的数据同步和选股流程。

    流程：
    1. 加载并校验配置（ValidationError 时终止）
    2. 初始化日志
    3. 初始化数据引擎并执行全市场增量同步
    4. 遍历所有策略依次执行选股
    5. 有选股结果时推送至对应飞书机器人

    Raises:
        SystemExit: 任意阶段发生未捕获异常时，以退出码 1 终止进程。
    """
    try:
        # 1. 初始化配置
        settings = get_settings()

        # 2. 初始化日志
        logger = get_logger(__name__)
        logger.info("superQ-X V2 启动")

        # 3. 数据同步
        engine = DataEngine(settings)
        a_share_insight_service = None
        if settings.a_share_insight_enabled:
            try:
                a_share_insight_service = AShareInsightService(engine=engine, settings=settings)
                logger.info("A 股增强洞察已启用")
            except Exception as exc:
                logger.warning(f"A 股增强洞察初始化失败，已降级为关闭：{exc}")

        if not settings.sync_market_data:
            logger.info("SYNC_MARKET_DATA=false，跳过行情同步，直接使用本地数据跑策略")
        elif date.today().weekday() < 5:  # 周一到周五：0, 1, 2, 3, 4
            logger.info("工作日，开始增量同步最新数据...")
            all_symbols = engine.get_all_symbols()
            summary = engine.sync_all(all_symbols)
            logger.info(
                f"数据同步完成 — 成功: {summary.success} | "
                f"跳过: {summary.skipped} | 失败: {summary.failed}"
            )
        else:
            logger.info("🌟 今天是周末，A股休市！直接跳过网络拉取，使用本地最新数据极速调试策略！")

        # 4. 策略列表（新增策略在此追加即可）
        turtle_strategy = TurtleTradeStrategy(engine=engine, settings=settings)
        rps_strategy = RpsBreakoutStrategy(engine=engine, settings=settings)
        strategies: list[BaseStrategy] = [
            MaVolumeStrategy(engine=engine, settings=settings),
            turtle_strategy,
            HighTightFlagStrategy(engine=engine, settings=settings),
            LimitUpShakeoutStrategy(engine=engine, settings=settings),
            UptrendLimitDownStrategy(engine=engine, settings=settings),
            rps_strategy,
            NewsConfirmStrategy(
                engine=engine,
                settings=settings,
                technical_strategies=[turtle_strategy, rps_strategy],
                insight_service=a_share_insight_service,
            ),
        ]

        notifier = FeishuNotifier(settings)
        wechat_notifier = WechatIlinkNotifier(settings)
        gm_signal_exporter = GmSignalExporter(settings)
        account_summary = GmAccountSnapshotReader(
            settings.gm_account_snapshot_path,
            db_path=settings.db_path,
        ).summary_text()

        # 5. 遍历策略，先收集所有候选，再统一打分和推送
        strategy_results: dict[str, list[str]] = {}
        final_news_scores: list[dict[str, object]] = []
        final_reverse_symbols: list[str] = []
        for strategy in strategies:
            strategy_name = type(strategy).__name__
            strategy_result_key = _unique_strategy_result_key(strategy_name, strategy_results)
            logger.info(f"执行策略：{strategy_name}")

            selected: list[str] = strategy.run()
            logger.info(f"{strategy_name} 选出 {len(selected)} 只股票")
            strategy_results[strategy_result_key] = selected
            news_scores = (
                strategy.gm_news_scores()
                if hasattr(strategy, "gm_news_scores")
                else None
            )
            reverse_symbols = (
                strategy.gm_reverse_symbols()
                if hasattr(strategy, "gm_reverse_symbols")
                else None
            )
            if news_scores:
                final_news_scores = news_scores
            if reverse_symbols:
                final_reverse_symbols = reverse_symbols

        (
            a_share_scores,
            a_share_risk_flags,
            a_share_hard_risk_symbols,
        ) = _build_a_share_final_inputs(
            strategy_results=strategy_results,
            insight_service=a_share_insight_service,
            settings=settings,
            logger=logger,
        )
        final_selection = build_final_selection(
            strategy_results=strategy_results,
            news_scores=final_news_scores,
            reverse_symbols=final_reverse_symbols,
            max_symbols=settings.final_selection_max_symbols,
            min_score=settings.final_selection_min_score,
            a_share_scores=a_share_scores,
            a_share_risk_flags=a_share_risk_flags,
            a_share_hard_risk_symbols=a_share_hard_risk_symbols,
        )
        final_symbols = [item.symbol for item in final_selection]
        logger.info(f"DailyTopSelection 最终选出 {len(final_symbols)} 只股票")
        if final_symbols:
            summary = final_selection_summary(final_selection)
            notifier.send(
                symbols=final_symbols,
                strategy_name="DailyTopSelection",
                webhook_key="default",
                account_summary=account_summary,
                news_summary=summary,
            )
            wechat_notifier.send(
                symbols=final_symbols,
                strategy_name="DailyTopSelection",
                account_summary=account_summary,
                news_summary=summary,
            )
            gm_signal_exporter.export(
                symbols=final_symbols,
                strategy_name="DailyTopSelection",
                news_scores=final_news_scores,
                reverse_symbols=[],
            )
        else:
            logger.info("DailyTopSelection 无最终选股结果，跳过推送和 GM 导出")

    except Exception:
        try:
            _logger = get_logger(__name__)
            _logger.exception("主流程发生未捕获异常，程序终止")
        except Exception:
            import traceback
            traceback.print_exc()
        sys.exit(1)

    logger.info("superQ-X V2 运行完成")


def _build_a_share_final_inputs(
    strategy_results: dict[str, list[str]],
    insight_service: object | None,
    settings: object,
    logger: object,
) -> tuple[dict[str, float], dict[str, list[str]], list[str]]:
    """把 A 股洞察快照转换为最终评分层需要的输入。"""
    if insight_service is None:
        return {}, {}, []

    candidate_symbols = _collect_candidate_symbols(strategy_results)
    if not candidate_symbols:
        return {}, {}, []

    try:
        insights = insight_service.refresh_symbols(candidate_symbols)
    except Exception as exc:
        logger.warning(f"A 股增强洞察刷新失败，已跳过最终评分增强：{exc}")
        return {}, {}, []

    weight = float(getattr(settings, "a_share_insight_score_weight", 1.0) or 1.0)
    exclude_hard_risk = bool(getattr(settings, "a_share_insight_hard_risk_exclude", True))
    a_share_scores: dict[str, float] = {}
    a_share_risk_flags: dict[str, list[str]] = {}
    a_share_hard_risk_symbols: list[str] = []

    for symbol, insight in insights.items():
        score = float(getattr(insight, "total_score", 0.0) or 0.0)
        risk_flags = list(getattr(insight, "risk_flags", []) or [])
        hard_risk = bool(getattr(insight, "hard_risk", False))
        a_share_scores[symbol] = round(score * weight, 2)
        if risk_flags:
            a_share_risk_flags[symbol] = risk_flags
        if exclude_hard_risk and hard_risk:
            a_share_hard_risk_symbols.append(symbol)

    return a_share_scores, a_share_risk_flags, a_share_hard_risk_symbols


def _collect_candidate_symbols(strategy_results: dict[str, list[str]]) -> list[str]:
    """按策略输出顺序去重候选股票。"""
    symbols: list[str] = []
    for result_symbols in strategy_results.values():
        symbols.extend(result_symbols)
    return list(dict.fromkeys(symbols))


def _unique_strategy_result_key(
    strategy_name: str,
    strategy_results: dict[str, list[str]],
) -> str:
    """生成不覆盖已有策略结果的字典键。"""
    if strategy_name not in strategy_results:
        return strategy_name
    index = 2
    while f"{strategy_name}#{index}" in strategy_results:
        index += 1
    return f"{strategy_name}#{index}"


if __name__ == "__main__":
    main()
