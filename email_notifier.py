"""
email_notifier.py - Send job digest emails via Gmail SMTP.

Uses only Python stdlib (smtplib + email.mime). Requires a Gmail address
and an App Password (not your regular Google password).

Generate an App Password at: https://myaccount.google.com/apppasswords
"""

import logging
import re
import smtplib
from email.message import EmailMessage
from datetime import datetime

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def _ascii_only(s: str) -> str:
    """Strip non-ASCII characters that can sneak in via copy-paste (e.g. \xa0).
    Email addresses and Gmail App Passwords must be pure ASCII for smtplib.login()."""
    return re.sub(r'[^\x00-\x7F]', '', str(s or '')).strip()


def _build_html_body(jobs, preferences):
    """Build an HTML email body with a job listing table."""
    job_titles = ", ".join(preferences.get("job_titles", []))
    locations = ", ".join(preferences.get("locations", []))
    now = datetime.now().strftime("%B %d, %Y %I:%M %p")

    rows = []
    for j in jobs:
        score = j["user_cv_score"] if j.get("user_cv_score") is not None else j.get("relevance_score", "N/A")
        apply_url = j.get("apply_url", "#")
        rows.append(
            f"<tr>"
            f'<td style="padding:8px;border-bottom:1px solid #eee;">'
            f'<strong>{j.get("role", "")}</strong><br>'
            f'<span style="color:#666;">{j.get("company", "")}</span></td>'
            f'<td style="padding:8px;border-bottom:1px solid #eee;">{j.get("date_found", "Today")}</td>'
            f'<td style="padding:8px;border-bottom:1px solid #eee;">{j.get("location", "")}</td>'
            f'<td style="padding:8px;border-bottom:1px solid #eee;">{j.get("remote_status", "")}</td>'
            f'<td style="padding:8px;border-bottom:1px solid #eee;">{j.get("portal", "")}</td>'
            f'<td style="padding:8px;border-bottom:1px solid #eee;text-align:center;">{score}</td>'
            f'<td style="padding:8px;border-bottom:1px solid #eee;">'
            f'<a href="{apply_url}" style="color:#4F46E5;">Apply</a></td>'
            f"</tr>"
        )

    table_rows = "\n".join(rows) if rows else (
        '<tr><td colspan="7" style="padding:16px;text-align:center;color:#999;">'
        "No new jobs found in this run.</td></tr>"
    )

    return f"""\
<html>
<body style="font-family:Arial,sans-serif;color:#333;max-width:800px;margin:0 auto;">
  <div style="background:#4F46E5;color:#fff;padding:20px;border-radius:8px 8px 0 0;">
    <h1 style="margin:0;font-size:22px;">Job Search Agent - Daily Digest</h1>
    <p style="margin:4px 0 0;font-size:13px;opacity:0.9;">{now}</p>
  </div>
  <div style="padding:16px;background:#f9fafb;border:1px solid #e5e7eb;">
    <p style="margin:0 0 4px;font-size:13px;"><strong>Searching for:</strong> {job_titles}</p>
    <p style="margin:0;font-size:13px;"><strong>Locations:</strong> {locations}</p>
  </div>
  <table style="width:100%;border-collapse:collapse;font-size:13px;margin-top:8px;">
    <thead>
      <tr style="background:#f3f4f6;">
        <th style="padding:8px;text-align:left;">Position / Company</th>
        <th style="padding:8px;text-align:left;">Date</th>
        <th style="padding:8px;text-align:left;">Location</th>
        <th style="padding:8px;text-align:left;">Type</th>
        <th style="padding:8px;text-align:left;">Source</th>
        <th style="padding:8px;text-align:center;">Score</th>
        <th style="padding:8px;text-align:left;">Link</th>
      </tr>
    </thead>
    <tbody>
      {table_rows}
    </tbody>
  </table>
  <div style="padding:16px;font-size:12px;color:#999;text-align:center;border-top:1px solid #e5e7eb;margin-top:8px;">
    <p>Found {len(jobs)} job(s) in this digest.</p>
    <p>Sent by Job Search Agent</p>
  </div>
</body>
</html>"""


def _build_plain_body(jobs, preferences):
    """Build a plain-text fallback email body."""
    job_titles = ", ".join(preferences.get("job_titles", []))
    locations = ", ".join(preferences.get("locations", []))
    now = datetime.now().strftime("%B %d, %Y %I:%M %p")

    lines = [
        "JOB SEARCH AGENT - DAILY DIGEST",
        f"Date: {now}",
        f"Searching for: {job_titles}",
        f"Locations: {locations}",
        "",
        "=" * 60,
        "",
    ]

    if not jobs:
        lines.append("No new jobs found in this run.")
    else:
        for i, j in enumerate(jobs, 1):
            lines.append(f"{i}. {j.get('role', '')} at {j.get('company', '')}")
            lines.append(f"   Location: {j.get('location', '')} | Type: {j.get('remote_status', '')}")
            score = j["user_cv_score"] if j.get("user_cv_score") is not None else j.get("relevance_score", "N/A")
            lines.append(f"   Source: {j.get('portal', '')} | Score: {score}")
            lines.append(f"   Apply: {j.get('apply_url', '')}")
            lines.append("")

    lines.append("=" * 60)
    lines.append(f"Found {len(jobs)} job(s). Sent by Job Search Agent.")
    return "\n".join(lines)


def send_html_email(recipient, subject, html_body, plain_body, preferences):
    """Send a pre-built HTML email (with a plain-text alternative) via Gmail SMTP.

    Generic sibling of send_job_email for non-job digests (e.g. the funding
    rundown). Reads gmail_address / gmail_app_password from preferences.
    Returns (ok: bool, err: str | None).
    """
    gmail_address = _ascii_only(preferences.get("gmail_address", ""))
    gmail_app_password = _ascii_only(preferences.get("gmail_app_password", ""))
    recipient = _ascii_only(recipient)

    if not gmail_address or not gmail_app_password:
        logger.info("Gmail credentials not configured - skipping email")
        return False, "no credentials"
    if not recipient:
        return False, "no recipient"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = gmail_address
    msg["To"] = recipient
    msg.set_content(plain_body or "", charset="utf-8")
    msg.add_alternative(html_body, subtype="html", charset="utf-8")

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(gmail_address, gmail_app_password)
            server.send_message(msg)
        logger.info("HTML email sent to %s (%r)", recipient, subject)
        return True, None
    except smtplib.SMTPAuthenticationError:
        return False, "Gmail authentication failed — use an App Password."
    except OSError as e:
        return False, f"Cannot reach Gmail SMTP ({e})."
    except Exception as e:
        logger.error("Failed to send email to %s: %s", recipient, e)
        return False, f"Send failed: {e}"


def send_job_email(recipient, jobs, preferences, subject=None):
    """
    Send a job digest email via Gmail SMTP.

    Args:
        recipient: Email address to send to (the user's email from prefs)
        jobs: List of job dicts to include in the email
        preferences: User preferences dict containing gmail_address and gmail_app_password
        subject: Optional subject line override (e.g. a routine name). When None,
                 a default "Job Digest - <date> (N jobs)" subject is used.

    Returns:
        True on success, False on failure
    """
    gmail_address = _ascii_only(preferences.get("gmail_address", ""))
    gmail_app_password = _ascii_only(preferences.get("gmail_app_password", ""))
    recipient = _ascii_only(recipient)

    if not gmail_address or not gmail_app_password:
        logger.info("Gmail credentials not configured - skipping email notification")
        return False

    if not recipient:
        logger.info("No recipient email configured - skipping email notification")
        return False

    msg = EmailMessage()
    msg["Subject"] = subject or f"Job Digest - {datetime.now().strftime('%b %d, %Y')} ({len(jobs)} jobs)"
    msg["From"] = gmail_address
    msg["To"] = recipient

    plain_body = _build_plain_body(jobs, preferences)
    html_body = _build_html_body(jobs, preferences)

    # set_content + add_alternative handles UTF-8 encoding natively —
    # no ascii codec errors from non-breaking spaces in scraped job data.
    msg.set_content(plain_body, charset="utf-8")
    msg.add_alternative(html_body, subtype="html", charset="utf-8")

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(gmail_address, gmail_app_password)
            server.send_message(msg)

        logger.info("Job digest email sent to %s (%d jobs)", recipient, len(jobs))
        return True, None

    except smtplib.SMTPAuthenticationError:
        msg = ("Gmail authentication failed — use an App Password, not your account password. "
               "Generate one at https://myaccount.google.com/apppasswords")
        logger.error(msg)
        return False, msg
    except OSError as e:
        # Covers ConnectionRefusedError, TimeoutError, and platform SMTP blocks.
        msg = f"Cannot reach Gmail SMTP ({e}). If running on Vercel, SMTP is blocked — use Resend instead."
        logger.error(msg)
        return False, msg
    except Exception as e:
        msg = f"Send failed: {e}"
        logger.error("Failed to send email to %s: %s", recipient, e)
        return False, msg
