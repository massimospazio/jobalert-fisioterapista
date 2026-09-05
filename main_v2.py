import csv
import json
import os
from dataclasses import asdict
from pathlib import Path

from core.config import load_all
from core.dedup import deduplicate_jobs, opportunity_key
from core.filters import evaluate_filters
from core.normalizer import enrich_job
from core.scoring import score_job
from core.state import load_state, merge_state, save_state_dict, stable_job_id
from reports.audit import format_console_audit, write_audit
from sources.bakeca import collect as collect_bakeca
from sources.indeed import collect as collect_indeed
from sources.linkedin import collect as collect_linkedin
from sources.ofi_lazio import collect as collect_ofi_lazio


STATE_PATH = "state/baseline_state.json"
NEW_JOBS_PATH = "logs/new_jobs.json"
BASELINE_JSON_PATH = "data/baseline_jobs.json"
BASELINE_CSV_PATH = "data/baseline_jobs.csv"

BASELINE_COLUMNS = [
    "source", "title", "company", "location", "province", "homecare", "homecare_only",
    "published_at", "application_deadline", "contract_type", "employment_type", "cooperative",
    "salary", "piva_required", "adi", "salary_present", "latitude", "longitude", "url", "score",
    "distance_km", "opportunity_id", "job_id", "raw_text",
]


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes"}


def _collect_source(source: dict, locations: dict, known_opportunities: set[str]):
    source_id = source.get("id")
    if source_id == "bakeca":
        if not _truthy_env("ALLOW_PAID_SOURCES"):
            print("SOURCE_SKIPPED_PAID bakeca: ALLOW_PAID_SOURCES non attivo")
            return []
        return collect_bakeca(source, locations)
    if source_id == "ofi_lazio":
        return collect_ofi_lazio(source, locations)
    if source_id == "linkedin":
        return collect_linkedin(source, locations, known_opportunities=known_opportunities)
    print(f"Fonte non ancora implementata: {source_id}")
    return []


def _job_payload(job, score_result, opportunity_id: str, job_id: str) -> dict:
    payload = asdict(job)
    payload["opportunity_id"] = opportunity_id
    payload["job_id"] = job_id
    payload["score"] = score_result.normalized_score if score_result else None
    payload["distance_km"] = score_result.distance_km if score_result else None
    payload["raw_text"] = payload.pop("text", "")
    return payload


def _write_json(items: list[dict], path: str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def _write_new_jobs(items: list[dict], path: str = NEW_JOBS_PATH) -> Path:
    return _write_json(items, path)


def _write_baseline(items: list[dict]) -> tuple[Path, Path]:
    json_path = _write_json(items, BASELINE_JSON_PATH)
    csv_path = Path(BASELINE_CSV_PATH)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=BASELINE_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for item in items:
            writer.writerow({column: item.get(column) for column in BASELINE_COLUMNS})
    return json_path, csv_path


def _coverage(items) -> dict[str, tuple[int, int]]:
    total = len(items)
    checks = {
        "location": lambda job: bool(job.location),
        "province": lambda job: bool(job.province),
        "published_at": lambda job: bool(job.published_at),
        "contract": lambda job: bool(job.contract_type and job.contract_type != "non_specificato"),
        "salary": lambda job: bool(job.salary),
        "deadline": lambda job: bool(job.application_deadline),
    }
    return {name: (sum(1 for job in items if check(job)), total) for name, check in checks.items()}


def _print_coverage(items) -> None:
    print("FIELD_COVERAGE")
    for name, (present, total) in _coverage(items).items():
        pct = (present / total * 100) if total else 0.0
        print(f"  {name:<12} {present}/{total} ({pct:.0f}%)")


def main() -> None:
    config = load_all()
    settings = config["settings"]
    filters_config = config["filters"]
    scoring_config = config["scoring"]
    locations = config["locations"].get("locations", {})
    output_dir = settings.get("audit", {}).get("output_dir", "logs")
    baseline_state = load_state(STATE_PATH)
    known_ids = set(baseline_state.get("jobs", {}).keys())
    known_opportunities = set(baseline_state.get("opportunities", {}).keys())

    source_configs = config["sources"].get("sources", [])
    sources = [source for source in source_configs if source.get("enabled") and source.get("id") != "indeed"]
    all_jobs = []

    for source in sources:
        source_id = source.get("id")
        print(f"\nRACCOLTA FONTE: {source.get('name', source_id)}")
        try:
            jobs = _collect_source(source, locations, known_opportunities)
            jobs = [enrich_job(job, locations) for job in jobs]
            print(f"Annunci raccolti: {len(jobs)}")
            all_jobs.extend(jobs)
        except Exception as exc:
            print(f"SOURCE_ERROR {source_id}: {exc}")

    primary_unique, primary_duplicates = deduplicate_jobs(all_jobs)
    print(f"\nPRIMARY_DEDUP raw={len(all_jobs)} unique_opportunities={len(primary_unique)} duplicates_removed={primary_duplicates}")

    indeed_config = next((source for source in source_configs if source.get("id") == "indeed"), None)
    if _truthy_env("ENABLE_INDEED_GAPFILL"):
        if not _truthy_env("ALLOW_PAID_SOURCES"):
            print("SOURCE_SKIPPED_PAID indeed: ALLOW_PAID_SOURCES non attivo")
        elif not indeed_config:
            print("SOURCE_ERROR indeed: configurazione non trovata")
        else:
            print("\nRACCOLTA FONTE: Indeed (gap filler)")
            try:
                indeed_jobs, usage = collect_indeed(indeed_config, locations)
                indeed_jobs = [enrich_job(job, locations) for job in indeed_jobs]
                all_jobs.extend(indeed_jobs)
                print(f"INDEED_GAPFILL primary_unique={len(primary_unique)} collected={len(indeed_jobs)} request_cost={usage.get('request_cost', 'n/a')}")
            except Exception as exc:
                print(f"SOURCE_ERROR indeed: {exc}")
    else:
        print("INDEED_GAPFILL_SKIPPED ENABLE_INDEED_GAPFILL non attivo")

    unique_jobs, duplicate_count = deduplicate_jobs(all_jobs)
    incremental_after_indeed = max(0, len(unique_jobs) - len(primary_unique))
    print(f"\nDEDUP raw={len(all_jobs)} unique_opportunities={len(unique_jobs)} duplicates_removed={duplicate_count} indeed_incremental={incremental_after_indeed}")
    _print_coverage(unique_jobs)

    included = excluded = new_included = known_included = 0
    audit_file = None
    included_jobs = []
    included_opportunity_ids = []
    new_jobs_output = []
    baseline_output = []

    for job in unique_jobs:
        filter_result = evaluate_filters(job, filters_config)
        score_result = score_job(job, scoring_config, settings) if filter_result.included else None
        included += int(filter_result.included)
        excluded += int(not filter_result.included)
        job_dict = asdict(job)
        job_id = stable_job_id(job_dict)
        opp_id = opportunity_key(job)
        state_status = "KNOWN" if job_id in known_ids or opp_id in known_opportunities else "NEW"
        if filter_result.included:
            included_jobs.append(job_dict)
            included_opportunity_ids.append(opp_id)
            payload = _job_payload(job, score_result, opp_id, job_id)
            baseline_output.append(payload)
            if state_status == "NEW":
                new_included += 1
                new_jobs_output.append(payload)
            else:
                known_included += 1
        print(f"STATO: {state_status} | JOB_ID: {job_id} | OPPORTUNITY_ID: {opp_id}")
        print(format_console_audit(job, filter_result, score_result))
        audit_file = write_audit(job, filter_result, score_result, output_dir)

    sort_key = lambda item: (-(item.get("score") or 0), item.get("distance_km") or 9999)
    new_jobs_output.sort(key=sort_key)
    baseline_output.sort(key=sort_key)
    new_jobs_file = _write_new_jobs(new_jobs_output)

    baseline_files = None
    if _truthy_env("PERSIST_BASELINE_DATA"):
        baseline_files = _write_baseline(baseline_output)
        print(f"BASELINE_EXPORT count={len(baseline_output)} json={baseline_files[0]} csv={baseline_files[1]}")
    else:
        print("BASELINE_EXPORT_SKIPPED PERSIST_BASELINE_DATA non attivo")

    if included_jobs:
        updated_state = merge_state(baseline_state, included_jobs, included_opportunity_ids)
        save_state_dict(updated_state, STATE_PATH)

    print("\n" + "=" * 72)
    print(f"RACCOLTI RAW: {len(all_jobs)} | OPPORTUNITA UNICHE: {len(unique_jobs)} | DUPLICATI: {duplicate_count} | INCLUSI: {included} | ESCLUSI: {excluded} | NUOVI INCLUSI: {new_included} | GIA NOTI: {known_included}")
    if audit_file:
        print(f"Audit JSONL: {audit_file}")
    print(f"Nuovi annunci JSON: {new_jobs_file}")
    if baseline_files:
        print(f"Baseline completa JSON: {baseline_files[0]}")
        print(f"Baseline completa CSV: {baseline_files[1]}")
    print(f"Stato persistente: {STATE_PATH}")
    print("=" * 72)


if __name__ == "__main__":
    main()
