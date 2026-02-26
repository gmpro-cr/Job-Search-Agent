"""
agent/state.py — LangGraph agent state definition.
"""

from typing_extensions import TypedDict


class AgentState(TypedDict):
    """State passed between LangGraph nodes."""
    jobs: list[dict]            # fresh jobs from DB (not yet processed)
    scored_jobs: list[dict]     # jobs after LLM scoring (field: llm_score, llm_reason)
    shortlisted: list[dict]     # jobs above score threshold
    with_contacts: list[dict]   # jobs with hiring_manager + hm_email attached
    drafted: list[dict]         # jobs with email_draft + linkedin_draft attached
    queued_count: int           # number of drafts saved to DB
    preferences: dict           # user preferences (for email, thresholds, etc.)
    config: dict                # app config
    errors: list[str]           # non-fatal errors logged during run
