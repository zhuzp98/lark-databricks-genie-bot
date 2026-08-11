"""Lark long-connection bot: Genie One (default) + optional Space agents.

Features:
  - Default engine: Genie One MCP (Ontology routing)
  - Genie answers as Card JSON 2.0 markdown (renders tables/headings)
  - Multi-card split when >4 markdown tables (avoids Lark 11310)
  - Reset / Switch via Bot Menu (application.bot.menu_v6) + text commands
  - Agent picker card only when switching agent
  - Fallback per-space Conversation API when a Space is selected

Events required in Developer Console (persistent connection):
  - im.message.receive_v1
  - application.bot.menu_v6
  - card.action.trigger  (agent picker buttons)
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lark_integration.bot.cards import agent_picker_card
from lark_integration.bot.dbx_auth import list_genie_spaces
from lark_integration.bot.file_ingest import ingest_inbound_file
from lark_integration.bot.genie_client import GenieClient
from lark_integration.bot.genie_one_client import GenieOneClient
from lark_integration.bot.i18n import DEFAULT_LANG, Lang, detect_lang, t
from lark_integration.bot.lark_user import LarkEmailError, email_for_open_id
from lark_integration.bot.obo_bind import (
    app_public_url,
    build_bind_url,
    has_genie_scope,
    jwt_scopes,
)
from lark_integration.bot.reply_format import format_one_reply, format_space_reply
from lark_integration.bot.session_store import Session, SessionStore
from lark_integration.bot.user_token_store import STORE
from lark_integration.bridge.auth import load_lark_credentials
from lark_integration.bridge.im_send import send_card, send_file, send_text


def _list_spaces(profile: str) -> list[dict[str, str]]:
    return list_genie_spaces(profile)


def _text_from_message(content: str, message_type: str) -> str | None:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return content
    if message_type == "text":
        return payload.get("text")
    if message_type == "post":
        return json.dumps(payload, ensure_ascii=False)[:2000]
    return None


def _parse_card_value(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"action": raw}
    return {}


class BotRuntime:
    def __init__(self, profile: str, inbound_dir: str):
        self.profile = profile
        self.inbound_dir = inbound_dir
        self.sessions = SessionStore()
        self._seen_message_ids: dict[str, float] = {}
        self._lang_by_open_id: dict[str, Lang] = {}
        try:
            self.spaces = _list_spaces(profile)
        except Exception as e:
            print(f"[warn] list-spaces failed: {e}")
            self.spaces = []

    def remember_lang(self, open_id: str, lang: Lang) -> None:
        if open_id:
            self._lang_by_open_id[open_id] = lang

    def lang_for(self, open_id: str, text: str | None = None) -> Lang:
        if text is not None and str(text).strip():
            lang = detect_lang(text)
            self.remember_lang(open_id, lang)
            return lang
        return self._lang_by_open_id.get(open_id, DEFAULT_LANG)

    def seen_message(self, message_id: str, ttl_sec: float = 600.0) -> bool:
        """Return True if this Lark message_id was already handled (dedupe)."""
        import time

        now = time.time()
        expired = [k for k, ts in self._seen_message_ids.items() if now - ts > ttl_sec]
        for k in expired:
            self._seen_message_ids.pop(k, None)
        if message_id in self._seen_message_ids:
            return True
        self._seen_message_ids[message_id] = now
        return False

    def resolve_target(self, open_id: str, chat_id: str | None = None) -> tuple[str, str]:
        """Return (receive_id, receive_id_type). Menu events often lack chat_id."""
        if chat_id:
            return chat_id, "chat_id"
        known = self.sessions.chat_for_open_id(open_id)
        if known:
            return known, "chat_id"
        # P2P: address user by open_id until we see a real chat_id
        return open_id, "open_id"

    def session_chat_id(self, open_id: str, chat_id: str | None = None) -> str:
        """Canonical chat key for SessionStore."""
        if chat_id:
            return chat_id
        return self.sessions.chat_for_open_id(open_id) or open_id

    def ensure_session(self, chat_id: str, open_id: str) -> Session:
        sess = self.sessions.get(chat_id, open_id)
        if sess:
            return sess
        # Migrate menu/open_id-keyed session into real chat_id
        alt = self.sessions.get(open_id, open_id)
        if alt and chat_id != open_id:
            return self.sessions.put(
                chat_id,
                open_id,
                mode=alt.mode,
                conversation_id=alt.conversation_id,
                space_id=alt.space_id,
                space_title=alt.space_title,
            )
        return self.sessions.put(chat_id, open_id, mode="genie_one", conversation_id="")

    def _notify(self, open_id: str, text: str, chat_id: str | None = None) -> None:
        rid, rtype = self.resolve_target(open_id, chat_id)
        send_text(rid, text, receive_id_type=rtype)

    def resolve_email(self, open_id: str) -> str:
        return email_for_open_id(open_id)

    def bind_message(self, open_id: str, email: str = "", lang: Lang | None = None) -> str:
        lang = lang or self.lang_for(open_id)
        try:
            url = build_bind_url(open_id=open_id, email=email or "")
        except RuntimeError as e:
            return t("bind_missing_app_url", lang, error=e)
        email_line = (
            t("bind_email_known", lang, email=email)
            if email
            else t("bind_email_unknown", lang)
        )
        home = app_public_url() or "(APP_PUBLIC_URL unset)"
        return t("bind_prompt", lang, email_line=email_line, url=url, home=home)

    def ensure_user_token(self, open_id: str) -> tuple[str, str] | None:
        """Return (email, access_token) or None if user must bind.

        Prefers open_id → token (works even when Lark Contact hides email).
        """
        entry = STORE.get_for_open_id(open_id)
        if entry:
            return entry.email, entry.access_token

        try:
            email = self.resolve_email(open_id)
        except LarkEmailError:
            return None
        entry = STORE.get(email)
        if entry:
            STORE.remember_open_id(open_id, email)
            return email, entry.access_token
        return None

    def send_bind_prompt(
        self, open_id: str, chat_id: str | None = None, lang: Lang | None = None
    ) -> None:
        lang = lang or self.lang_for(open_id)
        email = STORE.email_for_open_id(open_id) or ""
        if not email:
            try:
                email = self.resolve_email(open_id)
            except LarkEmailError:
                email = ""
        self._notify(open_id, self.bind_message(open_id, email, lang=lang), chat_id=chat_id)

    def register_email(
        self, open_id: str, email: str, chat_id: str | None = None, lang: Lang | None = None
    ) -> None:
        """Manual open_id → email mapping when Contact API cannot return email."""
        lang = lang or self.lang_for(open_id)
        email = (email or "").strip().lower()
        if "@" not in email:
            self._notify(open_id, t("bind_usage", lang), chat_id=chat_id)
            return
        STORE.remember_open_id(open_id, email)
        # If already bound under that email, done; else send bind link.
        entry = STORE.get(email)
        if entry:
            self._notify(
                open_id,
                t("bind_mapped_valid", lang, email=email, expires_in=entry.expires_in),
                chat_id=chat_id,
            )
            return
        self._notify(open_id, self.bind_message(open_id, email, lang=lang), chat_id=chat_id)

    def send_picker(
        self, chat_id: str, open_id: str, lang: Lang | None = None
    ) -> None:
        lang = lang or self.lang_for(open_id)
        sess = self.sessions.get(chat_id, open_id)
        label = sess.agent_label if sess else None
        rid, rtype = self.resolve_target(open_id, chat_id)
        send_card(
            rid,
            agent_picker_card(self.spaces, current_label=label, lang=lang),
            receive_id_type=rtype,
        )

    def do_reset(self, chat_id: str, open_id: str, lang: Lang | None = None) -> None:
        lang = lang or self.lang_for(open_id)
        sess = self.sessions.reset_conversation(chat_id, open_id)
        self._notify(
            open_id,
            t("reset_ok", lang, agent=sess.agent_label),
            chat_id=chat_id,
        )

    def do_select_agent(
        self,
        chat_id: str,
        open_id: str,
        value: dict[str, Any],
        lang: Lang | None = None,
    ) -> None:
        lang = lang or self.lang_for(open_id)
        mode = value.get("mode") or "genie_one"
        if mode == "genie_one":
            self.sessions.put(chat_id, open_id, mode="genie_one", conversation_id="")
            self._notify(open_id, t("switched_one", lang), chat_id=chat_id)
            return
        space_id = value.get("space_id")
        title = value.get("title") or space_id
        if not space_id:
            self._notify(open_id, t("invalid_space", lang), chat_id=chat_id)
            return
        self.sessions.put(
            chat_id,
            open_id,
            mode="space",
            conversation_id="",
            space_id=space_id,
            space_title=title,
        )
        self._notify(open_id, t("switched_space", lang, title=title), chat_id=chat_id)

    def ask(self, chat_id: str, open_id: str, question: str) -> None:
        lang = self.lang_for(open_id, question)
        sess = self.ensure_session(chat_id, open_id)
        rid, rtype = self.resolve_target(open_id, chat_id)

        try:
            creds = self.ensure_user_token(open_id)
        except Exception as e:
            send_text(rid, str(e), receive_id_type=rtype)
            return
        if not creds:
            self.send_bind_prompt(open_id, chat_id=chat_id, lang=lang)
            return
        email, user_token = creds
        scopes = jwt_scopes(user_token)
        if not has_genie_scope(scopes):
            STORE.invalidate(email)
            self._notify(
                open_id,
                t(
                    "missing_genie_scope",
                    lang,
                    scopes=", ".join(scopes) or "(empty)",
                ),
                chat_id=chat_id,
            )
            self.send_bind_prompt(open_id, chat_id=chat_id, lang=lang)
            return

        send_text(
            rid,
            t("thinking", lang, agent=sess.agent_label, email=email),
            receive_id_type=rtype,
        )
        from lark_integration.bot.slog import slog

        slog(
            "genie_ask_begin",
            component="lark_ws",
            mode=sess.mode,
            agent=sess.agent_label,
            email=email,
            lang=lang,
            question_len=len(question or ""),
        )
        try:
            if sess.mode == "space" and sess.space_id:
                client = GenieClient(
                    space_id=sess.space_id,
                    token=user_token,
                    profile=self.profile,
                )
                conv = sess.conversation_id or None
                result = client.ask(question, conversation_id=conv)
                self.sessions.put(
                    chat_id,
                    open_id,
                    mode="space",
                    conversation_id=result["conversation_id"],
                    space_id=sess.space_id,
                    space_title=sess.space_title,
                )
                formatted = format_space_reply(result, sess.agent_label, user_identity=True)
            else:
                client = GenieOneClient(token=user_token, profile=self.profile)
                conv = sess.conversation_id or None
                result = client.ask(question, conversation_id=conv)
                self.sessions.put(
                    chat_id,
                    open_id,
                    mode="genie_one",
                    conversation_id=result["conversation_id"],
                )
                formatted = format_one_reply(result, user_identity=True)
            slog(
                "genie_ask_ok",
                component="lark_ws",
                mode=sess.mode,
                email=email,
                conversation_id=(result.get("conversation_id") or "")[:64],
            )
            # Markdown cards (may be multiple if many tables); Menu handles Reset/Switch.
            for card in formatted.get("cards") or [formatted["card"]]:
                try:
                    send_card(rid, card, receive_id_type=rtype)
                except Exception as card_err:
                    err_s = str(card_err)
                    print(f"[card] send failed, fallback to text: {card_err}")
                    if "11310" in err_s or "table number over limit" in err_s or "230099" in err_s:
                        body = (card.get("body") or {}).get("elements") or []
                        texts = [
                            e.get("content", "")
                            for e in body
                            if isinstance(e, dict) and e.get("tag") == "markdown"
                        ]
                        plain = "\n\n".join(t_ for t_ in texts if t_).strip() or err_s
                        for i in range(0, len(plain), 3500):
                            send_text(rid, plain[i : i + 3500], receive_id_type=rtype)
                    else:
                        raise
            if "csv_bytes" in formatted:
                send_file(
                    rid,
                    formatted["csv_bytes"],
                    formatted["csv_name"],
                    receive_id_type=rtype,
                )
        except Exception as e:
            err = str(e)
            from lark_integration.bot.slog import slog

            slog(
                "genie_ask_failed",
                level="error",
                component="lark_ws",
                email=email,
                error=err[:500],
            )
            if (
                "re-bind" in err.lower()
                or "401" in err
                or "403" in err
                or "token" in err.lower()
                or "forbidden" in err.lower()
            ):
                STORE.invalidate(email)
                send_text(rid, t("genie_failed", lang, error=e), receive_id_type=rtype)
                self.send_bind_prompt(open_id, chat_id=chat_id, lang=lang)
            else:
                send_text(rid, t("genie_failed", lang, error=e), receive_id_type=rtype)

def build_handler(runtime: BotRuntime):
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

    def on_message(data: P2ImMessageReceiveV1) -> None:
        event = data.event
        message = event.message
        sender = event.sender
        chat_id = message.chat_id
        open_id = sender.sender_id.open_id if sender and sender.sender_id else ""
        msg_type = message.message_type
        message_id = message.message_id

        if sender and sender.sender_type == "app":
            return

        if message_id and runtime.seen_message(message_id):
            print(f"[dedupe] skip message_id={message_id}")
            return

        if msg_type in {"file", "media", "audio", "video"}:
            lang = runtime.lang_for(open_id)
            try:
                content = json.loads(message.content)
                file_key = content.get("file_key") or content.get("image_key")
                file_name = content.get("file_name") or f"{file_key}.bin"
                path = ingest_inbound_file(
                    message_id=message_id,
                    file_key=file_key,
                    file_name=file_name,
                    volume_path=runtime.inbound_dir,
                    resource_type="file" if msg_type == "file" else "image",
                    chat_id=chat_id,
                )
                send_text(chat_id, t("file_saved", lang, path=path))
            except Exception as e:
                send_text(chat_id, t("file_failed", lang, error=e))
            return

        text = _text_from_message(message.content, msg_type)
        if not text or not text.strip():
            send_text(chat_id, t("unsupported", runtime.lang_for(open_id)))
            return

        lang = runtime.lang_for(open_id, text)
        cmd = text.strip().lower()
        if cmd in {"/reset", "reset", "新对话"}:
            runtime.do_reset(chat_id, open_id, lang=lang)
            return
        if cmd in {"/switch", "switch", "/menu", "menu", "选择", "/agent"}:
            runtime.send_picker(chat_id, open_id, lang=lang)
            return
        # 绑定 / bind [email]
        bind_parts = text.strip().split(maxsplit=1)
        bind_cmd = bind_parts[0].lower().lstrip("/")
        if bind_cmd in {"bind", "绑定", "auth"}:
            if len(bind_parts) > 1 and "@" in bind_parts[1]:
                runtime.register_email(
                    open_id, bind_parts[1].strip(), chat_id=chat_id, lang=lang
                )
            else:
                runtime.send_bind_prompt(open_id, chat_id=chat_id, lang=lang)
            return

        threading.Thread(
            target=runtime.ask,
            args=(chat_id, open_id, text.strip()),
            daemon=True,
            name=f"genie-ask-{message_id or 'x'}",
        ).start()

    def on_message_read(_data) -> None:
        return None

    def on_bot_menu(data) -> None:
        """Bot custom menu: event_key reset / switch (configured in Developer Console)."""
        try:
            event = data.event
            key = (event.event_key or "").strip().lower() if event else ""
            op = event.operator if event else None
            oid = None
            if op and op.operator_id:
                oid = op.operator_id.open_id
            if not oid:
                print(f"[menu] missing open_id key={key}")
                return
            chat_id = runtime.session_chat_id(oid)
            lang = runtime.lang_for(oid)
            print(f"[menu] event_key={key} open_id={oid} chat_id={chat_id}")
            if key in {"reset", "menu_reset"}:
                runtime.ensure_session(chat_id, oid)
                runtime.do_reset(chat_id, oid, lang=lang)
            elif key in {"switch", "menu_switch", "select_agent"}:
                runtime.ensure_session(chat_id, oid)
                runtime.send_picker(chat_id, oid, lang=lang)
            elif key in {"bind", "menu_bind", "auth"}:
                runtime.send_bind_prompt(oid, chat_id=chat_id, lang=lang)
            else:
                runtime._notify(
                    oid, t("unknown_menu", lang, key=key), chat_id=chat_id
                )
        except Exception as e:
            print(f"[menu] handler error: {e}")

    def on_card_action(data):
        from lark_oapi.event.callback.model.p2_card_action_trigger import (
            CallBackToast,
            P2CardActionTriggerResponse,
        )

        try:
            event = data.event
            operator = event.operator
            open_id = operator.open_id if operator else ""
            action = event.action
            value = _parse_card_value(action.value if action else None)
            context = event.context
            chat_id = context.open_chat_id if context else None
            act = value.get("action")
            lang = runtime.lang_for(open_id)
            if not chat_id:
                print(f"[card] missing open_chat_id, value={value}")
                resp = P2CardActionTriggerResponse()
                resp.toast = CallBackToast(
                    {"type": "error", "content": t("toast_no_chat", lang)}
                )
                return resp

            if act == "reset":
                runtime.do_reset(chat_id, open_id, lang=lang)
                toast = t("toast_reset", lang)
            elif act == "switch":
                runtime.send_picker(chat_id, open_id, lang=lang)
                toast = t("toast_pick", lang)
            elif act == "select_agent":
                runtime.do_select_agent(chat_id, open_id, value, lang=lang)
                toast = t("toast_switched", lang)
            else:
                toast = t("toast_unknown", lang, act=act)

            resp = P2CardActionTriggerResponse()
            resp.toast = CallBackToast({"type": "info", "content": toast})
            return resp
        except Exception as e:
            print(f"[card] handler error: {e}")
            resp = P2CardActionTriggerResponse()
            resp.toast = CallBackToast({"type": "error", "content": str(e)[:80]})
            return resp

    return (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_message)
        .register_p2_im_message_message_read_v1(on_message_read)
        .register_p2_application_bot_menu_v6(on_bot_menu)
        .register_p2_card_action_trigger(on_card_action)
        .build()
    )


def _ensure_ssl_certs() -> None:
    if os.environ.get("SSL_CERT_FILE") and os.path.exists(os.environ["SSL_CERT_FILE"]):
        return
    try:
        import certifi

        os.environ["SSL_CERT_FILE"] = certifi.where()
        os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
    except Exception:
        pass


def main() -> None:
    from lark_integration.bot.runtime_lock import RuntimeLock
    from lark_integration.bot.slog import slog

    _ensure_ssl_certs()
    lock = RuntimeLock()
    if not lock.acquire():
        slog(
            "ws_not_started_lock_busy",
            level="error",
            component="lark_ws",
            hint="Stop local python -m lark_integration.bot.lark_ws or set LARK_RUNTIME_LOCK_STEAL=1",
        )
        raise SystemExit(
            "Another Lark WS bot holds the runtime lock. "
            "Stop the other instance (often local lark_ws) or set LARK_RUNTIME_LOCK_STEAL=1."
        )

    try:
        creds = load_lark_credentials()
        profile = os.environ.get("DATABRICKS_CONFIG_PROFILE", "DEFAULT")
        inbound_dir = os.environ.get(
            "LARK_INBOUND_DIR",
            str(REPO_ROOT / "tmp" / "lark_inbound"),
        )
        Path(inbound_dir).mkdir(parents=True, exist_ok=True)

        runtime = BotRuntime(profile=profile, inbound_dir=inbound_dir)
        handler = build_handler(runtime)

        import lark_oapi as lark

        domain = (
            lark.LARK_DOMAIN if "larksuite.com" in creds["base_url"] else lark.FEISHU_DOMAIN
        )
        cli = lark.ws.Client(
            creds["app_id"],
            creds["app_secret"],
            event_handler=handler,
            log_level=lark.LogLevel.INFO,
            domain=domain,
        )
        slog(
            "ws_starting",
            component="lark_ws",
            app_id=creds["app_id"],
            spaces=len(runtime.spaces),
            domain=str(domain),
            lock=lock.status(),
        )
        print(
            f"Starting Lark WS bot app_id={creds['app_id']} default=GenieOne "
            f"spaces={len(runtime.spaces)} domain={domain}"
        )
        print(
            "Events needed (persistent connection): "
            "im.message.receive_v1 + application.bot.menu_v6 + card.action.trigger"
        )
        cli.start()
    finally:
        lock.release()


if __name__ == "__main__":
    main()
