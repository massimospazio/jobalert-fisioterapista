import os
from dataclasses import asdict

from core.config import load_all
from core.filters import evaluate_filters
from core.scoring import score_job
from core.state import load_state, stable_job_id
from reports.audit import format_console_audit, write_audit
from sources.bakeca import collect as collect_bakeca
from sources.linkedin import collect as collect_linkedin
from sources.ofi_lazio import collect as collect_ofi_lazio


STATE_PATH = "state/baseline_state.json"


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


def main() -> None:
    config = load_all()
    settings = config["settings"]
    filters_config = config["filters"]
    scoring_config = config["scoring"]
    locations = config["locations"].get("locations", {})
    output_dir = settings.get("audit", {}).get("output_dir", "logs")
    baseline_state = load_state(STATE_PATH)
    known_ids = set(baseline_state.get("jobs", {}).keys())

    sources = [source for source in config["sources"].get("sources", []) if source.get("enabled")]
    all_jobs = []

    for source in sources:
        source_id = source.get("id")
        print(f"\nRACCOLTA FONTE: {source.get('name', source_id)}")
        try:
            jobs = _collect_source(source, locations)
            print(f"Annunci raccolti: {len(jobs)}")
            all_jobs.extend(jobs)
        except Exception as exc:
            print(f"SOURCE_ERROR {source_id}: {exc}")

    included = 0
    excluded = 0
    new_included = 0
    known_included = 0
    audit_file = None

    for job in all_jobs:
        filter_result = evaluate_filters(job, filters_config)
        score_result = score_job(job, scoring_config, settings) if filter_result.included else None
        included += int(filter_result.included)
        excluded += int(not filter_result.included)

        job_id = stable_job_id(asdict(job))
        state_status = "KNOWN" if job_id in known_ids else "NEW"
        if filter_result.included:
            if state_status == "NEW":
                new_included += 1
            else:
                known_included += 1

        print(f"STATO: {state_status} | JOB_ID: {job_id}")
        print(format_console_audit(job, filter_result, score_result))
        audit_file = write_audit(job, filter_result, score_result, output_dir)

    print("\n" + "=" * 72)
    print(
        f"RACCOLTI: {len(all_jobs)} | INCLUSI: {included} | ESCLUSI: {excluded} | "
        f"NUOVI INCLUSI: {new_included} | GIA NOTI: {known_included}"
    )
    if audit_file:
        print(f"Audit JSONL: {audit_file}")
    print("=" * 72)


if __name__ == "__main__":
    main()
