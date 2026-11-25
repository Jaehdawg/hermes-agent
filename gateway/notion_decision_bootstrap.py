"""Default-off, owner-configured Telegram decision service registration."""
from dataclasses import dataclass
import inspect
import re
import os
from pathlib import Path


_PROFILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")


def client_from_config(raw):
    """Parse an explicit gateway config block; never infer authority from model metadata."""
    from plugins.platforms.telegram.decision_service import UnixDecisionServiceClient
    if not isinstance(raw, dict) or raw.get("enabled") is not True:
        raise ValueError("decision service must be explicitly enabled")
    if set(raw) != {"enabled", "socket_path", "model_uid", "service_uid", "bindings", "unsafe_same_uid_best_effort"}:
        # The risk flag is optional in strict mode.
        if set(raw) != {"enabled", "socket_path", "model_uid", "service_uid", "bindings"}:
            raise ValueError("unexpected or missing decision service configuration")
    path = raw["socket_path"]
    if not isinstance(path, str) or not path.startswith("/") or len(os.fsencode(path)) >= 104:
        raise ValueError("absolute, bounded decision socket path required")
    uid, service_uid = raw["model_uid"], raw["service_uid"]
    if type(uid) is not int or uid < 0 or type(service_uid) is not int or service_uid < 0:
        raise ValueError("model_uid and service_uid must be nonnegative integers")
    risk = raw.get("unsafe_same_uid_best_effort", False)
    if type(risk) is not bool:
        raise ValueError("unsafe_same_uid_best_effort must be an explicit boolean")
    if len({os.getuid(), uid, service_uid}) != 3 and not risk:
        raise PermissionError("approval requires distinct gateway, model and service UIDs")
    entries = raw["bindings"]
    if not isinstance(entries, list) or not entries:
        raise ValueError("explicit decision bindings required")
    bindings = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"bot_profile", "profile", "sender_id", "chat_id"}:
            raise ValueError("exact decision binding fields required")
        bindings.append(DecisionBinding(**entry))
    client = UnixDecisionServiceClient(Path(path), uid, service_uid, unsafe_same_uid_best_effort=risk)
    validate_bindings(client.decide, bindings)
    return client, tuple(bindings)


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
