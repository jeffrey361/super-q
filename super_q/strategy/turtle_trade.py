"""海龟交易策略：20日新高突破 + 成交额过亿 + 动量阳线过滤。"""

import pandas as pd

from super_q.core.logger import get_logger
from super_q.strategy.base import BaseStrategy

logger = get_logger(__name__)


class TurtleTradeStrategy(BaseStrategy):
    """海龟交易策略（A股防诱多改良版）。

    选股条件（向量化，严禁 iterrows）：
    1. 突破新高：今日 close > 前20个交易日 high 的最大值
    2. 流动性：今日 turnover > 100,000,000
    3. 防诱多过滤：今日必须是实体阳线（今日 close > 今日 open），且必须真涨（今日 close > 昨日 close）

    Attributes:
        webhook_key: 路由到 'turtle' 专属飞书机器人。
    """

    webhook_key: str = "turtle"

    def run(self) -> list[str]:
        """
        遍历全市场，返回满足海龟突破条件的股票代码列表。
        """
        symbols = self.engine.get_local_symbols()
        selected: list[str] = []
        latest_date = self.engine.get_latest_date()
        breakout_days = max(1, self.settings.turtle_breakout_days)
        min_bars = breakout_days + 1

        for symbol in symbols:
            try:
                df = self.engine.get_ohlcv(symbol)
                if len(df) < min_bars:
                    continue

                # 向量化：前 N 日 high 的滚动最大值（不含当日，shift(1) 后取 rolling(N)）
                df["breakout_high"] = df["high"].shift(1).rolling(breakout_days).max()
                df["vol_ma20"] = df["volume"].rolling(20).mean()

                last = df.iloc[-1]
                prev = df.iloc[-2]  # 获取昨日数据，用于对比

                if latest_date and str(last["date"]) != latest_date:
                    continue
                if pd.isna(last["breakout_high"]) or pd.isna(last["vol_ma20"]):
                    continue

                # 核心条件 1：突破前 N 天最高点
                breakout = last["close"] > last["breakout_high"]
                # 核心条件 2：可配置流动性和量能确认
                liquid = last["turnover"] >= self.settings.turtle_min_turnover
                volume_confirm = (
                    last["volume"] >= last["vol_ma20"] * self.settings.turtle_min_volume_ratio
                )

                # 【新增防守条件】拒绝郑州煤电式的高开低走大阴线！
                is_yang = last["close"] > last["open"]   # 实体必须是阳线（红柱）
                is_up = last["close"] > prev["close"]    # 必须是真涨，不能是假阳线
                strong_enough = (
                    (last["close"] - prev["close"]) / prev["close"]
                    >= self.settings.turtle_min_daily_gain
                )

                if breakout and liquid and volume_confirm and is_yang and is_up and strong_enough:
                    selected.append(symbol)

            except Exception as exc:
                logger.warning(f"[{symbol}] TurtleTradeStrategy 计算失败：{exc}")
                continue

        logger.info(f"TurtleTradeStrategy 选出 {len(selected)} 只股票")
        return selected
