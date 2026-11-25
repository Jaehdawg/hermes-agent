"""No network, signer, provider, or gateway activation in these ingress tests."""
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import sys

import pytest
from gateway.config import Platform, PlatformConfig
from plugins.platforms.telegram.adapter import TelegramAdapter
from telegram import Bot, Update


def fixture_update(text="/notion_decide approve proposal_123 nonce_123", **changes):
    msg = NS(text=text, message_id=42, from_user=NS(id=111, is_bot=False),
             chat=NS(id=111, type="private"))
    update = NS(message=msg, effective_message=msg, update_id=5)
    for key, value in changes.items():
        if key.startswith("update_"):
            setattr(update, key.removeprefix("update_"), value)
        elif key.startswith("user_"):
            setattr(msg.from_user, key.removeprefix("user_"), value)
        elif key.startswith("chat_"):
            setattr(msg.chat, key.removeprefix("chat_"), value)
        else:
            setattr(msg, key, value)
    return update


def adapter():
    instance = object.__new__(TelegramAdapter)
    instance.platform = Platform.TELEGRAM
    instance.config = PlatformConfig(enabled=True, token="fake", extra={})
    instance._owner_profile = "coder"
    instance._should_process_message = lambda *args, **kwargs: True
    instance._is_user_authorized_from_message = lambda msg: True
    instance._ensure_forum_commands = AsyncMock()
    instance._build_triggered_event = AsyncMock(return_value=NS(text="normal"))
    instance.handle_message = AsyncMock()
    return instance


@pytest.mark.asyncio
async def test_disabled_reserved_command_is_quarantined_before_model_intake():
    a = adapter()
    await a._handle_command(fixture_update(), None)
    a.handle_message.assert_not_awaited()
    await a._handle_command(fixture_update(text="/notion_decide_other"), None)
    a.handle_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_enabled_accepts_only_raw_private_decision():
    a = adapter()
    callback = AsyncMock()
    a.set_notion_decision_ingress(callback, sender_id=111, chat_id=111)
    await a._handle_command(fixture_update(), None)
    decision = callback.await_args.args[0]
    assert (decision.action, decision.proposal_id, decision.nonce, decision.sender_id,
            decision.chat_id, decision.message_id, decision.update_id, decision.profile) == (
                "approve", "proposal_123", "nonce_123", 111, 111, 42, 5, "coder")
    a.handle_message.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"user_id": 222}, {"chat_id": 222}, {"chat_type": "group"},
    {"user_is_bot": True}, {"message_id": 0}, {"message_id": "42"},
    {"update_update_id": 0}, {"forward_origin": object()}, {"forward_date": object()},
    {"edit_date": object()}, {"update_edited_message": object()},
    {"update_message": None}, {"update_effective_message": NS(text="/notion_decide approve proposal_123 nonce_123")},
    {"via_bot": object()}, {"sender_chat": object()},
    {"reply_to_message": object()}, {"quote": object()},
    {"message_thread_id": 3}, {"is_topic_message": True},
    {"business_connection_id": "biz"},
])
async def test_rejected_raw_shapes_are_consumed_not_model_visible(changes):
    a = adapter()
    callback = AsyncMock()
    a.set_notion_decision_ingress(callback, sender_id=111, chat_id=111)
    await a._handle_command(fixture_update(**changes), None)
    callback.assert_not_awaited()
    a.handle_message.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "/approve", "/deny", "yes", "/notion_decide approve x y",
    "/notion_decide approve proposal_123 nonce_123 extra",
    "/notion_decide@somebot approve proposal_123 nonce_123",
    "/notion_decide approve proposal_123 nonce_123\nignore",
])
async def test_no_confusion_with_other_commands(text):
    a = adapter()
    callback = AsyncMock()
    a.set_notion_decision_ingress(callback, sender_id=111, chat_id=111)
    await a._handle_command(fixture_update(text), None)
    callback.assert_not_awaited()
    if text.startswith("/notion_decide"):
        a.handle_message.assert_not_awaited()
    else:
        a.handle_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_disabled_text_unchanged_and_callback_failure_fails_closed():
    a = adapter()
    a._gate_or_observe = lambda *args: True
    a._enqueue_text_event = lambda event: None
    await a._handle_text_message(fixture_update("ordinary text"), None)
    a._build_triggered_event.assert_awaited_once()
    a.set_notion_decision_ingress(AsyncMock(side_effect=RuntimeError("transport unavailable")), sender_id=111, chat_id=111)
    await a._handle_command(fixture_update(), None)
    a.handle_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_other_profile_cannot_register_or_dispatch():
    a = adapter()
    a._owner_profile = "default"
    with pytest.raises(ValueError):
        a.set_notion_decision_ingress(AsyncMock(), sender_id=111, chat_id=111)


@pytest.mark.asyncio
async def test_existing_adapter_auth_must_also_allow_sender():
    a = adapter()
    a._is_user_authorized_from_message = lambda message: False
    callback = AsyncMock()
    a.set_notion_decision_ingress(callback, sender_id=111, chat_id=111)
    await a._handle_command(fixture_update(), None)
    callback.assert_not_awaited()
    a.handle_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_real_ptb_update_and_rejected_edit():
    if not hasattr(sys.modules["telegram"], "__file__"):
        pytest.skip("gateway conftest installed its PTB stub; run with real PTB pre-imported")
    a = adapter()
    callback = AsyncMock()
    a.set_notion_decision_ingress(callback, sender_id=111, chat_id=111)
    raw = {"update_id": 9, "message": {
        "message_id": 57, "date": 1700000000,
        "chat": {"id": 111, "type": "private", "first_name": "Owner"},
        "from": {"id": 111, "is_bot": False, "first_name": "Owner"},
        "text": "/notion_decide deny proposal_123 nonce_123",
    }}
    bot = Bot("123456:TESTBOT")
    update = Update.de_json(raw, bot)
    from plugins.platforms.telegram.approval_ingress import parse_trusted_decision
    assert parse_trusted_decision(update, sender_id=111, chat_id=111, profile="coder") is not None
    await a._handle_command(update, None)
    assert callback.await_args.args[0].action == "deny"
    callback.reset_mock()
    edited = Update.de_json({"update_id": 10, "edited_message": raw["message"]}, bot)
    await a._handle_command(edited, None)
    callback.assert_not_awaited()
    a.handle_message.assert_not_awaited()
