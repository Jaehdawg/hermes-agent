"""Opt-in raw Telegram DM decision envelope; not an authorization or dispatch service.

Only the Telegram adapter calls this with the PTB update it received. The callback must
live behind a separately protected decision service and verify nonce, frozen payload,
expiry, replay, and effects. Never expose registration through a model-callable tool.
"""
from dataclasses import dataclass
from datetime import UTC, datetime
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
    reply_to_message_id: int
    message_date: int


def resolve_decision_profile(runner, adapter, source) -> Optional[str]:
    """Resolve the PTB transport's runtime profile, never a message/model profile hint.

    Use the gateway's route matcher with the receiving bot as a discriminator.
    An unreserved target or equally specific competing routes cannot authorize a
    decision, even though ordinary chat routing may choose the first route.
    """
    from pathlib import Path
    from hermes_constants import get_process_hermes_home

    config = getattr(runner, "config", None)
    if config is None or source.platform.value != "telegram":
        return None
    if not getattr(config, "multiplex_profiles", False):
        if (getattr(runner, "adapters", None) or {}).get(source.platform) is not adapter:
            return None
        owner = getattr(adapter, "_owner_profile", None) or "default"
        return owner if isinstance(owner, str) and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", owner) else None
    reserved = getattr(config, "_runtime_profile_homes", None)
    if not reserved:
        return None
    try:
        homes = {name: Path(path).resolve() for name, path in reserved}
        if len(homes) != len(reserved) or len(set(homes.values())) != len(homes):
            return None
        primary = [name for name, path in homes.items() if path == get_process_hermes_home().resolve()]
        if len(primary) != 1:
            return None
        owner = getattr(adapter, "_owner_profile", None) or primary[0]
        if owner not in homes:
            return None
        # Bind the receiving bot to exactly one live gateway adapter registry.
        # An old adapter after reconnect (or a duplicate bot registration) cannot
        # turn its retained callback into an approval for the replacement bot.
        registries = [(primary[0], getattr(runner, "adapters", None) or {})]
        registries.extend((name, mapping) for name, mapping in (getattr(runner, "_profile_adapters", None) or {}).items())
        bindings = [name for name, mapping in registries if mapping.get(source.platform) is adapter]
        if bindings != [owner]:
            return None
        matches = [route for route in (getattr(config, "profile_routes", None) or []) if route.matches(
            source.platform.value, guild_id=getattr(source, "guild_id", None),
            chat_id=source.chat_id, thread_id=source.thread_id,
            parent_chat_id=getattr(source, "parent_chat_id", None),
            adapter_profile=getattr(adapter, "_owner_profile", None), user_id=source.user_id)]
        if matches:
            rank = max(route.specificity for route in matches)
            best = [route for route in matches if route.specificity == rank]
            if len(best) != 1:
                return None
            target = best[0].profile
        else:
            target = owner
        if target not in homes or not isinstance(target, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", target):
            return None
        # Cross-check the gateway's canonical resolution, which handles served
        # profiles and bot scoping independently. Do not use source.profile.
        canonical = getattr(runner, "_profile_name_for_source", None)
        if not callable(canonical) or (canonical(source, adapter_profile=getattr(adapter, "_owner_profile", None)) or owner) != target:
            return None
        return target
    except Exception:
        return None


def parse_trusted_decision(update, *, sender_id: int, chat_id: int, bot_id: int,
                           profile: Optional[str]) -> Optional[TelegramNotionDecision]:
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
    if user is None or chat is None or not isinstance(profile, str) or not profile or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", profile):
        return None
    def exact_positive(value):
        return type(value) is int and value > 0
    uid, cid, mid, update_id = (getattr(user, "id", None), getattr(chat, "id", None),
                                 getattr(message, "message_id", None), getattr(update, "update_id", None))
    if not (exact_positive(uid) and exact_positive(cid) and exact_positive(mid) and exact_positive(update_id)):
        return None
    if not (type(sender_id) is int and type(chat_id) is int and exact_positive(bot_id)
            and uid == sender_id and cid == chat_id):
        return None
    if str(getattr(chat, "type", "")).lower() not in ("private", "chattype.private") or getattr(user, "is_bot", True):
        return None
    if any(getattr(message, attr, None) is not None for attr in (
        "forward_origin", "forward_date", "forward_from", "forward_from_chat",
        "forward_sender_name", "edit_date", "via_bot", "sender_chat",
        "quote", "business_connection_id", "message_thread_id",
    )) or getattr(message, "is_topic_message", False):
        return None
    # PTB retains legacy/unknown Telegram fields in api_kwargs. Fail closed if
    # an older forward shape was not promoted to a first-class Message field.
    if any(key.startswith("forward_") for key in (getattr(message, "api_kwargs", None) or {})):
        return None
    reply = getattr(message, "reply_to_message", None)
    reply_user, reply_chat = getattr(reply, "from_user", None), getattr(reply, "chat", None)
    reply_id = getattr(reply, "message_id", None)
    if (not exact_positive(reply_id) or reply_id >= mid
            or reply_user is None or getattr(reply_user, "is_bot", None) is not True
            or getattr(reply_user, "id", None) != bot_id
            or reply_chat is None or getattr(reply_chat, "id", None) != cid
            or str(getattr(reply_chat, "type", "")).lower() not in ("private", "chattype.private")):
        return None
    date = getattr(message, "date", None)
    if not isinstance(date, datetime) or date.tzinfo is None or date.utcoffset() is None:
        return None
    timestamp = date.astimezone(UTC).timestamp()
    if not timestamp.is_integer() or timestamp <= 0:
        return None
    text = getattr(message, "text", None)
    match = _COMMAND.fullmatch(text) if isinstance(text, str) else None
    if not match:
        return None
    return TelegramNotionDecision(match[1], match[2], match[3], uid, cid, mid, update_id,
                                  profile, reply_id, int(timestamp))
