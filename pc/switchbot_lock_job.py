"""5分ごとに SwitchBot Smart Lock を確認し、解錠中なら Slack へ通知する。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

SWITCHBOT_API_SUCCESS_STATUS = 100
DEFAULT_API_BASE_URL = "https://api.switch-bot.com"
DEFAULT_INTERVAL_SECONDS = 300


class SwitchBotError(Exception):
    """SwitchBot API 呼び出しに関するエラー。"""


def _build_sign(token: str, t_ms: int, nonce: str, secret: str) -> str:
    string_to_sign = f"{token}{t_ms}{nonce}".encode("utf-8")
    digest = hmac.new(
        secret.encode("utf-8"),
        msg=string_to_sign,
        digestmod=hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def _auth_headers(token: str, secret: str) -> dict[str, str]:
    t_ms = int(time.time() * 1000)
    nonce = str(uuid.uuid4())
    sign = _build_sign(token, t_ms, nonce, secret)
    return {
        "Authorization": token,
        "Content-Type": "application/json",
        "charset": "utf8",
        "t": str(t_ms),
        "sign": sign,
        "nonce": nonce,
    }


def _request_json(
    method: str,
    path: str,
    *,
    token: str,
    secret: str,
    api_base_url: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = api_base_url.rstrip("/") + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers=_auth_headers(token, secret),
        method=method,
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raise SwitchBotError(f"SwitchBot HTTP エラー: {e.code}") from e
    except urllib.error.URLError as e:
        raise SwitchBotError(f"SwitchBot 接続エラー: {e.reason}") from e

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SwitchBotError(f"JSON 解析に失敗しました: {raw[:200]}") from e

    if payload.get("statusCode") != SWITCHBOT_API_SUCCESS_STATUS:
        raise SwitchBotError(payload.get("message", "SwitchBot API エラー"))

    return payload


def _send_lock_slack_alert(
    webhook_url: str,
    mention_user_id: str,
    lock_state: Any,
    door_state: Any,
    battery: Any,
) -> None:
    status_parts = [f"`lockState: {lock_state}`"]
    if door_state is not None:
        status_parts.append(f"`doorState: {door_state}`")
    if battery is not None:
        status_parts.append(f"`battery: {battery} %`")
    status = "\n".join(status_parts)

    text = (
        f"<@{mention_user_id}> :rotating_light: :door: :warning: "
        f"*警告: 鍵が解錠されています* :lock:\n{status}"
    )
    payload = {
        "text": text,
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        ],
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30):
        pass


def lock_check(
    *,
    token: str,
    secret: str,
    webhook_url: str,
    device_id: str,
    mention_user_id: str,
    api_base_url: str,
) -> None:
    path = f"/v1.1/devices/{device_id}/status"
    response = _request_json(
        "GET",
        path,
        token=token,
        secret=secret,
        api_base_url=api_base_url,
    )
    body = response.get("body", {})
    lock_state = body.get("lockState")
    door_state = body.get("doorState")
    battery = body.get("battery")

    if lock_state == "unlocked":
        _send_lock_slack_alert(
            webhook_url,
            mention_user_id,
            lock_state,
            door_state,
            battery,
        )


def _configured(secrets: Any) -> bool:
    token = str(getattr(secrets, "SWITCHBOT_TOKEN", "") or "").strip()
    secret = str(getattr(secrets, "SWITCHBOT_CLIENT_SECRET", "") or "").strip()
    webhook = str(getattr(secrets, "SLACK_WEBHOOK_URL", "") or "").strip()
    device_id = str(getattr(secrets, "SWITCHBOT_LOCK_DEVICE_ID", "") or "").strip()
    mention = str(getattr(secrets, "SLACK_MENTION_USER_ID", "") or "").strip()
    return bool(token and secret and webhook and device_id and mention)


def lock_check_loop(secrets: Any) -> None:
    if not _configured(secrets):
        return

    token = str(secrets.SWITCHBOT_TOKEN).strip()
    secret = str(secrets.SWITCHBOT_CLIENT_SECRET).strip()
    webhook_url = str(secrets.SLACK_WEBHOOK_URL).strip()
    device_id = str(secrets.SWITCHBOT_LOCK_DEVICE_ID).strip()
    mention_user_id = str(secrets.SLACK_MENTION_USER_ID).strip()
    api_base_url = str(
        getattr(secrets, "SWITCHBOT_API_BASE_URL", DEFAULT_API_BASE_URL)
        or DEFAULT_API_BASE_URL
    ).strip()
    interval = int(
        getattr(secrets, "LOCK_CHECK_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS)
        or DEFAULT_INTERVAL_SECONDS
    )

    while True:
        try:
            lock_check(
                token=token,
                secret=secret,
                webhook_url=webhook_url,
                device_id=device_id,
                mention_user_id=mention_user_id,
                api_base_url=api_base_url,
            )
        except Exception:
            pass
        time.sleep(interval)


def start_lock_check_thread(secrets: Any) -> None:
    """設定があるときだけ、鍵監視をデーモン thread で開始する。"""
    if not _configured(secrets):
        return
    threading.Thread(
        target=lock_check_loop,
        args=(secrets,),
        daemon=True,
        name="switchbot-lock-check",
    ).start()
