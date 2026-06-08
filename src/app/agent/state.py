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

    Attributes:
        messages: Inherited from ``MessagesState``.  Full conversation
            history; new messages are appended via the built-in
            ``add_messages`` reducer — never overwrites existing entries.
        user_profile: Serialised ``UserProfile`` JSON loaded and merged by
            ``ProfilerNode`` from ``AsyncPostgresStore`` at the start of
            each turn.  ``None`` when no profile has been saved yet.
        retrieved_products: List of ``ProductPayload`` dicts returned by
            ``ProductRAGAgent`` during the current turn.  Empty list when
            the subagent has not run yet.
        trend_summary: Concise 2-3 sentence trend report produced by
            ``TrendScoutNode``, or ``None`` when the node has not run yet
            this turn.  Does NOT contain the image prompt (see
            ``image_prompt``).
        thread_title: Proposed (or finalised) thread title set by
            ``TitleGenerationNode``, or ``None`` until generated.
        correlation_id: Request-scoped UUID4 string assigned once at the
            API boundary when a ``POST /runs/stream`` request arrives
            (FR-001, FR-004).  Flows unchanged through every graph node.

            Bind it to structlog context vars inside each node so every
            log record in that async context is automatically tagged::

                structlog.contextvars.bind_contextvars(
                    correlation_id=state["correlation_id"]
                )

            Also forwarded to LangSmith via ``config["metadata"]`` so
            each LLM trace is linked to the originating HTTP request
            (NFR-021).

        user_id: Saleor user ID extracted from the request JWT.
        thread_id: UUID string identifying the current chat session.
        intent: Orchestrator classification result written by
            ``OrchestratorNode`` after each LLM routing call (FR-058).
            ``None`` before the orchestrate node runs for the first time.

            Valid values:

            - ``'need_product_search'`` — dispatch ``ProductRAGAgent``
            - ``'need_trend_info'`` — dispatch ``TrendScoutNode``
            - ``'sufficient'`` — enough data gathered; go to synthesize
            - ``'clarification_needed'`` — ask a clarifying question
            - ``'out_of_scope'`` — request is outside the assistant scope
            - ``'fallback'`` — forced route when step budget is low or no
              other intent applies

        title_generated: Whether the thread title has already been
            generated and persisted.  Loaded from the ``threads`` table at
            run start.  When ``False``, ``TitleGenerationNode`` runs as a
            parallel branch (FR-064).
        fallback_count: Number of consecutive turns where no satisfactory
            recommendation could be assembled.  Supplementary guard in
            addition to LangGraph ``config["remaining_steps"]`` (FR-059).
        image_url: Public S3 URL of the generated image after
            ``ImageGenerationNode`` completes, or ``None`` (FR-050,
            FR-053).  Image generation runs inside the graph — NOT via
            Celery (FR-048).
        image_prompt: Text-to-image prompt produced by ``TrendScoutNode``
            (as part of ``TrendScoutOutput.image_prompt``), or ``None``
            when not applicable.  ``ImageGenerationNode`` reads this value
            to call DALL-E; the prompt is also included in the
            ``image_ready`` SSE event (FR-053).
        summary: Accumulated conversation summary produced by
            ``SummarizeNode``.  Empty string when no summarisation has
            occurred yet.  Overwritten each time the message count reaches
            ``MESSAGE_SUMMARIZE_THRESHOLD``.  Injected as context by
            ``ResponseGeneratorNode``, ``ProductRAGAgent``, and
            ``TrendScoutNode`` after old messages are removed.
        generate_image: Whether the client requested image generation for
            this turn (FR-047).  Set once at the API boundary from
            ``ChatRequest.generate_image`` and remains constant through
            the full graph run.  ``ImageGenerationNode`` reads this flag
            to decide whether to proceed.
        first_user_message: Text of the very first ``HumanMessage`` in
            this thread.  Populated at the API boundary on the first turn;
            ``None`` until then.  ``TitleGenerationNode`` uses this as the
            seed for generating the thread title (FR-022).
    """

    # ── FR-057 required fields ─────────────────────────────────────────────
    # messages is inherited from MessagesState with the add_messages reducer
    user_profile: dict | None
    retrieved_products: list[dict]
    trend_summary: str | None
    thread_title: str | None
    correlation_id: str

    # ── Routing & control ──────────────────────────────────────────────────
    user_id: str
    thread_id: str
    intent: str | None
    title_generated: bool
    fallback_count: int
    image_url: str | None
    image_prompt: str | None

    # ── Memory & generation control (DRAFT 0.6) ───────────────────────────
    summary: str
    generate_image: bool
    first_user_message: str | None
