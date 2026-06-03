"""LangGraph agent state definition for the POD Stylist.

The ``AgentState`` TypedDict is the shared state object passed between
all nodes in the agent graph.  It is persisted to PostgreSQL via
``AsyncPostgresSaver`` using the thread ID as the checkpoint key.

Canonical field list defined by FR-057.  Additional routing and control
fields are appended after the required set.
"""

from __future__ import annotations

from langgraph.graph import MessagesState


class AgentState(MessagesState):
    """Shared mutable state for the POD Stylist agent graph.

    Required fields (FR-057)
    ------------------------
    messages:
        Inherited from ``MessagesState``.  Full conversation history;
        new messages are appended via the built-in ``add_messages``
        reducer — never overwrites existing entries.
    user_profile:
        Serialised ``UserProfile`` JSON loaded and merged by the
        ``profiler`` node from ``AsyncPostgresStore`` at the start of
        each turn.
    retrieved_products:
        List of ``ProductPayload`` dicts returned by the ``product_rag``
        subagent during the current turn.
    trend_summary:
        Free-text trend report returned by ``trend_scout``, or ``None``
        when the node has not run yet this turn.
    thread_title:
        Proposed (or finalised) thread title; ``None`` until the
        ``generate_title`` node produces one.
    correlation_id:
        Request-scoped UUID4 string assigned once at the API boundary
        when a ``POST /api/v1/threads/{thread_id}/runs/stream`` request
        arrives (FR-001, FR-004).  Flows unchanged through every graph node.

        Usage pattern in nodes::

            import structlog

            structlog.contextvars.bind_contextvars(
                correlation_id=state["correlation_id"]
            )

        After ``bind_contextvars``, every ``logger.*()`` call in that
        async context automatically includes ``correlation_id`` in the
        JSON log record — no need to pass it as a keyword argument
        each time (FR-067, FR-111).

        Also forwarded to LangSmith via ``config["metadata"]`` so each
        LLM trace is labelled with the originating HTTP request
        (NFR-021)::

            config = {"metadata": {"correlation_id": correlation_id}}

    Routing & control fields
    -------------------------
    user_id:
        Saleor user ID extracted from the request JWT.
    thread_id:
        UUID string identifying the current chat session.
    intent:
        Orchestrator classification result: ``'sufficient'``,
        ``'clarification_needed'``, ``'out_of_scope'``, or ``'fallback'``
        (FR-058).  ``None`` before the orchestrate node runs.
    title_generated:
        Loaded from the ``threads`` table at run start.  When ``False``
        the ``generate_title`` node runs as a parallel branch (FR-064).
    fallback_count:
        Number of consecutive turns where no satisfactory recommendation
        could be assembled.  Supplementary to LangGraph
        ``config["remaining_steps"]`` (FR-059).
    image_url:
        Public S3 URL of the inline-generated image after the
        ``ImageGenerationNode`` completes, or ``None`` (FR-050, FR-053).
        Image generation runs inside the graph — NOT via Celery (FR-048).
    image_prompt:
        The DALL-E prompt synthesised by ``ImageGenerationNode``, or
        ``None``.  Included in the ``image_ready`` SSE event (FR-053).
    """

    # ── FR-057 required fields ─────────────────────────────────────────────
    # messages is inherited from MessagesState with the add_messages reducer
    user_profile: dict | None
    retrieved_products: list[dict]
    trend_summary: str | None
    thread_title: str | None
    correlation_id: str

    # ── Routing & control ─────────────────────────────────────────────────
    user_id: str
    thread_id: str
    intent: str | None
    title_generated: bool
    fallback_count: int
    image_url: str | None
    image_prompt: str | None
