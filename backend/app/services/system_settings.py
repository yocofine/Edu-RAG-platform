from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from ..config import get_settings
from ..db import SessionLocal
from ..models import SystemSetting


DINGTALK_KEY = "dingtalk_robot"


def _cipher() -> Fernet:
    digest = hashlib.sha256(get_settings().jwt_secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encrypt(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return _cipher().encrypt(raw).decode("ascii")


def _decrypt(value: str) -> dict[str, Any]:
    if not value:
        return {}
    try:
        raw = _cipher().decrypt(value.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (InvalidToken, ValueError, json.JSONDecodeError):
        return {}


def get_dingtalk_settings() -> dict[str, Any]:
    with SessionLocal() as db:
        item = db.get(SystemSetting, DINGTALK_KEY)
        stored = _decrypt(item.encrypted_value) if item else {}
    env = get_settings()
    webhook = str(stored.get("webhook") or env.dingtalk_robot_webhook or "").strip()
    secret = str(stored.get("secret") or env.dingtalk_robot_secret or "").strip()
    enabled = bool(stored.get("enabled", bool(webhook)))
    return {"enabled": enabled, "webhook": webhook, "secret": secret}


def save_dingtalk_settings(
    *,
    enabled: bool,
    webhook: str,
    secret: str,
    updated_by: int,
) -> dict[str, Any]:
    current = get_dingtalk_settings()
    payload = {
        "enabled": bool(enabled),
        "webhook": webhook.strip() or current.get("webhook", ""),
        "secret": secret.strip() or current.get("secret", ""),
    }
    with SessionLocal() as db:
        item = db.get(SystemSetting, DINGTALK_KEY)
        if item is None:
            item = SystemSetting(key=DINGTALK_KEY)
            db.add(item)
        item.encrypted_value = _encrypt(payload)
        item.updated_by = updated_by
        db.commit()
    return payload
