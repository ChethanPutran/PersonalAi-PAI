"""Turn a raw task error into something a person can act on.

The raw ``error`` of a failed task is a developer string ("Authorization denied: Path is outside
allowed roots", "Device d1 did not respond in 60.0s", or even nothing at all). Every client sees the
same plain-language version, computed in ONE place (``serialize_task`` and the orchestrator's plan
result), so the Tasks screen, the chat reply and push events can never disagree.

Pure functions, no I/O. The raw text is always kept next to the explanation (for support / logs).
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

from pydantic import BaseModel

# kind: what class of problem this is. Clients may colour/iconify by it.
#   denied       a security rule or the user's own settings blocked it
#   declined     the user said no (or never answered)
#   unavailable  the device / plugin could not be reached
#   timeout      it took too long
#   cancelled    stopped on purpose
#   failed       it ran and went wrong / anything else


class UserError(BaseModel):
    code: str
    kind: str
    message: str                 # one or two plain sentences: what happened
    hint: Optional[str] = None   # what the user can do next
    raw: Optional[str] = None    # the original developer string

    def short(self) -> str:
        return f"{self.message} {self.hint}".strip() if self.hint else self.message


_PREFIXES = re.compile(r"^(authorization (denied|not granted)|security evaluation error)\s*:\s*", re.I)

# (regex, code, kind, message, hint). First match wins: most specific first.
_RULES: list[tuple[re.Pattern[str], str, str, str, Optional[str]]] = [
    (re.compile(r"blocked by system restrictions|critical-risk operation blocked|always denied", re.I),
     "blocked_by_policy", "denied",
     "A built-in safety rule blocks this kind of action, and an approval can't override it.",
     "Ask for something else, or do this one yourself."),
    (re.compile(r"outside allowed roots|path_outside|outside the allowed", re.I), "path_outside_roots", "denied",
     "That location isn't one this plugin is allowed to use.",
     "Pick a folder it can access, such as Documents, Downloads, Pictures or Videos."),
    (re.compile(r"no filesystem roots configured", re.I), "no_roots", "denied",
     "No folders are set up for file access, so the request was blocked.",
     "Reinstall or update the plugin, or ask an admin to configure its allowed folders."),
    (re.compile(r"requires a path|resource does not match", re.I), "path_missing", "denied",
     "The request didn't say which file or folder to use.", "Say which file or folder you mean and try again."),
    (re.compile(r"plugin is not enabled", re.I), "plugin_disabled", "denied",
     "This plugin isn't turned on for your account.", "Open Plugins, enable it, then try again."),
    (re.compile(r"not declared in the plugin manifest|no registered permission", re.I), "capability_unknown", "denied",
     "The plugin doesn't offer that action.", "Update the plugin, or ask for something else."),
    (re.compile(r"remote plugin use is turned off", re.I), "remote_invoke_disabled", "denied",
     "That device doesn't accept remote actions.",
     "On that device, turn on \"Allow remote plugin use\" in the PAI app (or in Settings)."),
    (re.compile(r"not allowlisted|allowed_devices|device.*not allowed", re.I), "device_not_allowed", "denied",
     "That device isn't allowed to run this action.", "Choose one of your own connected devices."),
    (re.compile(r"permission_not_allowed_on_device|permission.*(denied|pending).*device|not allowed on this device",
                re.I), "device_permission", "denied",
     "The device hasn't been given the permission this needs.",
     "Open the plugin's settings on that device and allow the permission."),
    (re.compile(r"needs approval|requires approval|explicit approval|consent is missing|requires user confirmation",
                re.I), "needs_approval", "declined",
     "This action needs your approval, and none was given.",
     "Ask again and approve the prompt on screen. Approvals can't be given by voice."),
    (re.compile(r"user denied|denied by user|declined|rejected by (the )?user", re.I), "user_declined", "declined",
     "You declined this action, so it wasn't run.", None),
    (re.compile(r"no authorization token|invalid authorization token|token rejected|token.*(expired|invalid)", re.I),
     "token_invalid", "denied",
     "The security check for this action expired or didn't pass.", "Try the request again."),
    (re.compile(r"did not respond|timed out|timeout", re.I), "timeout", "timeout",
     "The device didn't answer in time.", "Check that the device is on, online and the app is open, then try again."),
    (re.compile(r"executor is not running|no device_id|no device|not connected|offline|unreachable|device.*unavailable",
                re.I), "device_unavailable", "unavailable",
     "None of your devices could take this action right now.",
     "Make sure the device is on, connected and has the PAI app open."),
    (re.compile(r"cancel", re.I), "cancelled", "cancelled", "This was cancelled before it finished.", None),
    (re.compile(r"permission denied|eacces|operation not permitted|not permitted by (the )?(os|system)", re.I),
     "os_permission", "denied",
     "The device's system refused access.", "Grant the app the needed permission in the device's settings."),
    (re.compile(r"not found|no such file|does not exist|enoent", re.I), "not_found", "failed",
     "The file or item couldn't be found.", "Check the name or location and try again."),
]

_EMPTY = UserError(
    code="no_reason", kind="failed",
    message="This didn't finish, and nothing reported why.",
    hint="Try again. If it keeps happening, check the device is connected.")


def explain_error(error: Any, capability: Optional[str] = None) -> UserError:
    """Explain a raw error string. Always returns something: an empty error is explained too."""
    raw = ("" if error is None else str(error)).strip()
    if not raw:
        return _EMPTY.model_copy()
    for rx, code, kind, message, hint in _RULES:
        if rx.search(raw):
            return UserError(code=code, kind=kind, message=message, hint=hint, raw=raw)
    # Unknown: show the developer text rather than hide it, minus the technical prefix.
    cleaned = _PREFIXES.sub("", raw).strip().rstrip(".")
    what = f" while running {capability}" if capability else ""
    return UserError(
        code="unknown", kind="failed",
        message=f"Something went wrong{what}: {cleaned[:300]}.",
        hint="Try again, and if it keeps failing, check the Tasks screen for details.", raw=raw)


def error_fields(error: Any, capability: Optional[str] = None) -> Dict[str, Any]:
    """The ``error_info`` dict clients receive."""
    return explain_error(error, capability).model_dump(mode="json")
