"""微信 iLink Bot 通知模块。"""

import json
import sqlite3
import time
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import requests

from sequoia_x.core.config import Settings
from sequoia_x.core.logger import get_logger

logger = get_logger(__name__)

ILINK_APP_CLIENT_VERSION = str((2 << 16) | (2 << 8))


class WechatIlinkClient:
    """微信 iLink Bot 最小客户端，只实现扫码登录、拉取上下文和文本发送。"""

    base_login_url = "https://ilinkai.weixin.qq.com"

    def __init__(
        self,
        state_path: str,
        channel_version: str = "1.0.0",
        route_tag: str = "",
        login_timeout_seconds: int = 180,
        session: requests.Session | None = None,
    ) -> None:
        self.state_path = Path(state_path)
        self.channel_version = channel_version
        self.route_tag = route_tag
        self.login_timeout_seconds = login_timeout_seconds
        self.session = session or requests.Session()
        self.state: dict[str, Any] = self._load_state()

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"login": {}, "contexts": {}, "cursor": ""}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"微信 iLink 状态文件读取失败，将重新登录：{exc}")
            return {"login": {}, "contexts": {}, "cursor": ""}

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def ensure_ready(self, target_user_id: str) -> bool:
        """确保已登录且目标用户已有 contextToken。"""
        if not self.state.get("login", {}).get("bot_token"):
            if not self.login():
                return False

        if target_user_id not in self.state.get("contexts", {}):
            self.get_updates()

        if target_user_id not in self.state.get("contexts", {}):
            logger.warning(
                f"微信 iLink 缺少目标用户上下文：{target_user_id}。"
                "请先让该用户给 bot 发一条消息，再重新运行。"
            )
            return False

        return True

    def request_login_qrcode(self) -> dict[str, str]:
        """请求登录二维码，返回 qrcode 和可直接打开的扫码链接。"""
        resp = self.session.get(
            f"{self.base_login_url}/ilink/bot/get_bot_qrcode?bot_type=3",
            headers=self._login_headers(),
            timeout=35,
        )
        resp.raise_for_status()
        data = resp.json()
        qrcode = str(data.get("qrcode") or "")
        qrcode_url = str(data.get("qrcode_img_content") or qrcode)
        if not qrcode:
            raise RuntimeError(f"微信 iLink 获取二维码失败：{resp.text}")
        return {"qrcode": qrcode, "qrcode_url": qrcode_url}

    def login(self) -> bool:
        """获取二维码并轮询登录状态。"""
        try:
            qr = self.request_login_qrcode()
        except Exception as exc:
            logger.error(f"微信 iLink 获取二维码失败：{exc}")
            return False

        qrcode = qr["qrcode"]
        logger.warning(f"请用微信扫码登录 iLink Bot，二维码内容：{qr['qrcode_url']}")
        return self.poll_login_qrcode(qrcode)

    def poll_login_qrcode(self, qrcode: str) -> bool:
        """轮询已展示二维码的登录状态，成功后保存登录态。"""
        deadline = time.time() + self.login_timeout_seconds
        current_base_url = self.base_login_url
        refresh_count = 0
        while time.time() < deadline:
            try:
                status_resp = self.session.get(
                    f"{current_base_url.rstrip('/')}/ilink/bot/get_qrcode_status",
                    params={"qrcode": qrcode},
                    headers=self._login_headers(),
                    timeout=35,
                )
                status_resp.raise_for_status()
            except requests.RequestException as exc:
                logger.warning(f"微信 iLink 登录状态轮询失败，将继续等待：{exc}")
                time.sleep(2)
                continue
            status = status_resp.json()
            status_text = str(status.get("status") or "").lower()
            if status_text in {"wait", "waiting", "scaned", "scanned"}:
                time.sleep(2)
                continue
            if status_text == "scaned_but_redirect":
                redirect_host = str(status.get("redirect_host") or "").strip()
                if redirect_host:
                    current_base_url = f"https://{redirect_host}"
                time.sleep(2)
                continue
            if status_text == "expired":
                refresh_count += 1
                if refresh_count > 3:
                    logger.error("微信 iLink 登录二维码多次过期")
                    return False
                try:
                    qr = self.request_login_qrcode()
                    qrcode = qr["qrcode"]
                    current_base_url = self.base_login_url
                    logger.warning(f"微信 iLink 二维码已刷新：{qr['qrcode_url']}")
                except Exception as exc:
                    logger.error(f"微信 iLink 刷新二维码失败：{exc}")
                    return False
                continue
            if status_text in {"confirmed", "logged_in"}:
                self.state["login"] = {
                    "bot_token": status.get("bot_token"),
                    "bot_id": status.get("ilink_bot_id"),
                    "user_id": status.get("ilink_user_id"),
                    "base_url": status.get("baseurl"),
                }
                self._save_state()
                logger.info("微信 iLink 登录成功")
                return True

            time.sleep(2)

        logger.error("微信 iLink 登录超时")
        return False

    def get_updates(self) -> list[dict[str, Any]]:
        """拉取消息，并缓存每个用户最新 contextToken。"""
        login = self._require_login()
        payload = {
            "get_updates_buf": self.state.get("cursor", ""),
            "base_info": {"channel_version": self.channel_version},
        }
        data = self._post_business(login, "/ilink/bot/getupdates", payload)
        cursor = data.get("get_updates_buf")
        if cursor is not None:
            self.state["cursor"] = cursor

        messages = data.get("msgs") or []
        contexts = self.state.setdefault("contexts", {})
        for msg in messages:
            from_user_id = msg.get("from_user_id")
            context_token = msg.get("context_token")
            if from_user_id and context_token:
                contexts[from_user_id] = {
                    "context_token": context_token,
                    "message_id": msg.get("message_id"),
                    "create_time_ms": msg.get("create_time_ms"),
                }
        self._save_state()
        return messages

    def send_text(self, to_user_id: str, text: str) -> None:
        """向已建立上下文的用户发送文本消息。"""
        login = self._require_login()
        context = self.state.get("contexts", {}).get(to_user_id)
        if not context or not context.get("context_token"):
            raise RuntimeError(f"missing latest context token for userId={to_user_id}")

        payload = {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": f"superQ-{uuid.uuid4().hex}",
                "message_type": 2,
                "message_state": 2,
                "context_token": context["context_token"],
                "item_list": [{"type": 1, "text_item": {"text": text}}],
            },
            "base_info": {"channel_version": self.channel_version},
        }
        self._post_business(login, "/ilink/bot/sendmessage", payload)

    def _require_login(self) -> dict[str, str]:
        login = self.state.get("login") or {}
        required = ("bot_token", "bot_id", "base_url")
        if not all(login.get(key) for key in required):
            raise RuntimeError("wechat ilink is not logged in")
        return login

    def _post_business(
        self,
        login: dict[str, str],
        path: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        base_url = str(login["base_url"]).rstrip("/")
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        headers = self._business_headers(login["bot_token"], len(body.encode("utf-8")))
        resp = self.session.post(
            f"{base_url}{path}",
            data=body,
            headers=headers,
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        ret = data.get("ret", 0)
        errcode = data.get("errcode", 0)
        if ret == -14 or errcode == -14:
            raise RuntimeError("wechat ilink session expired")
        if ret not in (0, None) or errcode not in (0, None):
            raise RuntimeError(f"wechat ilink api failed: ret={ret}, errcode={errcode}")
        return data

    def _business_headers(self, bot_token: str, content_length: int) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "Authorization": f"Bearer {bot_token}",
            "X-WECHAT-UIN": str(int(time.time() * 1000) % 9000000000 + 1000000000),
            "Content-Length": str(content_length),
        }
        if self.route_tag:
            headers["SKRouteTag"] = self.route_tag
        return headers

    def _login_headers(self) -> dict[str, str]:
        headers = {
            "iLink-App-Id": "bot",
            "iLink-App-ClientVersion": ILINK_APP_CLIENT_VERSION,
        }
        if self.route_tag:
            headers["SKRouteTag"] = self.route_tag
        return headers


class WechatIlinkNotifier:
    """策略结果微信 iLink 文本推送器。"""

    def __init__(self, settings: Settings, client: WechatIlinkClient | None = None) -> None:
        self.settings = settings
        self.client = client or WechatIlinkClient(
            state_path=settings.wechat_ilink_state_path,
            channel_version=settings.wechat_ilink_channel_version,
            route_tag=settings.wechat_ilink_route_tag,
            login_timeout_seconds=settings.wechat_ilink_login_timeout_seconds,
        )

    def send(
        self,
        symbols: list[str],
        strategy_name: str,
        account_summary: str = "",
        news_summary: str = "",
    ) -> None:
        if not self.settings.wechat_ilink_enabled:
            return
        target_user_id = self.settings.wechat_ilink_target_user_id
        if not target_user_id:
            logger.warning("微信 iLink 已启用，但未配置 WECHAT_ILINK_TARGET_USER_ID")
            return
        if not self.client.ensure_ready(target_user_id):
            return

        self._refresh_context()
        self._send_keepalive_reminder_if_needed(target_user_id)
        text = self._format_text(symbols, strategy_name, account_summary, news_summary)
        try:
            self.client.send_text(target_user_id, text)
            logger.info(f"微信 iLink 推送成功，共 {len(symbols)} 只股票")
        except Exception as exc:
            if not self._should_retry_after_send_error(exc):
                logger.error(f"微信 iLink 推送失败：{exc}")
                return
            logger.warning(f"微信 iLink 推送疑似上下文失效，刷新上下文后重试：{exc}")
            self._refresh_context()
            try:
                self.client.send_text(target_user_id, text)
                logger.info(f"微信 iLink 推送成功，共 {len(symbols)} 只股票")
            except Exception as retry_exc:
                logger.error(
                    "微信 iLink 推送失败，上下文可能已失效。"
                    f"请给 bot 回复任意消息后重试：{retry_exc}"
                )

    def _format_text(
        self,
        symbols: list[str],
        strategy_name: str,
        account_summary: str = "",
        news_summary: str = "",
    ) -> str:
        today = date.today().strftime("%Y-%m-%d")
        stock_names = self._load_stock_names()
        symbol_text = "\n".join(
            f"{self._format_symbol(symbol)} {self._stock_name(stock_names, symbol)}"
            for symbol in symbols
        ) if symbols else "无选股结果"
        text = (
            f"superQ 选股播报\n"
            f"日期：{today}\n"
            f"策略：{strategy_name}\n"
            f"选股数量：{len(symbols)}\n"
            f"选股列表：{symbol_text}"
        )
        if news_summary:
            text = f"{text}\n\n{news_summary}"
        if account_summary:
            text = f"{text}\n\n掘金账户：\n{account_summary}"
        return text

    def _refresh_context(self) -> None:
        try:
            self.client.get_updates()
        except Exception as exc:
            logger.warning(f"微信 iLink 刷新上下文失败，将继续使用本地缓存：{exc}")

    def _send_keepalive_reminder_if_needed(self, target_user_id: str) -> None:
        if not self.settings.wechat_ilink_context_keepalive_enabled:
            return
        state = getattr(self.client, "state", None)
        if not isinstance(state, dict):
            return
        context = state.get("contexts", {}).get(target_user_id, {})
        if not isinstance(context, dict):
            return
        create_time_ms = int(context.get("create_time_ms") or 0)
        if not self._is_context_stale(create_time_ms):
            return

        today = date.today().strftime("%Y-%m-%d")
        reminders = state.setdefault("keepalive_reminders", {})
        if reminders.get(target_user_id) == today:
            return

        reminder = (
            "微信 iLink 上下文可能即将失效，请回复任意内容完成保活。"
            "回复后下次推送会自动刷新 contextToken。"
        )
        try:
            self.client.send_text(target_user_id, reminder)
            reminders[target_user_id] = today
            save_state = getattr(self.client, "_save_state", None)
            if callable(save_state):
                save_state()
            logger.info("微信 iLink 保活提醒已发送")
        except Exception as exc:
            logger.warning(f"微信 iLink 保活提醒发送失败，可能已失效：{exc}")

    def _is_context_stale(self, create_time_ms: int) -> bool:
        threshold_hours = self.settings.wechat_ilink_context_reminder_hours
        if threshold_hours <= 0:
            return False
        age_ms = int(time.time() * 1000) - create_time_ms
        return age_ms >= threshold_hours * 60 * 60 * 1000

    def _should_retry_after_send_error(self, exc: Exception) -> bool:
        if not self.settings.wechat_ilink_retry_on_context_error:
            return False
        message = str(exc).lower()
        return any(
            keyword in message
            for keyword in (
                "context",
                "token",
                "session expired",
                "missing latest context",
                "ret=-14",
                "errcode=-14",
            )
        )

    def _load_stock_names(self) -> dict[str, str]:
        db_path = Path(self.settings.db_path)
        if not db_path.exists():
            return {}
        try:
            with sqlite3.connect(db_path) as conn:
                rows = conn.execute("SELECT symbol, name FROM stock_names").fetchall()
        except sqlite3.Error:
            return {}
        return {str(symbol): str(name) for symbol, name in rows}

    def _stock_name(self, stock_names: dict[str, str], symbol: str) -> str:
        display_code = self._format_symbol(symbol)
        for key in (symbol, display_code, self._lookup_symbol_key(symbol)):
            if key in stock_names:
                return stock_names[key]
        return "未知"

    def _lookup_symbol_key(self, symbol: str) -> str:
        if "." not in symbol:
            return symbol
        left, right = symbol.split(".", 1)
        if left in {"SHSE", "SZSE", "BJSE"}:
            return right
        return symbol

    def _format_symbol(self, symbol: str) -> str:
        if "." not in symbol:
            if len(symbol) == 6 and symbol.isdigit():
                if symbol.startswith("6"):
                    return f"{symbol}.SH"
                if symbol.startswith(("4", "8")):
                    return f"{symbol}.BJ"
                return f"{symbol}.SZ"
            return symbol
        left, right = symbol.split(".", 1)
        exchange_suffix = {"SHSE": "SH", "SZSE": "SZ", "BJSE": "BJ"}
        if left in exchange_suffix:
            return f"{right}.{exchange_suffix[left]}"
        return symbol
