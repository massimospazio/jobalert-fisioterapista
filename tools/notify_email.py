import json
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path


NEW_JOBS_PATH = Path("logs/new_jobs.json")


def _load_jobs() -> list[dict]:
    if not NEW_JOBS_PATH.exists():
        return []
    data = json.loads(NEW_JOBS_PATH.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def _format_job(job: dict) -> str:
    score = job.get("score")
    distance = job.get("distance_km")
    parts = [
        job.get("title") or "Fisioterapista",
        job.get("company") or "Azienda non indicata",
        f"{job.get('location') or 'Località non indicata'} ({job.get('province') or '?'})",
    ]
    if score is not None:
        parts.append(f"score {score}/100")
    if distance is not None:
        parts.append(f"{distance:.1f} km da Albano Laziale")
    lines = [" | ".join(parts)]
    if job.get("contract_type") and job.get("contract_type") != "non_specificato":
        lines.append(f"Contratto: {job['contract_type']}")
    if job.get("salary"):
        lines.append(f"Retribuzione: {job['salary']}")
    if job.get("application_deadline"):
        lines.append(f"Scadenza: {job['application_deadline']}")
    if job.get("url"):
        lines.append(job["url"])
    return "\n".join(lines)


def main() -> None:
    jobs = _load_jobs()
    if not jobs:
        print("EMAIL_SKIPPED no_new_jobs")
        return

    host = os.environ.get("SMTP_HOST", "").strip()
    port = int(os.environ.get("SMTP_PORT", "587"))
    username = os.environ.get("SMTP_USERNAME", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "")
    sender = os.environ.get("ALERT_EMAIL_FROM", username).strip()
    recipient = os.environ.get("ALERT_EMAIL_TO", "").strip()

    if not all([host, username, password, sender, recipient]):
        print(f"EMAIL_SKIPPED configuration_missing new_jobs={len(jobs)}")
        return

    body = "\n\n".join(_format_job(job) for job in jobs)
    msg = EmailMessage()
    msg["Subject"] = f"Job Alert Fisioterapista: {len(jobs)} nuove opportunità"
    msg["From"] = sender
    msg["To"] = recipient
    msg.set_content(
        "Nuove opportunità incluse dal Job Alert (provincia di Roma):\n\n"
        + body
        + "\n\nElenco ordinato per punteggio e distanza."
    )

    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(username, password)
        smtp.send_message(msg)

    print(f"EMAIL_SENT new_jobs={len(jobs)} recipient={recipient}")


if __name__ == "__main__":
    main()
