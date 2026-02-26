# Job Search AI Agent — Design

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:writing-plans to create the implementation plan.

**Goal:** Transform the job-search tool from a rule-based automation pipeline into a semi-autonomous LangGraph AI agent that intelligently scores jobs, finds hiring managers, drafts personalized cold emails + LinkedIn messages, and sends them only after human approval.

**Autonomy level:** Semi-autonomous — LLM reasons and drafts; human approves each batch via email before anything is sent.

**LLM stack:** Ollama (primary, free, local) → OpenRouter/Claude Haiku (fallback if Ollama unavailable)

---

## Section 1: Overall Architecture

The pipeline changes from `scrape → keyword-score → notify` to `scrape → LLM-reason → draft → await-approval → send`.

```
Scheduler (7am / 7pm)
      ↓
 LangGraph Agent (agent/graph.py)
  ┌──────────────────────────────────────────────────┐
  │  scrape_fresh_jobs                               │
  │       ↓                                          │
  │  llm_score_and_filter  (Ollama → OpenRouter)     │
  │       ↓                                          │
  │  deduplicate  (filter job_ids in outreach_queue) │
  │       ↓                                          │
  │  find_hiring_managers  (Apollo API)              │
  │       ↓                                          │
  │  draft_outreach  (cold email + LinkedIn msg)     │
  │       ↓                                          │
  │  queue_for_approval  (save to DB, status=pending)│
  │       ↓                                          │
  │  send_approval_digest  (one email to user)       │
  └──────────────────────────────────────────────────┘
           ↓  (user clicks approve link in email)
  Flask /api/approve/<token>
           ↓
  Send cold email via Gmail SMTP → status=sent
  LinkedIn draft available in /outbox UI
```

**New files:**
- `agent/graph.py` — LangGraph graph definition
- `agent/nodes.py` — node functions
- `agent/state.py` — AgentState TypedDict
- `agent/llm.py` — Ollama → OpenRouter fallback abstraction
- `agent/tools.py` — Apollo lookup, Gmail send, draft helpers

**Existing files modified:**
- `app.py` — add `/outbox`, `/api/approve/<token>`, `/api/skip/<token>` routes
- `database.py` — add `outreach_queue` table + helpers
- scheduler in `app.py` — call `run_agent_pipeline()` instead of bare scrape

---

## Section 2: Agent Graph (LangGraph nodes)

Linear graph with deduplication gate:

```
scrape_fresh_jobs
      ↓
llm_score_and_filter        # LLM scores each JD vs CV, filters below threshold (default 60)
      ↓
deduplicate                 # remove job_ids already in outreach_queue
      ↓
find_hiring_managers        # Apollo /people/search per company domain
      ↓
draft_outreach              # LLM: cold email (150 words) + LinkedIn msg (50 words)
      ↓
queue_for_approval          # INSERT into outreach_queue, status=pending
      ↓
send_approval_digest        # single email listing all drafts with approve/skip links
      ↓
     END
```

### outreach_queue DB table

```sql
CREATE TABLE IF NOT EXISTS outreach_queue (
    job_id           TEXT PRIMARY KEY,
    company          TEXT,
    role             TEXT,
    hiring_manager   TEXT,
    hm_email         TEXT,
    email_draft      TEXT,
    linkedin_draft   TEXT,
    approval_token   TEXT UNIQUE,
    status           TEXT DEFAULT 'pending',  -- pending/approved/sent/skipped
    llm_score        INTEGER,
    llm_reason       TEXT,
    created_at       TEXT,
    sent_at          TEXT
)
```

**Deduplication rule:** Any `job_id` already present in `outreach_queue` (any status) is excluded before drafting. Jobs are shown to the user exactly once, ever.

---

## Section 3: LLM Layer (Ollama → OpenRouter fallback)

Single module `agent/llm.py` abstracts the provider:

```python
def call_llm(prompt: str, system: str = None) -> str:
    """Try Ollama first; fall back to OpenRouter on error/timeout."""
```

**Ollama (primary):**
- Model: `llama3.2:3b` (already in config.json)
- Timeout: 30s
- Endpoint: `http://localhost:11434/api/generate`

**OpenRouter (fallback):**
- Model: `anthropic/claude-haiku-4-5-20251001`
- Triggered: Ollama connection error or timeout
- Uses existing `OPENROUTER_API_KEY` from `.env`

**Two LLM tasks:**

| Task | Input | Output |
|------|-------|--------|
| Score job | JD text + CV summary (500 chars) | JSON: `{score: 0-100, reason: "2 sentences"}` |
| Draft outreach | JD + company + hiring manager name + CV | JSON: `{email: "150 words", linkedin: "50 words"}` |

LLM scoring replaces the current keyword regex — understands context (e.g. "credit risk at HDFC" matches even without exact keyword overlap).

---

## Section 4: Approval Flow (Email Digest + Token Links)

After drafting, the agent sends **one approval email** per run containing all shortlisted jobs.

**Email format:**
```
Subject: 🤖 Job Agent — N outreach drafts ready

Job 1: Product Manager @ Razorpay  [LLM Score: 87]
To: priya.sharma@razorpay.com

COLD EMAIL DRAFT:
"Hi Priya, I came across the PM role at Razorpay and wanted to reach out..."

LINKEDIN MESSAGE DRAFT:
"Hi Priya, would love to connect about the PM opening at Razorpay..."

[✅ APPROVE & SEND]    [❌ SKIP]
────────────────────────────────────
Job 2: Senior PM @ CRED  [Score: 82]
...

[✅ APPROVE ALL]
```

**Token mechanics:**
- Each job gets a UUID `approval_token` stored in `outreach_queue`
- `APPROVE` link → `http://<host>/api/approve/<token>` — one-click, no login
- `SKIP` link → `http://<host>/api/skip/<token>`
- Clicking approve: Flask sends cold email via Gmail SMTP, sets `status=sent`
- Clicking skip: sets `status=skipped`, job never reappears

---

## Section 5: Outbox UI Page (/outbox)

New page in the existing Flask app showing full outreach history.

**Sections:**
1. **Pending** — drafts awaiting approval (approve/skip buttons + copy LinkedIn)
2. **Sent** — cold emails sent, with timestamp and view-draft option
3. **Skipped** — jobs passed on (informational only)

**Key UI elements:**
- "Copy LinkedIn msg" button — copies draft to clipboard for manual paste
- Approve/Skip buttons — same as email links (backup in case email missed)
- Link to the job in the jobs page for full JD review

---

## Key Constraints

- No new Python packages beyond `langgraph` and `langchain-core`
- Ollama must be running locally for primary LLM (falls back gracefully)
- Apollo API key already configured — reuse existing `get_company_info()` if available
- Gmail SMTP reuses existing `email_notifier.py` send logic
- All outreach is cold email only — no form-filling, no portal automation
- LinkedIn messages are drafts only — user copy-pastes manually
- Agent runs on same 07:00 / 19:00 schedule as existing scraper

---

## New Dependencies

```
langgraph>=0.2
langchain-core>=0.3
```

Both are free, open-source (MIT). No API cost beyond LLM calls.
