"""Opt-in raw Telegram DM decision envelope; not an authorization or dispatch service.

Only the Telegram adapter calls this with the PTB update it received. The callback must
live behind a separately protected decision service and verify nonce, frozen payload,
expiry, replay, and effects. Never expose registration through a model-callable tool.
"""
from dataclasses import dataclass
import re
from typing import Optional

_COMMAND = re.compile(r"^/notion_decide (approve|deny) ([A-Za-z0-9_-]{8,128}) ([A-Za-z0-9_-]{8,128})$")


@dataclass(frozen=True)
class TelegramNotionDecision:
    action: str
    proposal_id: str
    nonce: str
    sender_id: int
    chat_id: int
    message_id: int
    update_id: int
    profile: str


def parse_trusted_decision(update, *, sender_id: int, chat_id: int, profile: Optional[str]) -> Optional[TelegramNotionDecision]:
    """Fail closed on anything except a new, unforwarded private raw PTB message.

    This is a shape check, not proof that an arbitrary Python caller is Telegram. It
    must only be invoked by the adapter's authenticated PTB message handler.
    """
    message = getattr(update, "message", None)
    if message is None or getattr(update, "effective_message", message) is not message:
        return None
    if getattr(update, "edited_message", None) is not None or getattr(update, "channel_post", None) is not None:
        return None
    user, chat = getattr(message, "from_user", None), getattr(message, "chat", None)
    if user is None or chat is None or profile != "coder":
        return None
    def exact_positive(value):
        return type(value) is int and value > 0
    uid, cid, mid, update_id = (getattr(user, "id", None), getattr(chat, "id", None),
                                 getattr(message, "message_id", None), getattr(update, "update_id", None))
    if not (exact_positive(uid) and exact_positive(cid) and exact_positive(mid) and exact_positive(update_id)):
        return None
    if not (type(sender_id) is int and type(chat_id) is int and uid == sender_id and cid == chat_id):
        return None
    if str(getattr(chat, "type", "")).lower() not in ("private", "chattype.private") or getattr(user, "is_bot", True):
        return None
    if any(getattr(message, attr, None) is not None for attr in (
        "forward_origin", "forward_date", "forward_from", "forward_from_chat",
        "forward_sender_name", "edit_date", "via_bot", "sender_chat", "reply_to_message",
        "quote", "business_connection_id", "message_thread_id",
    )) or getattr(message, "is_topic_message", False):
        return None
    # PTB retains legacy/unknown Telegram fields in api_kwargs. Fail closed if
    # an older forward shape was not promoted to a first-class Message field.
    if any(key.startswith("forward_") for key in (getattr(message, "api_kwargs", None) or {})):
        return None
    text = getattr(message, "text", None)
    match = _COMMAND.fullmatch(text) if isinstance(text, str) else None
    if not match:
        return None
    return TelegramNotionDecision(match[1], match[2], match[3], uid, cid, mid, update_id, profile)
