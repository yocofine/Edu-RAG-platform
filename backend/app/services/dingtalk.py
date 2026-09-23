from __future__ import annotations

import base64
import hashlib
import hmac
import html
import logging
import re
import time
from urllib.parse import quote_plus

import httpx

from ..config import get_settings
from .system_settings import get_dingtalk_settings


logger = logging.getLogger(__name__)
TAG_RE = re.compile(r"<[^>]+>")

# 钉钉 markdown 支持 <font color> 标签（实测：群里按该颜色渲染），所以推送到钉钉时
# 必须保留颜色标签；其余 HTML 标签照旧剥离，避免标签原文漏进消息。
FONT_TAG_RE = re.compile(r"<font\s+color=[\x22][^\x22]*[\x22]\s*>|</font>", re.IGNORECASE)
# 钉钉 markdown 支持 **加粗** 与 *斜体*，所以推送时把编辑器产生的 <b>/<strong>、
# <i>/<em> 以及对应的 span style 形式，转换成 markdown 语法。
BOLD_TAG_RE = re.compile(r"</?(?:b|strong)\s*>", re.IGNORECASE)
ITALIC_TAG_RE = re.compile(r"</?(?:i|em)\s*>", re.IGNORECASE)
# 部分浏览器 execCommand 产出 <span style="color: ...">，统一改写成 <font color>。
# Chrome 在同段文字上同时应用加粗与颜色时，会把多个样式合并进同一个 style 属性，
# 所以必须一次性解析整个 style，逐条单独匹配会漏掉其它样式（曾导致"只有颜色没有加粗"）。
SPAN_STYLE_RE = re.compile(
    r"<span[^>]*style=[\x22](?P<style>[^\x22]*)[\x22][^>]*>(?P<text>.*?)</span>",
    re.IGNORECASE | re.DOTALL,
)
SPAN_COLOR_VALUE_RE = re.compile(r"color:\s*(#[0-9a-fA-F]{3,6}|rgb\([^)]*\))", re.IGNORECASE)
SPAN_BOLD_VALUE_RE = re.compile(r"font-weight:\s*(?:bold|[6-9]00)", re.IGNORECASE)
SPAN_ITALIC_VALUE_RE = re.compile(r"font-style:\s*italic", re.IGNORECASE)


def _span_to_rich_text(match: re.Match) -> str:
    """把 span 的 color / font-weight / font-style 一次性翻译成钉钉可渲染的形式。"""
    style = match.group("style")
    text = match.group("text")
    if SPAN_BOLD_VALUE_RE.search(style):
        text = f"**{text}**"
    if SPAN_ITALIC_VALUE_RE.search(style):
        text = f"*{text}*"
    color = SPAN_COLOR_VALUE_RE.search(style)
    if color:
        text = f'<font color="{_normalize_color(color.group(1))}">{text}</font>'
    return text


def _normalize_color(raw: str) -> str:
    """把 #abc / #aabbcc / rgb(r,g,b) 统一成 #AABBCC，便于钉钉稳定渲染。"""
    value = (raw or "").strip().lower()
    match = re.match(r"^rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)$", value)
    if match:
        return "#%02X%02X%02X" % tuple(int(part) for part in match.groups())
    if re.match(r"^#[0-9a-f]{6}$", value):
        return value.upper()
    if re.match(r"^#[0-9a-f]{3}$", value):
        return ("#" + "".join(char * 2 for char in value[1:])).upper()
    return value


def dingtalk_configured() -> bool:
    config = get_dingtalk_settings()
    return bool(config.get("enabled") and str(config.get("webhook") or "").strip())


def _plain_text(value: str) -> str:
    text = html.unescape(value or "")
    # span 形式统一成 font 形式（钉钉只能稳定渲染 <font color>），且可能同时含
    # color / font-weight / font-style，必须一次性解析，否则会丢样式
    text = SPAN_STYLE_RE.sub(_span_to_rich_text, text)
    # <b>/<strong> -> **文字**，<i>/<em> -> *文字*（都是钉钉支持的 markdown）
    text = BOLD_TAG_RE.sub("**", text)
    text = ITALIC_TAG_RE.sub("*", text)
    # 剥掉其余标签之前，先把颜色标签用私有区占位符暂存，剥完再还原
    kept: list[str] = []

    def _stash(match: re.Match) -> str:
        kept.append(match.group(0))
        return f"\ue000{len(kept) - 1}\ue001"

    text = FONT_TAG_RE.sub(_stash, text)
    text = TAG_RE.sub(" ", text)
    text = re.sub(r"\ue000(\d+)\ue001", lambda match: kept[int(match.group(1))], text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _signed_webhook(webhook: str, secret: str) -> str:
    if not secret:
        return webhook
    timestamp = str(int(time.time() * 1000))
    digest = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}\n{secret}".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    sign = quote_plus(base64.b64encode(digest).decode("utf-8"))
    separator = "&" if "?" in webhook else "?"
    return f"{webhook}{separator}timestamp={timestamp}&sign={sign}"


async def send_rule_notification(
    *,
    title: str,
    content: str,
    group_name: str,
    tags: str,
    operator: str,
) -> dict:
    config = get_dingtalk_settings()
    webhook = str(config.get("webhook") or "").strip()
    if not config.get("enabled") or not webhook:
        raise RuntimeError("尚未配置 DINGTALK_ROBOT_WEBHOOK")

    plain_content = _plain_text(content)[:3500] or "（无正文）"
    tag_text = f"\n\n标签：{tags}" if tags.strip() else ""
    text = (
        f"### 规则通知｜{title}\n\n"
        f"{plain_content}\n\n"
        f"分组：{group_name or '通知规则'}{tag_text}\n\n"
        f"发布人：{operator}"
    )
    payload = {
        "msgtype": "markdown",
        "markdown": {"title": f"规则通知｜{title}", "text": text},
    }
    url = _signed_webhook(webhook, str(config.get("secret") or "").strip())
    timeout = httpx.Timeout(get_settings().dingtalk_robot_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        result = response.json()
    if int(result.get("errcode") or 0) != 0:
        raise RuntimeError(str(result.get("errmsg") or "钉钉机器人推送失败"))
    return result


async def send_test_notification(operator: str) -> dict:
    return await send_rule_notification(
        title="机器人配置测试",
        content="教辅知识库钉钉机器人连接成功。",
        group_name="系统设置",
        tags="测试",
        operator=operator,
    )


async def send_rule_notification_safely(**payload) -> None:
    if not dingtalk_configured():
        logger.warning("规则通知未推送：尚未配置钉钉机器人 Webhook")
        return
    try:
        await send_rule_notification(**payload)
    except Exception:
        logger.exception("规则通知推送钉钉失败：title=%s", payload.get("title"))
