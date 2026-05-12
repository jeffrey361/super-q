"""新闻确认策略：用实时财经新闻确认技术策略候选股，并过滤风险新闻。"""

from collections.abc import Callable
import hashlib
import sqlite3
from typing import Any

import pandas as pd

from super_q.core.logger import get_logger
from super_q.strategy.base import BaseStrategy
from super_q.strategy.rps_breakout import RpsBreakoutStrategy
from super_q.strategy.turtle_trade import TurtleTradeStrategy

logger = get_logger(__name__)

NewsItem = dict[str, Any]
TargetedNewsFetcher = Callable[[str, list[str]], list[NewsItem]]


def _clamp(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
    return max(lower, min(upper, value))


class NewsConfirmStrategy(BaseStrategy):
    """技术策略 + 新闻确认 + 风险过滤策略。"""

    webhook_key: str = "news_confirm"

    hard_risk_words: tuple[str, ...] = (
        "立案",
        "调查",
        "减持",
        "拟减持",
        "减持计划",
        "业绩预亏",
        "财务造假",
        "监管处罚",
        "实控人失联",
        "暂停上市",
        "终止上市",
        "退市",
    )
    soft_risk_words: tuple[str, ...] = (
        "预亏",
        "亏损",
        "下滑",
        "问询函",
        "监管函",
        "诉讼",
        "商誉减值",
        "解禁",
    )
    risk_words: tuple[str, ...] = tuple(
        dict.fromkeys([*hard_risk_words, *soft_risk_words])
    )
    positive_event_words: dict[str, tuple[str, ...]] = {
        "业绩": ("预增", "增长", "扭亏", "盈利", "净利", "超预期"),
        "订单": ("订单", "合同", "中标", "交付", "定点"),
        "政策": ("政策", "补贴", "规划", "试点", "改革", "支持"),
        "产业": ("涨价", "供需", "产能", "出口", "国产替代", "景气"),
        "资本": ("回购", "增持", "分红", "股权激励"),
        "评级": ("上调评级", "买入评级", "首次覆盖", "目标价"),
        "经营": ("新产品", "量产", "扩产", "投产", "战略合作"),
    }
    event_weights: dict[str, int] = {
        "业绩": 24,
        "订单": 24,
        "政策": 20,
        "产业": 16,
        "资本": 14,
        "评级": 10,
        "经营": 14,
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
    source_weight_keywords: tuple[tuple[tuple[str, ...], float], ...] = (
        (("公告", "交易所", "巨潮", "cninfo", "sse", "szse", "bse"), 1.0),
        (("东方财富", "同花顺", "财联社", "证券时报", "中国证券报", "上海证券报"), 0.8),
        (("研报", "券商", "证券研究", "评级"), 0.7),
        (("新浪", "腾讯", "网易", "凤凰"), 0.5),
    )

    def __init__(
        self,
        *args,
        technical_strategies: list[Any] | None = None,
        news_fetcher: Callable[[], list[NewsItem]] | None = None,
        targeted_news_fetcher: TargetedNewsFetcher | None = None,
        symbol_keywords: dict[str, list[str]] | None = None,
        insight_service: Any | None = None,
        now: Callable[[], pd.Timestamp] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.technical_strategies = technical_strategies
        self.news_fetcher = news_fetcher or self._fetch_market_news
        self.targeted_news_fetcher = targeted_news_fetcher
        self.symbol_keywords = symbol_keywords
        self._provided_symbol_keywords = symbol_keywords is not None
        self.symbol_theme_keywords: dict[str, list[str]] | None = None
        self.insight_service = insight_service
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

    def _fetch_targeted_news(
        self,
        symbols: list[str],
        keywords: dict[str, list[str]],
    ) -> list[NewsItem]:
        news: list[NewsItem] = []
        for symbol in symbols:
            symbol_keywords = keywords.get(symbol, [symbol])
            if self.targeted_news_fetcher is not None:
                try:
                    news.extend(self.targeted_news_fetcher(symbol, symbol_keywords))
                except Exception as exc:
                    logger.warning(f"[{symbol}] 定向新闻拉取失败：{exc}")
                continue

            if getattr(self.settings, "news_targeted_search_enabled", False):
                news.extend(self._fetch_searxng_symbol_news(symbol, symbol_keywords))
        return news

    def _fetch_searxng_symbol_news(self, symbol: str, keywords: list[str]) -> list[NewsItem]:
        import requests

        base_url = str(getattr(self.settings, "news_searxng_url", "") or "").strip()
        if not base_url:
            return []

        username = str(getattr(self.settings, "news_searxng_username", "") or "")
        password = str(getattr(self.settings, "news_searxng_password", "") or "")
        auth = (username, password) if username or password else None
        search_url = base_url.rstrip("/")
        if not search_url.endswith("/search"):
            search_url = f"{search_url}/search"

        name = next((keyword for keyword in keywords if keyword and not keyword.isdigit()), symbol)
        year = self.now().year
        queries = (
            f"{name} {symbol} 最新消息 {year} 股票",
            f"{name} {symbol} 业绩 财报 股票",
            f"{name} {symbol} 行业 政策 股票",
            f"{name} {symbol} site:eastmoney.com",
            f"{name} {symbol} site:10jqka.com.cn",
        )
        limit = max(1, int(getattr(self.settings, "news_targeted_search_limit", 3)))
        results: list[NewsItem] = []
        relevance_terms = [
            term
            for term in dict.fromkeys([symbol, *keywords])
            if term and (term == symbol or len(term) >= 3)
        ]
        for query in queries:
            try:
                resp = requests.get(
                    search_url,
                    params={"q": query, "format": "json", "language": "zh-CN"},
                    auth=auth,
                    timeout=8,
                )
                resp.raise_for_status()
                payload = resp.json()
            except Exception as exc:
                logger.warning(f"[{symbol}] SearXNG 搜索失败：{exc}")
                continue

            for record in (payload.get("results") or [])[:limit]:
                title = str(record.get("title") or "")
                content = str(record.get("content") or "")
                if not title and not content:
                    continue
                url = str(record.get("url") or "")
                searchable_text = f"{title} {content} {url}"
                if relevance_terms and not any(
                    term in searchable_text for term in relevance_terms
                ):
                    continue
                results.append(
                    {
                        "source": str(record.get("engine") or "searxng"),
                        "title": title,
                        "content": content,
                        "published_at": record.get("publishedDate") or record.get("published_at"),
                        "url": url,
                    }
                )
        return results

    def _load_symbol_keywords(self) -> dict[str, list[str]]:
        if self.symbol_keywords is not None:
            return {
                symbol: self._identity_keywords(symbol, values)
                for symbol, values in self.symbol_keywords.items()
            }

        keywords: dict[str, list[str]] = {}
        with sqlite3.connect(self.engine.db_path) as conn:
            rows = conn.execute(
                "SELECT symbol, name, keywords FROM stock_names"
            ).fetchall()
            alias_rows = conn.execute("SELECT symbol, alias FROM stock_aliases").fetchall()

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
        for symbol, values in list(keywords.items()):
            keywords[symbol] = self._identity_keywords(symbol, values)

        self.symbol_keywords = keywords
        return keywords

    def _load_symbol_theme_keywords(self) -> dict[str, list[str]]:
        if self.symbol_theme_keywords is not None:
            return self.symbol_theme_keywords
        if self._provided_symbol_keywords and self.symbol_keywords is not None:
            self.symbol_theme_keywords = {
                symbol: list(dict.fromkeys(value for value in values if value))
                for symbol, values in self.symbol_keywords.items()
            }
            return self.symbol_theme_keywords

        themes: dict[str, list[str]] = {}
        with sqlite3.connect(self.engine.db_path) as conn:
            rows = conn.execute("SELECT symbol, keywords FROM stock_names").fetchall()
            concept_rows = conn.execute("SELECT symbol, concept FROM stock_concepts").fetchall()

        for symbol, keyword_text in rows:
            values = [
                part.strip()
                for part in str(keyword_text or "").split(",")
                if part.strip()
            ]
            if values:
                themes[symbol] = values
        for symbol, concept in concept_rows:
            if concept:
                themes.setdefault(symbol, []).append(concept)
        for symbol, values in list(themes.items()):
            themes[symbol] = list(dict.fromkeys(value for value in values if value))

        self.symbol_theme_keywords = themes
        return themes

    def _identity_keywords(self, symbol: str, values: list[str]) -> list[str]:
        """返回可用于识别股票身份的关键词，排除宽泛题材词。"""
        candidates = [symbol, *values]
        broad_words = set(self.theme_words)
        return list(
            dict.fromkeys(
                value
                for value in candidates
                if value and value not in broad_words
            )
        )

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

    def _risk_words_in_text(self, text: str) -> dict[str, list[str]]:
        hard = [word for word in self.hard_risk_words if word in text]
        soft = [
            word
            for word in self.soft_risk_words
            if word in text and word not in hard
        ]
        return {
            "hard": list(dict.fromkeys(hard)),
            "soft": list(dict.fromkeys(soft)),
        }

    def _source_weight(self, item: NewsItem) -> float:
        source_text = " ".join(
            str(item.get(key) or "")
            for key in ("source", "title", "url")
        ).lower()
        for keywords, weight in self.source_weight_keywords:
            if any(keyword.lower() in source_text for keyword in keywords):
                return weight
        return 0.5 if item.get("url") else 0.4

    def _freshness_score(self, item: NewsItem) -> float:
        published_at = self._parse_time(item.get("published_at"))
        try:
            age_days = (self.now() - pd.Timestamp(published_at)).total_seconds() / 86400
        except Exception:
            return 5.0
        if age_days <= 1:
            return 10.0
        if age_days <= 3:
            return 8.0
        if age_days <= 7:
            return 5.0
        return 2.0

    def _sentiment_score(self, matched_news: list[dict[str, Any]]) -> float:
        positive = 0
        negative = 0
        for item in matched_news:
            if item["events"]:
                positive += 1
            if item["hard_risks"] or item["soft_risks"]:
                negative += 1
        return _clamp(10 + positive * 4 - negative * 8, 0, 20)

    def _event_score(self, events: list[str]) -> float:
        return _clamp(sum(self.event_weights.get(event, 8) for event in events), 0, 30)

    def _score_symbol(
        self,
        symbol: str,
        news: list[NewsItem],
        identity_keywords: dict[str, list[str]],
        theme_keywords: dict[str, list[str]],
        technical_sources: list[str],
    ) -> dict[str, Any]:
        matched_news = []
        events: list[str] = []
        themes: list[str] = []
        hard_risks: list[str] = []
        soft_risks: list[str] = []
        keyword_values = theme_keywords.get(symbol, [])
        for item in news:
            text = f"{item.get('title', '')} {item.get('content', '')}"
            if not self._matches_symbol(symbol, text, identity_keywords):
                continue
            item_events = self._events_in_text(text)
            item_themes = self._themes_in_text(text, keyword_values)
            item_risks = self._risk_words_in_text(text)
            events.extend(item_events)
            themes.extend(item_themes)
            hard_risks.extend(item_risks["hard"])
            soft_risks.extend(item_risks["soft"])
            matched_news.append(
                {
                    "title": str(item.get("title") or ""),
                    "published_at": str(item.get("published_at") or ""),
                    "source": str(item.get("source") or ""),
                    "events": item_events,
                    "themes": item_themes,
                    "hard_risks": item_risks["hard"],
                    "soft_risks": item_risks["soft"],
                    "source_weight": self._source_weight(item),
                    "freshness_score": self._freshness_score(item),
                }
            )

        events = list(dict.fromkeys(events))
        themes = list(dict.fromkeys(themes))
        hard_risks = list(dict.fromkeys(hard_risks))
        soft_risks = list(dict.fromkeys(soft_risks))
        risk_penalty = len(hard_risks) * 100 + len(soft_risks) * 15
        technical_score = _clamp(len(technical_sources) * 15, 0, 30)
        catalyst_score = self._event_score(events)
        theme_score = _clamp(len(themes) * 4, 0, 10)
        sentiment_score = self._sentiment_score(matched_news)
        freshness_score = (
            max((item["freshness_score"] for item in matched_news), default=0.0)
        )
        source_score = (
            sum(item["source_weight"] for item in matched_news) / len(matched_news) * 10
            if matched_news
            else 0.0
        )
        final_score = round(
            _clamp(
                technical_score
                + catalyst_score
                + theme_score
                + sentiment_score
                + freshness_score
                + source_score
                - risk_penalty
            )
        )
        risks = list(dict.fromkeys([*hard_risks, *soft_risks]))
        return {
            "symbol": symbol,
            "technical_sources": technical_sources,
            "matched_news": matched_news,
            "events": events,
            "themes": themes,
            "risks": risks,
            "hard_risks": hard_risks,
            "soft_risks": soft_risks,
            "score_parts": {
                "technical": technical_score,
                "catalyst": catalyst_score,
                "theme": theme_score,
                "sentiment": sentiment_score,
                "freshness": freshness_score,
                "source": source_score,
                "risk_penalty": risk_penalty,
            },
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

    def _load_insight_keywords(self, candidates: list[str]) -> dict[str, list[str]]:
        if self.insight_service is None:
            return {}
        try:
            return self.insight_service.get_symbol_keywords(candidates)
        except Exception as exc:
            logger.warning(f"A 股洞察关键词加载失败，跳过增强：{exc}")
            return {}

    def _load_insight_risks(self, candidates: list[str]) -> dict[str, Any]:
        if self.insight_service is None:
            return {}
        risks: dict[str, Any] = {}
        for symbol in candidates:
            try:
                risks[symbol] = self.insight_service.get_symbol_insight(symbol)
            except Exception as exc:
                logger.warning(f"[{symbol}] A 股洞察风险加载失败，跳过增强：{exc}")
        return risks

    def _merge_theme_keywords(
        self,
        theme_keywords: dict[str, list[str]],
        insight_keywords: dict[str, list[str]],
    ) -> dict[str, list[str]]:
        merged = {symbol: list(values) for symbol, values in theme_keywords.items()}
        for symbol, values in insight_keywords.items():
            merged.setdefault(symbol, [])
            merged[symbol] = list(dict.fromkeys([*merged[symbol], *values]))
        return merged

    def _apply_insight_risk(self, score: dict[str, Any], insight: Any | None) -> dict[str, Any]:
        if insight is None:
            return score
        risk_flags = list(getattr(insight, "risk_flags", []) or [])
        if not risk_flags:
            return score
        if bool(getattr(insight, "hard_risk", False)):
            score["hard_risks"] = list(dict.fromkeys([*score.get("hard_risks", []), *risk_flags]))
        else:
            score["soft_risks"] = list(dict.fromkeys([*score.get("soft_risks", []), *risk_flags]))
        score["risks"] = list(dict.fromkeys([*score.get("hard_risks", []), *score.get("soft_risks", [])]))
        score["reject_reason"] = "、".join(score["risks"])
        return score

    def _insight_reject_score(self, symbol: str, technical_sources: list[str], insight: Any) -> dict[str, Any]:
        risk_flags = list(getattr(insight, "risk_flags", []) or [])
        return {
            "symbol": symbol,
            "technical_sources": technical_sources,
            "matched_news": [],
            "events": [],
            "themes": list(getattr(insight, "theme_keywords", []) or []),
            "risks": risk_flags,
            "hard_risks": risk_flags,
            "soft_risks": [],
            "score_parts": {"a_share_insight_risk": -100},
            "final_score": 0,
            "reject_reason": "、".join(risk_flags),
        }

    def news_summary_text(self) -> str:
        if not self.last_scores and not self.rejected_scores:
            return ""
        lines = ["新闻确认："]
        for item in self.last_scores:
            risks = "、".join(item.get("soft_risks") or item.get("risks") or []) or "无"
            lines.append(
                f"{item['symbol']} 综合分 {item['final_score']} "
                f"催化剂：{','.join(item['events']) or '无'} "
                f"风险：{risks} "
                f"题材：{','.join(item['themes']) or '无'}"
            )
            for news in item["matched_news"][: self.settings.news_max_items_per_stock]:
                if news.get("title"):
                    source = f"({news['source']})" if news.get("source") else ""
                    lines.append(f"- {source}{news['title']}")
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
        keywords = self._load_symbol_keywords()
        theme_keywords = self._merge_theme_keywords(
            self._load_symbol_theme_keywords(),
            self._load_insight_keywords(candidates),
        )
        insight_risks = self._load_insight_risks(candidates)
        targeted_news = self._fetch_targeted_news(candidates, keywords)
        if targeted_news:
            self._cache_news(targeted_news)
        news = self._load_recent_cached_news()
        threshold = getattr(self.settings, "news_score_threshold", 20)

        for symbol in candidates:
            insight = insight_risks.get(symbol)
            score = self._score_symbol(
                symbol,
                news,
                keywords,
                theme_keywords,
                candidate_sources[symbol],
            )
            score = self._apply_insight_risk(score, insight)
            if insight is not None and bool(getattr(insight, "hard_risk", False)) and not score["matched_news"]:
                self.rejected_scores.append(
                    self._insight_reject_score(symbol, candidate_sources[symbol], insight)
                )
                continue
            if not score["matched_news"]:
                continue
            if score["hard_risks"]:
                self.rejected_scores.append(score)
                continue
            if score["final_score"] >= threshold:
                self.last_scores.append(score)

        self.last_scores.sort(key=lambda item: item["final_score"], reverse=True)
        selected = [item["symbol"] for item in self.last_scores]
        logger.info(f"NewsConfirmStrategy 选出 {len(selected)} 只股票")
        return selected
