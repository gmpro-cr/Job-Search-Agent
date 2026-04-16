"""
digest_generator.py - Generates HTML and TXT job digest files.
Creates beautiful, responsive HTML digests and plain text versions.
"""

import os
import logging
import webbrowser
from datetime import datetime, timedelta
from html import escape

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_IS_VERCEL = bool(os.environ.get("VERCEL"))
if _IS_VERCEL:
    DIGEST_DIR = "/tmp/digests"
else:
    DIGEST_DIR = os.path.join(os.environ.get("DATA_DIR", _BASE_DIR), "digests")
try:
    os.makedirs(DIGEST_DIR, exist_ok=True)
except OSError:
    pass


PORTAL_COLORS = {
    "LinkedIn": "#00CED1",
    "Indeed": "#00A8AB",
    "Naukri": "#14B8A6",
    "HiringCafe": "#00CED1",
    "Wellfound": "#0F3332",
    "IIMJobs": "#002D2F",
}

REMOTE_BADGES = {
    "remote": ("Remote", "#10B981", "#ECFDF5"),
    "hybrid": ("Hybrid", "#F59E0B", "#FFFBEB"),
    "on-site": ("On-site", "#6B7280", "#F3F4F6"),
}

COMPANY_ICONS = {
    "startup": "&#x1F680;",  # rocket
    "corporate": "&#x1F3E2;",  # office building
}


def generate_html_digest(jobs, portal_results, preferences, stats):
    """Generate a beautiful HTML digest file and return the file path."""
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d_%H-%M")
    display_date = now.strftime("%B %d, %Y")
    filename = f"digest_{date_str}.html"
    filepath = os.path.join(DIGEST_DIR, filename)

    total_portals = len(portal_results)
    succeeded = sum(1 for r in portal_results.values() if r["status"] == "success")
    total_from_portals = sum(r["count"] for r in portal_results.values())

    # Portal breakdown for footer
    portal_breakdown = ""
    for portal, result in sorted(portal_results.items(), key=lambda x: x[1]["count"], reverse=True):
        color = PORTAL_COLORS.get(portal.capitalize(), "#6B7280")
        status_icon = "&#x2705;" if result["status"] == "success" else "&#x274C;"
        portal_breakdown += f"""
            <div style="display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid #F3F4F6;">
                <span>{status_icon} <span style="color:{color};font-weight:600;">{portal.capitalize()}</span></span>
                <span style="color:#6B7280;">{result['count']} jobs ({result['time']}s)</span>
            </div>"""

    # Top companies
    top_companies_html = ""
    if stats.get("top_companies"):
        for company, count in stats["top_companies"][:5]:
            top_companies_html += f"<li>{escape(company)} ({count} jobs)</li>"

    # Top roles
    top_roles_html = ""
    if stats.get("top_roles"):
        for role, count in stats["top_roles"][:5]:
            top_roles_html += f"<li>{escape(role)} ({count})</li>"

    # Next digest time
    digest_time = preferences.get("digest_time", "6:00 AM")
    tomorrow = (now + timedelta(days=1)).strftime("%B %d, %Y")

    # Job cards
    job_cards = ""
    for i, job in enumerate(jobs):
        portal = job.get("portal", "Unknown")
        portal_color = PORTAL_COLORS.get(portal, "#6B7280")
        remote = job.get("remote_status", "on-site")
        remote_label, remote_color, remote_bg = REMOTE_BADGES.get(remote, REMOTE_BADGES["on-site"])
        company_type = job.get("company_type", "corporate")
        company_icon = COMPANY_ICONS.get(company_type, "")
        score = job.get("relevance_score", 0)
        skills = job.get("skills", [])
        salary = job.get("salary") or "Not disclosed"
        currency = job.get("salary_currency", "INR")
        if salary != "Not disclosed":
            salary = f"{currency} {salary}" if currency not in salary else salary

        # Score bar color
        if score >= 80:
            score_color = "#00CED1"
        elif score >= 65:
            score_color = "#00A8AB"
        else:
            score_color = "#002D2F"

        # Skills tags
        skills_html = ""
        for skill in skills[:6]:
            skills_html += f'<span style="display:inline-block;background:#E0FAFB;color:#00CED1;padding:2px 8px;border-radius:12px;font-size:12px;margin:2px;">{escape(skill)}</span>'

        # Description excerpt
        desc = job.get("job_description", "")[:200]
        if len(job.get("job_description", "")) > 200:
            desc += "..."

        # Application email (collapsed by default)
        email = escape(job.get("application_email", ""))
        email_id = f"email_{i}"

        apply_url = job.get("apply_url", "#")

        job_cards += f"""
        <div style="background:#FFFFFF;border:1px solid #E5E7EB;border-radius:12px;padding:24px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,0.05);">
            <!-- Header row -->
            <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:12px;flex-wrap:wrap;gap:8px;">
                <div>
                    <span style="display:inline-block;background:{portal_color};color:white;padding:2px 10px;border-radius:12px;font-size:12px;font-weight:600;">{escape(portal)}</span>
                    <span style="display:inline-block;background:{remote_bg};color:{remote_color};padding:2px 10px;border-radius:12px;font-size:12px;font-weight:600;margin-left:4px;">{remote_label}</span>
                    <span style="font-size:14px;margin-left:4px;">{company_icon} {escape(company_type.capitalize())}</span>
                </div>
                <div style="text-align:right;">
                    <div style="font-size:14px;font-weight:700;color:{score_color};">Score: {score}/100</div>
                    <div style="background:#F3F4F6;border-radius:4px;width:120px;height:8px;margin-top:4px;">
                        <div style="background:{score_color};border-radius:4px;height:8px;width:{score}%;"></div>
                    </div>
                </div>
            </div>

            <!-- Title & Company -->
            <h3 style="margin:0 0 4px 0;font-size:18px;color:#111827;">{escape(job.get('role', 'Unknown Role'))}</h3>
            <p style="margin:0 0 8px 0;font-size:15px;color:#4B5563;font-weight:500;">{escape(job.get('company', 'Unknown Company'))}</p>

            <!-- Location & Salary -->
            <div style="display:flex;gap:16px;margin-bottom:12px;flex-wrap:wrap;">
                <span style="color:#6B7280;font-size:14px;">&#x1F4CD; {escape(job.get('location', 'Unknown'))}</span>
                <span style="color:#6B7280;font-size:14px;">&#x1F4B0; {escape(salary)}</span>
            </div>

            <!-- Skills -->
            <div style="margin-bottom:12px;">{skills_html}</div>

            <!-- Description -->
            {"<p style='color:#6B7280;font-size:13px;line-height:1.5;margin-bottom:12px;'>" + escape(desc) + "</p>" if desc else ""}

            <!-- Application Email (collapsible) -->
            <details style="margin-bottom:12px;background:#F9FAFB;border-radius:8px;padding:12px;">
                <summary style="cursor:pointer;font-size:13px;font-weight:600;color:#00CED1;">&#x2709; View Personalized Application Draft</summary>
                <pre style="white-space:pre-wrap;font-family:inherit;font-size:13px;color:#374151;margin-top:8px;line-height:1.5;">{email}</pre>
            </details>

            <!-- Apply Button -->
            <div style="display:flex;gap:8px;">
                <a href="{escape(apply_url)}" target="_blank" style="display:inline-block;background:#00CED1;color:white;padding:10px 24px;border-radius:8px;text-decoration:none;font-weight:600;font-size:14px;">Apply Now &#x2192;</a>
            </div>
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Job Digest - {display_date}</title>
    <style>
        * {{ margin:0; padding:0; box-sizing:border-box; }}
        body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif; background:#F9FAFB; color:#111827; line-height:1.6; }}
        .container {{ max-width:720px; margin:0 auto; padding:24px 16px; }}
        a {{ color:#00CED1; }}
    </style>
</head>
<body>
    <div class="container">
        <!-- Header -->
        <div style="text-align:center;padding:32px 0;border-bottom:2px solid #E5E7EB;margin-bottom:24px;">
            <h1 style="font-size:28px;color:#111827;margin-bottom:8px;">&#x1F4CB; Job Digest</h1>
            <p style="font-size:16px;color:#6B7280;">{display_date}</p>
        </div>

        <!-- Summary -->
        <div style="background:linear-gradient(135deg,#00CED1 0%,#00A8AB 100%);border-radius:12px;padding:24px;color:white;margin-bottom:24px;">
            <h2 style="font-size:20px;margin-bottom:8px;">Found {len(jobs)} matching jobs from {succeeded}/{total_portals} portals</h2>
            <div style="display:flex;gap:24px;flex-wrap:wrap;font-size:14px;opacity:0.9;">
                <span>&#x1F4C5; Today: {stats.get('jobs_today', 0)}</span>
                <span>&#x1F4C6; This week: {stats.get('jobs_this_week', 0)}</span>
                <span>&#x1F4E6; Total tracked: {stats.get('total_jobs', 0)}</span>
            </div>
        </div>

        <!-- Job Cards -->
        {job_cards if job_cards else '<div style="text-align:center;padding:40px;color:#6B7280;"><h3>No matching jobs found today</h3><p>Try adjusting your preferences or check back tomorrow.</p></div>'}

        <!-- Footer -->
        <div style="border-top:2px solid #E5E7EB;padding-top:24px;margin-top:24px;">
            <h3 style="font-size:16px;margin-bottom:12px;">&#x1F4CA; Portal Breakdown</h3>
            <div style="margin-bottom:20px;">{portal_breakdown}</div>

            {"<h3 style='font-size:16px;margin-bottom:8px;'>&#x1F3E2; Top Companies</h3><ul style='margin-bottom:16px;padding-left:20px;color:#6B7280;font-size:14px;'>" + top_companies_html + "</ul>" if top_companies_html else ""}

            {"<h3 style='font-size:16px;margin-bottom:8px;'>&#x1F4BC; Top Roles</h3><ul style='margin-bottom:16px;padding-left:20px;color:#6B7280;font-size:14px;'>" + top_roles_html + "</ul>" if top_roles_html else ""}

            <div style="text-align:center;padding:16px;color:#9CA3AF;font-size:12px;">
                <p>Digest generated at {now.strftime('%Y-%m-%d %H:%M:%S')}</p>
                <p>Next digest scheduled for {tomorrow} at {digest_time}</p>
                <p style="margin-top:8px;">Powered by Job Search Agent</p>
            </div>
        </div>
    </div>
</body>
</html>"""

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)

    logger.info("HTML digest saved to %s", filepath)
    return filepath


def generate_txt_digest(jobs, portal_results, preferences, stats):
    """Generate a plain text digest file and return the file path."""
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d_%H-%M")
    display_date = now.strftime("%B %d, %Y")
    filename = f"digest_{date_str}.txt"
    filepath = os.path.join(DIGEST_DIR, filename)

    lines = []
    lines.append("=" * 60)
    lines.append(f"  JOB DIGEST - {display_date}")
    lines.append("=" * 60)
    lines.append("")

    succeeded = sum(1 for r in portal_results.values() if r["status"] == "success")
    lines.append(f"Found {len(jobs)} matching jobs from {succeeded}/{len(portal_results)} portals")
    lines.append(f"Jobs today: {stats.get('jobs_today', 0)} | This week: {stats.get('jobs_this_week', 0)} | Total: {stats.get('total_jobs', 0)}")
    lines.append("")
    lines.append("-" * 60)

    for i, job in enumerate(jobs, 1):
        lines.append("")
        lines.append(f"  [{i}] {job.get('role', 'Unknown Role')}")
        lines.append(f"      Company:  {job.get('company', 'Unknown')}")
        lines.append(f"      Portal:   {job.get('portal', 'Unknown')}")
        lines.append(f"      Location: {job.get('location', 'Unknown')}")
        salary = job.get("salary") or "Not disclosed"
        lines.append(f"      Salary:   {salary}")
        lines.append(f"      Type:     {job.get('company_type', 'corporate').capitalize()} | {job.get('remote_status', 'on-site').capitalize()}")
        lines.append(f"      Score:    {job.get('relevance_score', 0)}/100")
        skills = job.get("skills", [])
        if skills:
            lines.append(f"      Skills:   {', '.join(skills)}")
        desc = job.get("job_description", "")[:150]
        if desc:
            lines.append(f"      Desc:     {desc}...")
        lines.append(f"      Apply:    {job.get('apply_url', 'N/A')}")
        lines.append("")
        lines.append(f"      --- Application Draft ---")
        for email_line in (job.get("application_email", "")).split("\n"):
            lines.append(f"      {email_line}")
        lines.append("")
        lines.append("-" * 60)

    # Footer
    lines.append("")
    lines.append("PORTAL BREAKDOWN:")
    for portal, result in sorted(portal_results.items(), key=lambda x: x[1]["count"], reverse=True):
        status = "OK" if result["status"] == "success" else "FAILED"
        lines.append(f"  {portal.capitalize():15s} {status:8s} {result['count']:3d} jobs ({result['time']}s)")

    if stats.get("top_companies"):
        lines.append("")
        lines.append("TOP COMPANIES:")
        for company, count in stats["top_companies"][:5]:
            lines.append(f"  - {company} ({count} jobs)")

    if stats.get("top_roles"):
        lines.append("")
        lines.append("TOP ROLES:")
        for role, count in stats["top_roles"][:5]:
            lines.append(f"  - {role} ({count})")

    lines.append("")
    lines.append(f"Generated: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    digest_time = preferences.get("digest_time", "6:00 AM")
    tomorrow = (now + timedelta(days=1)).strftime("%B %d, %Y")
    lines.append(f"Next digest: {tomorrow} at {digest_time}")
    lines.append("")

    text = "\n".join(lines)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(text)

    logger.info("TXT digest saved to %s", filepath)
    return filepath


def generate_digest(jobs, portal_results, preferences, stats, open_browser=True):
    """
    Generate both HTML and TXT digests.
    Returns (html_path, txt_path).
    """
    html_path = generate_html_digest(jobs, portal_results, preferences, stats)
    txt_path = generate_txt_digest(jobs, portal_results, preferences, stats)

    if open_browser:
        try:
            webbrowser.open(f"file://{html_path}")
            logger.info("Opened digest in browser")
        except Exception as e:
            logger.warning("Could not open browser: %s", e)

    return html_path, txt_path


def get_latest_digest():
    """Find and return the path to the most recent HTML digest."""
    try:
        files = [
            os.path.join(DIGEST_DIR, f)
            for f in os.listdir(DIGEST_DIR)
            if f.startswith("digest_") and f.endswith(".html")
        ]
        if not files:
            return None
        return max(files, key=os.path.getmtime)
    except Exception:
        return None
