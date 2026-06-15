"""TrendScout subagent wrapper for the parent graph (D11.12 + D11.13).

Builds and exposes the ``create_agent`` compiled subgraph that runs
the ReAct loop with Tavily (single exposed tool, internal DDG
fallback per D11.12) and returns a ``TrendScoutOutput`` as
structured response. A ``SummarizationMiddleware`` (D11.13) is
attached to keep the subagent's message history bounded for very
long threads.

The wrapper translates ``AgentState`` -> ``TrendScoutState``,
invokes the compiled agent, and maps the result back.

Compiled once at import time as ``_TREND_SCOUT_GRAPH``.

Public entry point used by the parent graph::

    builder.add_node("run_trend_scout", run_trend_scout)

Returns a partial state update with ``trend_summary`` and
``image_prompt`` keys. Returns both as ``None`` when both search
tools fail (D11.6).
"""

from __future__ import annotations

from typing import Any, cast

import structlog
from langchain.agents import create_agent
from langchain.agents.middleware import SummarizationMiddleware
from langchain.agents.middleware.summarization import ContextTokens
from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI

from app.agent.state import AgentState
from app.agent.subagents.trend_scout.schemas import TrendScoutOutput
from app.agent.subagents.trend_scout.state import TrendScoutState
from app.agent.subagents.trend_scout.system import _build_trend_scout_system
from app.agent.subagents.trend_scout.tools import tavily_search
from app.config import get_settings

logger = structlog.get_logger(__name__)


# D11.13: fallback threshold used when ``model.profile`` does not
# expose ``max_input_tokens`` (e.g. for project-specific model
# names like ``gpt-5.4-mini`` that are not in the open models.dev
# catalog). Matches the conservative default suggested in the
# ``SummarizationMiddleware`` docs.
_SUMMARIZE_FALLBACK_TOKENS = 100_000


def _resolve_summarize_trigger(model: ChatOpenAI) -> ContextTokens:
    """Compute ``SummarizationMiddleware`` trigger from model profile.

    Returns ``("tokens", N)`` where ``N = int(0.8 *
    max_input_tokens)``. The explicit ``tokens`` form is used (not
    ``("fraction", 0.8)``) so the threshold is visible in logs and
    test assertions.

    If the model profile is missing (no models.dev data for this
    model, or the partner package returned an empty profile),
    falls back to ``_SUMMARIZE_FALLBACK_TOKENS`` and logs a warning.
    The middleware requires ``langchain>=1.1`` and
    ``model.profile["max_input_tokens"]`` for the ``fraction`` form;
    the explicit ``tokens`` form is used here so the threshold is
    visible in logs and tests.
    """
    profile = getattr(model, "profile", None) or {}
    max_input = profile.get("max_input_tokens") if isinstance(profile, dict) else None
    if max_input:
        trigger_tokens = int(0.8 * max_input)
        logger.info(
            "trend_scout_summarize_trigger_computed",
            model=model.model_name,
            max_input_tokens=max_input,
            trigger_tokens=trigger_tokens,
            fraction=0.8,
        )
        return cast("ContextTokens", ("tokens", trigger_tokens))
    logger.warning(
        "trend_scout_summarize_trigger_fallback",
        model=model.model_name,
        fallback_tokens=_SUMMARIZE_FALLBACK_TOKENS,
        reason="model.profile.max_input_tokens missing",
    )
    return cast("ContextTokens", ("tokens", _SUMMARIZE_FALLBACK_TOKENS))


def _build_trend_scout_graph():
    """Construct the TrendScout ``create_agent`` subgraph.

    Returns a compiled graph with:

    - D11.12: ``tools=[tavily_search]`` (single exposed tool;
      ``duckduckgo_search`` is a private fallback helper, not a
      ``@tool``).
    - D11.13: ``middleware=[SummarizationMiddleware(...)]`` with
      trigger = 80% of model profile's max_input_tokens; summary
      model = ``settings.summarize_model``.
    - ``checkpointer=None`` -- subgraph state is transient and the
      parent graph's ``AsyncPostgresSaver`` handles thread-level
      checkpoints.

    The returned graph is cached at module level as
    ``_TREND_SCOUT_GRAPH`` and reused for every call; only the
    data passed at invocation time varies.
    """
    settings = get_settings()
    # ``api_key`` is passed via a callable so we never need to mutate
    # ``os.environ`` at import time -- the latter would leak
    # ``OPENAI_API_KEY`` into the test process and break
    # ``test_settings_missing_required_field`` (which asserts that
    # the env var is missing when constructing ``Settings(_env_file=None)``).
    # ``ChatOpenAI`` accepts ``str`` only wrapped in a callable or
    # ``SecretStr``; we use a small ``def`` to keep ruff E731 happy
    # and give the closure a name for trace/debug readability.

    def _api_key_provider() -> str:
        return settings.openai_api_key

    subagent_model = ChatOpenAI(
        model=settings.orchestrator_model,
        api_key=_api_key_provider,
    )
    summary_model = ChatOpenAI(
        model=settings.summarize_model,
        api_key=_api_key_provider,
    )
    trigger = _resolve_summarize_trigger(subagent_model)
    return create_agent(
        model=subagent_model,
        tools=[tavily_search],  # D11.12: only one tool exposed
        state_schema=TrendScoutState,
        response_format=TrendScoutOutput,
        middleware=[
            SummarizationMiddleware(
                model=summary_model,  # D11.13: reuse settings.summarize_model
                trigger=trigger,
                keep=("messages", 20),
            ),
        ],
    )


_TREND_SCOUT_GRAPH = _build_trend_scout_graph()


async def run_trend_scout(
    state: AgentState,
    config: RunnableConfig | None = None,
) -> dict:
    """Run the TrendScout subagent and return trend_summary + image_prompt.

    Translates the parent ``AgentState`` into a ``TrendScoutState``
    (injecting a dynamic ``SystemMessage`` at ``messages[0]``),
    invokes the compiled subgraph, and returns a partial state
    update with the relevant ``TrendScoutOutput`` fields.

    Args:
        state: Current ``AgentState`` from the parent graph.
        config: Optional ``RunnableConfig`` forwarded by the
            parent. When provided, ``config["metadata"]`` is
            augmented with ``correlation_id`` so LangSmith traces
            (NFR-021) link back to the originating request.

    Returns:
        ``{"trend_summary": str | None, "image_prompt": str | None}``.
        Both fields are ``None`` when the subgraph raises
        (D11.6 graceful degradation) or when the structured
        response is missing / malformed.
    """
    structlog.contextvars.bind_contextvars(
        correlation_id=state["correlation_id"],
        node="trend_scout",
    )

    sub_state: TrendScoutState = {
        "messages": [
            SystemMessage(
                content=_build_trend_scout_system(
                    summary=state.get("summary", "") or "",
                    user_profile=state.get("user_profile"),
                    retrieved_products=state.get("retrieved_products", []),
                    generate_image=state.get("generate_image", False),
                ),
            ),
            *list(state["messages"]),
        ],
        "generate_image": state.get("generate_image", False),
    }

    invoke_config: RunnableConfig = {}
    if config is not None:
        invoke_config.update(config)
    invoke_config.setdefault("configurable", {})
    metadata = invoke_config.setdefault("metadata", {})
    metadata["correlation_id"] = state["correlation_id"]

    try:
        # ``TrendScoutState.messages`` is typed as ``list[AnyMessage]`` but
        # ``CompiledStateGraph.ainvoke`` accepts a wider input-state schema
        # (``list[AnyMessage | dict[str, Any]]``). The values we pass are
        # real ``AnyMessage`` instances so the cast is safe; pyright is
        # strict about list variance here.
        result = await _TREND_SCOUT_GRAPH.ainvoke(
            cast("Any", sub_state),
            config=invoke_config,
        )
    except Exception as exc:
        logger.warning(
            "trend_scout_subgraph_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return {"trend_summary": None, "image_prompt": None}

    output = result.get("structured_response") if isinstance(result, dict) else None
    if not isinstance(output, TrendScoutOutput):
        logger.warning(
            "trend_scout_no_structured_output",
            result_type=type(output).__name__ if output is not None else "None",
        )
        return {"trend_summary": None, "image_prompt": None}

    logger.info(
        "trend_scout_subgraph_completed",
        thread_id=state["thread_id"],
        has_summary=output.trend_summary is not None,
        has_image_prompt=output.image_prompt is not None,
    )

    return {
        "trend_summary": output.trend_summary,
        "image_prompt": output.image_prompt,
    }
