"""Build a "Funding Rundown" email — recently-funded startups in The Rundown AI
newsletter format: a table-of-contents up top, then per-startup blocks with
"The Rundown / The details / Why it matters".

build_funding_digest(items) -> (subject, html, text)
Each item is a dict with: startup, amount, round, region, source, source_url,
title, posted_date.
"""
import html as _html
import json
import logging
import os
import re
from datetime import datetime

logger = logging.getLogger(__name__)


def _esc(s):
    return _html.escape(str(s or "")).strip()


def _clean_name(s):
    """Tidy a scraped startup name for display: drop '{Funding Alert}' tags and
    trailing descriptor clauses ('Baseten, a US-based company,' -> 'Baseten')."""
    s = re.sub(r"\{[^}]*\}", "", str(s or "")).strip()
    s = re.sub(r"^(?:funding alert:?\s*)", "", s, flags=re.I).strip()
    s = re.split(r"\s*[,;]\s*", s)[0]               # cut trailing clause
    s = re.split(r"\s+(?:raises|raised|secures|secured|closes|lands|bags|nets)\b",
                 s, flags=re.I)[0]
    return s.strip(" -–—:\"") or str(s or "")


def gemini_blurbs(items, timeout=30):
    """Best-effort one-line "what they do" per startup via the Gemini REST API
    (no SDK — uses requests, which is in the slim deps). Returns a dict keyed by
    the 1-based item index as a string, e.g. {"1": "Blockchain data infra"}.
    Returns {} when GEMINI_API_KEY is unset or anything fails — callers must
    treat blurbs as optional. Index-keyed (not name-keyed) so messy startup
    names still map back cleanly.
    """
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key or not items:
        return {}
    import requests
    listing = "\n".join(
        f"{i}. {it.get('startup')} — {it.get('title') or ''}" for i, it in enumerate(items, 1)
    )
    prompt = (
        "These startups just raised funding. For EACH numbered item, write a very "
        "short description (max 14 words) of what the company does or its sector, "
        "grounded in the company name and the funding headline. Do not invent "
        "specific metrics or claims. Return ONLY a JSON object mapping the item "
        "number (as a string) to its description.\n\n" + listing
    )
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1024}}
    for model in ("gemini-flash-lite-latest", "gemini-2.0-flash-lite", "gemini-1.5-flash"):
        try:
            url = ("https://generativelanguage.googleapis.com/v1beta/models/"
                   f"{model}:generateContent?key={key}")
            r = requests.post(url, json=body, timeout=timeout)
            if r.status_code != 200:
                continue
            txt = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            txt = re.sub(r"^```(?:json)?|```$", "", txt, flags=re.M).strip()
            data = json.loads(txt)
            logger.info("Gemini blurbs: %d via %s", len(data), model)
            return {str(k).strip(): str(v).strip() for k, v in data.items()}
        except Exception as e:
            logger.warning("Gemini blurbs failed on %s: %s", model, e)
    return {}


def _headline(it):
    amt = (it.get("amount") or "").strip()
    return f"{_esc(it.get('startup'))} raises {_esc(amt)}" if amt else f"{_esc(it.get('startup'))} lands new funding"


# Purpose clause ("...to bring AI agents to banking") and sector descriptor
# ("Blockchain data startup ...") pulled straight from the headline — a free,
# hallucination-proof "what they do" when Gemini isn't available.
_PURPOSE = re.compile(
    r'\bto\s+((?:build|bring|make|develop|provide|power|help|create|automate|scale|'
    r'deliver|launch|enable|offer|expand|accelerate|transform|deploy|fight|tackle|'
    r'modernize|digitize|reinvent)\b.{4,64}?)(?:\s*[-–—|:]|\.|$)', re.I)
_SECTOR = re.compile(
    r'\b((?:[A-Za-z][\w&/.-]*\s+){1,4}?'
    r'(?:startup|start-up|platform|fintech|fin-tech|insurtech|healthtech|edtech|'
    r'deeptech|biotech|agritech|marketplace|network|maker|provider))\b', re.I)


def _blurb_from_title(title, startup):
    t = _html.unescape(re.sub(r"<[^>]+>", "", title or "")).strip()
    if not t:
        return ""
    m = _PURPOSE.search(t)
    if m:
        s = m.group(1).strip().rstrip(".")
        return s[:1].upper() + s[1:] if s else ""
    m = _SECTOR.search(t)
    if m:
        phrase = m.group(1).strip()
        # Reject when the match is really just the company's own name.
        sw = (startup or "").strip().lower()
        if phrase.lower() == sw or (sw and sw.startswith(phrase.split()[0].lower())
                                    and phrase.lower().endswith(("labs", "lab", "app"))):
            return ""
        return phrase
    return ""


def _why_it_matters(it):
    rnd = (it.get("round") or "").strip()
    region = (it.get("region") or "").strip()
    startup = (it.get("startup") or "this startup").strip()
    if rnd:
        return (f"A fresh {rnd} round usually precedes hiring and product pushes — "
                f"{startup} is one to watch" + (f" in {region}." if region else "."))
    return (f"New capital means more runway and, often, open roles — keep "
            f"{startup} on your radar" + (f" ({region})." if region else "."))


def _website_link(startup):
    # DuckDuckGo "ducky" redirect lands on the company's own site.
    from urllib.parse import quote
    return "https://duckduckgo.com/?q=" + quote("\\" + (startup or "") + " official website")


def _read_minutes(n):
    return max(1, round(n * 0.25))


def build_funding_digest(items):
    items = list(items or [])
    for it in items:
        it["startup"] = _clean_name(it.get("startup"))
        # Effective "what they do": Gemini blurb if set, else derive from headline.
        if not it.get("blurb"):
            it["blurb"] = _blurb_from_title(it.get("title"), it.get("startup"))
    today = datetime.now().strftime("%A, %b %d, %Y")
    n = len(items)
    top = items[0]["startup"] if n else "startups"
    extra = f" + {n - 1} more" if n > 1 else ""
    subject = f"💰 Funding Rundown: {top}{extra}"

    # ---- HTML ----
    ink, muted, line, accent, bg = "#16161a", "#6b7280", "#e6e6ea", "#0b5cff", "#ffffff"
    css_h = "font:700 20px/1.3 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:%s;margin:0 0 6px" % ink
    css_p = "font:400 15px/1.6 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:%s;margin:0 0 10px" % ink
    css_lbl = "font:700 13px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:%s" % ink

    toc = "".join(
        f'<li style="margin:0 0 6px">{_esc(it.get("startup"))} '
        f'<span style="color:{muted}">— {_esc(it.get("amount") or it.get("round") or "new round")}'
        f'{(" · " + _esc(it.get("region"))) if it.get("region") else ""}</span></li>'
        for it in items
    )

    blocks = []
    for i, it in enumerate(items, 1):
        details = []
        if it.get("amount"):  details.append(("Amount", it["amount"]))
        if it.get("round"):   details.append(("Round", it["round"]))
        if it.get("region"):  details.append(("Region", it["region"]))
        if it.get("source"):  details.append(("Reported by", it["source"]))
        if it.get("posted_date"): details.append(("Posted", str(it["posted_date"])[:10]))
        det_rows = "".join(
            f'<li style="margin:0 0 4px"><span style="{css_lbl}">{_esc(k)}:</span> '
            f'<span style="font:400 14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:{ink}">{_esc(v)}</span></li>'
            for k, v in details
        )
        src = it.get("source_url") or "#"
        blocks.append(f"""
        <tr><td style="padding:22px 0 0">
          <div style="font:700 12px/1 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:{accent};letter-spacing:.04em">{i:02d}</div>
          <h2 style="{css_h};margin-top:6px">{_headline(it)}</h2>
          {(f'<p style="font:400 14px/1.55 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:{muted};margin:0 0 10px;font-style:italic">' + _esc(it.get("blurb")) + '</p>') if it.get("blurb") else ""}
          <p style="{css_p}"><span style="{css_lbl}">The Rundown:</span> {_esc(it.get('title') or _headline(it))}</p>
          <p style="{css_lbl};margin:0 0 4px">The details:</p>
          <ul style="margin:0 0 10px;padding-left:18px">{det_rows}</ul>
          <p style="{css_p}"><span style="{css_lbl}">Why it matters:</span> {_why_it_matters(it)}</p>
          <p style="margin:0 0 4px">
            <a href="{_esc(src)}" style="font:600 14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:{accent};text-decoration:none">Read the announcement →</a>
            &nbsp;&nbsp;
            <a href="{_website_link(it.get('startup'))}" style="font:600 14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:{muted};text-decoration:none">Visit {_esc(it.get('startup'))} →</a>
          </p>
          <hr style="border:none;border-top:1px solid {line};margin:18px 0 0">
        </td></tr>""")

    html_doc = f"""<!doctype html><html><body style="margin:0;background:#f4f4f6;padding:24px 12px">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
    <table role="presentation" width="640" cellpadding="0" cellspacing="0" style="max-width:640px;width:100%;background:{bg};border:1px solid {line};border-radius:14px;padding:28px 28px 22px">
      <tr><td>
        <div style="font:700 13px/1 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:{accent};letter-spacing:.08em;text-transform:uppercase">The Funding Rundown</div>
        <div style="font:400 13px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:{muted};margin-top:4px">{today} · {_read_minutes(n)} min read</div>
        <p style="{css_p};margin-top:14px">Here's who just raised — {n} freshly funded startup{'s' if n != 1 else ''} across our sources.</p>
        <p style="{css_lbl};margin:14px 0 6px">In today's rundown:</p>
        <ul style="margin:0 0 6px;padding-left:18px;font:400 14px/1.6 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:{ink}">{toc}</ul>
        <hr style="border:none;border-top:2px solid {ink};margin:14px 0 0">
      </td></tr>
      {''.join(blocks)}
      <tr><td style="padding-top:18px">
        <p style="font:400 12px/1.6 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:{muted};margin:0">
          {n} startups · aggregated from FinSMEs, Google News & TechCrunch by your Job Search Agent.
          Want roles at any of these? Reply and I'll pull openings.</p>
      </td></tr>
    </table></td></tr></table></body></html>"""

    # ---- Plain text ----
    tlines = [f"THE FUNDING RUNDOWN — {today} ({_read_minutes(n)} min read)", ""]
    tlines.append(f"Here's who just raised — {n} freshly funded startup(s).")
    tlines.append("")
    tlines.append("In today's rundown:")
    for it in items:
        tlines.append(f"  - {it.get('startup')} — {it.get('amount') or it.get('round') or 'new round'}"
                      + (f" ({it.get('region')})" if it.get('region') else ""))
    tlines.append("")
    for i, it in enumerate(items, 1):
        tlines.append(f"{i:02d}. {_html.unescape(_headline(it))}")
        if it.get("blurb"):
            tlines.append(f"    {it['blurb']}")
        tlines.append(f"    The Rundown: {it.get('title') or ''}")
        det = []
        for k in ("amount", "round", "region", "source", "posted_date"):
            if it.get(k):
                det.append(f"{k.replace('_',' ').title()}: {str(it[k])[:10] if k=='posted_date' else it[k]}")
        if det:
            tlines.append("    The details: " + " | ".join(det))
        tlines.append(f"    Why it matters: {_html.unescape(_why_it_matters(it))}")
        if it.get("source_url"):
            tlines.append(f"    Read: {it['source_url']}")
        tlines.append("")
    text = "\n".join(tlines)

    return subject, html_doc, text
