# Job Search AI Agent — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Transform the job-search tool into a semi-autonomous LangGraph AI agent that scores jobs with an LLM, finds hiring managers via Apollo, drafts cold emails + LinkedIn messages, and sends them only after the user approves via email links.

**Architecture:** A LangGraph linear graph with 7 nodes runs after each scrape cycle. It scores fresh jobs with Ollama (→ OpenRouter fallback), deduplicates against `outreach_queue`, finds hiring managers via Apollo, drafts outreach with the LLM, saves drafts to DB, and emails the user an approval digest. Clicking an approve link in the email triggers Gmail SMTP send.

**Tech Stack:** Flask, LangGraph 0.2+, langchain-core 0.3+, Ollama (llama3.2:3b), OpenRouter (Claude Haiku fallback), Apollo API, Gmail SMTP, SQLite, Jinja2

---

## Task 1: Install dependencies + create agent package

**Files:**
- Create: `agent/__init__.py`
- Modify: `requirements.txt`

**Step 1: Install langgraph and langchain-core**

```bash
cd /Users/gaurav/job-search-agent
pip install "langgraph>=0.2" "langchain-core>=0.3"
```

**Step 2: Verify install**

```bash
python -c "import langgraph; import langchain_core; print('OK')"
```
Expected: `OK`

**Step 3: Add to requirements.txt**

Open `requirements.txt` and add at the end:
```
langgraph>=0.2
langchain-core>=0.3
```

**Step 4: Create the agent package**

```bash
mkdir -p /Users/gaurav/job-search-agent/agent
touch /Users/gaurav/job-search-agent/agent/__init__.py
```

**Step 5: Commit**

```bash
git add requirements.txt agent/__init__.py
git commit -m "feat: add langgraph + langchain-core, create agent package"
```

---

## Task 2: LLM abstraction layer (agent/llm.py)

**Files:**
- Create: `agent/llm.py`

This module tries Ollama first; falls back to OpenRouter if Ollama is unavailable or times out.

**Step 1: Create `agent/llm.py`**

```python
"""
agent/llm.py — LLM provider abstraction.
Tries Ollama first; falls back to OpenRouter on error or timeout.
"""

import json
import logging
import os
import re

import requests

logger = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_TIMEOUT = 30  # seconds

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "anthropic/claude-haiku-4-5-20251001"


def call_llm(prompt: str, system: str = "") -> str:
    """
    Call LLM with prompt. Returns plain text response.
    Tries Ollama first; falls back to OpenRouter.
    """
    try:
        return _call_ollama(prompt, system)
    except Exception as e:
        logger.warning("Ollama unavailable (%s), falling back to OpenRouter", e)
        return _call_openrouter(prompt, system)


def _call_ollama(prompt: str, system: str) -> str:
    """Call local Ollama instance."""
    # Read model from config if available
    try:
        import json as _json
        with open(os.path.join(os.path.dirname(__file__), "..", "config.json")) as f:
            model = _json.load(f).get("scoring", {}).get("ollama_model", "llama3.2:3b")
    except Exception:
        model = "llama3.2:3b"

    full_prompt = f"{system}\n\n{prompt}" if system else prompt
    resp = requests.post(
        OLLAMA_URL,
        json={"model": model, "prompt": full_prompt, "stream": False},
        timeout=OLLAMA_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json().get("response", "").strip()


def _call_openrouter(prompt: str, system: str) -> str:
    """Call OpenRouter API."""
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not set and Ollama unavailable")

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    resp = requests.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": OPENROUTER_MODEL, "messages": messages},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def call_llm_json(prompt: str, system: str = "") -> dict:
    """
    Call LLM and parse JSON from response.
    Extracts first JSON object found in response text.
    Returns empty dict on parse failure.
    """
    text = call_llm(prompt, system)
    # Try to extract JSON block from markdown code fences or raw JSON
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    logger.warning("LLM did not return valid JSON: %s", text[:200])
    return {}
```

**Step 2: Verify module loads**

```bash
cd /Users/gaurav/job-search-agent
python -c "from agent.llm import call_llm, call_llm_json; print('OK')"
```
Expected: `OK`

**Step 3: Commit**

```bash
git add agent/llm.py
git commit -m "feat: LLM abstraction layer with Ollama→OpenRouter fallback"
```

---

## Task 3: AgentState + outreach_queue DB table

**Files:**
- Create: `agent/state.py`
- Modify: `database.py`

**Step 1: Create `agent/state.py`**

```python
"""
agent/state.py — LangGraph agent state definition.
"""

from typing import Any
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
```

**Step 2: Add `outreach_queue` table to database.py**

Open `database.py`. Find the `_create_tables()` function (or equivalent — the function that runs `CREATE TABLE IF NOT EXISTS` statements). Add the new table creation there.

Find the line that has `CREATE TABLE IF NOT EXISTS job_listings` and after that table's creation block, add:

```python
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS outreach_queue (
            job_id           TEXT PRIMARY KEY,
            company          TEXT,
            role             TEXT,
            hiring_manager   TEXT,
            hm_email         TEXT,
            email_draft      TEXT,
            linkedin_draft   TEXT,
            approval_token   TEXT UNIQUE,
            status           TEXT DEFAULT 'pending',
            llm_score        INTEGER,
            llm_reason       TEXT,
            created_at       TEXT,
            sent_at          TEXT
        )
    """)
```

**Step 3: Add DB helper functions to database.py**

At the end of `database.py`, add:

```python
# ── Outreach queue helpers ──────────────────────────────────────────────────

def is_job_processed(job_id: str) -> bool:
    """Return True if this job_id already exists in outreach_queue."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM outreach_queue WHERE job_id = ?", (job_id,))
    result = cursor.fetchone()
    conn.close()
    return result is not None


def insert_outreach_draft(job_id: str, company: str, role: str,
                           hiring_manager: str, hm_email: str,
                           email_draft: str, linkedin_draft: str,
                           approval_token: str, llm_score: int,
                           llm_reason: str) -> None:
    """Insert a new outreach draft into the queue."""
    from datetime import datetime
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR IGNORE INTO outreach_queue
        (job_id, company, role, hiring_manager, hm_email,
         email_draft, linkedin_draft, approval_token,
         status, llm_score, llm_reason, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
    """, (job_id, company, role, hiring_manager, hm_email,
          email_draft, linkedin_draft, approval_token,
          llm_score, llm_reason, datetime.now().isoformat()))
    conn.commit()
    conn.close()


def get_outreach_by_token(token: str) -> dict | None:
    """Return outreach_queue row for the given approval_token."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM outreach_queue WHERE approval_token = ?", (token,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def update_outreach_status(token: str, status: str) -> None:
    """Update status for a given approval_token. Sets sent_at if status=sent."""
    from datetime import datetime
    conn = get_connection()
    cursor = conn.cursor()
    sent_at = datetime.now().isoformat() if status == "sent" else None
    cursor.execute(
        "UPDATE outreach_queue SET status = ?, sent_at = ? WHERE approval_token = ?",
        (status, sent_at, token)
    )
    conn.commit()
    conn.close()


def get_outreach_queue(status: str = None) -> list[dict]:
    """
    Return outreach_queue rows, optionally filtered by status.
    Status options: 'pending', 'sent', 'skipped', or None for all.
    """
    conn = get_connection()
    cursor = conn.cursor()
    if status:
        cursor.execute(
            "SELECT * FROM outreach_queue WHERE status = ? ORDER BY created_at DESC",
            (status,)
        )
    else:
        cursor.execute("SELECT * FROM outreach_queue ORDER BY created_at DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]
```

**Step 4: Verify**

```bash
cd /Users/gaurav/job-search-agent
python -c "
from database import is_job_processed, get_outreach_queue
print('DB helpers OK')
print('Queue:', get_outreach_queue())
"
```
Expected: `DB helpers OK` then `Queue: []`

**Step 5: Commit**

```bash
git add agent/state.py database.py
git commit -m "feat: AgentState TypedDict + outreach_queue DB table and helpers"
```

---

## Task 4: LLM scoring node (agent/nodes.py — score + deduplicate)

**Files:**
- Create: `agent/nodes.py` (first two node functions)

**Step 1: Create `agent/nodes.py` with `fetch_fresh_jobs` and `llm_score_and_filter`**

```python
"""
agent/nodes.py — LangGraph node functions for the job outreach agent.
Each function takes AgentState and returns a partial AgentState update.
"""

import logging
from agent.llm import call_llm_json
from agent.state import AgentState

logger = logging.getLogger(__name__)

# Score threshold — jobs below this won't be processed
DEFAULT_SCORE_THRESHOLD = 60


def fetch_fresh_jobs(state: AgentState) -> dict:
    """
    Fetch jobs from DB that were scraped in the last 24 hours.
    Caps at 50 to avoid hammering the LLM on large DB runs.
    """
    from database import get_connection
    from datetime import datetime, timedelta

    cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT * FROM job_listings
           WHERE date_found >= ?
             AND (hidden = 0 OR hidden IS NULL)
           ORDER BY relevance_score DESC
           LIMIT 50""",
        (cutoff,)
    )
    jobs = [dict(r) for r in cursor.fetchall()]
    conn.close()
    logger.info("fetch_fresh_jobs: found %d jobs from last 24h", len(jobs))
    return {"jobs": jobs}


def llm_score_and_filter(state: AgentState) -> dict:
    """
    Score each job against the user's CV using the LLM.
    Attaches llm_score (0-100) and llm_reason to each job.
    Filters to jobs above DEFAULT_SCORE_THRESHOLD.
    """
    from analyzer import load_cv_data

    cv_data = load_cv_data()
    if not cv_data:
        logger.warning("No CV uploaded — skipping LLM scoring")
        return {"scored_jobs": [], "shortlisted": []}

    # Build a compact CV summary for the prompt (avoid token bloat)
    cv_skills = ", ".join((cv_data.get("skills") or [])[:20])
    cv_summary = (cv_data.get("raw_text") or "")[:600]

    scored = []
    threshold = state.get("preferences", {}).get("agent_score_threshold", DEFAULT_SCORE_THRESHOLD)

    for job in state["jobs"]:
        jd = (job.get("job_description") or "")[:800]
        role = job.get("role", "")
        company = job.get("company", "")

        prompt = f"""Score this job against the candidate's CV on a scale of 0-100.

JOB:
Role: {role}
Company: {company}
Description: {jd}

CANDIDATE CV SUMMARY:
Skills: {cv_skills}
Background: {cv_summary}

Return ONLY valid JSON in this exact format:
{{"score": <integer 0-100>, "reason": "<one sentence why this is or isn't a good fit>"}}"""

        result = call_llm_json(prompt)
        score = int(result.get("score", 0))
        reason = result.get("reason", "")

        job = dict(job)
        job["llm_score"] = score
        job["llm_reason"] = reason
        scored.append(job)
        logger.debug("Scored '%s' @ %s: %d", role, company, score)

    shortlisted = [j for j in scored if j["llm_score"] >= threshold]
    logger.info("llm_score_and_filter: %d scored, %d above threshold %d",
                len(scored), len(shortlisted), threshold)
    return {"scored_jobs": scored, "shortlisted": shortlisted}


def deduplicate(state: AgentState) -> dict:
    """
    Remove jobs already present in outreach_queue (any status).
    Ensures each job is shown to the user exactly once.
    """
    from database import is_job_processed

    fresh = [j for j in state["shortlisted"] if not is_job_processed(j["job_id"])]
    skipped = len(state["shortlisted"]) - len(fresh)
    if skipped:
        logger.info("deduplicate: removed %d already-processed jobs", skipped)
    return {"shortlisted": fresh}
```

**Step 2: Verify**

```bash
cd /Users/gaurav/job-search-agent
python -c "
from agent.nodes import fetch_fresh_jobs, llm_score_and_filter, deduplicate
print('Nodes import OK')
"
```
Expected: `Nodes import OK`

**Step 3: Commit**

```bash
git add agent/nodes.py
git commit -m "feat: agent nodes — fetch_fresh_jobs, llm_score_and_filter, deduplicate"
```

---

## Task 5: Find hiring managers node

**Files:**
- Modify: `agent/nodes.py` — add `find_hiring_managers`

**Step 1: Add `find_hiring_managers` to `agent/nodes.py`**

```python
def find_hiring_managers(state: AgentState) -> dict:
    """
    For each shortlisted job, find the hiring manager's email via Apollo API.
    Attaches hiring_manager (name) and hm_email to each job dict.
    Jobs without a contact are still kept — email draft will note "Dear Hiring Manager".
    """
    import re
    import requests as _req

    api_key = state.get("preferences", {}).get("apollo_api_key", "")
    if not api_key:
        import os
        api_key = os.getenv("APOLLO_API_KEY", "")

    results = []
    for job in state["shortlisted"]:
        job = dict(job)
        company = job.get("company", "")
        role = job.get("role", "")

        if not api_key or not company:
            job["hiring_manager"] = ""
            job["hm_email"] = ""
            results.append(job)
            continue

        try:
            # Derive domain guess from company name (Apollo also accepts org_name)
            resp = _req.post(
                "https://api.apollo.io/v1/people/search",
                headers={"Content-Type": "application/json", "Cache-Control": "no-cache"},
                json={
                    "api_key": api_key,
                    "q_organization_name": company,
                    "person_titles": ["hiring manager", "talent acquisition",
                                      "recruiter", "hr manager", "people operations"],
                    "per_page": 1,
                },
                timeout=15,
            )
            data = resp.json()
            people = data.get("people", [])
            if people:
                person = people[0]
                name = f"{person.get('first_name','')} {person.get('last_name','')}".strip()
                email = person.get("email", "")
                job["hiring_manager"] = name
                job["hm_email"] = email
                logger.info("Apollo: found %s <%s> at %s", name, email, company)
            else:
                job["hiring_manager"] = ""
                job["hm_email"] = ""
                logger.debug("Apollo: no contact found for %s", company)
        except Exception as e:
            logger.warning("Apollo lookup failed for %s: %s", company, e)
            job["hiring_manager"] = ""
            job["hm_email"] = ""

        results.append(job)

    return {"with_contacts": results}
```

**Step 2: Verify**

```bash
python -c "from agent.nodes import find_hiring_managers; print('OK')"
```

**Step 3: Commit**

```bash
git add agent/nodes.py
git commit -m "feat: agent node — find_hiring_managers via Apollo API"
```

---

## Task 6: Draft outreach node

**Files:**
- Modify: `agent/nodes.py` — add `draft_outreach`

**Step 1: Add `draft_outreach` to `agent/nodes.py`**

```python
def draft_outreach(state: AgentState) -> dict:
    """
    For each job with contact info, draft a cold email (~150 words)
    and a LinkedIn message (~50 words) using the LLM.
    """
    from analyzer import load_cv_data

    cv_data = load_cv_data() or {}
    cv_skills = ", ".join((cv_data.get("skills") or [])[:15])
    cv_summary = (cv_data.get("raw_text") or "")[:500]

    drafted = []
    for job in state["with_contacts"]:
        job = dict(job)
        role = job.get("role", "the role")
        company = job.get("company", "your company")
        jd = (job.get("job_description") or "")[:600]
        hm_name = job.get("hiring_manager") or "Hiring Manager"
        hm_first = hm_name.split()[0] if hm_name else "there"
        score_reason = job.get("llm_reason", "")

        prompt = f"""Write a personalized cold email and LinkedIn message for this job application.

ROLE: {role} at {company}
JOB DESCRIPTION EXCERPT: {jd}
FIT REASON: {score_reason}

CANDIDATE BACKGROUND:
Skills: {cv_skills}
Summary: {cv_summary}

Rules:
- Cold email: 120-150 words, professional but warm, mention specific role + company
- LinkedIn message: 40-55 words, conversational, reference the role
- Address the person as "{hm_first}"
- Do NOT use phrases like "I hope this email finds you well"
- Do NOT include subject line in the email body

Return ONLY valid JSON:
{{
  "email": "<full cold email body, no subject line>",
  "linkedin": "<linkedin message>"
}}"""

        result = call_llm_json(prompt)
        job["email_draft"] = result.get("email", f"Hi {hm_first},\n\nI wanted to reach out about the {role} position at {company}. My background in {cv_skills[:80]} makes me a strong fit.\n\nWould love to connect.\n\nBest regards")
        job["linkedin_draft"] = result.get("linkedin", f"Hi {hm_first}, I noticed the {role} opening at {company} and would love to connect. My background in {cv_skills[:60]} aligns well with what you're looking for.")
        drafted.append(job)
        logger.info("Drafted outreach for '%s' @ %s", role, company)

    return {"drafted": drafted}
```

**Step 2: Verify**

```bash
python -c "from agent.nodes import draft_outreach; print('OK')"
```

**Step 3: Commit**

```bash
git add agent/nodes.py
git commit -m "feat: agent node — draft_outreach (cold email + LinkedIn msg)"
```

---

## Task 7: Queue for approval + send approval digest nodes

**Files:**
- Modify: `agent/nodes.py` — add `queue_for_approval` and `send_approval_digest`

**Step 1: Add both nodes to `agent/nodes.py`**

```python
def queue_for_approval(state: AgentState) -> dict:
    """
    Save each drafted job to outreach_queue with status=pending.
    Generates a unique approval_token per job.
    """
    import uuid
    from database import insert_outreach_draft

    count = 0
    for job in state["drafted"]:
        token = uuid.uuid4().hex
        insert_outreach_draft(
            job_id=job["job_id"],
            company=job.get("company", ""),
            role=job.get("role", ""),
            hiring_manager=job.get("hiring_manager", ""),
            hm_email=job.get("hm_email", ""),
            email_draft=job.get("email_draft", ""),
            linkedin_draft=job.get("linkedin_draft", ""),
            approval_token=token,
            llm_score=job.get("llm_score", 0),
            llm_reason=job.get("llm_reason", ""),
        )
        job["approval_token"] = token
        count += 1

    logger.info("queue_for_approval: queued %d drafts", count)
    return {"queued_count": count, "drafted": state["drafted"]}


def send_approval_digest(state: AgentState) -> dict:
    """
    Send a single approval email to the user listing all drafted outreach.
    Each job has APPROVE and SKIP links using the approval_token.
    """
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    if not state["drafted"]:
        logger.info("send_approval_digest: nothing to send")
        return {}

    prefs = state.get("preferences", {})
    gmail_address = prefs.get("gmail_address", "")
    gmail_password = prefs.get("gmail_app_password", "")
    recipient = prefs.get("email_recipient", gmail_address)

    if not gmail_address or not gmail_password:
        logger.warning("Gmail not configured — skipping approval digest")
        return {}

    # Determine base URL for approve/skip links
    host = prefs.get("agent_host", "http://localhost:5001")

    # Build HTML email
    jobs_html = ""
    for i, job in enumerate(state["drafted"], 1):
        token = job.get("approval_token", "")
        approve_url = f"{host}/api/approve/{token}"
        skip_url = f"{host}/api/skip/{token}"
        hm_email = job.get("hm_email", "No email found")
        email_draft = job.get("email_draft", "").replace("\n", "<br>")
        linkedin_draft = job.get("linkedin_draft", "").replace("\n", "<br>")

        jobs_html += f"""
        <div style="border:1px solid #e4e4e7; border-radius:8px; padding:16px; margin-bottom:20px; font-family:sans-serif;">
          <h3 style="margin:0 0 4px 0; color:#111;">{i}. {job.get('role','')} @ {job.get('company','')}
            <span style="font-size:13px; color:#6366f1; font-weight:normal;">[LLM Score: {job.get('llm_score',0)}]</span>
          </h3>
          <p style="margin:0 0 12px 0; color:#555; font-size:13px;">To: {hm_email} · {job.get('llm_reason','')}</p>

          <p style="font-size:12px; font-weight:600; color:#374151; margin-bottom:4px;">COLD EMAIL DRAFT:</p>
          <div style="background:#f9fafb; border-radius:6px; padding:12px; font-size:13px; color:#374151; margin-bottom:12px;">{email_draft}</div>

          <p style="font-size:12px; font-weight:600; color:#374151; margin-bottom:4px;">LINKEDIN MESSAGE DRAFT:</p>
          <div style="background:#f0f9ff; border-radius:6px; padding:12px; font-size:13px; color:#374151; margin-bottom:16px;">{linkedin_draft}</div>

          <a href="{approve_url}" style="background:#6366f1; color:#fff; padding:8px 18px; border-radius:6px; text-decoration:none; font-size:13px; font-weight:600; margin-right:8px;">✅ APPROVE &amp; SEND</a>
          <a href="{skip_url}" style="background:#f1f5f9; color:#475569; padding:8px 18px; border-radius:6px; text-decoration:none; font-size:13px; font-weight:600;">❌ SKIP</a>
        </div>"""

    html = f"""
    <html><body style="font-family:sans-serif; max-width:700px; margin:0 auto; padding:20px;">
      <h2 style="color:#111;">🤖 Job Agent — {len(state['drafted'])} outreach draft{'s' if len(state['drafted'])>1 else ''} ready</h2>
      <p style="color:#555;">Review and approve the cold emails below. Clicking Approve will send the email immediately.</p>
      {jobs_html}
      <p style="color:#999; font-size:12px; margin-top:24px;">LinkedIn message drafts are saved in your <a href="{host}/outbox">Outbox</a> — copy-paste them manually.</p>
    </body></html>"""

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"🤖 Job Agent — {len(state['drafted'])} outreach drafts ready for approval"
        msg["From"] = gmail_address
        msg["To"] = recipient
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(gmail_address, gmail_password)
            smtp.sendmail(gmail_address, recipient, msg.as_string())

        logger.info("send_approval_digest: sent to %s (%d drafts)", recipient, len(state["drafted"]))
    except Exception as e:
        logger.error("send_approval_digest failed: %s", e)

    return {}
```

**Step 2: Verify**

```bash
python -c "from agent.nodes import queue_for_approval, send_approval_digest; print('OK')"
```

**Step 3: Commit**

```bash
git add agent/nodes.py
git commit -m "feat: agent nodes — queue_for_approval, send_approval_digest"
```

---

## Task 8: Assemble LangGraph graph (agent/graph.py)

**Files:**
- Create: `agent/graph.py`

**Step 1: Create `agent/graph.py`**

```python
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


def run_agent_pipeline(preferences: dict, config: dict) -> dict:
    """
    Run the full AI agent pipeline.
    Returns final AgentState dict.
    """
    logger.info("AI agent pipeline starting")
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
    }
    try:
        result = _agent.invoke(initial_state)
        logger.info("AI agent pipeline complete — %d drafts queued", result.get("queued_count", 0))
        return result
    except Exception as e:
        logger.error("AI agent pipeline error: %s", e)
        return {**initial_state, "errors": [str(e)]}
```

**Step 2: Verify graph builds without error**

```bash
cd /Users/gaurav/job-search-agent
python -c "
from agent.graph import run_agent_pipeline
print('Graph compiled OK')
"
```
Expected: `Graph compiled OK`

**Step 3: Commit**

```bash
git add agent/graph.py
git commit -m "feat: LangGraph agent graph — 7-node pipeline assembled"
```

---

## Task 9: Flask routes for approve/skip + Outbox page

**Files:**
- Modify: `app.py` — add 3 new routes
- Create: `templates/outbox.html`

**Step 1: Add routes to app.py**

Find the `/api/cv/profile-score` route (around line 1160). After it, add:

```python
# ── Outreach agent routes ────────────────────────────────────────────────────

@app.route("/outbox")
def outbox():
    """Outbox page showing all outreach drafts (pending/sent/skipped)."""
    from database import get_outreach_queue
    pending = get_outreach_queue("pending")
    sent = get_outreach_queue("sent")
    skipped = get_outreach_queue("skipped")
    return render_template("outbox.html",
                           pending=pending, sent=sent, skipped=skipped)


@app.route("/api/approve/<token>")
def approve_outreach(token):
    """Approve and send a cold email for the given token."""
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from database import get_outreach_by_token, update_outreach_status

    item = get_outreach_by_token(token)
    if not item:
        return "Invalid or expired link.", 404
    if item["status"] != "pending":
        return f"This outreach was already {item['status']}.", 200

    prefs = load_preferences() or DEFAULT_PREFS.copy()
    gmail_address = prefs.get("gmail_address", "")
    gmail_password = prefs.get("gmail_app_password", "")

    if not gmail_address or not gmail_password:
        return "Gmail not configured. Please set it up in Preferences.", 500

    recipient_email = item.get("hm_email", "")
    if not recipient_email:
        return "No hiring manager email on file for this job.", 400

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"Regarding the {item['role']} role at {item['company']}"
        msg["From"] = gmail_address
        msg["To"] = recipient_email
        msg.attach(MIMEText(item["email_draft"], "plain"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(gmail_address, gmail_password)
            smtp.sendmail(gmail_address, recipient_email, msg.as_string())

        update_outreach_status(token, "sent")
        return render_template("approve_result.html",
                               success=True, item=item,
                               message=f"Email sent to {recipient_email}!")
    except Exception as e:
        logger.error("approve_outreach send failed: %s", e)
        return f"Failed to send email: {e}", 500


@app.route("/api/skip/<token>")
def skip_outreach(token):
    """Mark an outreach draft as skipped."""
    from database import get_outreach_by_token, update_outreach_status

    item = get_outreach_by_token(token)
    if not item:
        return "Invalid or expired link.", 404
    update_outreach_status(token, "skipped")
    return render_template("approve_result.html",
                           success=False, item=item,
                           message="Skipped. This job won't appear again.")
```

**Step 2: Create `templates/approve_result.html`**

```html
{% extends "base.html" %}
{% block title %}{{ 'Email Sent' if success else 'Skipped' }} — Job Agent{% endblock %}
{% block content %}
<div class="max-w-lg mx-auto mt-12 text-center">
  <div class="bg-white rounded-xl border border-gray-200 p-8">
    <div class="text-4xl mb-4">{{ '✅' if success else '❌' }}</div>
    <h1 class="text-xl font-semibold text-gray-900 mb-2">{{ message }}</h1>
    <p class="text-gray-500 text-sm mb-2">
      {{ item.role }} @ {{ item.company }}
    </p>
    {% if success %}
    <p class="text-gray-400 text-xs">Sent to {{ item.hm_email }}</p>
    {% endif %}
    <div class="mt-6 flex justify-center gap-3">
      <a href="/outbox" class="px-4 py-2 bg-indigo-600 text-white text-sm rounded-lg hover:bg-indigo-700">View Outbox</a>
      <a href="/jobs" class="px-4 py-2 bg-gray-100 text-gray-700 text-sm rounded-lg hover:bg-gray-200">Browse Jobs</a>
    </div>
  </div>
</div>
{% endblock %}
```

**Step 3: Create `templates/outbox.html`**

```html
{% extends "base.html" %}
{% block title %}Outbox — Job Agent{% endblock %}
{% block content %}
<div class="mb-6 flex items-center justify-between">
  <div>
    <h1 class="text-2xl font-semibold text-gray-900">Outbox</h1>
    <p class="text-gray-500 mt-1 text-sm">Agent-drafted cold emails and LinkedIn messages.</p>
  </div>
  {% if pending %}
  <span class="px-3 py-1 bg-indigo-100 text-indigo-700 rounded-full text-sm font-semibold">{{ pending|length }} pending</span>
  {% endif %}
</div>

{% macro outreach_card(item, show_actions) %}
<div class="bg-white border border-gray-200 rounded-xl p-5 mb-4">
  <div class="flex items-start justify-between mb-3">
    <div>
      <h3 class="font-semibold text-gray-900">{{ item.role }} @ {{ item.company }}</h3>
      <p class="text-xs text-gray-400 mt-0.5">
        To: {{ item.hm_email or 'No email found' }} ·
        Score: {{ item.llm_score }} ·
        {{ item.created_at[:10] }}
      </p>
      {% if item.llm_reason %}
      <p class="text-xs text-gray-500 mt-1 italic">{{ item.llm_reason }}</p>
      {% endif %}
    </div>
    <span class="text-xs px-2 py-1 rounded-full flex-shrink-0 ml-4
      {% if item.status == 'pending' %}bg-amber-100 text-amber-700
      {% elif item.status == 'sent' %}bg-green-100 text-green-700
      {% else %}bg-gray-100 text-gray-500{% endif %}">
      {{ item.status | capitalize }}
    </span>
  </div>

  <div class="mb-3">
    <p class="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-1">Cold Email</p>
    <div class="bg-gray-50 rounded-lg p-3 text-sm text-gray-700 whitespace-pre-wrap max-h-40 overflow-y-auto">{{ item.email_draft }}</div>
  </div>

  <div class="mb-4">
    <p class="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-1">LinkedIn Message</p>
    <div class="bg-blue-50 rounded-lg p-3 text-sm text-gray-700" id="li-{{ item.approval_token }}">{{ item.linkedin_draft }}</div>
    <button onclick="copyText('li-{{ item.approval_token }}', this)"
            class="mt-1 text-xs text-indigo-600 hover:text-indigo-800 underline">Copy LinkedIn msg</button>
  </div>

  {% if show_actions and item.hm_email %}
  <div class="flex gap-2">
    <a href="/api/approve/{{ item.approval_token }}"
       class="px-4 py-2 bg-indigo-600 text-white text-sm font-medium rounded-lg hover:bg-indigo-700">✅ Approve &amp; Send</a>
    <a href="/api/skip/{{ item.approval_token }}"
       class="px-4 py-2 bg-gray-100 text-gray-600 text-sm font-medium rounded-lg hover:bg-gray-200">❌ Skip</a>
  </div>
  {% elif show_actions %}
  <p class="text-xs text-amber-600">⚠ No hiring manager email found — cannot send. <a href="/api/skip/{{ item.approval_token }}" class="underline">Skip</a></p>
  {% endif %}
</div>
{% endmacro %}

{% if pending %}
<h2 class="text-base font-semibold text-gray-700 mb-3">Pending Approval ({{ pending|length }})</h2>
{% for item in pending %}{{ outreach_card(item, true) }}{% endfor %}
{% else %}
<div class="bg-white border border-gray-200 rounded-xl p-8 text-center text-gray-400 text-sm mb-6">
  No pending drafts. The agent will populate this after the next scheduled run.
</div>
{% endif %}

{% if sent %}
<h2 class="text-base font-semibold text-gray-700 mb-3 mt-6">Sent ({{ sent|length }})</h2>
{% for item in sent %}{{ outreach_card(item, false) }}{% endfor %}
{% endif %}

{% if skipped %}
<h2 class="text-base font-semibold text-gray-700 mb-3 mt-6">Skipped ({{ skipped|length }})</h2>
{% for item in skipped %}{{ outreach_card(item, false) }}{% endfor %}
{% endif %}

{% endblock %}

{% block scripts %}
<script>
function copyText(elId, btn) {
  const text = document.getElementById(elId).innerText;
  navigator.clipboard.writeText(text).then(() => {
    btn.textContent = '✓ Copied!';
    setTimeout(() => btn.textContent = 'Copy LinkedIn msg', 2000);
  });
}
</script>
{% endblock %}
```

**Step 4: Verify routes**

```bash
cd /Users/gaurav/job-search-agent && python -c "
from app import app
with app.test_client() as c:
    r = c.get('/outbox')
    print('Outbox status:', r.status_code)
    assert r.status_code == 200
    assert b'Outbox' in r.data
    print('OK')
" 2>/dev/null
```

**Step 5: Commit**

```bash
git add app.py templates/outbox.html templates/approve_result.html
git commit -m "feat: /outbox page, /api/approve/<token>, /api/skip/<token> routes"
```

---

## Task 10: Add Outbox link to nav + wire agent into scheduler

**Files:**
- Modify: `templates/base.html` — add Outbox nav link
- Modify: `app.py` — wire `run_agent_pipeline()` into `_scheduled_pipeline_run()`

**Step 1: Find nav links in base.html**

Search `templates/base.html` for the nav links (look for `/jobs`, `/cv`, `/dashboard`). Add Outbox link in the same style.

**Step 2: Add Outbox to nav**

Find the nav items block. After the CV/Profile link, add:
```html
<a href="/outbox" class="... {% if request.path == '/outbox' %}active{% endif %}">Outbox</a>
```
Use the exact same CSS classes as the adjacent nav links.

**Step 3: Wire agent pipeline into scheduler in app.py**

Find `_scheduled_pipeline_run()` (around line 143). It calls `_run_scraper_pipeline()`. After that call completes, add the agent run:

Find:
```python
def _scheduled_pipeline_run():
    ...
    _run_scraper_pipeline()
    ...
```

Add after `_run_scraper_pipeline()` call:
```python
    # Run AI agent pipeline after scraping
    try:
        from agent.graph import run_agent_pipeline
        prefs = load_preferences() or DEFAULT_PREFS.copy()
        import json as _json
        with open(os.path.join(_BASE_DIR, "config.json")) as f:
            _config = _json.load(f)
        run_agent_pipeline(prefs, _config)
    except Exception as e:
        logger.error("AI agent pipeline error in scheduler: %s", e)
```

**Step 4: Verify app still starts**

```bash
python -c "from app import app; print('App OK')" 2>/dev/null
```

**Step 5: Commit**

```bash
git add templates/base.html app.py
git commit -m "feat: wire AI agent into scheduler, add Outbox to nav"
```

---

## Task 11: Add agent_host + agent_score_threshold to preferences

**Files:**
- Modify: `app.py` — add two new fields to `DEFAULT_PREFS`
- Modify: `templates/preferences.html` (or equivalent settings page) — expose the two fields

**Step 1: Find DEFAULT_PREFS in app.py**

Search for `DEFAULT_PREFS = {` in app.py. Add two new keys:

```python
"agent_score_threshold": 60,   # min LLM score to include a job in outreach
"agent_host": "http://localhost:5001",  # base URL for approve/skip links in email
```

**Step 2: Find the preferences template**

Search for the preferences/settings page template (likely `templates/preferences.html` or `templates/settings.html`). Add two new form fields for these settings using the same input style as existing fields.

**Step 3: Verify preferences page loads**

```bash
python -c "
from app import app
with app.test_client() as c:
    r = c.get('/preferences')
    print('Status:', r.status_code)
" 2>/dev/null
```

**Step 4: Commit**

```bash
git add app.py templates/preferences.html
git commit -m "feat: add agent_score_threshold and agent_host to preferences"
```

---

## Final Step: Push

```bash
cd /Users/gaurav/job-search-agent
git pull --rebase origin main && git push origin main
```

---

## Summary

| Task | Files | What it builds |
|------|-------|----------------|
| 1 | `requirements.txt`, `agent/__init__.py` | LangGraph dep + package |
| 2 | `agent/llm.py` | Ollama → OpenRouter LLM abstraction |
| 3 | `agent/state.py`, `database.py` | AgentState + outreach_queue DB table |
| 4 | `agent/nodes.py` | fetch_fresh_jobs, llm_score_and_filter, deduplicate |
| 5 | `agent/nodes.py` | find_hiring_managers (Apollo API) |
| 6 | `agent/nodes.py` | draft_outreach (cold email + LinkedIn) |
| 7 | `agent/nodes.py` | queue_for_approval, send_approval_digest |
| 8 | `agent/graph.py` | LangGraph 7-node graph + run_agent_pipeline() |
| 9 | `app.py`, `templates/outbox.html`, `templates/approve_result.html` | Flask routes + Outbox UI |
| 10 | `templates/base.html`, `app.py` | Nav link + scheduler wiring |
| 11 | `app.py`, `templates/preferences.html` | Agent config in preferences |
