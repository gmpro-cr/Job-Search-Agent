"""
hiring_managers.py — Generic HR / TA recruiter finder.

Works for any user: searches LinkedIn for recruiters hiring for the user's
target roles in their preferred location, using their CV skills as context.

Main functions:
    get_new_hiring_managers(sent_contacts, role_keywords, skills, location, target) -> list
    format_hiring_section(contacts, user_name, user_summary)                        -> str
    load_hr_sent(reminder_id)                                                        -> list
    update_hr_sent(reminder_id, contacts)                                            -> None
"""

import json
import logging
import re
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

# Sent contacts stored per-reminder: {reminder_id: [{name, company, date_sent}]}
HR_SENT_FILE = Path.home() / "Documents" / "Claude" / "hr_sent_contacts.json"


# ── Sent-contacts tracker ─────────────────────────────────────────────────────

def load_hr_sent(reminder_id: str) -> list:
    """Load sent contacts for a specific reminder."""
    try:
        data = json.loads(HR_SENT_FILE.read_text(encoding="utf-8"))
        return data.get(reminder_id, [])
    except Exception:
        return []


def update_hr_sent(reminder_id: str, contacts: list) -> None:
    """Append today's new contacts for a reminder."""
    today = date.today().isoformat()
    try:
        data = json.loads(HR_SENT_FILE.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    existing = data.get(reminder_id, [])
    for c in contacts:
        existing.append({
            "name": c["name"],
            "company": c["company"],
            "their_role": c.get("their_role", ""),
            "linkedin_url": c.get("linkedin_url", ""),
            "date_sent": today,
        })
    data[reminder_id] = existing
    HR_SENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    HR_SENT_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    logger.info("HR sent contacts updated for %s (%d total)", reminder_id, len(existing))


def load_all_contacts() -> list:
    """Return all sent contacts across all reminders, newest first, with dedup by name."""
    try:
        data = json.loads(HR_SENT_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    seen_names = set()
    result = []
    all_entries = []
    for reminder_id, contacts in data.items():
        for c in contacts:
            all_entries.append(dict(c, reminder_id=reminder_id))
    # Sort newest first
    all_entries.sort(key=lambda x: x.get("date_sent", ""), reverse=True)
    for c in all_entries:
        key = c.get("name", "").lower().strip()
        if key and key not in seen_names:
            seen_names.add(key)
            result.append(c)
    return result


# ── Query builder ─────────────────────────────────────────────────────────────

def _build_queries(role_keywords: list, location: str) -> list:
    """Build DuckDuckGo search queries from role keywords and location."""
    # Take top 3 keywords, quote them
    top = role_keywords[:3] if role_keywords else ["project manager"]
    roles_or = " OR ".join(f'"{k}"' for k in top)
    loc = location.strip() if location else "India"

    return [
        f'site:linkedin.com/in "talent acquisition" {roles_or} {loc}',
        f'site:linkedin.com/in recruiter {roles_or} {loc} hiring',
        f'site:linkedin.com/in "talent acquisition manager" {roles_or} {loc} 2026',
        f'site:linkedin.com/in HR recruiter {roles_or} {loc}',
        f'site:linkedin.com/in "technical recruiter" OR "IT recruiter" {roles_or} {loc}',
        f'site:linkedin.com/in "talent acquisition" "delivery manager" OR "program manager" {loc}',
        f'site:linkedin.com/in recruiter {roles_or} {loc} linkedin 2026',
    ]


# ── Title parser ──────────────────────────────────────────────────────────────

def _parse_title(title: str) -> dict:
    # Strip trailing "| LinkedIn" or "- LinkedIn"
    title = re.sub(r'[\|\-–]\s*LinkedIn\s*$', '', title, flags=re.IGNORECASE).strip()

    # Fix camelCase in name part (DuckDuckGo sometimes squishes spaces)
    parts = re.split(r'\s*[\-–]\s*', title, maxsplit=1)
    if len(parts) == 2:
        name_fixed = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', parts[0]).strip()
        title = f"{name_fixed} - {parts[1]}"

    # "Name - Role at Company"
    m = re.match(r'^(.+?)\s*[\-–]\s*(.+?)\s+at\s+(.+)$', title, re.IGNORECASE)
    if m:
        role = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', m.group(2)).strip()
        return {"name": m.group(1).strip(), "their_role": role,
                "company": m.group(3).strip().rstrip('|').strip()}

    # "Name - Role | Company"
    m = re.match(r'^(.+?)\s*[\-–]\s*(.+?)\s*\|\s*(.+)$', title)
    if m:
        role = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', m.group(2)).strip()
        company = m.group(3).strip().rstrip('|').strip()
        if len(company.split()) > 5:
            company = "IT/Consulting"
        return {"name": m.group(1).strip(), "their_role": role, "company": company}

    # "Name - Something" — detect if role or company
    m = re.match(r'^(.+?)\s*[\-–]\s*(.+)$', title)
    if m:
        rest = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', m.group(2)).strip()
        role_kws = ["recruit", "talent", "hr ", "human resource", "manager",
                    "specialist", "lead", "head", "director", "coordinator",
                    "acquisition", "partner", "consultant", "engineer", "analyst"]
        if any(kw in rest.lower() for kw in role_kws):
            return {"name": m.group(1).strip(), "their_role": rest, "company": "IT/Consulting"}
        return {"name": m.group(1).strip(), "their_role": "Recruiter / TA", "company": rest}

    return {"name": title.strip(), "their_role": "Recruiter / TA", "company": "IT/Consulting"}


def _infer_hiring_for(body: str, their_role: str, role_keywords: list) -> str:
    text = (body + " " + their_role).lower()
    found = [kw for kw in role_keywords if kw.lower() in text]
    return " / ".join(found) if found else " / ".join(role_keywords[:2])


def _infer_location(body: str, title: str, preferred: str) -> str:
    text = (body + " " + title).lower()
    if "pune" in text:        return "Pune"
    if "remote" in text:      return "Remote"
    if "hybrid" in text:      return "Hybrid"
    if "bangalore" in text or "bengaluru" in text: return "Bangalore"
    if "mumbai" in text:      return "Mumbai"
    if "hyderabad" in text:   return "Hyderabad"
    return preferred or "India"


def _is_india_profile(url: str, body: str, title: str) -> bool:
    text = (url + " " + body + " " + title).lower()
    if "in.linkedin.com" in url:
        return True
    india_kws = ["india", "pune", "mumbai", "bangalore", "bengaluru",
                 "hyderabad", "delhi", "chennai", "noida", "gurgaon"]
    return any(kw in text for kw in india_kws)


def _already_sent(name: str, sent_contacts: list) -> bool:
    return name.lower().strip() in {c.get("name", "").lower().strip() for c in sent_contacts}


# ── Main search function ──────────────────────────────────────────────────────

def get_new_hiring_managers(
    sent_contacts: list,
    role_keywords: list,
    skills: list = None,
    location: str = "India",
    target: int = 5,
) -> list:
    """
    Search for TA/HR recruiters on LinkedIn hiring for role_keywords in location.
    Filters out already-sent contacts. Returns up to target new contacts.
    """
    try:
        from ddgs import DDGS
    except ImportError:
        raise ImportError("ddgs not installed. Run: pip3 install ddgs")

    queries = _build_queries(role_keywords, location)
    seen_urls = set()
    new_contacts = []

    for query in queries:
        if len(new_contacts) >= target:
            break
        logger.info("Searching: %s", query)
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=8))
        except Exception as e:
            logger.warning("Search failed: %s", e)
            continue

        for r in results:
            if len(new_contacts) >= target + 3:
                break
            url   = r.get("href", "")
            title = r.get("title", "")
            body  = r.get("body", "")

            if "linkedin.com/in/" not in url or url in seen_urls:
                continue
            seen_urls.add(url)

            if not _is_india_profile(url, body, title):
                continue

            parsed  = _parse_title(title)
            name    = parsed["name"]
            company = parsed["company"].lstrip("| ").strip() or "IT/Consulting"

            if len(name.split()) < 2:
                continue
            if _already_sent(name, sent_contacts):
                logger.info("  Skipping (already sent): %s", name)
                continue

            new_contacts.append({
                "name":        name,
                "company":     company,
                "their_role":  parsed["their_role"],
                "hiring_for":  _infer_hiring_for(body, parsed["their_role"], role_keywords),
                "location":    _infer_location(body, title, location),
                "linkedin_url": url,
            })
            logger.info("  Found: %s @ %s", name, company)

    if len(new_contacts) < 3:
        logger.warning("Only %d new contacts found after all searches.", len(new_contacts))

    return new_contacts[:target]


# ── Email formatting ──────────────────────────────────────────────────────────

def _extract_headline(raw_text: str) -> str:
    """Pull the professional headline from the top of a CV (usually line 2)."""
    lines = [l.strip() for l in (raw_text or "").splitlines() if l.strip()]
    # First non-empty line is usually the name; second line is the headline/title
    if len(lines) >= 2:
        headline = lines[1]
        # Strip contact info fragments (phone, email, linkedin, location keywords)
        headline = re.sub(
            r'[\|,]?\s*(\+?\d[\d\s\-]{7,}|[\w.]+@[\w.]+|\blinkedin\b.*|pune|bangalore|mumbai|india)\s*',
            '', headline, flags=re.IGNORECASE
        ).strip().strip('|').strip()
        if len(headline) > 10:
            return headline
    return ""


def format_hiring_section(
    contacts: list,
    user_name: str = "",
    user_summary: str = "",
    role_keywords: list = None,
) -> str:
    today = date.today().strftime("%d %b %Y")
    roles_str = " / ".join((role_keywords or [])[:3]) or "your target roles"

    # Build a clean headline from the raw CV text (user_summary contains raw_text[:300])
    headline = _extract_headline(user_summary)
    background_blurb = headline if headline else "experienced professional"

    lines = [
        "",
        "─" * 58,
        f"  🎯 Today's Hiring Managers & Recruiters ({today})",
        "─" * 58,
        "",
        f"Recruiters actively hiring for {roles_str}:",
        "",
    ]

    if not contacts:
        lines.append("  (No new contacts found today — check back tomorrow)")
        lines.append("")
    else:
        for i, c in enumerate(contacts, 1):
            # Per-contact personalised outreach (keep under 300 chars)
            hiring_for = c.get("hiring_for", roles_str)
            msg = (
                f"Hi {c['name'].split()[0]}, I'm a {background_blurb} actively "
                f"exploring {hiring_for} roles. Came across your profile and would "
                f"love to connect and learn about any relevant openings!"
            )
            if len(msg) > 300:
                msg = (
                    f"Hi {c['name'].split()[0]}, I'm a {background_blurb} "
                    f"looking for {hiring_for} roles. Would love to connect!"
                )

            lines += [
                f"{i}.",
                f"  👤 {c['name']}",
                f"  🏢 Company   : {c['company']}",
                f"  💼 Their Role: {c['their_role']}",
                f"  📋 Hiring For: {c['hiring_for']}",
                f"  📍 Location  : {c['location']}",
                f"  🔗 LinkedIn  : {c['linkedin_url']}",
                f"  ✉️  Outreach   : {msg}",
                f"  📏 Chars      : {len(msg)}/300",
                "",
            ]

    lines += [
        "─" * 58,
        "",
    ]
    return "\n".join(lines)
