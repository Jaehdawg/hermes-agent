"""Fake-only decision boundary tests; no socket is opened and no effect is sent."""
import hashlib
import json
import os
import struct
from dataclasses import replace
from unittest.mock import patch

import pytest
from telegram import Bot, Update

from plugins.platforms.telegram.approval_ingress import parse_trusted_decision
from plugins.platforms.telegram.decision_service import UnixDecisionServiceClient, encode_decision


PROPOSAL = "proposal_123"
NONCE = "nonce_123"
TEXT = f"/notion_decide approve {PROPOSAL} {NONCE}"


def ptb_update(update_id=9, message_id=57, text=TEXT, **changes):
    message = {
        "message_id": message_id, "date": 1700000000,
        "chat": {"id": 111, "type": "private", "first_name": "Owner"},
        "from": {"id": 111, "is_bot": False, "first_name": "Owner"}, "text": text,
        "reply_to_message": {"message_id": 53, "date": 1699999999,
                             "chat": {"id": 111, "type": "private", "first_name": "Owner"},
                             "from": {"id": 123456, "is_bot": True, "first_name": "Bot"}},
    }
    message.update(changes)
    return Update.de_json({"update_id": update_id, "message": message}, Bot("123456:TESTBOT"))


def parse(update):
    return parse_trusted_decision(update, sender_id=111, chat_id=111, bot_id=123456, profile="coder")


class FakeDecisionService:
    """Test double: frozen payload lookup and one-use message/nonce, no effects."""
    def __init__(self):
        self.proposals = {PROPOSAL: {
            "digest": hashlib.sha256(b'frozen-target-and-payload').hexdigest(),
            "nonce": NONCE, "sender_id": 111, "chat_id": 111,
            "profile": "coder", "expires": 1800000000,
            "card_message_id": 53, "card_sent_at": 1699999999,
        }}
        self.frozen_payloads = {PROPOSAL: b'frozen-target-and-payload'}
        self.seen_messages = set()
        self.seen_nonces = set()
        self.decisions = []
        self.now = 1700000001

    async def decide(self, decision):
        proposal = self.proposals.get(decision.proposal_id)
        payload = self.frozen_payloads.get(decision.proposal_id)
        actual_digest = hashlib.sha256(payload).hexdigest() if payload is not None else None
        message_key = (decision.chat_id, decision.message_id)
        nonce_key = (decision.proposal_id, decision.nonce)
        if (proposal is None or decision.action not in ("approve", "deny")
                or proposal["digest"] != actual_digest
                or proposal["nonce"] != decision.nonce
                or any(getattr(decision, key) != proposal[key] for key in ("sender_id", "chat_id", "profile"))
                or decision.reply_to_message_id != proposal["card_message_id"]
                or not proposal["card_sent_at"] <= decision.message_date < proposal["expires"]
                or self.now >= proposal["expires"]
                or message_key in self.seen_messages or nonce_key in self.seen_nonces):
            return False
        self.seen_messages.add(message_key)
        self.seen_nonces.add(nonce_key)
        self.decisions.append(decision)
        return True


@pytest.mark.asyncio
async def test_real_ptb_parser_and_fake_service_replay_and_digest():
    service = FakeDecisionService()
    decision = parse(ptb_update())
    assert decision is not None
    service.frozen_payloads[PROPOSAL] = b'changed'
    assert not await service.decide(decision)
    service.frozen_payloads[PROPOSAL] = b'frozen-target-and-payload'
    service.now = 1800000000
    assert not await service.decide(decision)
    service.now = 1700000001
    assert await service.decide(decision)
    assert not await service.decide(decision)
    assert not await service.decide(parse(ptb_update(update_id=10, message_id=58)))
    assert len(service.decisions) == 1

@pytest.mark.asyncio
async def test_fake_service_refuses_wrong_delivered_card_and_event_time():
    service = FakeDecisionService()
    decision = parse(ptb_update())
    assert not await service.decide(replace(decision, reply_to_message_id=52))
    assert not await service.decide(replace(decision, message_date=1699999998))
    assert not await service.decide(replace(decision, message_date=1800000000))
    assert await service.decide(decision)


@pytest.mark.parametrize("field,value", [
    ("forward_date", 1700000000), ("edit_date", 1700000000),
    ("reply_to_message", None),
    ("reply_to_message", {"message_id": 2, "date": 1700000000,
                          "chat": {"id": 111, "type": "private"},
                          "from": {"id": 111, "is_bot": False, "first_name": "Owner"}}),
    ("via_bot", {"id": 333, "is_bot": True, "first_name": "Bot"}),
    ("sender_chat", {"id": -111, "type": "channel", "title": "Channel"}),
])
def test_real_ptb_negative_message_shapes(field, value):
    assert parse(ptb_update(**{field: value})) is None

@pytest.mark.parametrize("field,value", [
    ("message_id", 0), ("message_id", -1), ("message_id", "53"),
    ("chat", {"id": 222, "type": "private"}),
    ("from", {"id": 999, "is_bot": True, "first_name": "Other bot"}),
    ("from", {"id": 123456, "is_bot": False, "first_name": "Fake bot"}),
])
def test_real_ptb_reply_must_be_exact_bot_card(field, value):
    reply = {"message_id": 53, "date": 1699999999,
             "chat": {"id": 111, "type": "private"},
             "from": {"id": 123456, "is_bot": True, "first_name": "Bot"}}
    reply[field] = value
    assert parse(ptb_update(reply_to_message=reply)) is None

@pytest.mark.parametrize("date", [None, 0, -1, 1700000000.5])
def test_real_ptb_invalid_event_date(date):
    assert parse(ptb_update(date=date)) is None


def test_real_ptb_wrong_actor_and_chat():
    wrong_actor = ptb_update()
    # PTB's own parsed user object is used, never caller-supplied model metadata.
    assert parse_trusted_decision(wrong_actor, sender_id=222, chat_id=111, bot_id=123456, profile="coder") is None
    assert parse_trusted_decision(wrong_actor, sender_id=111, chat_id=222, bot_id=123456, profile="coder") is None
    assert parse_trusted_decision(wrong_actor, sender_id=111, chat_id=111, bot_id=999, profile="coder") is None
    assert parse_trusted_decision(wrong_actor, sender_id=111, chat_id=111, bot_id=123456, profile="default").profile == "default"
    assert parse_trusted_decision(wrong_actor, sender_id=111, chat_id=111, bot_id=123456, profile="") is None


@pytest.mark.asyncio
async def test_same_uid_is_hard_stop_even_when_socket_exists(tmp_path):
    client = UnixDecisionServiceClient(tmp_path / "service.sock", model_uid=os.getuid(), service_uid=os.getuid() + 1)
    with patch.object(UnixDecisionServiceClient, "_send") as send:
        with pytest.raises(PermissionError, match="distinct"):
            await client.decide(parse(ptb_update()))
        send.assert_not_called()


def test_envelope_is_exact_parser_fields():
    decision = parse(ptb_update())
    assert decision is not None
    frame = encode_decision(decision)
    assert struct.unpack("!I", frame[:4])[0] == len(frame) - 4
    wire = json.loads(frame[4:])
    assert wire["version"] == 2
    assert wire["decision"]["proposal_id"] == PROPOSAL
    assert set(wire["decision"]) == {
        "action", "proposal_id", "nonce", "sender_id", "chat_id",
        "message_id", "update_id", "profile", "reply_to_message_id", "message_date",
    }
    assert wire["decision"]["reply_to_message_id"] == 53
    assert wire["decision"]["message_date"] == 1700000000
    assert "digest" not in frame[4:].decode()
