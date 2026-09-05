import html
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from core.zenrows_usage import forecast, load_state

BASELINE = Path("data/baseline_jobs.json")
NEW_JOBS = Path("logs/new_jobs.json")
RUN_HEALTH = Path("logs/run_health.json")
DOCS = Path("docs")
LATEST_HTML = DOCS / "latest.html"
SUMMARY_JSON = Path("logs/run_summary.json")


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _audit_records() -> list[dict]:
    files = sorted(Path("logs").glob("audit-*.jsonl"))
    if not files:
        return []
    records = []
    for line in files[-1].read_text(encoding="utf-8").splitlines():
        try:
            records.append(json.loads(line))
        except Exception:
            pass
    return records


def _cell(value):
    return html.escape("" if value is None else str(value))


def _job_row(job: dict, is_new: bool) -> str:
    score = job.get("score")
    distance = job.get("distance_km")
    url = job.get("url") or ""
    title = _cell(job.get("title") or "Fisioterapista")
    if url:
        title = f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener">{title}</a>'
    opening = "<tr class='new'>" if is_new else "<tr>"
    return opening + "".join([
        f"<td>{_cell(score)}</td>", f"<td>{'' if distance is None else f'{distance:.1f} km'}</td>",
        f"<td>{_cell(job.get('location'))}</td>", f"<td>{_cell(job.get('company'))}</td>", f"<td>{title}</td>",
        f"<td>{_cell(job.get('contract_type'))}</td>", f"<td>{_cell(job.get('published_at'))}</td>", f"<td>{_cell(job.get('source'))}</td>", "</tr>",
    ])


def main() -> None:
    baseline = _load_json(BASELINE, [])
    new_jobs = _load_json(NEW_JOBS, [])
    health = _load_json(RUN_HEALTH, {"status": "OK", "warnings": [], "source_errors": [], "zenrows_by_source": {}})
    audit = _audit_records()
    new_ids = {j.get("job_id") for j in new_jobs}
    decisions = Counter(r.get("decision") for r in audit)
    exclusions = Counter()
    for record in audit:
        if record.get("decision") == "EXCLUDED":
            for rule in (record.get("filter") or {}).get("exclusion_rules") or []:
                exclusions[rule] += 1
    source_counts = Counter((r.get("job") or {}).get("source") for r in audit)
    zenrows = forecast(load_state(), credits_per_run=50)
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(), "raw_audit_records": len(audit),
        "included": decisions.get("INCLUDED", 0), "excluded": decisions.get("EXCLUDED", 0), "new": len(new_jobs),
        "baseline_count": len(baseline), "source_counts": dict(source_counts), "exclusion_rules": dict(exclusions),
        "zenrows": zenrows, "health": health,
    }
    SUMMARY_JSON.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    rows = "".join(_job_row(job, job.get("job_id") in new_ids) for job in baseline)
    source_text = " · ".join(f"{_cell(k)}: {v}" for k, v in sorted(source_counts.items())) or "n/d"
    exclusion_text = " · ".join(f"{_cell(k)}: {v}" for k, v in sorted(exclusions.items())) or "nessuna"
    generated = datetime.now().astimezone().strftime("%d/%m/%Y %H:%M")
    zrisk = {"SAFE":"🟢 SAFE","WARNING":"🟠 ATTENZIONE","RISK":"🔴 RISCHIO"}.get(zenrows["risk"], zenrows["risk"])
    recommendation = "giornaliera" if zenrows["recommended_every_days"] == 1 else f"ogni {zenrows['recommended_every_days']} giorni"
    run_status = health.get("status", "OK")
    run_label = "🟢 OK" if run_status == "OK" else "🟠 DEGRADED"
    issues = list(health.get("warnings") or []) + [f"{e.get('source')}: {e.get('message')}" for e in health.get("source_errors") or []]
    issues_text = " · ".join(_cell(x) for x in issues) or "nessun problema rilevato"
    zr_sources = health.get("zenrows_by_source") or {}
    zr_detail = " · ".join(f"{_cell(name)} {item.get('credits', 0)} crediti" for name, item in zr_sources.items()) or "nessun consumo nel run"
    page = f"""<!doctype html><html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Job Alert Fisioterapista</title>
<style>body{{font-family:system-ui,-apple-system,sans-serif;margin:24px;background:#f6f7f9;color:#1f2937}} .wrap{{max-width:1400px;margin:auto}} .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:18px 0}} .card,.box{{background:white;padding:16px;border-radius:12px;box-shadow:0 1px 4px #0001}} .big{{font-size:28px;font-weight:700}} table{{width:100%;border-collapse:collapse;background:white}} th,td{{padding:10px;border-bottom:1px solid #e5e7eb;text-align:left}} th{{position:sticky;top:0;background:#111827;color:white}} tr.new{{background:#ecfdf5}} a{{color:#0369a1}} .meta{{color:#6b7280}} .box{{margin:12px 0;overflow:auto}}</style></head><body><div class="wrap">
<h1>Job Alert Fisioterapista</h1><div class="meta">Ultimo aggiornamento: {generated}</div>
<div class="cards"><div class="card"><div class="big">{len(baseline)}</div>offerte incluse</div><div class="card"><div class="big">{len(new_jobs)}</div>nuove</div><div class="card"><div class="big">{decisions.get('EXCLUDED',0)}</div>escluse</div><div class="card"><div class="big">{len(audit)}</div>record elaborati</div></div>
<div class="box"><strong>Stato run:</strong> {run_label}<br><strong>Diagnostica:</strong> {issues_text}</div>
<div class="box"><strong>ZenRows:</strong> {zrisk}<br>Run corrente: {zr_detail} · totale {health.get('zenrows_run_credits', 0)} crediti<br>Usati: {zenrows['consumed']}/{zenrows['monthly_limit']} · residui: {zenrows['remaining']}<br>Stima fine mese con frequenza giornaliera: {zenrows['projected_consumed']}/{zenrows['monthly_limit']} ({zenrows['projected_pct']}%) · residui stimati: {zenrows['projected_remaining']}<br>Frequenza consigliata: {recommendation}</div>
<div class="box"><strong>Fonti:</strong> {source_text}<br><strong>Esclusioni:</strong> {exclusion_text}</div>
<div class="box"><strong>Legenda:</strong> righe verdi = nuove offerte nell'ultimo run.</div>
<div style="overflow:auto"><table><thead><tr><th>Score</th><th>Distanza</th><th>Località</th><th>Azienda</th><th>Offerta</th><th>Contratto</th><th>Pubblicata</th><th>Fonte</th></tr></thead><tbody>{rows}</tbody></table></div>
</div></body></html>"""
    DOCS.mkdir(parents=True, exist_ok=True)
    LATEST_HTML.write_text(page, encoding="utf-8")
    print(f"REPORT_HTML path={LATEST_HTML} jobs={len(baseline)} new={len(new_jobs)} run_status={run_status} zenrows_risk={zenrows['risk']} projected={zenrows['projected_consumed']}/{zenrows['monthly_limit']}")


if __name__ == "__main__":
    main()
