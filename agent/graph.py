"""
agent/graph.py — LangGraph agent graph assembly.
Entry point: run_agent_pipeline(preferences, config)
"""

import logging
from langgraph.graph import StateGraph, END
from agent.state import AgentState
from agent.nodes import (
    fetch_fresh_jobs,
    llm_score_and_filter,
    deduplicate,
    find_hiring_managers,
    draft_outreach,
    queue_for_approval,
    send_approval_digest,
)

logger = logging.getLogger(__name__)


def _build_graph() -> StateGraph:
    """Build and compile the LangGraph agent graph."""
    graph = StateGraph(AgentState)

    graph.add_node("fetch_fresh_jobs", fetch_fresh_jobs)
    graph.add_node("llm_score_and_filter", llm_score_and_filter)
    graph.add_node("deduplicate", deduplicate)
    graph.add_node("find_hiring_managers", find_hiring_managers)
    graph.add_node("draft_outreach", draft_outreach)
    graph.add_node("queue_for_approval", queue_for_approval)
    graph.add_node("send_approval_digest", send_approval_digest)

    graph.set_entry_point("fetch_fresh_jobs")
    graph.add_edge("fetch_fresh_jobs", "llm_score_and_filter")
    graph.add_edge("llm_score_and_filter", "deduplicate")
    graph.add_edge("deduplicate", "find_hiring_managers")
    graph.add_edge("find_hiring_managers", "draft_outreach")
    graph.add_edge("draft_outreach", "queue_for_approval")
    graph.add_edge("queue_for_approval", "send_approval_digest")
    graph.add_edge("send_approval_digest", END)

    return graph.compile()


# Module-level compiled graph (created once)
_agent = _build_graph()


def run_agent_pipeline(preferences: dict, config: dict, owner_user_id: int = None) -> dict:
    """
    Run the full AI agent pipeline.

    owner_user_id identifies which user owns the resulting outreach
    drafts (used by per-user ownership checks). Resolved from
    OWNER_EMAIL env var when omitted.

    Returns final AgentState dict.
    """
    logger.info("AI agent pipeline starting")
    if owner_user_id is None:
        import os
        try:
            from database import get_user_by_email as _gube
            owner = (os.environ.get("OWNER_EMAIL", "") or "").strip().lower()
            row = _gube(owner) if owner else None
            owner_user_id = row["id"] if row else None
        except Exception as e:
            logger.warning("Could not resolve owner_user_id: %s", e)
            owner_user_id = None
    initial_state: AgentState = {
        "jobs": [],
        "scored_jobs": [],
        "shortlisted": [],
        "with_contacts": [],
        "drafted": [],
        "queued_count": 0,
        "preferences": preferences,
        "config": config,
        "errors": [],
        "owner_user_id": owner_user_id,
    }
    try:
        result = _agent.invoke(initial_state)
        logger.info("AI agent pipeline complete — %d drafts queued", result.get("queued_count", 0))
        return result
    except Exception as e:
        logger.error("AI agent pipeline error: %s", e)
        return {**initial_state, "errors": [str(e)]}
