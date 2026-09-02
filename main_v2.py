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
from sources.linkedin import collect as collect_linkedin
from sources.ofi_lazio import collect as collect_ofi_lazio


STATE_PATH = "state/baseline_state.json"
NEW_JOBS_PATH = "logs/new_jobs.json"


def _collect_source(source: dict, locations: dict):
    source_id = source.get("id")
    if source_id == "bakeca":
        if os.environ.get("ALLOW_PAID_SOURCES", "").lower() not in {"1", "true", "yes"}:
            print("SOURCE_SKIPPED_PAID bakeca: ALLOW_PAID_SOURCES non attivo")
            return []
        return collect_bakeca(source, locations)
    if source_id == "ofi_lazio":
        return collect_ofi_lazio(source, locations)
    if source_id == "linkedin":
        return collect_linkedin(source, locations)
    print(f"Fonte non ancora implementata: {source_id}")
    return []


def _new_job_payload(job, score_result, opportunity_id: str) -> dict:
    payload = asdict(job)
    payload["opportunity_id"] = opportunity_id
    payload["score"] = score_result.normalized_score if score_result else None
    payload["distance_km"] = score_result.distance_km if score_result else None
    payload["raw_text"] = payload.pop("text", "")
    return payload


def _write_new_jobs(items: list[dict], path: str = NEW_JOBS_PATH) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


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

    sources = [source for source in config["sources"].get("sources", []) if source.get("enabled")]
    all_jobs = []

    for source in sources:
        source_id = source.get("id")
        print(f"\nRACCOLTA FONTE: {source.get('name', source_id)}")
        try:
            jobs = _collect_source(source, locations)
            jobs = [enrich_job(job, locations) for job in jobs]
            print(f"Annunci raccolti: {len(jobs)}")
            all_jobs.extend(jobs)
        except Exception as exc:
            print(f"SOURCE_ERROR {source_id}: {exc}")

    unique_jobs, duplicate_count = deduplicate_jobs(all_jobs)
    print(
        f"\nDEDUP raw={len(all_jobs)} unique_opportunities={len(unique_jobs)} "
        f"duplicates_removed={duplicate_count}"
    )
    _print_coverage(unique_jobs)

    included = 0
    excluded = 0
    new_included = 0
    known_included = 0
    audit_file = None
    included_jobs = []
    included_opportunity_ids = []
    new_jobs_output = []

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
            if state_status == "NEW":
                new_included += 1
                new_jobs_output.append(_new_job_payload(job, score_result, opp_id))
            else:
                known_included += 1

        print(f"STATO: {state_status} | JOB_ID: {job_id} | OPPORTUNITY_ID: {opp_id}")
        print(format_console_audit(job, filter_result, score_result))
        audit_file = write_audit(job, filter_result, score_result, output_dir)

    new_jobs_output.sort(key=lambda item: (-(item.get("score") or 0), item.get("distance_km") or 9999))
    new_jobs_file = _write_new_jobs(new_jobs_output)

    if included_jobs:
        updated_state = merge_state(baseline_state, included_jobs, included_opportunity_ids)
        save_state_dict(updated_state, STATE_PATH)

    print("\n" + "=" * 72)
    print(
        f"RACCOLTI RAW: {len(all_jobs)} | OPPORTUNITA UNICHE: {len(unique_jobs)} | "
        f"DUPLICATI: {duplicate_count} | INCLUSI: {included} | ESCLUSI: {excluded} | "
        f"NUOVI INCLUSI: {new_included} | GIA NOTI: {known_included}"
    )
    if audit_file:
        print(f"Audit JSONL: {audit_file}")
    print(f"Nuovi annunci JSON: {new_jobs_file}")
    print(f"Stato persistente: {STATE_PATH}")
    print("=" * 72)


if __name__ == "__main__":
    main()
