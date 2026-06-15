"""TrendScout subgraph state definition.

Extends ``langchain.agents.AgentState`` (re-exported from
``langchain.agents.middleware.types``) which already carries:

- ``messages`` with the ``add_messages`` reducer.
- ``structured_response`` (auto-populated when ``response_format``
  is passed to ``create_agent``).
- ``jump_to`` (internal control field).

The TrendScout subgraph adds a single domain field:
``generate_image``, which the dynamic SystemMessage builder
reads to decide whether to instruct the agent to emit an
``image_prompt`` in its structured response.

The wrapper (``run_trend_scout``) translates the parent
``AgentState`` into this ``TrendScoutState``, invokes the
compiled subgraph, and maps the result back.
"""

from __future__ import annotations

from langchain.agents import AgentState


class TrendScoutState(AgentState, total=False):
    """Transient state for the TrendScout subgraph.

    Attributes:
        messages: Inherited from ``langchain.agents.AgentState`` with
            the ``add_messages`` reducer. ``messages[0]`` is the
            dynamic ``SystemMessage`` built by the wrapper; the rest
            are forwarded from the parent ``AgentState``.
        structured_response: Inherited. Auto-populated by the
            ``create_agent`` ``response_format`` machinery. The
            wrapper reads it from the result and writes the
            relevant fields back to the parent state.
        generate_image: ``True`` if the parent graph asked the
            agent to include a text-to-image prompt in its
            response. Read by ``_build_trend_scout_system`` to
            append the "Output note" section to the system prompt.
    """

    generate_image: bool
