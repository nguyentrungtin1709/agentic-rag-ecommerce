"""Unit tests for app.agent.state.AgentState."""

from __future__ import annotations

import typing


def test_agent_state_inherits_messages_state() -> None:
    from app.agent.state import AgentState

    # TypedDicts resolve to (dict,) in __bases__, so we verify inheritance by
    # checking that the 'messages' key (defined on MessagesState) is present
    # in the resolved type hints of AgentState.
    hints = typing.get_type_hints(AgentState)
    assert "messages" in hints, "AgentState must inherit 'messages' from MessagesState"


def test_agent_state_has_core_conversation_fields() -> None:
    from app.agent.state import AgentState

    annotations = AgentState.__annotations__
    for field in (
        "user_profile",
        "retrieved_products",
        "trend_summary",
        "thread_title",
        "correlation_id",
    ):
        assert field in annotations, f"AgentState is missing core field: {field}"


def test_agent_state_has_routing_fields() -> None:
    from app.agent.state import AgentState

    annotations = AgentState.__annotations__
    for field in (
        "user_id",
        "thread_id",
        "intent",
        "title_generated",
        "fallback_count",
        "image_url",
        "image_prompt",
    ):
        assert field in annotations, f"Missing routing field: {field}"


def test_agent_state_has_summary_field() -> None:
    """summary field must exist (DRAFT 0.6 — SummarizeNode)."""
    from app.agent.state import AgentState

    assert "summary" in AgentState.__annotations__


def test_agent_state_has_generate_image_field() -> None:
    """generate_image field must exist (FR-047)."""
    from app.agent.state import AgentState

    assert "generate_image" in AgentState.__annotations__


def test_agent_state_has_first_user_message_field() -> None:
    """first_user_message field must exist (FR-022)."""
    from app.agent.state import AgentState

    assert "first_user_message" in AgentState.__annotations__


def test_agent_state_summary_is_str_type() -> None:
    from app.agent.state import AgentState

    # Use get_type_hints to resolve ForwardRefs produced by 'from __future__ import annotations'.
    hints = typing.get_type_hints(AgentState)
    assert hints["summary"] is str


def test_agent_state_first_user_message_allows_none() -> None:
    from app.agent.state import AgentState

    hints = typing.get_type_hints(AgentState)
    annotation = hints["first_user_message"]
    # str | None resolves to types.UnionType; Optional[str] resolves to typing.Optional[str]
    import types

    is_union = isinstance(annotation, types.UnionType)
    is_optional = getattr(annotation, "__origin__", None) is typing.Union
    assert is_union or is_optional, f"Expected str | None, got {annotation}"
