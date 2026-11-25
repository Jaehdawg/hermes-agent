"""Process-local, default-off Telegram decision callback registration.

No sender or decision service is supplied here. A privileged integrator must explicitly
provide the callback and every bot/target/owner binding; config/env cannot activate it.
"""
from dataclasses import dataclass
import inspect
import re


_PROFILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")


@dataclass(frozen=True)
class DecisionBinding:
    bot_profile: str
    profile: str
    sender_id: int
    chat_id: int

    def __post_init__(self):
        if (not isinstance(self.bot_profile, str) or not _PROFILE.fullmatch(self.bot_profile)
                or not isinstance(self.profile, str) or not _PROFILE.fullmatch(self.profile)
                or type(self.sender_id) is not int or self.sender_id <= 0
                or type(self.chat_id) is not int or self.chat_id <= 0
                or self.sender_id != self.chat_id):
            raise ValueError("Decision binding requires exact bot/target profiles and private owner chat")


def validate_bindings(callback, bindings):
    if not callable(callback) or not inspect.iscoroutinefunction(callback):
        raise ValueError("Decision ingress requires an async callback")
    try:
        bindings = tuple(bindings)
    except TypeError as exc:
        raise ValueError("Decision bindings must be explicit") from exc
    if not bindings or any(not isinstance(b, DecisionBinding) for b in bindings):
        raise ValueError("Decision bindings must be explicit and typed")
    if len({b.bot_profile for b in bindings}) != len(bindings):
        raise ValueError("Only one private owner binding per receiving bot")
    return bindings


def binding_for_adapter(runner, adapter, bindings):
    if getattr(getattr(adapter, "platform", None), "value", None) != "telegram":
        return None
    owner = getattr(adapter, "_owner_profile", None) or "default"
    binding = next((b for b in bindings if b.bot_profile == owner), None)
    if binding is None:
        return None
    config = getattr(runner, "config", None)
    if getattr(config, "multiplex_profiles", False):
        homes = getattr(config, "_runtime_profile_homes", ()) or ()
        if binding.profile not in {name for name, _ in homes} or owner not in {name for name, _ in homes}:
            return None
    elif binding.profile != owner:
        return None
    if binding.profile != owner:
        # Cross-profile decisions demand an exact, bot-scoped route; broad chat
        # routing is suitable for ordinary chat but cannot authorize an approval.
        routes = getattr(config, "profile_routes", ()) or ()
        if not any(r.enabled and r.platform == "telegram" and r.profile == binding.profile
                   and (r.bot_profile or "default") == owner
                   and r.user_id == str(binding.sender_id) and r.chat_id == str(binding.chat_id)
                   for r in routes):
            return None
    return binding
