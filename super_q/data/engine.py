"""数据引擎模块：负责 SQLite 行情数据存储与 akshare 增量同步。"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pandas as pd

from super_q.core.config import Settings
from super_q.core.logger import get_logger

logger = get_logger(__name__)


@dataclass
class SyncResult:
    """单个 symbol 同步结果。"""

    symbol: str
    status: Literal["success", "skip", "fail"]
    rows_added: int = 0


@dataclass
class SyncSummary:
    """全市场同步汇总统计。"""

    success: int = 0
    skipped: int = 0
    failed: int = 0


_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS stock_daily (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol   TEXT    NOT NULL,
    date     TEXT    NOT NULL,
    open     REAL,
    high     REAL,
    low      REAL,
    close    REAL,
    volume   REAL,
    turnover REAL,
    UNIQUE (symbol, date)
);
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_symbol_date ON stock_daily (symbol, date);
"""


class DataEngine:
    """行情数据引擎，负责 SQLite 存储和 akshare 增量同步。"""

    def __init__(self, settings: Settings) -> None:
        """
        初始化 DataEngine。

        Args:
            settings: 系统配置实例，提供 db_path 和 start_date。
        """
        self.settings = settings
        self.db_path: str = settings.db_path
        self.start_date: str = settings.start_date
        self.market_data_provider: str = settings.market_data_provider.lower()
        self.market_data_timeout_seconds: float = settings.market_data_timeout_seconds
        self.sync_exclude_qualified_markets: bool = settings.sync_exclude_qualified_markets
        self._init_db()

    def _init_db(self) -> None:
        """
        初始化数据库：创建 data/ 目录、建表、建唯一索引。
        若表和索引已存在则跳过（幂等）。
        """
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(_CREATE_TABLE_SQL)
            conn.execute(_CREATE_INDEX_SQL)
            conn.commit()
        logger.info(f"数据库初始化完成：{self.db_path}")

    def _get_last_date(self, symbol: str) -> str | None:
        """
        查询某 symbol 在本地数据库中的最新日期。

        Args:
            symbol: 股票代码，如 '000001'。

        Returns:
            最新日期字符串（格式 YYYY-MM-DD），无数据时返回 None。
        """
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT MAX(date) FROM stock_daily WHERE symbol = ?",
                (symbol,),
            ).fetchone()
        return row[0] if row and row[0] else None

    def get_ohlcv(self, symbol: str) -> pd.DataFrame:
        """
        读取某 symbol 的全量 OHLCV 数据，供策略层调用。

        Args:
            symbol: 股票代码。

        Returns:
            包含 date/open/high/low/close/volume/turnover 列的 DataFrame。
        """
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql(
                "SELECT * FROM stock_daily WHERE symbol = ? ORDER BY date",
                conn,
                params=(symbol,),
            )
        return df

    def _normalize_daily_frame(
        self,
        symbol: str,
        df: pd.DataFrame,
        provider: str = "eastmoney",
    ) -> pd.DataFrame:
        df = df.copy()
        if provider == "sina" and "amount" in df.columns and "turnover" in df.columns:
            df = df.drop(columns=["turnover"])
        col_map = self._daily_column_map(provider)
        df = df.rename(columns=col_map)
        df["symbol"] = symbol

        keep_cols = ["symbol", "date", "open", "high", "low", "close", "volume", "turnover"]
        missing = [column for column in keep_cols if column not in df.columns]
        if missing:
            raise ValueError(f"{provider} 行情缺少字段：{','.join(missing)}")
        df = df[keep_cols]
        df["date"] = df["date"].astype(str)
        for column in ("open", "high", "low", "close", "volume", "turnover"):
            df[column] = pd.to_numeric(df[column], errors="coerce")
        if provider == "sina":
            # 新浪 volume 单位为股；东财和现有库中 volume 使用手，保持策略输入一致。
            df["volume"] = df["volume"] / 100
        df = df.dropna(subset=["date", "open", "high", "low", "close", "volume", "turnover"])
        return df

    def _daily_column_map(self, provider: str) -> dict[str, str]:
        if provider == "sina":
            return {
                "date": "date",
                "open": "open",
                "high": "high",
                "low": "low",
                "close": "close",
                "volume": "volume",
                "amount": "turnover",
            }
        return {
            "日期": "date",
            "开盘": "open",
            "最高": "high",
            "最低": "low",
            "收盘": "close",
            "成交量": "volume",
            "成交额": "turnover",
        }

    def _providers_for_sync(self) -> list[str]:
        provider = self.market_data_provider
        if provider == "auto":
            return ["eastmoney", "sina"]
        if provider in {"eastmoney", "sina"}:
            return [provider]
        logger.warning(f"未知行情源 MARKET_DATA_PROVIDER={provider}，回退到 auto")
        return ["eastmoney", "sina"]

    def _prefixed_symbol(self, symbol: str) -> str:
        if symbol.startswith(("6", "9")):
            return f"sh{symbol}"
        return f"sz{symbol}"

    def _fetch_daily_frame(
        self,
        provider: str,
        symbol: str,
        start: str,
        end: str,
    ) -> pd.DataFrame:
        import akshare as ak

        if provider == "eastmoney":
            return ak.stock_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date=start,
                end_date=end,
                adjust="qfq",
                timeout=self.market_data_timeout_seconds,
            )
        if provider == "sina":
            return ak.stock_zh_a_daily(
                symbol=self._prefixed_symbol(symbol),
                start_date=start,
                end_date=end,
                adjust="qfq",
            )
        raise ValueError(f"不支持的行情源：{provider}")

    def _write_daily_frame(self, symbol: str, df: pd.DataFrame) -> int:
        rows = len(df)
        try:
            with sqlite3.connect(self.db_path) as conn:
                df.to_sql(
                    "stock_daily",
                    conn,
                    if_exists="append",
                    index=False,
                    method="multi",
                )
        except sqlite3.IntegrityError as exc:
            logger.warning(f"[{symbol}] 写入时遇到重复数据，已跳过：{exc}")
        return rows

    def sync_symbol(self, symbol: str) -> SyncResult:
        import random
        import time
        from datetime import date, timedelta

        last_date = self._get_last_date(symbol)
        today_date = date.today()
        today_str = today_date.strftime("%Y%m%d")

        if last_date is None:
            start = self.start_date.replace("-", "")
        else:
            last_date_obj = date.fromisoformat(last_date)
            # 👇 核心优化：如果本地数据已经是今天（或更晚），直接跳过，物理阻断网络请求！
            if last_date_obj >= today_date:
                return SyncResult(symbol=symbol, status="skip")

            start = (last_date_obj + timedelta(days=1)).strftime("%Y%m%d")

        df = None
        selected_provider = ""
        provider_errors: list[str] = []
        max_retries = 3
        for provider in self._providers_for_sync():
            for attempt in range(max_retries):
                try:
                    time.sleep(random.uniform(0.1, 0.4))
                    df = self._fetch_daily_frame(provider, symbol, start, today_str)
                    selected_provider = provider
                    break
                except Exception as exc:
                    error_str = str(exc)
                    should_retry = (
                        ("RemoteDisconnected" in error_str or "Connection aborted" in error_str)
                        and attempt < max_retries - 1
                    )
                    if should_retry:
                        sleep_time = (attempt + 1) * 3
                        logger.warning(
                            f"[{symbol}] {provider} 触发反爬，"
                            f"蛰伏 {sleep_time} 秒后第 {attempt + 2} 次重试..."
                        )
                        time.sleep(sleep_time)
                        continue
                    provider_errors.append(f"{provider}: {error_str}")
                    break
            if df is not None:
                break

        if df is None:
            logger.warning(f"[{symbol}] 行情拉取最终失败：{' | '.join(provider_errors)}")
            return SyncResult(symbol=symbol, status="fail")

        if df is None or df.empty:
            return SyncResult(symbol=symbol, status="skip")

        try:
            df = self._normalize_daily_frame(symbol, df, provider=selected_provider)
        except ValueError as exc:
            logger.warning(f"[{symbol}] {selected_provider} 行情字段不完整，跳过写入：{exc}")
            return SyncResult(symbol=symbol, status="fail")

        rows = self._write_daily_frame(symbol, df)
        logger.info(f"[{symbol}] 使用 {selected_provider} 同步 {rows} 行")
        return SyncResult(symbol=symbol, status="success", rows_added=rows)

    def get_all_symbols(self) -> list[str]:
        """
        从 akshare 获取全市场 A 股 symbol 列表（轻量接口）。
        包含网络重试机制，防止服务器掐断连接。

        Returns:
            股票代码字符串列表，如 ['000001', '000002', ...]。
        """
        import akshare as ak
        import time

        max_retries = 5
        for attempt in range(max_retries):
            try:
                logger.info(f"正在获取全市场股票列表 (第 {attempt + 1}/{max_retries} 次尝试)...")
                df = ak.stock_info_a_code_name()
                logger.info(f"成功获取股票列表，共 {len(df)} 只股票。")
                symbols = self._symbols_from_code_name_frame(df)
                if self.sync_exclude_qualified_markets:
                    logger.info(
                        f"已开启同步权限过滤，保留 {len(symbols)} 只普通股票用于同步。"
                    )
                return symbols
            except Exception as e:
                logger.warning(f"获取全市场列表失败: {e}。3秒后重试...")
                time.sleep(3)

        logger.error("获取全市场列表最终失败！请检查网络连接。")
        return []

    def _symbols_from_code_name_frame(self, df: pd.DataFrame) -> list[str]:
        """从 AkShare 代码名称表提取同步股票列表，并按配置排除需权限品种。"""
        code_column = "code" if "code" in df.columns else "代码"
        name_column = "name" if "name" in df.columns else "名称"
        symbols: list[str] = []
        excluded = 0
        for _, row in df.iterrows():
            symbol = str(row.get(code_column, "")).strip().zfill(6)
            name = str(row.get(name_column, "") or "")
            if not symbol:
                continue
            if self.sync_exclude_qualified_markets and self._is_qualified_market_symbol(
                symbol,
                name,
            ):
                excluded += 1
                continue
            symbols.append(symbol)
        if self.sync_exclude_qualified_markets and excluded:
            logger.info(f"同步列表已排除需权限或高风险品种 {excluded} 只")
        return symbols

    @staticmethod
    def _is_qualified_market_symbol(symbol: str, name: str) -> bool:
        """判断股票是否属于需要额外权限或应避免同步的品种。"""
        normalized_name = name.upper().replace("＊", "*").strip()
        if symbol.startswith(("688", "689")):
            return True
        if symbol.startswith(("4", "8", "920")):
            return True
        if symbol.startswith(("110", "113", "118", "123", "127", "128")):
            return True
        if any(keyword in name for keyword in ("转债", "可转债", "退市", "退整理")):
            return True
        if name.startswith("退"):
            return True
        return "ST" in normalized_name

    def get_local_symbols(self) -> list[str]:
        """
        从本地 SQLite 数据库获取已有数据的股票代码列表，无需网络请求。

        Returns:
            本地已存在数据的股票代码列表。
        """
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT DISTINCT symbol FROM stock_daily"
            ).fetchall()
        return [row[0] for row in rows]

    def get_latest_date(self) -> str | None:
        """返回本地行情库中的全市场最新交易日期。"""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT MAX(date) FROM stock_daily").fetchone()
        return row[0] if row and row[0] else None

    def sync_all(self, symbols: list[str]) -> SyncSummary:
        """
        批量增量同步全市场，展示 rich 进度条。

        Args:
            symbols: 股票代码列表，通常由 get_all_symbols() 提供。

        Returns:
            SyncSummary，包含 success / skipped / failed 计数。
        """
        from rich.progress import (
            BarColumn,
            MofNCompleteColumn,
            Progress,
            SpinnerColumn,
            TextColumn,
            TimeElapsedColumn,
        )

        summary = SyncSummary()
        unique_symbols = list(dict.fromkeys(symbols))

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]同步中[/bold cyan]"),
            BarColumn(bar_width=40),
            MofNCompleteColumn(),
            TextColumn("[yellow]{task.fields[symbol]}[/yellow]"),
            TimeElapsedColumn(),
        ) as progress:
            task = progress.add_task("sync", total=len(unique_symbols), symbol="")

            for symbol in unique_symbols:
                progress.update(task, symbol=symbol)
                result = self.sync_symbol(symbol)

                if result.status == "success":
                    summary.success += 1
                elif result.status == "skip":
                    summary.skipped += 1
                else:
                    summary.failed += 1

                progress.advance(task)

        logger.info(
            f"同步完成 — 成功: {summary.success} | "
            f"跳过: {summary.skipped} | "
            f"失败: {summary.failed}"
        )
        return summary
