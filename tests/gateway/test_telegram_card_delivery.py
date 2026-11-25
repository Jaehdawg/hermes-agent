"""Fake PTB Bot only: never open a Telegram connection or send a real message."""
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import struct
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import pytest
from telegram import Bot, Message, User

from gateway.config import Platform
from plugins.platforms.telegram.card_delivery import deliver_card, encode_card_delivery
from plugins.platforms.telegram.decision_service import UnixDecisionServiceClient

CARD = "Frozen owner card 🚀\nexact  two spaces"
DATE = datetime.fromtimestamp(1700000000, UTC)


def fixture(profile="default"):
    message = NS(from_user=NS(id=123456, is_bot=True), chat=NS(id=111, type="private"),
                 message_id=53, date=DATE, text=CARD, edit_date=None)
    bot = NS(id=123456, send_message=AsyncMock(return_value=message))
    adapter = NS(platform=Platform.TELEGRAM, _bot=bot, _owner_profile=None if profile == "default" else profile)
    adapter.gateway_runner = NS(config=NS(multiplex_profiles=False), adapters={Platform.TELEGRAM: adapter})
    client = NS(record_card_delivery=AsyncMock())
    return adapter, bot, message, client


async def run(adapter, client, profile="default", card=CARD):
    return await deliver_card(adapter=adapter, proposal_id="proposal_123", card=card,
                              owner_chat_id=111, profile=profile, receipt_client=client)


@pytest.mark.asyncio
async def test_exact_card_send_and_receipt_with_explicit_default_profile():
    adapter, bot, _, client = fixture()
    receipt = await run(adapter, client)
    bot.send_message.assert_awaited_once_with(chat_id=111, text=CARD, parse_mode=None,
                                              disable_web_page_preview=True)
    client.record_card_delivery.assert_awaited_once_with(receipt)
    assert receipt.profile == "default"
    assert receipt.chat_id == 111 and receipt.message_id == 53 and receipt.at == 1700000000
    assert receipt.card_digest == hashlib.sha256(CARD.encode("utf-8")).hexdigest()
    frame = encode_card_delivery(receipt)
    assert struct.unpack("!I", frame[:4])[0] == len(frame) - 4
    assert json.loads(frame[4:]) == {"version": 2, "card_delivery": {
        "proposal_id": "proposal_123", "profile": "default", "chat_id": 111,
        "message_id": 53, "card_digest": receipt.card_digest, "at": 1700000000}}
    assert "🚀" not in frame[4:].decode("utf-8")  # only a digest crosses IPC


@pytest.mark.asyncio
async def test_explicit_dedicated_profile_and_route_mismatch_before_send():
    adapter, bot, _, client = fixture("coder")
    assert (await run(adapter, client, profile="coder")).profile == "coder"
    with pytest.raises(PermissionError, match="route"):
        await run(adapter, client, profile="default")
    assert bot.send_message.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("from_user", NS(id=999, is_bot=True)), ("from_user", NS(id=123456, is_bot=False)),
    ("chat", NS(id=222, type="private")), ("chat", NS(id=111, type="group")),
    ("message_id", 0), ("message_id", "53"), ("text", "different card"),
    ("edit_date", DATE),
    ("date", None), ("date", datetime(2023, 11, 14)),
    ("date", datetime.fromtimestamp(0, UTC)),
    ("date", datetime.fromtimestamp(1700000000.5, UTC)),
])
async def test_untrusted_send_response_never_records_receipt(field, value):
    adapter, bot, message, client = fixture()
    setattr(message, field, value)
    with pytest.raises(ValueError):
        await run(adapter, client)
    bot.send_message.assert_awaited_once()
    client.record_card_delivery.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_failure_and_record_failure_are_not_hidden():
    adapter, bot, _, client = fixture()
    bot.send_message.side_effect = RuntimeError("fake send failure")
    with pytest.raises(RuntimeError, match="fake send failure"):
        await run(adapter, client)
    client.record_card_delivery.assert_not_awaited()
    bot.send_message.side_effect = None
    client.record_card_delivery.side_effect = RuntimeError("fake record failure")
    with pytest.raises(RuntimeError, match="fake record failure"):
        await run(adapter, client)
    client.record_card_delivery.assert_awaited_once()


@pytest.mark.asyncio
async def test_same_uid_guard_refuses_receipt_without_socket_or_send():
    adapter, bot, _, _ = fixture()
    client = UnixDecisionServiceClient(Path("/nonexistent/service.sock"), model_uid=os.getuid(),
                                       service_uid=os.getuid() + 1)
    with patch.object(UnixDecisionServiceClient, "_send_frame") as send:
        with pytest.raises(PermissionError, match="distinct"):
            await run(adapter, client)
        send.assert_not_called()
    bot.send_message.assert_not_awaited()  # same-UID refusal precedes the send


@pytest.mark.asyncio
async def test_receipt_client_sends_exact_frame_without_socket():
    adapter, _, _, _ = fixture()
    client = UnixDecisionServiceClient(Path("/nonexistent/service.sock"),
                                       model_uid=os.getuid() + 1, service_uid=os.getuid() + 2)
    with patch.object(UnixDecisionServiceClient, "_send_frame") as send:
        receipt = await run(adapter, client)
    send.assert_called_once_with(encode_card_delivery(receipt))


@pytest.mark.asyncio
async def test_real_ptb_message_from_fake_bot_only():
    bot = Bot("123456:TESTBOT")
    bot._bot_user = User(id=123456, is_bot=True, first_name="Bot")
    message = Message.de_json({
        "message_id": 73, "date": 1700000000,
        "chat": {"id": 111, "type": "private", "first_name": "Owner"},
        "from": {"id": 123456, "is_bot": True, "first_name": "Bot"},
        "text": CARD,
    }, bot)
    adapter, _, _, client = fixture()
    adapter._bot = bot
    with patch.object(Bot, "send_message", new_callable=AsyncMock, return_value=message) as fake_send:
        receipt = await run(adapter, client)
    fake_send.assert_awaited_once_with(chat_id=111, text=CARD, parse_mode=None,
                                       disable_web_page_preview=True)
    assert receipt.message_id == 73 and receipt.at == 1700000000
    client.record_card_delivery.assert_awaited_once_with(receipt)


@pytest.mark.asyncio
async def test_preflight_invalid_proposal_and_bot_never_send():
    adapter, bot, _, client = fixture()
    with pytest.raises(ValueError):
        await deliver_card(adapter=adapter, proposal_id="short", card=CARD,
                           owner_chat_id=111, profile="default", receipt_client=client)
    bot.send_message.assert_not_awaited()
    adapter._bot = NS(send_message=bot.send_message)
    with pytest.raises(PermissionError):
        await run(adapter, client)
    bot.send_message.assert_not_awaited()
