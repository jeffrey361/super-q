"""新闻确认策略：用实时财经新闻确认技术策略候选股，并过滤风险新闻。"""

from collections.abc import Callable
import hashlib
import sqlite3
from typing import Any

import pandas as pd

from sequoia_x.core.logger import get_logger
from sequoia_x.strategy.base import BaseStrategy
from sequoia_x.strategy.rps_breakout import RpsBreakoutStrategy
from sequoia_x.strategy.turtle_trade import TurtleTradeStrategy

logger = get_logger(__name__)

NewsItem = dict[str, Any]


class NewsConfirmStrategy(BaseStrategy):
    """技术策略 + 新闻确认 + 风险过滤策略。"""

    webhook_key: str = "news_confirm"

    risk_words: tuple[str, ...] = (
        "立案",
        "调查",
        "减持",
        "预亏",
        "亏损",
        "问询函",
        "监管函",
        "退市",
        "诉讼",
        "商誉减值",
        "解禁",
    )
    positive_event_words: dict[str, tuple[str, ...]] = {
        "业绩": ("预增", "增长", "扭亏", "盈利", "净利"),
        "订单": ("订单", "合同", "中标", "交付"),
        "政策": ("政策", "补贴", "规划", "试点", "改革"),
        "产业": ("涨价", "供需", "产能", "出口", "国产替代"),
        "资本": ("回购", "增持", "分红", "股权激励"),
    }
    theme_words: tuple[str, ...] = (
        "机器人",
        "半导体",
        "AI",
        "人工智能",
        "算力",
        "电力",
        "新能源",
        "储能",
        "国产替代",
        "低空经济",
        "军工",
        "医药",
        "消费",
    )

    def __init__(
        self,
        *args,
        technical_strategies: list[Any] | None = None,
        news_fetcher: Callable[[], list[NewsItem]] | None = None,
        symbol_keywords: dict[str, list[str]] | None = None,
        now: Callable[[], pd.Timestamp] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.technical_strategies = technical_strategies
        self.news_fetcher = news_fetcher or self._fetch_market_news
        self.symbol_keywords = symbol_keywords
        self.now = now or (lambda: pd.Timestamp.now())
        self.last_scores: list[dict[str, Any]] = []
        self.rejected_scores: list[dict[str, Any]] = []
        self._init_cache_tables()

    def _init_cache_tables(self) -> None:
        with sqlite3.connect(self.engine.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS news_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    news_hash TEXT NOT NULL UNIQUE,
                    source TEXT,
                    title TEXT NOT NULL,
                    content TEXT,
                    published_at TEXT NOT NULL,
                    url TEXT,
                    created_at TEXT NOT NULL
                );
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_news_published_at ON news_items (published_at);"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS stock_names (
                    symbol TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    keywords TEXT,
                    updated_at TEXT NOT NULL
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS stock_aliases (
                    symbol TEXT NOT NULL,
                    alias TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(symbol, alias)
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS stock_concepts (
                    symbol TEXT NOT NULL,
                    concept TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(symbol, concept)
                );
                """
            )
            conn.commit()

    def upsert_stock_names(self, names: dict[str, str | list[str]]) -> None:
        updated_at = self.now().strftime("%Y-%m-%d %H:%M:%S")
        rows = []
        for symbol, value in names.items():
            if isinstance(value, list):
                if not value:
                    continue
                name = value[0]
                keywords = ",".join(dict.fromkeys([*value, symbol]))
            else:
                name = value
                keywords = ",".join(dict.fromkeys([value, symbol]))
            rows.append((symbol, name, keywords, updated_at))

        if not rows:
            return

        with sqlite3.connect(self.engine.db_path) as conn:
            conn.executemany(
                """
                INSERT INTO stock_names (symbol, name, keywords, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    name = excluded.name,
                    keywords = excluded.keywords,
                    updated_at = excluded.updated_at
                """,
                rows,
            )
            conn.commit()

    def upsert_stock_aliases(self, aliases: dict[str, list[str]]) -> None:
        self._upsert_symbol_terms("stock_aliases", "alias", aliases)

    def upsert_stock_concepts(self, concepts: dict[str, list[str]]) -> None:
        self._upsert_symbol_terms("stock_concepts", "concept", concepts)

    def _upsert_symbol_terms(
        self,
        table: str,
        column: str,
        values: dict[str, list[str]],
    ) -> None:
        updated_at = self.now().strftime("%Y-%m-%d %H:%M:%S")
        rows = [
            (symbol, term.strip(), updated_at)
            for symbol, terms in values.items()
            for term in terms
            if term and term.strip()
        ]
        if not rows:
            return
        with sqlite3.connect(self.engine.db_path) as conn:
            conn.executemany(
                f"""
                INSERT OR REPLACE INTO {table} (symbol, {column}, updated_at)
                VALUES (?, ?, ?)
                """,
                rows,
            )
            conn.commit()

    def _parse_time(self, value: Any) -> str:
        if value is None or value == "":
            return self.now().strftime("%Y-%m-%d %H:%M:%S")

        parsed = pd.to_datetime(value, errors="coerce")
        if pd.isna(parsed):
            return self.now().strftime("%Y-%m-%d %H:%M:%S")
        return parsed.strftime("%Y-%m-%d %H:%M:%S")

    def _news_hash(self, item: NewsItem) -> str:
        raw = "|".join(
            str(item.get(key, ""))
            for key in ("source", "title", "content", "published_at", "url")
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _cache_news(self, news: list[NewsItem]) -> None:
        created_at = self.now().strftime("%Y-%m-%d %H:%M:%S")
        rows = []
        for item in news:
            title = str(item.get("title") or "")
            content = str(item.get("content") or "")
            if not title and not content:
                continue

            normalized = {
                "source": str(item.get("source") or "unknown"),
                "title": title,
                "content": content,
                "published_at": self._parse_time(item.get("published_at")),
                "url": str(item.get("url") or ""),
            }
            rows.append((
                self._news_hash(normalized),
                normalized["source"],
                normalized["title"],
                normalized["content"],
                normalized["published_at"],
                normalized["url"],
                created_at,
            ))

        if not rows:
            return

        with sqlite3.connect(self.engine.db_path) as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO news_items (
                    news_hash, source, title, content, published_at, url, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()

    def _load_recent_cached_news(self) -> list[NewsItem]:
        cutoff = (
            self.now() - pd.Timedelta(days=self.settings.news_lookback_days)
        ).strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect(self.engine.db_path) as conn:
            rows = conn.execute(
                """
                SELECT source, title, content, published_at, url
                FROM news_items
                WHERE published_at >= ?
                ORDER BY published_at DESC
                """,
                (cutoff,),
            ).fetchall()

        return [
            {
                "source": row[0],
                "title": row[1],
                "content": row[2],
                "published_at": row[3],
                "url": row[4],
            }
            for row in rows
        ]

    def _fetch_market_news(self) -> list[NewsItem]:
        import akshare as ak

        frames: list[pd.DataFrame] = []
        for fetch in (
            ak.stock_info_global_em,
            ak.stock_info_global_sina,
            ak.stock_info_global_cls,
            ak.stock_info_global_ths,
        ):
            try:
                frames.append(fetch())
            except Exception as exc:
                logger.warning(f"新闻接口拉取失败：{exc}")

        news: list[NewsItem] = []
        for df in frames:
            for record in df.head(50).to_dict("records"):
                title = str(record.get("标题") or record.get("内容") or "")
                content = str(record.get("摘要") or record.get("内容") or "")
                news.append({
                    "source": "akshare",
                    "title": title,
                    "content": content,
                    "published_at": (
                        record.get("发布时间")
                        or record.get("时间")
                        or record.get("发布日期")
                    ),
                    "url": record.get("链接") or "",
                })
        return news

    def _load_symbol_keywords(self) -> dict[str, list[str]]:
        if self.symbol_keywords is not None:
            return self.symbol_keywords

        keywords: dict[str, list[str]] = {}
        with sqlite3.connect(self.engine.db_path) as conn:
            rows = conn.execute(
                "SELECT symbol, name, keywords FROM stock_names"
            ).fetchall()
            alias_rows = conn.execute("SELECT symbol, alias FROM stock_aliases").fetchall()
            concept_rows = conn.execute("SELECT symbol, concept FROM stock_concepts").fetchall()

        for symbol, name, keyword_text in rows:
            values = [symbol, name]
            values.extend(
                part.strip()
                for part in str(keyword_text or "").split(",")
                if part.strip()
            )
            keywords[symbol] = list(dict.fromkeys(values))
        for symbol, alias in alias_rows:
            keywords.setdefault(symbol, [symbol]).append(alias)
        for symbol, concept in concept_rows:
            keywords.setdefault(symbol, [symbol]).append(concept)
        for symbol, values in list(keywords.items()):
            keywords[symbol] = list(dict.fromkeys(value for value in values if value))

        self.symbol_keywords = keywords
        return keywords

    def _technical_candidates(self) -> list[str]:
        strategies = self.technical_strategies
        if strategies is None:
            strategies = [
                TurtleTradeStrategy(engine=self.engine, settings=self.settings),
                RpsBreakoutStrategy(engine=self.engine, settings=self.settings),
            ]

        candidates: list[str] = []
        seen: set[str] = set()
        for strategy in strategies:
            for symbol in strategy.run():
                if symbol not in seen:
                    seen.add(symbol)
                    candidates.append(symbol)
        return candidates

    def _news_texts(self, news: list[NewsItem]) -> list[str]:
        return [
            f"{item.get('title', '')} {item.get('content', '')}"
            for item in news
        ]

    def _matches_symbol(self, symbol: str, text: str, keywords: dict[str, list[str]]) -> bool:
        candidates = [symbol, *keywords.get(symbol, [])]
        return any(keyword and keyword in text for keyword in candidates)

    def _cleanup_old_news(self) -> None:
        retention_days = getattr(self.settings, "news_cache_retention_days", 90)
        if retention_days <= 0:
            return
        cutoff = (self.now() - pd.Timedelta(days=retention_days)).strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect(self.engine.db_path) as conn:
            conn.execute("DELETE FROM news_items WHERE published_at < ?", (cutoff,))
            conn.commit()

    def _events_in_text(self, text: str) -> list[str]:
        events = []
        for event, words in self.positive_event_words.items():
            if any(word in text for word in words):
                events.append(event)
        return events

    def _themes_in_text(self, text: str, symbol_keywords: list[str]) -> list[str]:
        candidates = [*self.theme_words, *symbol_keywords]
        return list(dict.fromkeys(word for word in candidates if word and word in text))

    def _risk_words_in_text(self, text: str) -> list[str]:
        return [word for word in self.risk_words if word in text]

    def _score_symbol(
        self,
        symbol: str,
        news: list[NewsItem],
        keywords: dict[str, list[str]],
        technical_sources: list[str],
    ) -> dict[str, Any]:
        matched_news = []
        events: list[str] = []
        themes: list[str] = []
        risks: list[str] = []
        keyword_values = keywords.get(symbol, [])
        for item in news:
            text = f"{item.get('title', '')} {item.get('content', '')}"
            if not self._matches_symbol(symbol, text, keywords):
                continue
            item_events = self._events_in_text(text)
            item_themes = self._themes_in_text(text, keyword_values)
            item_risks = self._risk_words_in_text(text)
            events.extend(item_events)
            themes.extend(item_themes)
            risks.extend(item_risks)
            matched_news.append(
                {
                    "title": str(item.get("title") or ""),
                    "published_at": str(item.get("published_at") or ""),
                    "events": item_events,
                    "themes": item_themes,
                    "risks": item_risks,
                }
            )

        events = list(dict.fromkeys(events))
        themes = list(dict.fromkeys(themes))
        risks = list(dict.fromkeys(risks))
        final_score = (
            len(matched_news) * 15
            + len(events) * 20
            + len(themes) * 8
            + len(technical_sources) * 10
            - len(risks) * 100
        )
        return {
            "symbol": symbol,
            "technical_sources": technical_sources,
            "matched_news": matched_news,
            "events": events,
            "themes": themes,
            "risks": risks,
            "final_score": final_score,
            "reject_reason": "、".join(risks) if risks else "",
        }

    def _technical_candidate_sources(self) -> dict[str, list[str]]:
        strategies = self.technical_strategies
        if strategies is None:
            strategies = [
                TurtleTradeStrategy(engine=self.engine, settings=self.settings),
                RpsBreakoutStrategy(engine=self.engine, settings=self.settings),
            ]
        sources: dict[str, list[str]] = {}
        for strategy in strategies:
            name = type(strategy).__name__
            for symbol in strategy.run():
                sources.setdefault(symbol, []).append(name)
        return sources

    def news_summary_text(self) -> str:
        if not self.last_scores and not self.rejected_scores:
            return ""
        lines = ["新闻确认："]
        for item in self.last_scores:
            lines.append(
                f"{item['symbol']} 综合分 {item['final_score']} "
                f"事件：{','.join(item['events']) or '无'} "
                f"题材：{','.join(item['themes']) or '无'}"
            )
            for news in item["matched_news"][: self.settings.news_max_items_per_stock]:
                if news.get("title"):
                    lines.append(f"- {news['title']}")
        for item in self.rejected_scores:
            lines.append(f"{item['symbol']} 被过滤：{item.get('reject_reason') or '风险新闻'}")
        return "\n".join(lines)

    def gm_news_scores(self) -> list[dict[str, Any]]:
        return self.last_scores

    def gm_reverse_symbols(self) -> list[str]:
        return [str(item["symbol"]) for item in self.rejected_scores if item.get("symbol")]

    def run(self) -> list[str]:
        candidate_sources = self._technical_candidate_sources()
        candidates = list(candidate_sources)
        self.last_scores = []
        self.rejected_scores = []
        if not candidates:
            return []

        self._cache_news(self.news_fetcher())
        self._cleanup_old_news()
        news = self._load_recent_cached_news()
        keywords = self._load_symbol_keywords()
        threshold = getattr(self.settings, "news_score_threshold", 20)

        for symbol in candidates:
            score = self._score_symbol(symbol, news, keywords, candidate_sources[symbol])
            if not score["matched_news"]:
                continue
            if score["risks"]:
                self.rejected_scores.append(score)
                continue
            if score["final_score"] >= threshold:
                self.last_scores.append(score)

        self.last_scores.sort(key=lambda item: item["final_score"], reverse=True)
        selected = [item["symbol"] for item in self.last_scores]
        logger.info(f"NewsConfirmStrategy 选出 {len(selected)} 只股票")
        return selected
