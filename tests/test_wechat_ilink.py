"""微信 iLink 通知最小版测试。"""

import json
import sqlite3
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from sequoia_x.core.config import Settings
from sequoia_x.notify.wechat_ilink import WechatIlinkClient, WechatIlinkNotifier


def make_settings(tmp_path: Path, **kwargs: object) -> Settings:
    data = {
        "db_path": str(tmp_path / "test.db"),
        "start_date": "2024-01-01",
        "feishu_webhook_url": "https://example.com/hook",
    }
    data.update(kwargs)
    return Settings(**data)


def test_disabled_wechat_notifier_does_not_send(tmp_path: Path) -> None:
    """未启用微信 iLink 时，通知器不应发送网络请求。"""
    settings = make_settings(tmp_path, wechat_ilink_enabled=False)
    client = MagicMock()

    notifier = WechatIlinkNotifier(settings=settings, client=client)
    notifier.send(["300054"], "TestStrategy")

    client.send_text.assert_not_called()


def test_wechat_notifier_formats_strategy_result_text(tmp_path: Path) -> None:
    """微信 iLink 通知器应把策略结果格式化为纯文本并发送给目标用户。"""
    settings = make_settings(
        tmp_path,
        wechat_ilink_enabled=True,
        wechat_ilink_target_user_id="abc@im.wechat",
    )
    client = MagicMock()
    client.ensure_ready.return_value = True

    notifier = WechatIlinkNotifier(settings=settings, client=client)
    notifier.send(
        ["300054", "000007"],
        "NewsConfirmStrategy",
        account_summary="账户余额：88000.50",
        news_summary="新闻确认：\n300054 综合分 80",
    )

    client.send_text.assert_called_once()
    assert client.send_text.call_args.args[0] == "abc@im.wechat"
    text = client.send_text.call_args.args[1]
    assert "superQ 选股播报" in text
    assert "NewsConfirmStrategy" in text
    assert "300054" in text
    assert "000007" in text
    assert "账户余额：88000.50" in text
    assert "300054 综合分 80" in text


def test_wechat_notifier_uses_template_with_stock_names(tmp_path: Path) -> None:
    """微信推送应按 template.md 展示逐行选股代码和名称。"""
    db_path = tmp_path / "test.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE stock_names (symbol TEXT PRIMARY KEY, name TEXT NOT NULL, keywords TEXT, updated_at TEXT)"
        )
        conn.executemany(
            "INSERT INTO stock_names (symbol, name, keywords, updated_at) VALUES (?, ?, ?, ?)",
            [
                ("300054", "鼎龙股份", "鼎龙股份,300054", "2026-04-25"),
                ("000333", "美的集团", "美的集团,000333", "2026-04-25"),
            ],
        )
    settings = make_settings(
        tmp_path,
        db_path=str(db_path),
        wechat_ilink_enabled=True,
        wechat_ilink_target_user_id="abc@im.wechat",
    )
    client = MagicMock()
    client.ensure_ready.return_value = True

    notifier = WechatIlinkNotifier(settings=settings, client=client)
    notifier.send(["SZSE.300054", "000333"], "WechatPushTest", account_summary="持仓信息：无")

    text = client.send_text.call_args.args[1]
    assert "superQ 选股播报" in text
    assert "策略：WechatPushTest" in text
    assert "选股列表：" in text
    assert "300054.SZ 鼎龙股份" in text
    assert "000333.SZ 美的集团" in text
    assert "掘金账户：" in text


def test_wechat_notifier_sends_keepalive_reminder_for_stale_context(tmp_path: Path) -> None:
    """上下文长期未互动时，应先推送保活提醒并记录当天已提醒。"""
    settings = make_settings(
        tmp_path,
        wechat_ilink_enabled=True,
        wechat_ilink_target_user_id="abc@im.wechat",
        wechat_ilink_context_reminder_hours=12,
    )
    client = MagicMock()
    client.ensure_ready.return_value = True
    client.state = {
        "contexts": {
            "abc@im.wechat": {
                "context_token": "ctx",
                "create_time_ms": 0,
            }
        }
    }

    notifier = WechatIlinkNotifier(settings=settings, client=client)
    notifier.send(["000333"], "WechatPushTest")

    assert client.send_text.call_count == 2
    reminder_text = client.send_text.call_args_list[0].args[1]
    assert "上下文可能即将失效" in reminder_text
    assert "请回复任意内容完成保活" in reminder_text
    assert client.state["keepalive_reminders"]["abc@im.wechat"] == date.today().strftime("%Y-%m-%d")
    client._save_state.assert_called()


def test_wechat_notifier_does_not_repeat_keepalive_reminder_same_day(tmp_path: Path) -> None:
    """同一天已提醒过时，不应重复发送保活提醒。"""
    today = date.today().strftime("%Y-%m-%d")
    settings = make_settings(
        tmp_path,
        wechat_ilink_enabled=True,
        wechat_ilink_target_user_id="abc@im.wechat",
        wechat_ilink_context_reminder_hours=12,
    )
    client = MagicMock()
    client.ensure_ready.return_value = True
    client.state = {
        "contexts": {
            "abc@im.wechat": {
                "context_token": "ctx",
                "create_time_ms": 0,
            }
        },
        "keepalive_reminders": {"abc@im.wechat": today},
    }

    notifier = WechatIlinkNotifier(settings=settings, client=client)
    notifier.send(["000333"], "WechatPushTest")

    client.send_text.assert_called_once()
    assert "上下文可能即将失效" not in client.send_text.call_args.args[1]


def test_wechat_notifier_refreshes_context_and_retries_once_when_send_fails(tmp_path: Path) -> None:
    """推送失败疑似上下文失效时，应 getUpdates 后重试一次。"""
    settings = make_settings(
        tmp_path,
        wechat_ilink_enabled=True,
        wechat_ilink_target_user_id="abc@im.wechat",
    )
    client = MagicMock()
    client.ensure_ready.return_value = True
    client.state = {"contexts": {"abc@im.wechat": {"context_token": "ctx"}}}
    client.send_text.side_effect = [RuntimeError("context token expired"), None]

    notifier = WechatIlinkNotifier(settings=settings, client=client)
    notifier.send(["000333"], "WechatPushTest")

    assert client.send_text.call_count == 2
    client.get_updates.assert_called_once()


def test_client_send_text_uses_ilink_payload_and_cached_context(tmp_path: Path) -> None:
    """客户端发送文本时应使用 Java SDK 同款 sendmessage payload 和本地 contextToken。"""
    state_path = tmp_path / "wechat_state.json"
    state_path.write_text(
        json.dumps(
            {
                "login": {
                    "bot_token": "token",
                    "bot_id": "bot@im.bot",
                    "user_id": "owner@im.wechat",
                    "base_url": "https://api.example.com/",
                },
                "contexts": {
                    "abc@im.wechat": {
                        "context_token": "ctx-token",
                        "message_id": "msg-1",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    session = MagicMock()
    session.post.return_value = MagicMock(status_code=200, text='{"ret":0,"errcode":0}')
    session.post.return_value.json.return_value = {"ret": 0, "errcode": 0}

    client = WechatIlinkClient(state_path=str(state_path), session=session)
    client.send_text("abc@im.wechat", "hello")

    url = session.post.call_args.args[0]
    assert url == "https://api.example.com/ilink/bot/sendmessage"
    payload = json.loads(session.post.call_args.kwargs["data"])
    assert payload["base_info"]["channel_version"] == "1.0.0"
    assert payload["msg"]["to_user_id"] == "abc@im.wechat"
    assert payload["msg"]["context_token"] == "ctx-token"
    assert payload["msg"]["item_list"][0]["type"] == 1
    assert payload["msg"]["item_list"][0]["text_item"]["text"] == "hello"
    headers = session.post.call_args.kwargs["headers"]
    assert headers["AuthorizationType"] == "ilink_bot_token"
    assert headers["Authorization"] == "Bearer token"


def test_request_login_qrcode_returns_scan_link(tmp_path: Path) -> None:
    """客户端应可单独获取登录链接，方便先展示给用户扫码。"""
    session = MagicMock()
    session.get.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "qrcode": "qr-token",
            "qrcode_img_content": "https://liteapp.weixin.qq.com/q/test",
        },
    )

    client = WechatIlinkClient(state_path=str(tmp_path / "wechat_state.json"), session=session)

    qr = client.request_login_qrcode()

    assert qr == {
        "qrcode": "qr-token",
        "qrcode_url": "https://liteapp.weixin.qq.com/q/test",
    }


def test_login_handles_redirect_host_before_confirmed(tmp_path: Path) -> None:
    """扫码状态返回 scaned_but_redirect 时，应切换到 redirect_host 继续轮询。"""
    session = MagicMock()
    session.get.side_effect = [
        MagicMock(status_code=200, json=lambda: {"qrcode": "qr", "qrcode_img_content": "qr-url"}),
        MagicMock(status_code=200, json=lambda: {"status": "scaned_but_redirect", "redirect_host": "redirect.example.com"}),
        MagicMock(
            status_code=200,
            json=lambda: {
                "status": "confirmed",
                "bot_token": "token",
                "ilink_bot_id": "bot@im.bot",
                "ilink_user_id": "owner@im.wechat",
                "baseurl": "https://api.example.com",
            },
        ),
    ]
    client = WechatIlinkClient(
        state_path=str(tmp_path / "wechat_state.json"),
        login_timeout_seconds=30,
        session=session,
    )

    with patch("time.sleep", return_value=None):
        assert client.login() is True

    assert session.get.call_args_list[2].args[0].startswith(
        "https://redirect.example.com/ilink/bot/get_qrcode_status"
    )


def test_poll_login_qrcode_saves_confirmed_credentials(tmp_path: Path) -> None:
    """已展示的 qrcode 被确认后，应保存登录凭证到状态文件。"""
    session = MagicMock()
    session.get.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "status": "confirmed",
            "bot_token": "token",
            "ilink_bot_id": "bot@im.bot",
            "ilink_user_id": "owner@im.wechat",
            "baseurl": "https://api.example.com",
        },
    )
    state_path = tmp_path / "wechat_state.json"
    client = WechatIlinkClient(
        state_path=str(state_path),
        login_timeout_seconds=30,
        session=session,
    )

    assert client.poll_login_qrcode("qr-token") is True
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["login"]["bot_token"] == "token"
    assert state["login"]["bot_id"] == "bot@im.bot"


def test_login_polling_timeout_does_not_crash(tmp_path: Path) -> None:
    """扫码登录轮询遇到单次网络超时时应返回失败而不是崩溃。"""
    session = MagicMock()
    session.get.side_effect = [
        MagicMock(status_code=200, json=lambda: {"qrcode": "qr", "qrcode_img_content": "qr-url"}),
        requests.ReadTimeout("poll timeout"),
        MagicMock(
            status_code=200,
            json=lambda: {
                "status": "CONFIRMED",
                "bot_token": "token",
                "ilink_bot_id": "bot@im.bot",
                "ilink_user_id": "owner@im.wechat",
                "baseurl": "https://api.example.com",
            },
        ),
    ]

    client = WechatIlinkClient(
        state_path=str(tmp_path / "wechat_state.json"),
        login_timeout_seconds=30,
        session=session,
    )

    with patch("time.sleep", return_value=None):
        assert client.login() is True
