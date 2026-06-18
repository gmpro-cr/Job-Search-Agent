"""Email the owner when the scrape workflow fails or is cancelled.

Email is the only ops channel. Sends a short alert via Gmail SMTP using the
same credentials the digests use. Best-effort: never raises.
"""
import os
import smtplib
from email.message import EmailMessage


def main():
    addr = os.environ.get("GMAIL_ADDRESS", "").strip()
    pw = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    to = (os.environ.get("EMAIL_RECIPIENT", "").strip()
          or os.environ.get("OWNER_EMAIL", "").strip()
          or addr)
    if not (addr and pw and to):
        print("notify_failure: email not configured — skipping")
        return
    run_url = os.environ.get("RUN_URL", "(run URL unavailable)")
    msg = EmailMessage()
    msg["Subject"] = "[ALERT] Job Search Agent: scrape run failed"
    msg["From"] = addr
    msg["To"] = to
    msg.set_content(
        "The Daily Job Scrape workflow failed or was cancelled before finishing.\n\n"
        f"Run: {run_url}\n\n"
        "Open the GitHub Actions logs to see which phase failed."
    )
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as s:
            s.ehlo(); s.starttls(); s.ehlo()
            s.login(addr, pw)
            s.send_message(msg)
        print("notify_failure: alert email sent to", to)
    except Exception as e:
        print("notify_failure: could not send alert:", e)


if __name__ == "__main__":
    main()
