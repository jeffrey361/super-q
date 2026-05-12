"""A 股增强洞察服务：封装资金、热度、题材和风险数据。"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from super_q.core.config import Settings
from super_q.core.logger import get_logger
from super_q.data.engine import DataEngine

logger = get_logger(__name__)


_CREATE_INSIGHT_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS a_share_insights (
    symbol TEXT PRIMARY KEY,
    snapshot_date TEXT NOT NULL,
    money_flow_score REAL NOT NULL DEFAULT 0,
    heat_score REAL NOT NULL DEFAULT 0,
    theme_score REAL NOT NULL DEFAULT 0,
    risk_score REAL NOT NULL DEFAULT 0,
    total_score REAL NOT NULL DEFAULT 0,
    hard_risk INTEGER NOT NULL DEFAULT 0,
    risk_flags TEXT NOT NULL DEFAULT '[]',
    theme_keywords TEXT NOT NULL DEFAULT '[]',
    raw_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);
"""


HARD_RISK_WORDS: tuple[str, ...] = (
    "立案",
    "调查",
    "监管处罚",
    "财务造假",
    "实控人失联",
    "暂停上市",
    "终止上市",
    "退市",
)
SOFT_RISK_WORDS: tuple[str, ...] = (
    "减持",
    "拟减持",
    "预亏",
    "亏损",
    "问询函",
    "监管函",
    "诉讼",
    "商誉减值",
    "解禁",
    "质押",
)


@dataclass(frozen=True)
class AShareInsight:
    """单只 A 股增强洞察快照。"""

    symbol: str
    snapshot_date: str
    money_flow_score: float = 0.0
    heat_score: float = 0.0
    theme_score: float = 0.0
    risk_score: float = 0.0
    total_score: float = 0.0
    hard_risk: bool = False
    risk_flags: list[str] = field(default_factory=list)
    theme_keywords: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    updated_at: str = ""


@dataclass(frozen=True)
class AShareScore:
    """最终评分层使用的 A 股增强分。"""

    symbol: str
    score: float
    risk_flags: list[str]
    hard_risk: bool = False


class AShareInsightService:
    """A 股增强数据服务，负责 AkShare 调用、标准化和 SQLite 缓存。"""

    def __init__(
        self,
        engine: DataEngine,
        settings: Settings,
        ak_client: Any | None = None,
        now: Any | None = None,
    ) -> None:
        self.engine = engine
        self.settings = settings
        self.ak = ak_client
        self.now = now or datetime.now
        self._warned_api_errors: set[tuple[str, str]] = set()
        self._market_risk_frames: dict[str, pd.DataFrame] = {}
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.engine.db_path) as conn:
            conn.execute(_CREATE_INSIGHT_TABLE_SQL)
            conn.commit()

    def refresh_symbols(self, symbols: list[str]) -> dict[str, AShareInsight]:
        """刷新一批股票洞察；单只失败时返回中性结果。"""
        return {
            symbol: self.get_symbol_insight(symbol, force_refresh=True)
            for symbol in dict.fromkeys(symbols)
        }

    def get_symbol_insight(self, symbol: str, force_refresh: bool = False) -> AShareInsight:
        """读取单只股票洞察，优先使用缓存。"""
        if not getattr(self.settings, "a_share_insight_enabled", True):
            return self._neutral_insight(symbol)

        if not force_refresh:
            cached = self._load_cache(symbol)
            if cached is not None:
                return cached

        try:
            insight = self._fetch_and_score(symbol)
        except Exception as exc:  # pragma: no cover - 双保险，单股不能拖垮主流程
            logger.warning(f"[{symbol}] A 股增强洞察失败，返回中性结果：{exc}")
            insight = self._neutral_insight(symbol)

        self._write_cache(insight)
        return insight

    def get_symbol_keywords(self, symbols: list[str]) -> dict[str, list[str]]:
        """返回股票题材关键词，供新闻策略使用。"""
        keywords: dict[str, list[str]] = {}
        for symbol in symbols:
            insight = self.get_symbol_insight(symbol)
            if insight.theme_keywords:
                keywords[symbol] = insight.theme_keywords
        return keywords

    def score_symbol(self, symbol: str) -> AShareScore:
        """返回最终评分层可直接使用的分数对象。"""
        insight = self.get_symbol_insight(symbol)
        weight = float(getattr(self.settings, "a_share_insight_score_weight", 1.0) or 1.0)
        return AShareScore(
            symbol=symbol,
            score=round(insight.total_score * weight, 2),
            risk_flags=insight.risk_flags,
            hard_risk=insight.hard_risk,
        )

    def _fetch_and_score(self, symbol: str) -> AShareInsight:
        ak = self._ak()
        raw: dict[str, Any] = {}

        money_flow_score = self._money_flow_score(symbol, ak, raw)
        heat_score = self._heat_score(symbol, ak, raw)
        theme_keywords = self._theme_keywords(symbol, ak, raw)
        theme_score = min(len(theme_keywords) * 2.0, 8.0)
        risk_flags, hard_risk = self._risk_flags(symbol, ak, raw)
        risk_score = -30.0 if hard_risk else -min(len(risk_flags) * 8.0, 24.0)
        total_score = self._clamp(
            money_flow_score + heat_score + theme_score + risk_score,
            -30.0,
            30.0,
        )

        return AShareInsight(
            symbol=symbol,
            snapshot_date=self._today(),
            money_flow_score=money_flow_score,
            heat_score=heat_score,
            theme_score=theme_score,
            risk_score=risk_score,
            total_score=round(total_score, 2),
            hard_risk=hard_risk,
            risk_flags=risk_flags,
            theme_keywords=theme_keywords,
            raw=raw,
            updated_at=self._now_str(),
        )

    def _money_flow_score(self, symbol: str, ak: Any, raw: dict[str, Any]) -> float:
        try:
            market = "sh" if symbol.startswith(("6", "9")) else "sz"
            df = ak.stock_individual_fund_flow(stock=symbol, market=market)
            raw["money_flow"] = self._records(df, limit=3)
            if df is None or df.empty:
                return 0.0
            latest = df.iloc[-1]
            value = self._first_numeric(
                latest,
                ("主力净流入-净额", "主力净流入净额", "净额", "主力净流入", "大单净额"),
            )
            if value is None:
                return 0.0
            if value >= 100_000_000:
                return 12.0
            if value >= 50_000_000:
                return 8.0
            if value >= 10_000_000:
                return 4.0
            if value <= -100_000_000:
                return -12.0
            if value <= -50_000_000:
                return -8.0
            if value <= -10_000_000:
                return -4.0
            return 0.0
        except Exception as exc:
            logger.warning(f"[{symbol}] 资金流接口失败：{exc}")
            return 0.0

    def _heat_score(self, symbol: str, ak: Any, raw: dict[str, Any]) -> float:
        try:
            df = ak.stock_hot_rank_latest_em(symbol="A股")
            raw["hot_rank"] = self._records(df, limit=20)
            if df is None or df.empty:
                return 0.0
            for index, row in df.iterrows():
                code = self._row_text(row, ("代码", "股票代码", "证券代码"))
                if code and code[-6:] == symbol:
                    rank = self._first_numeric(row, ("排名", "当前排名", "序号")) or float(index + 1)
                    if rank <= 20:
                        return 8.0
                    if rank <= 50:
                        return 5.0
                    if rank <= 100:
                        return 2.0
            return 0.0
        except Exception as exc:
            logger.warning(f"[{symbol}] 热度接口失败：{exc}")
            return 0.0

    def _theme_keywords(self, symbol: str, ak: Any, raw: dict[str, Any]) -> list[str]:
        try:
            timeout = float(
                getattr(self.settings, "a_share_insight_request_timeout_seconds", 10.0)
                or 10.0
            )
            df = ak.stock_individual_info_em(symbol=symbol, timeout=timeout)
            raw["individual_info"] = self._records(df, limit=20)
            if df is None or df.empty:
                return []
            values: list[str] = []
            for _, row in df.iterrows():
                item = self._row_text(row, ("item", "项目", "指标"))
                value = self._row_text(row, ("value", "值", "内容"))
                if item and any(key in item for key in ("行业", "概念", "主营", "名称", "简称")):
                    values.extend(self._split_terms(value))
            return list(dict.fromkeys(term for term in values if term and term != symbol))
        except Exception as exc:
            self._warn_api_failure(symbol, "题材接口", exc)
            return []

    def _risk_flags(self, symbol: str, ak: Any, raw: dict[str, Any]) -> tuple[list[str], bool]:
        texts: list[str] = []
        for name, loader in {
            "disclosure": lambda: ak.stock_zh_a_disclosure_report_cninfo(symbol=symbol),
            "restricted_release": lambda: self._market_risk_frame(
                "restricted_release",
                ak.stock_restricted_release_detail_em,
            ),
            "pledge": lambda: self._market_risk_frame(
                "pledge",
                ak.stock_gpzy_pledge_ratio_detail_em,
            ),
        }.items():
            try:
                df = loader()
                raw[name] = self._records(df, limit=10)
                texts.extend(self._frame_texts(df, symbol))
            except Exception as exc:
                self._warn_api_failure(symbol, f"风险接口 {name}", exc)

        flags: list[str] = []
        hard = False
        joined = " ".join(texts)
        for word in HARD_RISK_WORDS:
            if word in joined:
                flags.append(word)
                hard = True
        for word in SOFT_RISK_WORDS:
            if word in joined and word not in flags:
                flags.append(word)
        return list(dict.fromkeys(flags)), hard

    def _market_risk_frame(self, name: str, loader: Any) -> pd.DataFrame:
        """读取全市场风险表；同一轮运行内复用，避免每只股票重复请求。"""
        if name not in self._market_risk_frames:
            df = loader()
            if not isinstance(df, pd.DataFrame):
                df = pd.DataFrame()
            self._market_risk_frames[name] = df
        return self._market_risk_frames[name]

    def _load_cache(self, symbol: str) -> AShareInsight | None:
        cache_hours = int(getattr(self.settings, "a_share_insight_cache_hours", 8) or 8)
        with sqlite3.connect(self.engine.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM a_share_insights WHERE symbol = ?",
                (symbol,),
            ).fetchone()
        if row is None:
            return None
        updated_at = datetime.fromisoformat(str(row["updated_at"]))
        if datetime.now() - updated_at > timedelta(hours=cache_hours):
            return None
        return self._row_to_insight(row)

    def _write_cache(self, insight: AShareInsight) -> None:
        try:
            payload = asdict(insight)
            with sqlite3.connect(self.engine.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO a_share_insights (
                        symbol, snapshot_date, money_flow_score, heat_score,
                        theme_score, risk_score, total_score, hard_risk,
                        risk_flags, theme_keywords, raw_json, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol) DO UPDATE SET
                        snapshot_date = excluded.snapshot_date,
                        money_flow_score = excluded.money_flow_score,
                        heat_score = excluded.heat_score,
                        theme_score = excluded.theme_score,
                        risk_score = excluded.risk_score,
                        total_score = excluded.total_score,
                        hard_risk = excluded.hard_risk,
                        risk_flags = excluded.risk_flags,
                        theme_keywords = excluded.theme_keywords,
                        raw_json = excluded.raw_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        insight.symbol,
                        insight.snapshot_date,
                        insight.money_flow_score,
                        insight.heat_score,
                        insight.theme_score,
                        insight.risk_score,
                        insight.total_score,
                        1 if insight.hard_risk else 0,
                        json.dumps(payload["risk_flags"], ensure_ascii=False),
                        json.dumps(payload["theme_keywords"], ensure_ascii=False),
                        json.dumps(payload["raw"], ensure_ascii=False, default=str),
                        insight.updated_at or self._now_str(),
                    ),
                )
                conn.commit()
        except Exception as exc:
            logger.warning(f"[{insight.symbol}] A 股洞察缓存写入失败：{exc}")

    def _row_to_insight(self, row: sqlite3.Row) -> AShareInsight:
        return AShareInsight(
            symbol=str(row["symbol"]),
            snapshot_date=str(row["snapshot_date"]),
            money_flow_score=float(row["money_flow_score"] or 0),
            heat_score=float(row["heat_score"] or 0),
            theme_score=float(row["theme_score"] or 0),
            risk_score=float(row["risk_score"] or 0),
            total_score=float(row["total_score"] or 0),
            hard_risk=bool(row["hard_risk"]),
            risk_flags=json.loads(row["risk_flags"] or "[]"),
            theme_keywords=json.loads(row["theme_keywords"] or "[]"),
            raw=json.loads(row["raw_json"] or "{}"),
            updated_at=str(row["updated_at"]),
        )

    def _neutral_insight(
        self,
        symbol: str,
        theme_keywords: list[str] | None = None,
        risk_flags: list[str] | None = None,
        hard_risk: bool = False,
    ) -> AShareInsight:
        return AShareInsight(
            symbol=symbol,
            snapshot_date=self._today(),
            hard_risk=hard_risk,
            risk_flags=risk_flags or [],
            theme_keywords=theme_keywords or [],
            updated_at=self._now_str(),
        )

    def _ak(self) -> Any:
        if self.ak is not None:
            return self.ak
        import akshare as ak

        self.ak = ak
        return ak

    def _warn_api_failure(self, symbol: str, api_name: str, exc: Exception) -> None:
        """接口失败时压缩日志，并对同类错误只 warning 一次。"""
        error_type = type(exc).__name__
        key = (api_name, error_type)
        message = self._compact_error(exc)
        text = f"[{symbol}] {api_name}失败：{message}"
        if key in self._warned_api_errors:
            logger.debug(text)
            return
        self._warned_api_errors.add(key)
        logger.warning(text)

    @staticmethod
    def _clamp(value: float, lower: float, upper: float) -> float:
        return max(lower, min(upper, value))

    @staticmethod
    def _compact_error(exc: Exception, limit: int = 240) -> str:
        text = " ".join(str(exc).split())
        if len(text) <= limit:
            return text
        return f"{text[:limit]}..."

    @staticmethod
    def _first_numeric(row: Any, names: tuple[str, ...]) -> float | None:
        for name in names:
            if name in row.index:
                value = pd.to_numeric(row[name], errors="coerce")
                if not pd.isna(value):
                    return float(value)
        return None

    @staticmethod
    def _row_text(row: Any, names: tuple[str, ...]) -> str:
        for name in names:
            if name in row.index and row[name] is not None:
                return str(row[name])
        return ""

    @staticmethod
    def _split_terms(value: str) -> list[str]:
        text = str(value or "")
        for sep in ("，", "、", ";", "；", "/", "|"):
            text = text.replace(sep, ",")
        return [part.strip() for part in text.split(",") if part.strip()]

    @staticmethod
    def _records(df: Any, limit: int = 5) -> list[dict[str, Any]]:
        if isinstance(df, pd.DataFrame) and not df.empty:
            return df.head(limit).where(pd.notna(df.head(limit)), None).to_dict("records")
        return []

    @staticmethod
    def _frame_texts(df: Any, symbol: str) -> list[str]:
        if not isinstance(df, pd.DataFrame) or df.empty:
            return []
        texts = []
        for _, row in df.head(50).iterrows():
            row_text = " ".join(
                f"{column} {value}"
                for column, value in row.items()
                if value is not None
            )
            if symbol in row_text or not any(char.isdigit() for char in row_text):
                texts.append(row_text)
        return texts

    def _today(self) -> str:
        return self.now().strftime("%Y-%m-%d")

    def _now_str(self) -> str:
        return self.now().strftime("%Y-%m-%d %H:%M:%S")
