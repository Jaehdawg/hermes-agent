"""Explicit, unregistered Telegram card delivery seam. Never called by gateway startup.

The protected service must independently verify this receipt against its frozen
proposal and enforce replay/expiry. An uncertain send/receipt must not be retried
blindly: Telegram may have delivered a card even if the acknowledgement was lost.
"""
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
import re
import struct

from gateway.config import Platform
from gateway.session import SessionSource
from .approval_ingress import resolve_decision_profile


@dataclass(frozen=True)
class CardDeliveryReceipt:
    proposal_id: str
    profile: str
    chat_id: int
    message_id: int
    card_digest: str
    at: int


def encode_card_delivery(receipt: CardDeliveryReceipt) -> bytes:
    """Exact v2 envelope, four-byte network-order length, bounded UTF-8 JSON."""
    if not isinstance(receipt, CardDeliveryReceipt):
        raise TypeError("card receipt required")
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", receipt.proposal_id):
        raise ValueError("invalid proposal ID")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", receipt.profile):
        raise ValueError("invalid profile")
    if not all(type(value) is int and value > 0 for value in
               (receipt.chat_id, receipt.message_id, receipt.at)):
        raise ValueError("positive integer chat, message and time required")
    if not re.fullmatch(r"[0-9a-f]{64}", receipt.card_digest):
        raise ValueError("invalid card digest")
    data = json.dumps({"version": 2, "card_delivery": asdict(receipt)},
                      sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(data) > 2048:
        raise ValueError("oversize delivery receipt")
    return struct.pack("!I", len(data)) + data


async def deliver_card(*, adapter, proposal_id: str, card: str, owner_chat_id: int,
                       profile: str, receipt_client) -> CardDeliveryReceipt:
    """Send frozen text via the explicitly selected PTB bot, then record its receipt.

    Caller must be trusted gateway-side code, not a model tool. There is no
    registration, startup hook, or automatic retry. A receipt error propagates.
    """
    if type(owner_chat_id) is not int or owner_chat_id <= 0:
        raise ValueError("positive private owner chat required")
    if not isinstance(card, str) or not card or not isinstance(profile, str):
        raise ValueError("frozen card and profile required")
    # Validate before any external effect, including the serializable envelope.
    digest = hashlib.sha256(card.encode("utf-8", errors="strict")).hexdigest()
    encode_card_delivery(CardDeliveryReceipt(proposal_id, profile, owner_chat_id, 1, digest, 1))
    # The known same-UID denial must happen before a potentially irreversible send.
    preflight = getattr(receipt_client, "_require_distinct_uids", None)
    if callable(preflight):
        preflight()
    if not callable(getattr(receipt_client, "record_card_delivery", None)):
        raise PermissionError("receipt service unavailable")
    bot = getattr(adapter, "_bot", None)
    if bot is None:
        raise PermissionError("initialized receiving bot required")
    try:
        bot_id = bot.id  # PTB raises RuntimeError for an uninitialized bot.
    except (AttributeError, RuntimeError) as exc:
        raise PermissionError("initialized receiving bot required") from exc
    if type(bot_id) is not int or bot_id <= 0 or getattr(adapter, "platform", None) != Platform.TELEGRAM:
        raise PermissionError("initialized Telegram bot required")
    runner = getattr(adapter, "gateway_runner", None)
    source = SessionSource(platform=Platform.TELEGRAM, chat_id=str(owner_chat_id),
                           chat_type="dm", user_id=str(owner_chat_id))
    if resolve_decision_profile(runner, adapter, source) != profile:
        raise PermissionError("card target does not match receiving adapter route")
    if not callable(getattr(bot, "send_message", None)):
        raise PermissionError("Telegram sender unavailable")
    # No parse mode, preview transformation, intermediary send_message tool, or
    # gateway formatter: the exact Python string is handed to the PTB bot.
    message = await bot.send_message(chat_id=owner_chat_id, text=card,
                                     parse_mode=None, disable_web_page_preview=True)
    author, chat = getattr(message, "from_user", None), getattr(message, "chat", None)
    message_id = getattr(message, "message_id", None)
    if (author is None or type(getattr(author, "id", None)) is not int
            or author.id != bot_id or getattr(author, "is_bot", None) is not True
            or chat is None or type(getattr(chat, "id", None)) is not int
            or chat.id != owner_chat_id
            or str(getattr(chat, "type", "")).lower() not in ("private", "chattype.private")
            or type(message_id) is not int or message_id <= 0
            or getattr(message, "text", None) != card
            or getattr(message, "edit_date", None) is not None):
        raise ValueError("untrusted Telegram delivery response")
    date = getattr(message, "date", None)
    if not isinstance(date, datetime) or date.tzinfo is None or date.utcoffset() is None:
        raise ValueError("invalid Telegram delivery date")
    timestamp = date.astimezone(UTC).timestamp()
    if not timestamp.is_integer() or timestamp <= 0:
        raise ValueError("invalid Telegram delivery date")
    receipt = CardDeliveryReceipt(proposal_id, profile, owner_chat_id, message_id, digest, int(timestamp))
    await receipt_client.record_card_delivery(receipt)
    return receipt
