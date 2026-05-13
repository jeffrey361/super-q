"""微信 iLink 登录脚本。"""

from dotenv import load_dotenv

from super_q.core.config import Settings
from super_q.notify.wechat_ilink import WechatIlinkClient


def main() -> None:
    load_dotenv(".env", override=True)
    settings = Settings()
    client = WechatIlinkClient(
        state_path=settings.wechat_ilink_state_path,
        channel_version=settings.wechat_ilink_channel_version,
        route_tag=settings.wechat_ilink_route_tag,
        login_timeout_seconds=480,
    )
    print("开始微信 iLink 登录，请打开输出链接扫码确认")
    ok = client.login()
    print(f"WECHAT_LOGIN_OK={ok}")
    print(f"STATE_PATH={settings.wechat_ilink_state_path}")


if __name__ == "__main__":
    main()
