"""Prompt-injection defense + LLM output sanitization.

Two independent stages:

  inspect_request(...)      runs BEFORE the LLM sees the prompt.
                            Detects injection patterns, escapes boundary
                            tokens so user content can't break out of its
                            envelope, returns cleaned blocks + a report.

  sanitize_response(...)    runs AFTER the LLM generates a reply.
                            Strips tool-call envelopes the model leaked,
                            redacts secrets (API keys, JWTs, private keys),
                            detects jailbreak-success markers, and falls
                            back to a safe reply if stripping left nothing.

Both stages are non-throwing by default: they log loudly, redact in place,
and let the caller decide whether to proceed. Pass `mode="reject"` to turn
detections into raised exceptions instead.

Design notes
------------
- No imports from pai.orchestration / pai.llm — this must be callable from
  anywhere (decomposer, chat reply, future agents, tool wrappers).
- Patterns are compiled once at import time; per-call cost is regex matching
  against strings that are already in memory.
- Every field the caller might care about is exposed on the result dataclass
  so tracing, tests, and metrics can be added without changing signatures.
"""
from __future__ import annotations

from enum import Enum
import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from pai.config.models.prompts import PromptsGuardConfig

logger = logging.getLogger(__name__)


# ============================================================================
# Boundary tokens — must match what the prompt templates use
# ============================================================================

BOUNDARIES = {
    "user_goal":              ("<<<BEGIN_USER_GOAL>>>",             "<<<END_USER_GOAL>>>"),
    "live_capabilities":      ("<<<BEGIN_LIVE_CAPABILITIES>>>",     "<<<END_LIVE_CAPABILITIES>>>"),
    "past_interactions":      ("<<<BEGIN_PAST_INTERACTIONS>>>",     "<<<END_PAST_INTERACTIONS>>>"),
    "conversation_history":   ("<<<BEGIN_CONVERSATION_HISTORY>>>",  "<<<END_CONVERSATION_HISTORY>>>"),
    "tool_results":           ("<<<BEGIN_TOOL_RESULTS>>>",          "<<<END_TOOL_RESULTS>>>"),
}

# Matches any boundary token, including variants (extra dashes, case changes)
# so user content can't smuggle a fake "END" marker to break out of an envelope.
_BOUNDARY_ESCAPE_RE = re.compile(
    r"<<<\s*(?:BEGIN|END)[\s_]*[A-Z_]{3,40}\s*>>>",
    re.IGNORECASE,
)
#                        ↑ [] not \s, and * not +

# Replacement used when user content contains a boundary token.
_BOUNDARY_REPLACEMENT = "[boundary-token-redacted]"


# ============================================================================
# Injection patterns — matched against user-supplied text
# ============================================================================
# (regex, tag). Tag is surfaced in logs/spans so operators can see what kind
# of attack was attempted without dumping the offending text.
_INJECTION_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(
        r'\b(?:ignore|disregard|forget|override)\s+(?:all\s+)?'
        r'(?:of\s+)?(?:the\s+)?(?:previous|prior|above|earlier|system)\s+'
        r'(?:instructions?|prompts?|directions?|rules?|context)',
        re.IGNORECASE,
    ), "instruction_override"),

    (re.compile(
        r'\byou\s+are\s+(?:now|no\s+longer)\s+(?:a|an|the|DAN|jailbroken|'
        r'unrestricted|free|in\s+developer\s+mode)',
        re.IGNORECASE,
    ), "role_change"),

    (re.compile(
        r'^\s*(?:system|assistant|developer|admin|root)\s*:\s*',
        re.MULTILINE | re.IGNORECASE,
    ), "role_injection"),

    (re.compile(
        r'\b(?:reveal|show|print|output|repeat|leak|tell\s+me)\s+'
        r'(?:your|the)\s+(?:system\s+)?(?:prompt|instructions?|rules|'
        r'system\s+message|hidden\s+context)',
        re.IGNORECASE,
    ), "prompt_extraction"),

    (re.compile(
        r'\b(?:jailbreak|DAN\s+mode|developer\s+mode|god\s+mode|'
        r'sudo\s+mode|unlocked\s+mode)\b',
        re.IGNORECASE,
    ), "jailbreak_keyword"),

    (re.compile(
        r'\bact\s+as\s+(?:if\s+you\s+(?:are|were)|a|an)\s+(?:DAN|unrestricted|'
        r'unfiltered|different\s+ai)',
        re.IGNORECASE,
    ), "role_play_jailbreak"),

    (re.compile(
        r'</?(?:system|instructions?|system_prompt|system-message)>',
        re.IGNORECASE,
    ), "xml_role_tag"),

    (re.compile(
        r'\b(?:new|updated|revised)\s+(?:system\s+)?instructions?\s*:',
        re.IGNORECASE,
    ), "instruction_injection"),
]


# ============================================================================
# Secret patterns — matched against LLM output
# ============================================================================
_SECRET_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r'\bsk-[a-zA-Z0-9]{20,}\b'),                       "openai_key"),
    (re.compile(r'\bsk-ant-[a-zA-Z0-9\-_]{20,}\b'),               "anthropic_key"),
    (re.compile(r'\bghp_[a-zA-Z0-9]{36,}\b'),                     "github_pat"),
    (re.compile(r'\bgithub_pat_[a-zA-Z0-9_]{60,}\b'),             "github_pat_v2"),
    (re.compile(r'\bxox[baprs]-[a-zA-Z0-9\-]{10,}'),              "slack_token"),
    (re.compile(r'\bAIza[0-9A-Za-z_\-]{35}\b'),                   "google_api_key"),
    (re.compile(r'\bAKIA[0-9A-Z]{16}\b'),                         "aws_access_key"),
    (re.compile(r'\bASIA[0-9A-Z]{16}\b'),                         "aws_temp_key"),
    (re.compile(
        r'-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]+?'
        r'-----END [A-Z ]+PRIVATE KEY-----',
    ),                                                             "private_key"),
    (re.compile(
        r'\beyJ[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]{10,}\b',
    ),                                                             "jwt"),
    (re.compile(r'\bBearer\s+[A-Za-z0-9_\-.]{20,}\b'),            "bearer_token"),
    (re.compile(r'\bpostgres(?:ql)?://[^\s"\']+:[^\s"\'@]+@'),    "db_url_with_password"),
    (re.compile(r'\bmongodb(?:\+srv)?://[^\s"\']+:[^\s"\'@]+@'),  "mongo_url_with_password"),
]


# ============================================================================
# Tool-call leak patterns — the model occasionally emits tool syntax anyway
# ============================================================================
_TOOL_CALL_PATTERNS: List[Tuple[re.Pattern, str]] = [
    # Single-line inline JSON: {"tool": "x", ...} or {"capability": "y", ...}
    (re.compile(
        r'^\s*\{\s*"(?:tool|capability|action|function)"\s*:\s*"[^"]+"'
        r'[^\n]*\}\s*$',
        re.MULTILINE,
    ), "inline_json_envelope"),

    # Fenced ```json {...}``` blocks that contain a tool envelope
    (re.compile(
        r'```(?:json)?\s*\{[\s\S]*?"(?:tool|capability|action)"[\s\S]*?```',
    ), "fenced_json_envelope"),

    # Tag-style calls some models emit: <tool_call>location.get</tool_call>, <function_call>...,
    # <tool_use>...; an unclosed opening tag is removed up to the end of the text.
    (re.compile(
        r'<\s*(tool_call|function_call|tool_use|tool)\b[^>]*>[\s\S]*?(?:<\s*/\s*\1\s*>|\Z)',
        re.IGNORECASE,
    ), "tag_tool_call"),

    # Function-call syntax: tool_name(arg1=val, arg2=val)
    (re.compile(
        r'^\s*[a-zA-Z_][\w.]*\.[a-zA-Z_]\w*\([^\n]*\)\s*$',
        re.MULTILINE,
    ), "function_call_syntax"),
]


# ============================================================================
# Jailbreak-success markers — if these appear, model followed injected text
# ============================================================================
_JAILBREAK_SUCCESS_PATTERNS: List[re.Pattern] = [
    re.compile(
        r'\bI(?:\'m| am)\s+(?:now\s+)?(?:DAN|in\s+developer\s+mode|'
        r'jailbroken|unrestricted|free\s+from\s+(?:my|all)\s+(?:rules|'
        r'restrictions|guidelines))',
        re.IGNORECASE,
    ),
    re.compile(
        r'\bI(?:\'ll| will)\s+ignore\s+(?:all\s+)?(?:my|the)\s+'
        r'(?:previous\s+)?(?:instructions?|rules|guidelines)',
        re.IGNORECASE,
    ),
    re.compile(
        r'\bhere\s+(?:is|are)\s+(?:my|the)\s+(?:system\s+)?prompt',
        re.IGNORECASE,
    ),
]


# ============================================================================
# Result dataclasses
# ============================================================================

@dataclass
class RequestInspection:
    """Result of inspect_request(). All block fields are already sanitized."""
    safe: bool
    user_text: str
    memory_block: str
    history_block: str
    tool_results_block: str
    injection_tags: List[Tuple[str, str]] = field(default_factory=list)
    boundary_escapes: int = 0
    fields_touched: List[str] = field(default_factory=list)


@dataclass
class ResponseInspection:
    """Result of sanitize_response(). Use clean_text, ignore the rest."""
    clean_text: str
    original: str
    tool_calls_stripped: int = 0
    secrets_redacted: int = 0
    redacted_types: List[str] = field(default_factory=list)
    jailbreak_success: bool = False
    jailbreak_tags: List[str] = field(default_factory=list)
    fallback_used: bool = False


class PromptInjectionDetected(RuntimeError):
    """Raised in mode='reject' when inspect_request() finds a hit."""


class SensitiveOutputDetected(RuntimeError):
    """Raised in mode='reject' when sanitize_response() finds a hit."""


# ============================================================================
# PromptGuard
# ============================================================================

class PromptGuard:
    """Stateless-ish guard. Safe to share across requests."""


    def __init__(
        self,
        config: PromptsGuardConfig
    ) -> None:
        if not isinstance(config, PromptsGuardConfig):
            raise ValueError("config must be an instance of PromptsGuardConfig")
        
        self.request_mode = config.request_mode
        self.response_mode = config.response_mode
        self.fallback_reply = config.fallback_reply
        self.redact_secrets = config.redact_secrets
        self.strip_tool_calls = config.strip_tool_calls
        self.detect_jailbreak_success = config.detect_jailbreak_success

    # ------------------------------------------------------------------
    # Pre-LLM: inspect and sanitize request-side blocks
    # ------------------------------------------------------------------

    def inspect_request(
        self,
        *,
        user_text: str,
        memory_block: str = "",
        history_block: str = "",
        tool_results_block: str = "",
        span=None,
    ) -> RequestInspection:
        _req_mode = self.request_mode.value if hasattr(self.request_mode, "value") else self.request_mode
        if _req_mode == "off":
            return RequestInspection(
                safe=True,
                user_text=user_text,
                memory_block=memory_block,
                history_block=history_block,
                tool_results_block=tool_results_block,
            )

        result = RequestInspection(
            safe=True,
            user_text=user_text,
            memory_block=memory_block,
            history_block=history_block,
            tool_results_block=tool_results_block,
        )

        # (a) Boundary-token escaping in every untrusted block
        for name, (value, setter) in self._untrusted_blocks(result).items():
            escaped, count = self._escape_boundaries(value)
            if count:
                setter(escaped)
                result.boundary_escapes += count
                result.fields_touched.append(name)

        # (b) Injection pattern detection
        for name, (value, _) in self._untrusted_blocks(result).items():
            for pattern, tag in _INJECTION_PATTERNS:
                if pattern.search(value):
                    result.injection_tags.append((tag, name))
                    result.safe = False

        # (c) Emit span attributes
        if span is not None:
            try:
                span.set_attribute(
                    "guard.request.safe", result.safe,
                )
                span.set_attribute(
                    "guard.request.boundary_escapes",
                    result.boundary_escapes,
                )
                if result.injection_tags:
                    span.set_attribute(
                        "guard.request.injection_tags",
                        [f"{t}:{f}" for t, f in result.injection_tags],
                    )
            except Exception:
                logger.debug("llm_guard: span write failed", exc_info=True)

        # (d) Log findings
        if result.injection_tags:
            logger.warning(
                "PromptGuard: injection patterns detected: %s "
                "(mode=%s, boundary_escapes=%d)",
                result.injection_tags, self.request_mode,
                result.boundary_escapes,
            )

        # (e) Reject mode
        if not result.safe and _req_mode == "reject":
            raise PromptInjectionDetected(
                f"Prompt injection detected in {set(f for _, f in result.injection_tags)}: "
                f"tags={sorted(set(t for t, _ in result.injection_tags))}"
            )

        return result

    # ------------------------------------------------------------------
    # Post-LLM: sanitize the model's response
    # ------------------------------------------------------------------

    def sanitize_response(
        self,
        *,
        raw_text: str,
        span=None,
        request_id: Optional[str] = None,
    ) -> ResponseInspection:
        result = ResponseInspection(clean_text=raw_text, original=raw_text)
        _resp_mode = self.response_mode.value if hasattr(self.response_mode, "value") else self.response_mode

        if _resp_mode == "off":
            return result

        text = raw_text or ""

        # (a) Strip leaked tool-call envelopes
        if self.strip_tool_calls:
            for pattern, _tag in _TOOL_CALL_PATTERNS:
                matches = pattern.findall(text)
                if matches:
                    text = pattern.sub("", text)
                    result.tool_calls_stripped += len(matches)

        # (b) Redact secrets
        if self.redact_secrets:
            for pattern, tag in _SECRET_PATTERNS:
                matches = pattern.findall(text)
                if matches:
                    text = pattern.sub(f"[REDACTED:{tag}]", text)
                    result.secrets_redacted += len(matches)
                    result.redacted_types.append(tag)

        # (c) Detect jailbreak-success markers (informational; we don't
        #     auto-fallback on this because it's rare and the caller can
        #     decide — e.g. log to the security team, rotate a key, etc.)
        if self.detect_jailbreak_success:
            for pattern in _JAILBREAK_SUCCESS_PATTERNS:
                if pattern.search(text):
                    result.jailbreak_success = True
                    result.jailbreak_tags.append(pattern.pattern[:60])

        # (d) Collapse leftover whitespace, trim
        text = re.sub(r'\n{3,}', '\n\n', text).strip()

        # (e) If stripping left nothing, use fallback
        if not text:
            text = self.fallback_reply
            result.fallback_used = True

        result.clean_text = text

        # (f) Span attributes
        if span is not None:
            try:
                span.set_attribute(
                    "guard.response.tool_calls_stripped",
                    result.tool_calls_stripped,
                )
                span.set_attribute(
                    "guard.response.secrets_redacted",
                    result.secrets_redacted,
                )
                span.set_attribute(
                    "guard.response.jailbreak_success",
                    result.jailbreak_success,
                )
                span.set_attribute(
                    "guard.response.fallback_used",
                    result.fallback_used,
                )
            except Exception:
                logger.debug("llm_guard: span write failed", exc_info=True)

        # (g) Log
        if result.tool_calls_stripped or result.secrets_redacted:
            logger.warning(
                "PromptGuard: sanitized response request=%s "
                "tool_calls=%d secrets=%d types=%s "
                "(original_len=%d clean_len=%d)",
                request_id,
                result.tool_calls_stripped,
                result.secrets_redacted,
                sorted(set(result.redacted_types)),
                len(result.original),
                len(result.clean_text),
            )
        if result.jailbreak_success:
            logger.error(
                "PromptGuard: JAILBREAK SUCCESS MARKERS in response "
                "request=%s tags=%s",
                request_id, result.jailbreak_tags,
            )

        # (h) Reject mode
        if _resp_mode == "reject" and (
            result.tool_calls_stripped or result.secrets_redacted
            or result.jailbreak_success
        ):
            raise SensitiveOutputDetected(
                f"Response contained {result.tool_calls_stripped} tool-call "
                f"envelope(s) and {result.secrets_redacted} secret(s); "
                f"jailbreak_success={result.jailbreak_success}"
            )

        return result

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _untrusted_blocks(self, r: RequestInspection):
        """Yield (field_name, (current_value, setter)) for each block.

        Setter writes back to the dataclass. Centralized so escape/injection
        passes don't have to duplicate this plumbing.
        """
        return {
            "user_text": (r.user_text, self._set_user_text(r)),
            "memory_block": (r.memory_block, self._set_memory(r)),
            "history_block": (r.history_block, self._set_history(r)),
            "tool_results_block":
                (r.tool_results_block, self._set_tool_results(r)),
        }

    @staticmethod
    def _set_user_text(r: RequestInspection):
        def _set(v: str) -> None: r.user_text = v
        return _set

    @staticmethod
    def _set_memory(r: RequestInspection):
        def _set(v: str) -> None: r.memory_block = v
        return _set

    @staticmethod
    def _set_history(r: RequestInspection):
        def _set(v: str) -> None: r.history_block = v
        return _set

    @staticmethod
    def _set_tool_results(r: RequestInspection):
        def _set(v: str) -> None: r.tool_results_block = v
        return _set

    @staticmethod
    def _escape_boundaries(text: str) -> Tuple[str, int]:
        """Replace any boundary-like token with a safe marker.

        Prevents user content from containing `<<<END_USER_GOAL>>>` and
        breaking out of its envelope. Returns (new_text, num_replacements).
        """
        if not text:
            return text, 0
        count = 0
        def _sub(m):
            nonlocal count
            count += 1
            return _BOUNDARY_REPLACEMENT
        return _BOUNDARY_ESCAPE_RE.sub(_sub, text), count

# ---------------------------------------------------------------------------
# Public aliases — other defenses (e.g. the memory write/recall guard) reuse the
# same pattern sets instead of copying them, so the rules never drift apart.
# ---------------------------------------------------------------------------
INJECTION_PATTERNS = _INJECTION_PATTERNS
SECRET_PATTERNS = _SECRET_PATTERNS
BOUNDARY_ESCAPE_RE = _BOUNDARY_ESCAPE_RE
BOUNDARY_REPLACEMENT = _BOUNDARY_REPLACEMENT
TOOL_CALL_PATTERNS = _TOOL_CALL_PATTERNS
JAILBREAK_SUCCESS_PATTERNS = _JAILBREAK_SUCCESS_PATTERNS
