"""No network, signer, provider, or gateway activation in these ingress tests."""
from types import SimpleNamespace as NS
from datetime import UTC, datetime
from unittest.mock import AsyncMock
import sys

import pytest
from gateway.config import Platform, PlatformConfig
from plugins.platforms.telegram.adapter import TelegramAdapter
from telegram import Bot, Update


def fixture_update(text="/notion_decide approve proposal_123 nonce_123", **changes):
    msg = NS(text=text, message_id=42, from_user=NS(id=111, is_bot=False),
             chat=NS(id=111, type="private"), date=datetime.fromtimestamp(1700000000, UTC),
             reply_to_message=NS(message_id=41, from_user=NS(id=123456, is_bot=True),
                                 chat=NS(id=111, type="private")))
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
    instance._bot = NS(id=123456)
    instance.gateway_runner = NS(config=NS(multiplex_profiles=False, profile_routes=[]),
                                 adapters={Platform.TELEGRAM: instance})
    instance._should_process_message = lambda *args, **kwargs: True
    instance._is_user_authorized_from_message = lambda msg: True
    instance._authorization_check = lambda user_id, chat_type, chat_id: True
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
            decision.chat_id, decision.message_id, decision.update_id, decision.profile,
            decision.reply_to_message_id, decision.message_date) == (
                "approve", "proposal_123", "nonce_123", 111, 111, 42, 5, "coder", 41, 1700000000)
    a.handle_message.assert_not_awaited()

@pytest.mark.asyncio
async def test_missing_or_wrong_receiving_bot_identity_fails_closed():
    a = adapter()
    callback = AsyncMock()
    a.set_notion_decision_ingress(callback, sender_id=111, chat_id=111)
    a._bot = None
    await a._handle_command(fixture_update(), None)
    a._bot = NS(id=999)
    await a._handle_command(fixture_update(), None)
    callback.assert_not_awaited()
    a.handle_message.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"user_id": 222}, {"chat_id": 222}, {"chat_type": "group"},
    {"user_is_bot": True}, {"message_id": 0}, {"message_id": "42"},
    {"update_update_id": 0}, {"forward_origin": object()}, {"forward_date": object()},
    {"edit_date": object()}, {"update_edited_message": object()},
    {"update_message": None}, {"update_effective_message": NS(text="/notion_decide approve proposal_123 nonce_123")},
    {"via_bot": object()}, {"sender_chat": object()},
    {"reply_to_message": None}, {"reply_to_message": object()}, {"quote": object()},
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
@pytest.mark.parametrize("owner", [None, "coder", "alberta"])
async def test_dedicated_profile_receives_its_own_decision(owner):
    a = adapter()
    a._owner_profile = owner
    callback = AsyncMock()
    a.set_notion_decision_ingress(callback, sender_id=111, chat_id=111)
    await a._handle_command(fixture_update(), None)
    assert callback.await_args.args[0].profile == (owner or "default")


@pytest.mark.asyncio
async def test_shared_bot_routes_to_target_not_adapter_owner():
    from gateway.profile_routing import ProfileRoute
    a = adapter()
    a._owner_profile = None
    a.gateway_runner.config = NS(multiplex_profiles=True, profile_routes=[
        ProfileRoute(name="alberta", platform="telegram", profile="alberta", user_id="111")])
    from hermes_constants import get_process_hermes_home
    a.gateway_runner.config._runtime_profile_homes = (("default", get_process_hermes_home()), ("alberta", "/fake/alberta"))
    from gateway.run import GatewayRunner
    from types import MethodType
    a.gateway_runner._profile_name_for_source = MethodType(GatewayRunner._profile_name_for_source, a.gateway_runner)
    a.gateway_runner.adapters = {Platform.TELEGRAM: a}
    callback = AsyncMock()
    a.set_notion_decision_ingress(callback, sender_id=111, chat_id=111)
    await a._handle_command(fixture_update(), None)
    assert callback.await_args.args[0].profile == "alberta"


@pytest.mark.asyncio
@pytest.mark.parametrize("routes", [
    [("a", "coder"), ("b", "alberta")], [("missing", "unserved")],
])
async def test_ambiguous_or_unserved_route_is_consumed_without_decision(routes):
    from gateway.profile_routing import ProfileRoute
    a = adapter()
    a._owner_profile = None
    from hermes_constants import get_process_hermes_home
    a.gateway_runner.config = NS(multiplex_profiles=True, profile_routes=[
        ProfileRoute(name=name, platform="telegram", profile=profile, user_id="111")
        for name, profile in routes],
        _runtime_profile_homes=(("default", get_process_hermes_home()), ("coder", "/fake/coder"), ("alberta", "/fake/alberta")))
    a.gateway_runner._profile_name_for_source = lambda source, adapter_profile=None: routes[0][1]
    a.gateway_runner.adapters = {Platform.TELEGRAM: a}
    callback = AsyncMock()
    a.set_notion_decision_ingress(callback, sender_id=111, chat_id=111)
    await a._handle_command(fixture_update(), None)
    callback.assert_not_awaited()
    a.handle_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_gateway_or_multiplex_reservation_fails_closed():
    a = adapter()
    a.set_notion_decision_ingress(AsyncMock(), sender_id=111, chat_id=111)
    a.gateway_runner = None
    await a._handle_command(fixture_update(), None)
    a._notion_decision_ingress[0].assert_not_awaited()
    a.gateway_runner = NS(config=NS(multiplex_profiles=True, profile_routes=[]))
    await a._handle_command(fixture_update(), None)
    a._notion_decision_ingress[0].assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", [None, "coder", "research", "localish-ingestion"])
async def test_multiplex_no_routes_uses_registered_bot_owner(owner):
    from hermes_constants import get_process_hermes_home
    a = adapter()
    a._owner_profile = owner
    name = owner or "default"
    a.gateway_runner.config = NS(multiplex_profiles=True, profile_routes=[], _runtime_profile_homes=(
        ("default", get_process_hermes_home()), ("coder", "/fake/coder"),
        ("research", "/fake/research"), ("localish-ingestion", "/fake/localish")))
    a.gateway_runner.adapters = {Platform.TELEGRAM: a} if owner is None else {}
    a.gateway_runner._profile_adapters = {owner: {Platform.TELEGRAM: a}} if owner else {}
    a.gateway_runner._profile_name_for_source = lambda source, adapter_profile=None: None
    callback = AsyncMock()
    a.set_notion_decision_ingress(callback, sender_id=111, chat_id=111)
    await a._handle_command(fixture_update(), None)
    assert callback.await_args.args[0].profile == name
    callback.reset_mock()
    a.gateway_runner.adapters = {Platform.TELEGRAM: object()}
    a.gateway_runner._profile_adapters = {}
    await a._handle_command(fixture_update(), None)
    callback.assert_not_awaited()  # stale or unregistered receiving adapter


@pytest.mark.asyncio
async def test_route_is_scoped_to_receiving_bot_and_canonical_mismatch_denied():
    from hermes_constants import get_process_hermes_home
    from gateway.profile_routing import ProfileRoute
    a = adapter()
    a._owner_profile = "research"
    a.gateway_runner.config = NS(multiplex_profiles=True, _runtime_profile_homes=(
        ("default", get_process_hermes_home()), ("research", "/fake/research"),
        ("coder", "/fake/coder")), profile_routes=[
            ProfileRoute(name="shared-bot-only", platform="telegram", profile="coder", user_id="111")])
    a.gateway_runner.adapters = {}
    a.gateway_runner._profile_adapters = {"research": {Platform.TELEGRAM: a}}
    a.gateway_runner._profile_name_for_source = lambda source, adapter_profile=None: None
    callback = AsyncMock()
    a.set_notion_decision_ingress(callback, sender_id=111, chat_id=111)
    await a._handle_command(fixture_update(), None)
    assert callback.await_args.args[0].profile == "research"
    callback.reset_mock()
    a.gateway_runner.config.profile_routes = [ProfileRoute(
        name="research-bot", platform="telegram", profile="coder", user_id="111", bot_profile="research")]
    # An inconsistent gateway resolution cannot grant the selected target.
    await a._handle_command(fixture_update(), None)
    callback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("auth", [None, False, "yes"])
async def test_gateway_authorization_unknown_or_denied_fails_closed(auth):
    a = adapter()
    a._authorization_check = None if auth is None else (lambda *args: auth)
    callback = AsyncMock()
    a.set_notion_decision_ingress(callback, sender_id=111, chat_id=111)
    await a._handle_command(fixture_update(), None)
    callback.assert_not_awaited()
    a.handle_message.assert_not_awaited()


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
        "reply_to_message": {"message_id": 53, "date": 1699999999,
                             "chat": {"id": 111, "type": "private", "first_name": "Owner"},
                             "from": {"id": 123456, "is_bot": True, "first_name": "Bot"}},
    }}
    bot = Bot("123456:TESTBOT")
    update = Update.de_json(raw, bot)
    from plugins.platforms.telegram.approval_ingress import parse_trusted_decision
    assert parse_trusted_decision(update, sender_id=111, chat_id=111, bot_id=123456, profile="coder") is not None
    await a._handle_command(update, None)
    assert callback.await_args.args[0].action == "deny"
    assert callback.await_args.args[0].reply_to_message_id == 53
    assert callback.await_args.args[0].message_date == 1700000000
    callback.reset_mock()
    edited = Update.de_json({"update_id": 10, "edited_message": raw["message"]}, bot)
    await a._handle_command(edited, None)
    callback.assert_not_awaited()
    a.handle_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_gateway_bootstrap_default_off_scoped_disable_and_replacement():
    from gateway.run_adapters import GatewayAdapterLifecycleMixin
    from gateway.notion_decision_bootstrap import DecisionBinding
    first = adapter()
    runner = first.gateway_runner
    runner._wire_notion_decision_adapter = lambda adp: GatewayAdapterLifecycleMixin._wire_notion_decision_adapter(runner, adp)
    runner.configure_notion_decision_ingress = lambda callback=None, bindings=(): GatewayAdapterLifecycleMixin.configure_notion_decision_ingress(runner, callback, bindings=bindings)
    runner._profile_adapters = {}
    runner.adapters = {Platform.TELEGRAM: first}
    first.gateway_runner = runner
    callback = AsyncMock()
    await first._handle_command(fixture_update(), None)
    first.handle_message.assert_not_awaited()
    binding = DecisionBinding(bot_profile="coder", profile="coder", sender_id=111, chat_id=111)
    runner.configure_notion_decision_ingress(callback, bindings=[binding])
    await first._handle_command(fixture_update(), None)
    callback.assert_awaited_once()
    second = adapter()
    second.gateway_runner = runner
    runner.adapters[Platform.TELEGRAM] = second
    runner._wire_notion_decision_adapter(second)
    callback.reset_mock()
    await first._handle_command(fixture_update(), None)
    callback.assert_not_awaited()
    await second._handle_command(fixture_update(), None)
    callback.assert_awaited_once()
    runner.configure_notion_decision_ingress()  # runtime kill switch
    callback.reset_mock()
    await second._handle_command(fixture_update(), None)
    callback.assert_not_awaited()
    second.handle_message.assert_not_awaited()
    third = adapter()
    third.gateway_runner = runner
    runner._wire_notion_decision_adapter(third)
    assert getattr(third, "_notion_decision_ingress", None) is None


def test_bootstrap_rejects_unscoped_and_duplicate_bindings():
    from gateway.notion_decision_bootstrap import DecisionBinding
    from gateway.run_adapters import GatewayAdapterLifecycleMixin
    a = adapter()
    runner = a.gateway_runner
    configure = lambda bindings: GatewayAdapterLifecycleMixin.configure_notion_decision_ingress(
        runner, AsyncMock(), bindings=bindings)
    with pytest.raises(ValueError):
        configure([DecisionBinding(bot_profile="coder", profile="coder", sender_id=111, chat_id=222)])
    binding = DecisionBinding(bot_profile="coder", profile="coder", sender_id=111, chat_id=111)
    with pytest.raises(ValueError):
        configure([binding, binding])


@pytest.mark.asyncio
async def test_multiplex_bootstrap_default_and_named_bots_with_exact_cross_route():
    from gateway.run_adapters import GatewayAdapterLifecycleMixin as Mixin
    from gateway.notion_decision_bootstrap import DecisionBinding
    from gateway.profile_routing import ProfileRoute
    from hermes_constants import get_process_hermes_home
    primary, named = adapter(), adapter()
    primary._owner_profile = None
    named._owner_profile = "research"
    runner = primary.gateway_runner
    runner.config = NS(multiplex_profiles=True, profile_routes=[], _runtime_profile_homes=(
        ("default", get_process_hermes_home()), ("research", "/fake/research"),
        ("coder", "/fake/coder")))
    runner.adapters = {Platform.TELEGRAM: primary}
    runner._profile_adapters = {"research": {Platform.TELEGRAM: named}}
    runner._profile_name_for_source = lambda source, adapter_profile=None: None
    primary.gateway_runner = named.gateway_runner = runner
    runner._wire_notion_decision_adapter = lambda adp: Mixin._wire_notion_decision_adapter(runner, adp)
    callback = AsyncMock()
    bindings = [DecisionBinding("default", "default", 111, 111),
                DecisionBinding("research", "research", 111, 111)]
    Mixin.configure_notion_decision_ingress(runner, callback, bindings=bindings)
    await primary._handle_command(fixture_update(), None)
    await named._handle_command(fixture_update(), None)
    assert [x.args[0].profile for x in callback.await_args_list] == ["default", "research"]
    # Broad user-only route is ordinary chat routing, not approval authority.
    runner.config.profile_routes = [ProfileRoute("broad", "telegram", "coder", user_id="111",
                                                bot_profile="research")]
    Mixin.configure_notion_decision_ingress(runner, callback, bindings=[
        bindings[0], DecisionBinding("research", "coder", 111, 111)])
    callback.reset_mock()
    await named._handle_command(fixture_update(), None)
    callback.assert_not_awaited()
    runner.config.profile_routes = [ProfileRoute("exact", "telegram", "coder", user_id="111",
                                                chat_id="111", bot_profile="research")]
    runner._profile_name_for_source = lambda source, adapter_profile=None: "coder"
    runner._wire_notion_decision_adapter(named)
    await named._handle_command(fixture_update(), None)
    assert callback.await_args.args[0].profile == "coder"
    callback.reset_mock()
    runner.config.profile_routes = [ProfileRoute("other-bot", "telegram", "coder", user_id="111",
                                                chat_id="111")]
    await named._handle_command(fixture_update(), None)
    callback.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconfiguration_revokes_captured_callback():
    from gateway.run_adapters import GatewayAdapterLifecycleMixin as Mixin
    from gateway.notion_decision_bootstrap import DecisionBinding
    a = adapter()
    runner = a.gateway_runner
    runner._wire_notion_decision_adapter = lambda adp: Mixin._wire_notion_decision_adapter(runner, adp)
    cb = AsyncMock()
    binding = DecisionBinding("coder", "coder", 111, 111)
    Mixin.configure_notion_decision_ingress(runner, cb, bindings=[binding])
    captured = a._notion_decision_ingress[0]
    Mixin.configure_notion_decision_ingress(runner)
    await captured(NS(profile="coder"))
    cb.assert_not_awaited()


def test_shared_adapter_wiring_bootstraps_default_off_for_primary_and_secondary():
    from unittest.mock import MagicMock
    from gateway.run_adapters import GatewayAdapterLifecycleMixin as Mixin
    runner = MagicMock()
    runner.session_store = object()
    runner._human_delay = None
    runner._busy_text_mode = "off"
    runner._busy_text_timing = None
    transport = MagicMock()
    transport.platform = Platform.TELEGRAM
    Mixin._wire_adapter_handlers(runner, transport)
    runner._wire_notion_decision_adapter.assert_called_once_with(transport)
    runner._wire_notion_decision_adapter.reset_mock()
    Mixin._wire_adapter_handlers(runner, transport, message_handler=AsyncMock())
    runner._wire_notion_decision_adapter.assert_called_once_with(transport)
