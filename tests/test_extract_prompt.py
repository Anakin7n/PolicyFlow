"""Regression tests for router prompt extraction.

The router classifies based on the *real* user prompt, not the
``<system-reminder>`` / ``<command-name>`` / ``<ide_selection>`` blocks that
Claude Code splices into the same user turn.  Before the fix, the adapter
merged all text blocks into one string (`"<system-reminder>...</system-reminder>\n审查整个项目..."`)
and the router's `text.startswith("<")` check rejected the entire merged
string, leading every CLAUDE.md keyword (refactor / 架构设计 / tradeoff / 原因)
to be misclassified.
"""

from __future__ import annotations

from policyflow.models import ChatCompletionRequest, Message
from policyflow.router import _extract_prompt, _strip_injected_blocks


def _req(user_content) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model="claude-sonnet-4-6",
        messages=[Message(role="user", content=user_content)],
    )


def test_strip_paired_block():
    text = "<system-reminder>\nrefactor 架构设计 tradeoff\n</system-reminder>\n审查整个项目"
    assert _strip_injected_blocks(text).strip() == "审查整个项目"


def test_strip_multiple_blocks():
    text = (
        "<system-reminder>foo</system-reminder>\n"
        "<ide_selection>bar refactor</ide_selection>\n"
        "实际问题"
    )
    assert _strip_injected_blocks(text).strip() == "实际问题"


def test_strip_preserves_unpaired_angle_brackets():
    """Plain prose with `<` like 'if x < 5' should not be stripped."""
    text = "if x < 5 then refactor"
    assert _strip_injected_blocks(text) == text


def test_strip_handles_attributes():
    text = '<system-reminder attr="x">junk refactor</system-reminder>real prompt'
    assert _strip_injected_blocks(text).strip() == "real prompt"


def test_extract_prompt_string_content():
    """The Claude Code regression scenario: one user message, merged content."""
    raw = (
        "<system-reminder>\n"
        "# CLAUDE.md\n"
        "Don't refactor things that aren't broken. 架构设计 tradeoff 原因\n"
        "</system-reminder>\n"
        "审查整个项目，是否存在代码问题和潜在 bug"
    )
    assert _extract_prompt(_req(raw)) == "审查整个项目，是否存在代码问题和潜在 bug"


def test_extract_prompt_list_content():
    """Anthropic adapter may also pass content as list[{type,text}, ...]."""
    blocks = [
        {"type": "text", "text": "<system-reminder>refactor 架构设计</system-reminder>"},
        {"type": "text", "text": "审查整个项目"},
    ]
    assert _extract_prompt(_req(blocks)) == "<system-reminder>refactor 架构设计</system-reminder>\n审查整个项目".replace(
        "<system-reminder>refactor 架构设计</system-reminder>\n", ""
    )


def test_extract_prompt_pure_injection_falls_back():
    """If every user message is 100% injected blocks, fall back to raw text
    so we still have something to classify on (rare, but possible during a
    tool-result-only turn)."""
    raw = "<system-reminder>nothing real here</system-reminder>"
    # cleaned is empty → falls back to last_user's raw text
    assert _extract_prompt(_req(raw)) == raw


def test_extract_prompt_skips_assistant_messages():
    req = ChatCompletionRequest(
        model="claude-sonnet-4-6",
        messages=[
            Message(role="user", content="第一轮真问题"),
            Message(role="assistant", content="...回答..."),
            Message(role="user", content="<system-reminder>noise refactor</system-reminder>\n继续"),
        ],
    )
    # Latest user message has real text after stripping → use it
    assert _extract_prompt(req) == "继续"
