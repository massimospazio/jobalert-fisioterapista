import json
import os
from pathlib import Path

import requests

SUMMARY = Path("logs/run_summary.json")
RUN_HEALTH = Path("logs/run_health.json")
NEW_JOBS = Path("logs/new_jobs.json")


def _load(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _short_job(job: dict) -> str:
    title = job.get("title") or "Fisioterapista"
    company = job.get("company") or "Azienda non indicata"
    location = job.get("location") or "Località non indicata"
    score = job.get("score")
    distance = job.get("distance_km")
    bits = [f"• {title} — {company} — {location}"]
    if score is not None:
        bits.append(f"score {score}")
    if distance is not None:
        bits.append(f"{distance:.1f} km")
    return " | ".join(bits)


def _impact_label(pct: float) -> str:
    if pct <= 10:
        return "BASSO"
    if pct <= 35:
        return "MEDIO"
    return "ALTO"


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("TELEGRAM_SKIPPED configuration_missing")
        return

    status = os.environ.get("JOB_STATUS", "success").lower()
    summary = _load(SUMMARY, {})
    new_jobs = _load(NEW_JOBS, [])
    health = summary.get("health") or _load(RUN_HEALTH, {})
    repo = os.environ.get("GITHUB_REPOSITORY", "massimospazio/jobalert-fisioterapista")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    actions_url = f"{server}/{repo}/actions/runs/{run_id}" if run_id else f"{server}/{repo}/actions"
    report_url = os.environ.get("REPORT_URL", "").strip()

    if status == "success" and health.get("status") == "DEGRADED":
        icon, label = "🟠", "DEGRADED"
    elif status == "success":
        icon, label = "🟢", "OK"
    elif status == "cancelled":
        icon, label = "🟠", "CANCELLED"
    else:
        icon, label = "🔴", "FAILED"

    lines = [f"{icon} Job Alert Fisioterapista — {label}"]
    if summary:
        lines += ["", f"Elaborati: {summary.get('raw_audit_records', 0)}", f"Inclusi: {summary.get('included', 0)}", f"Esclusi: {summary.get('excluded', 0)}", f"Nuovi: {summary.get('new', len(new_jobs))}"]
        sources = summary.get("source_counts") or {}
        if sources:
            lines.append("Fonti: " + " · ".join(f"{k} {v}" for k, v in sorted(sources.items())))

    linkedin = health.get("linkedin") or {}
    impacted = int(linkedin.get("detail_impacted") or 0)
    new_linkedin = int(linkedin.get("new_opportunities") or 0)
    impact_pct = float(linkedin.get("impact_pct") or 0)
    if impacted:
        lines += [
            "",
            f"LinkedIn DEGRADED: {impacted}/{new_linkedin} nuove opportunità senza dettaglio ({impact_pct:.1f}%) · impatto {_impact_label(impact_pct)}",
            "Dati base (titolo, azienda, località, data) comunque disponibili.",
        ]

    issues = list(health.get("warnings") or [])
    issues += [f"{e.get('source')}: {e.get('message')}" for e in health.get("source_errors") or []]
    if issues:
        lines += ["", "⚠️ Diagnostica:"] + [f"• {item}" for item in issues[:4]]

    zr = summary.get("zenrows") or {}
    run_sources = health.get("zenrows_by_source") or {}
    if run_sources:
        detail = " + ".join(f"{name} {item.get('credits', 0)}" for name, item in run_sources.items())
        lines.append(f"\nZenRows run: {detail} = {health.get('zenrows_run_credits', 0)} crediti")
    if zr:
        risk_icon = {"SAFE": "🟢", "WARNING": "🟠", "RISK": "🔴"}.get(zr.get("risk"), "⚪")
        lines += [
            f"{risk_icon} ZenRows mese: {zr.get('consumed', 0)}/{zr.get('monthly_limit', 5000)} usati · {zr.get('remaining', 0)} residui",
            f"Stima fine mese: {zr.get('projected_consumed', 0)}/{zr.get('monthly_limit', 5000)} ({zr.get('projected_pct', 0)}%)",
        ]
        every = int(zr.get("recommended_every_days") or 1)
        if every > 1:
            lines.append(f"⚠️ Frequenza consigliata: ogni {every} giorni")
        else:
            lines.append("Frequenza giornaliera sostenibile")

    if new_jobs:
        lines += ["", "Nuove opportunità:"]
        for job in new_jobs[:5]:
            lines.append(_short_job(job))
        if len(new_jobs) > 5:
            lines.append(f"+ {len(new_jobs) - 5} altre")

    lines += ["", f"GitHub Actions: {actions_url}"]
    if report_url:
        lines.append(f"Report HTML: {report_url}")

    response = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat_id, "text": "\n".join(lines), "disable_web_page_preview": True}, timeout=30)
    response.raise_for_status()
    print(f"TELEGRAM_SENT status={label} new_jobs={len(new_jobs)}")


if __name__ == "__main__":
    main()
