"""Build a "Funding Rundown" email — recently-funded startups in The Rundown AI
newsletter format: a table-of-contents up top, then per-startup blocks with
"The Rundown / The details / Why it matters".

build_funding_digest(items) -> (subject, html, text)
Each item is a dict with: startup, amount, round, region, source, source_url,
title, posted_date.
"""
import html as _html
from datetime import datetime


def _esc(s):
    return _html.escape(str(s or "")).strip()


def _headline(it):
    amt = (it.get("amount") or "").strip()
    return f"{_esc(it.get('startup'))} raises {_esc(amt)}" if amt else f"{_esc(it.get('startup'))} lands new funding"


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
