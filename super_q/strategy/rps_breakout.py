import pandas as pd
import sqlite3
from super_q.strategy.base import BaseStrategy
from super_q.core.logger import get_logger

logger = get_logger(__name__)


class RpsBreakoutStrategy(BaseStrategy):
    """RPS 极强动量突破策略"""

    webhook_key: str = "rps"

    def run(self) -> list[str]:
        try:
            with sqlite3.connect(self.engine.db_path) as conn:
                df = pd.read_sql(
                    "SELECT symbol, date, open, close, high, turnover FROM stock_daily",
                    conn,
                )
        except Exception as exc:
            logger.error(f"读取数据库失败: {exc}")
            return []

        if df.empty:
            return []

        rps_period = max(1, self.settings.rps_period)
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values(['symbol', 'date'])

        # 纵向计算涨幅
        df['close_shift'] = df.groupby('symbol')['close'].shift(rps_period)
        df['prev_close'] = df.groupby('symbol')['close'].shift(1)
        df['pct_change'] = (df['close'] - df['close_shift']) / df['close_shift']

        latest_date = df['date'].max()
        latest_df = df[df['date'] == latest_date].copy()
        latest_df = latest_df.dropna(subset=['pct_change'])

        # 横向排位 (RPS)
        latest_df['rps'] = latest_df['pct_change'].rank(pct=True) * 100
        strong_stocks = latest_df[latest_df['rps'] >= self.settings.rps_threshold].copy()

        # 计算滚动最高价
        roll_high = df.groupby('symbol')['high'].rolling(
            window=rps_period, min_periods=max(1, rps_period // 2)
        ).max().reset_index(level=0, drop=True)
        df['roll_high'] = roll_high

        latest_roll_high = df[df['date'] == latest_date][['symbol', 'roll_high']]
        strong_stocks = strong_stocks.merge(latest_roll_high, on='symbol')

        # 强势近高点判定 + 可执行性过滤
        near_high = strong_stocks['close'] >= (
            strong_stocks['roll_high'] * self.settings.rps_near_high_ratio
        )
        liquid = strong_stocks['turnover'] >= self.settings.rps_min_turnover
        positive_day = (
            (strong_stocks['close'] > strong_stocks['open'])
            & (strong_stocks['close'] > strong_stocks['prev_close'])
        )
        if not self.settings.rps_require_positive_day:
            positive_day = pd.Series(True, index=strong_stocks.index)

        selected = strong_stocks[near_high & liquid & positive_day]

        logger.info(f"RpsBreakoutStrategy 选出 {len(selected)} 只股票")
        return selected['symbol'].tolist()
