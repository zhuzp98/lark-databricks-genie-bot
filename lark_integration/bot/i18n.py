"""Lightweight zh/en copy for Lark bot UX (not Genie answer content)."""

from __future__ import annotations

import re
from typing import Any, Literal

Lang = Literal["zh", "en"]

_CJK = re.compile(r"[\u4e00-\u9fff]")

# Prefer Chinese when ambiguous (product started zh); English when no CJK.
DEFAULT_LANG: Lang = "zh"


def detect_lang(text: str | None) -> Lang:
    """Heuristic: any CJK → zh; otherwise en (covers /bind, reset, English Qs)."""
    t = (text or "").strip()
    if not t:
        return DEFAULT_LANG
    if _CJK.search(t):
        return "zh"
    return "en"


_MESSAGES: dict[str, dict[Lang, str]] = {
    "bind_missing_app_url": {
        "zh": "请先绑定 Databricks，但 APP_PUBLIC_URL 未配置：{error}",
        "en": "Please connect Databricks first, but APP_PUBLIC_URL is not set: {error}",
    },
    "bind_email_known": {
        "zh": "当前识别邮箱：{email}\n",
        "en": "Detected email: {email}\n",
    },
    "bind_email_unknown": {
        "zh": (
            "未能从 Lark 读取邮箱（需权限 contact:user.email:readonly）。\n"
            "打开链接后将使用你登录 Databricks 的邮箱完成绑定。\n"
            "也可先发送：绑定 your@email.com\n"
        ),
        "en": (
            "Could not read your email from Lark (needs contact:user.email:readonly).\n"
            "Opening the link will use the email of your Databricks login.\n"
            "Or send: bind your@email.com\n"
        ),
    },
    "bind_prompt": {
        "zh": (
            "请先绑定 Databricks（按用户身份调用 Genie）。\n"
            "{email_line}"
            "请复制下面整段链接到浏览器打开（需已登录 Databricks；路径必须含 /bind）：\n"
            "{url}\n\n"
            "或先打开 App 首页同意授权：{home}/\n"
            "绑定成功页应显示 Token scopes 含 genie。Token 约 55 分钟有效。"
        ),
        "en": (
            "Please connect your Lark to Databricks (Genie runs as your user).\n"
            "{email_line}"
            "Copy and open this full URL in a browser (must be logged into Databricks; path must include /bind):\n"
            "{url}\n\n"
            "Or open the App home first to consent: {home}/\n"
            "The success page should list Token scopes including genie. Token lasts ~55 minutes."
        ),
    },
    "bind_usage": {
        "zh": "用法：绑定 your@email.com（需与 Databricks 登录邮箱一致）",
        "en": "Usage: bind your@email.com (must match your Databricks login email)",
    },
    "bind_mapped_valid": {
        "zh": "已关联 {email}，且 Databricks token 有效（约 {expires_in}s）。可直接提问。",
        "en": "Linked {email}; Databricks token is valid (~{expires_in}s). You can ask now.",
    },
    "missing_genie_scope": {
        "zh": (
            "当前绑定的 Databricks token 缺少 genie scope（Genie One MCP 会 403）。\n"
            "token scopes: {scopes}\n\n"
            "请先打开 App 首页同意 User authorization（需含 genie），再重新绑定："
        ),
        "en": (
            "Your Databricks token is missing the genie scope (Genie One MCP returns 403).\n"
            "token scopes: {scopes}\n\n"
            "Open the App home, accept User authorization (including genie), then connect again:"
        ),
    },
    "thinking": {
        "zh": "正在通过 {agent} 查询（{email}），请稍候…",
        "en": "Thinking via {agent} for {email}, please wait…",
    },
    "genie_failed": {
        "zh": "Genie 调用失败：{error}",
        "en": "Genie request failed: {error}",
    },
    "reset_ok": {
        "zh": "会话已重置。当前 Agent：{agent}。直接提问，或点 Switch 更换。",
        "en": "Conversation reset. Current agent: {agent}. Ask a question, or tap Switch to change.",
    },
    "switched_one": {
        "zh": "已切换：现在使用 Genie One (Ontology)。请直接提问。",
        "en": "Switched to Genie One (Ontology). Go ahead and ask.",
    },
    "switched_space": {
        "zh": "已切换：现在使用 Genie Agent：{title}。请直接提问。",
        "en": "Switched to Genie Agent: {title}. Go ahead and ask.",
    },
    "invalid_space": {
        "zh": "无效的 Space 选择。",
        "en": "Invalid Space selection.",
    },
    "file_saved": {
        "zh": "已收到文件，已保存：`{path}`",
        "en": "File received and saved: `{path}`",
    },
    "file_failed": {
        "zh": "文件接收失败：{error}",
        "en": "Failed to receive file: {error}",
    },
    "unsupported": {
        "zh": "暂只支持文本提问、文件，以及 Bot Menu / 选 Agent 卡片。",
        "en": "Only text questions, files, and Bot Menu / agent picker cards are supported.",
    },
    "unknown_menu": {
        "zh": "未知菜单：{key}（请配置 event_key=reset|switch|bind）",
        "en": "Unknown menu: {key} (configure event_key=reset|switch|bind)",
    },
    "toast_no_chat": {
        "zh": "无法识别会话",
        "en": "Cannot identify chat",
    },
    "toast_reset": {
        "zh": "会话已重置",
        "en": "Conversation reset",
    },
    "toast_pick": {
        "zh": "请选择 Agent",
        "en": "Pick an agent",
    },
    "toast_switched": {
        "zh": "已切换 Agent",
        "en": "Agent switched",
    },
    "toast_unknown": {
        "zh": "未知操作: {act}",
        "en": "Unknown action: {act}",
    },
    "picker_current": {
        "zh": "当前：**{label}**\n\n",
        "en": "Current: **{label}**\n\n",
    },
    "picker_body": {
        "zh": (
            "请选择问答引擎：\n\n"
            "- **Genie One**：跨工作区 Ontology 自动路由（推荐）\n"
            "- 或指定某个 Genie Agent / Space"
        ),
        "en": (
            "Choose a Q&A engine:\n\n"
            "- **Genie One**: workspace Ontology auto-routing (recommended)\n"
            "- Or a specific Genie Agent / Space"
        ),
    },
    "picker_title": {
        "zh": "选择 Genie Agent",
        "en": "Choose Genie Agent",
    },
}


def t(key: str, lang: Lang | str = DEFAULT_LANG, **kwargs: Any) -> str:
    lang_n: Lang = "en" if lang == "en" else "zh"
    template = _MESSAGES.get(key, {}).get(lang_n) or _MESSAGES.get(key, {}).get("zh") or key
    try:
        return template.format(**kwargs)
    except KeyError:
        return template
