"""Prefix-stability audit — does the bytes-on-wire prefix stay identical
across turns of the same conversation?

PolicyFlow's session-sticky routing assumes the upstream's prompt cache fires
on every turn after the first.  That assumption only holds if the bytes
PolicyFlow sends are themselves stable — same model, same system, same tools,
same earlier messages, byte-for-byte.  If anything in PolicyFlow's adapter
chain mutates that prefix between turns, the cache evicts every turn and the
upstream charges full-miss prices on 50k+ tokens of repeated context.

This file is a one-shot audit, not a runtime probe.  Each test simulates a
multi-turn session in memory, builds the payload PolicyFlow would actually
forward, and asserts that the cache prefix is byte-identical.  If a test
fails, the diff in the assertion message names the field that's moving.

Run::
    pytest tests/test_prefix_stability.py -v
"""

from __future__ import annotations

import hashlib
import json

import pytest

from policyflow.anthropic_adapter import (
    anthropic_to_chat_request,
    openai_to_anthropic_request,
)
from policyflow.models import ChatCompletionRequest, Message


# ─────────────────────────────────────────────────────────────────────
# Helpers — mirror UpstreamProxy._build_payload without instantiating it
# ─────────────────────────────────────────────────────────────────────

def _build_openai_payload(req: ChatCompletionRequest) -> dict:
    """Same logic as ``UpstreamProxy._build_payload`` on the OpenAI branch."""
    payload = req.model_dump(exclude_none=True, exclude={"extra"})
    for k, v in (req.extra or {}).items():
        if not k.startswith("_"):
            payload[k] = v
    return payload


def _build_anthropic_payload(req: ChatCompletionRequest) -> dict:
    """Same logic as ``UpstreamProxy._build_payload`` on the Anthropic branch."""
    return openai_to_anthropic_request(req)


def _cache_prefix_bytes(payload: dict, n_prefix_messages: int) -> bytes:
    """Serialize the cache-prefix portion of a payload deterministically.

    Cache key = model + system + tools + first N messages.  Anything past
    those N messages is the current turn's tail and is allowed to differ.
    """
    parts = {
        "model": payload.get("model"),
        "system": payload.get("system"),
        "tools": payload.get("tools"),
        "messages": payload.get("messages", [])[:n_prefix_messages],
    }
    # sort_keys=False because we want to see real-world output;
    # if pydantic / our adapter is unstable, that *is* the bug.
    return json.dumps(parts, ensure_ascii=False, sort_keys=False).encode("utf-8")


def _diff_prefixes(a: dict, b: dict, n: int) -> str:
    """Human-readable unified diff between two prefix payloads."""
    import difflib
    def fmt(p):
        return json.dumps({
            "model": p.get("model"),
            "system": p.get("system"),
            "tools": p.get("tools"),
            "messages": p.get("messages", [])[:n],
        }, indent=2, ensure_ascii=False)
    return "\n".join(difflib.unified_diff(
        fmt(a).splitlines(), fmt(b).splitlines(),
        fromfile="turn N (cached)", tofile="turn N+1 (sent now)",
        lineterm="",
    ))


# ─────────────────────────────────────────────────────────────────────
# Fixtures — realistic Claude Code multi-turn session
# ─────────────────────────────────────────────────────────────────────

SYSTEM_TEXT = "You are Claude Code, Anthropic's official CLI."

TOOLS_ANTHROPIC = [
    {
        "name": "Bash",
        "description": "Run a shell command",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
    {
        "name": "Read",
        "description": "Read a file from disk",
        "input_schema": {
            "type": "object",
            "properties": {"file_path": {"type": "string"}},
            "required": ["file_path"],
        },
    },
]


def _simulate_anthropic_session(n_turns: int) -> list[dict]:
    """Build N successive Anthropic Messages bodies for one conversation.

    Every other turn includes an assistant tool_use + user tool_result pair,
    matching how Claude Code actually drives the API in real sessions.
    """
    history: list[dict] = []
    bodies: list[dict] = []
    for i in range(n_turns):
        msgs = list(history) + [
            {"role": "user", "content": [{"type": "text", "text": f"please do task {i}"}]},
        ]
        bodies.append({
            "model": "claude-sonnet-4-6",
            "system": SYSTEM_TEXT,
            "tools": TOOLS_ANTHROPIC,
            "messages": msgs,
            "max_tokens": 1024,
            "stream": False,
            "temperature": 0.7,
        })
        # Extend history with the user msg + a synthetic assistant reply
        history.append(msgs[-1])
        if i % 2 == 0:
            history.append({
                "role": "assistant",
                "content": [
                    {"type": "text", "text": f"sure, running task {i}"},
                    {
                        "type": "tool_use",
                        "id": f"toolu_t{i}_abc",
                        "name": "Bash",
                        "input": {"command": "ls"},
                    },
                ],
            })
            history.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": f"toolu_t{i}_abc",
                    "content": "file1.txt\nfile2.txt",
                }],
            })
        else:
            history.append({
                "role": "assistant",
                "content": [{"type": "text", "text": f"done with task {i}"}],
            })
    return bodies


# ─────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("n_turns", [3, 5])
def test_openai_path_prefix_stable(n_turns):
    """Pure OpenAI flow: each turn's cache prefix must equal the previous
    turn's payload bytes (everything except this turn's new user message).
    """
    history: list[Message] = [Message(role="system", content="be helpful")]
    payloads: list[dict] = []
    for i in range(n_turns):
        history.append(Message(role="user", content=f"please do task {i}"))
        req = ChatCompletionRequest(
            model="deepseek-v4-flash",
            messages=list(history),
            stream=False,
            temperature=0.7,
            max_tokens=1024,
        )
        payloads.append(_build_openai_payload(req))
        history.append(Message(role="assistant", content=f"done with task {i}"))

    # For each turn i ≥ 1: payload[i].messages[:len(payload[i-1].messages)]
    # must be byte-identical to payload[i-1].messages.
    for i in range(1, n_turns):
        prev_len = len(payloads[i - 1]["messages"])
        h_prev = hashlib.sha256(_cache_prefix_bytes(payloads[i - 1], prev_len)).hexdigest()
        h_cur = hashlib.sha256(_cache_prefix_bytes(payloads[i], prev_len)).hexdigest()
        assert h_prev == h_cur, (
            f"OpenAI prefix drifted between turn {i - 1} and turn {i}:\n"
            + _diff_prefixes(payloads[i - 1], payloads[i], prev_len)
        )


@pytest.mark.parametrize("n_turns", [3, 5])
def test_anthropic_round_trip_prefix_stable(n_turns):
    """Claude-Code flow: Anthropic in → router's OpenAI form → Anthropic out
    (the case when PolicyFlow routes back to api.anthropic.com).  The outbound
    Anthropic body's prefix must be byte-stable across turns.
    """
    bodies = _simulate_anthropic_session(n_turns)
    outbound: list[dict] = []
    for body in bodies:
        req = anthropic_to_chat_request(body)
        outbound.append(_build_anthropic_payload(req))

    for i in range(1, n_turns):
        prev_len = len(outbound[i - 1]["messages"])
        h_prev = hashlib.sha256(_cache_prefix_bytes(outbound[i - 1], prev_len)).hexdigest()
        h_cur = hashlib.sha256(_cache_prefix_bytes(outbound[i], prev_len)).hexdigest()
        assert h_prev == h_cur, (
            f"Anthropic round-trip prefix drifted between turn {i - 1} and turn {i}:\n"
            + _diff_prefixes(outbound[i - 1], outbound[i], prev_len)
        )


def test_anthropic_system_list_preserved():
    """Anthropic clients send ``system`` as a list of typed blocks, often with
    ``cache_control: {"type": "ephemeral"}`` on the last block to mark the
    cache breakpoint.  PolicyFlow's adapter must round-trip that list — if it
    collapses to a plain string, the cache_control marker is destroyed and the
    upstream's prompt cache never fires.
    """
    body = {
        "model": "claude-sonnet-4-6",
        "system": [
            {"type": "text", "text": "You are Claude Code."},
            {
                "type": "text",
                "text": "Big context block...",
                "cache_control": {"type": "ephemeral"},
            },
        ],
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        ],
        "max_tokens": 1024,
    }
    req = anthropic_to_chat_request(body)
    out = openai_to_anthropic_request(req)

    sys_field = out.get("system")
    if isinstance(sys_field, str):
        pytest.fail(
            "system field collapsed to plain string after round-trip — "
            "the cache_control breakpoint is lost.\n"
            f"  Original  : {body['system']!r}\n"
            f"  Round-trip: {sys_field!r}"
        )
    if not isinstance(sys_field, list):
        pytest.fail(f"system became {type(sys_field).__name__}, expected list: {sys_field!r}")
    has_cache_marker = any(
        isinstance(b, dict) and b.get("cache_control")
        for b in sys_field
    )
    assert has_cache_marker, (
        f"cache_control marker missing from round-tripped system blocks:\n{sys_field!r}"
    )


def test_anthropic_tool_use_id_preserved():
    """Assistant tool_use blocks carry stable IDs from Anthropic.  If the
    adapter ever generates a fresh ``uuid.uuid4()`` for a missing id, the
    same assistant turn will hash differently on every replay, breaking cache.
    """
    body = {
        "model": "claude-sonnet-4-6",
        "system": SYSTEM_TEXT,
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "list files"}]},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "running"},
                    {
                        "type": "tool_use",
                        "id": "toolu_abc123",
                        "name": "Bash",
                        "input": {"command": "ls"},
                    },
                ],
            },
            {
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": "toolu_abc123",
                    "content": "a.txt",
                }],
            },
            {"role": "user", "content": [{"type": "text", "text": "ok"}]},
        ],
        "max_tokens": 1024,
    }
    # Replay the same body twice — outbound bytes must be identical.
    a = _build_anthropic_payload(anthropic_to_chat_request(body))
    b = _build_anthropic_payload(anthropic_to_chat_request(body))
    assert json.dumps(a, ensure_ascii=False) == json.dumps(b, ensure_ascii=False), (
        "Identical input produced different outbound bytes — "
        "a non-deterministic field (likely a fresh uuid) is being generated.\n"
        + _diff_prefixes(a, b, len(a.get("messages", [])))
    )


def test_message_cache_control_preserved():
    """Anthropic clients (Claude Code in particular) put ``cache_control`` on
    individual content blocks inside ``messages`` to mark cache breakpoints
    deeper than the system prompt.  This is the most common cache marker in
    real traffic — losing it kills the cache for every long conversation.
    """
    body = {
        "model": "claude-sonnet-4-6",
        "system": SYSTEM_TEXT,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "earlier context " * 1000},
                    {
                        "type": "text",
                        "text": "marker block",
                        "cache_control": {"type": "ephemeral"},
                    },
                ],
            },
            {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
            {"role": "user", "content": [{"type": "text", "text": "new question"}]},
        ],
        "max_tokens": 1024,
    }
    out = _build_anthropic_payload(anthropic_to_chat_request(body))
    serialized = json.dumps(out, ensure_ascii=False)
    assert '"cache_control"' in serialized, (
        "cache_control marker on user message block was dropped after round-trip "
        "— prompt cache breakpoint is destroyed.\n"
        f"  Outbound messages: {out.get('messages')!r}"
    )


def test_tools_cache_control_preserved():
    """Anthropic also supports ``cache_control`` on the last tool definition
    to cache the entire tools array (often 30-50k tokens for agent clients).
    """
    body = {
        "model": "claude-sonnet-4-6",
        "system": SYSTEM_TEXT,
        "tools": [
            {
                "name": "Bash",
                "description": "Run a shell command",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "Read",
                "description": "Read a file",
                "input_schema": {"type": "object", "properties": {}},
                "cache_control": {"type": "ephemeral"},
            },
        ],
        "messages": [{"role": "user", "content": [{"type": "text", "text": "go"}]}],
        "max_tokens": 1024,
    }
    out = _build_anthropic_payload(anthropic_to_chat_request(body))
    tools = out.get("tools") or []
    last = tools[-1] if tools else {}
    assert last.get("cache_control") == {"type": "ephemeral"}, (
        "cache_control marker on last tool definition was dropped — "
        "tools-array cache breakpoint is destroyed.\n"
        f"  Outbound tools: {tools!r}"
    )


def test_anthropic_raw_stash_not_leaked_to_openai_upstream():
    """The ``_anthropic_raw`` stash is internal adapter state.  Forwarding it
    to an OpenAI-compatible upstream would bloat every request with the full
    original Anthropic body — and Pydantic strict-mode upstreams would 400.
    """
    body = {
        "model": "claude-sonnet-4-6",
        "system": SYSTEM_TEXT,
        "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        "max_tokens": 1024,
    }
    req = anthropic_to_chat_request(body)
    assert "_anthropic_raw" in req.extra, "stash setup invariant broken"

    openai_payload = _build_openai_payload(req)
    assert "_anthropic_raw" not in openai_payload, (
        "_anthropic_raw stash leaked into OpenAI-bound payload — "
        "upstream will reject or silently waste 50k+ tokens per request.\n"
        f"  Keys in payload: {list(openai_payload.keys())}"
    )
    # No underscore-prefixed key should ever reach the wire.
    leaked = [k for k in openai_payload if k.startswith("_")]
    assert not leaked, f"Internal stash keys leaked to upstream: {leaked}"


def test_anthropic_round_trip_byte_identical():
    """End-to-end: Anthropic body → OpenAI form → Anthropic body must produce
    an outbound body byte-identical to the input (modulo ``model`` and the
    minor sync fields the stash path overwrites).
    """
    body = {
        "model": "claude-sonnet-4-6",
        "system": [
            {"type": "text", "text": "core"},
            {"type": "text", "text": "extra", "cache_control": {"type": "ephemeral"}},
        ],
        "tools": [{
            "name": "Bash",
            "description": "shell",
            "input_schema": {"type": "object", "properties": {}},
            "cache_control": {"type": "ephemeral"},
        }],
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "step 1"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
            {"role": "user", "content": [
                {"type": "text", "text": "step 2", "cache_control": {"type": "ephemeral"}},
            ]},
        ],
        "max_tokens": 2048,
        "stream": False,
        "temperature": 0.7,
        "metadata": {"user_id": "test-user-42"},
    }
    out = _build_anthropic_payload(anthropic_to_chat_request(body))
    # Anthropic-only fields the OpenAI intermediate doesn't carry —
    # but the stash path should preserve them verbatim.
    assert out["system"] == body["system"]
    assert out["tools"] == body["tools"]
    assert out["messages"] == body["messages"]
    assert out.get("metadata") == body["metadata"]
