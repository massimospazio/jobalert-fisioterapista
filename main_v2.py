from core.config import load_all
from core.filters import evaluate_filters
from core.scoring import score_job
from reports.audit import format_console_audit, write_audit
from sources.bakeca import collect as collect_bakeca


def main() -> None:
    config = load_all()
    settings = config["settings"]
    filters_config = config["filters"]
    scoring_config = config["scoring"]
    locations = config["locations"].get("locations", {})
    output_dir = settings.get("audit", {}).get("output_dir", "logs")

    sources = [source for source in config["sources"].get("sources", []) if source.get("enabled")]
    all_jobs = []

    for source in sources:
        source_id = source.get("id")
        print(f"\nRACCOLTA FONTE: {source.get('name', source_id)}")
        try:
            if source_id == "bakeca":
                jobs = collect_bakeca(source, locations)
            else:
                print(f"Fonte non ancora implementata: {source_id}")
                continue
            print(f"Annunci raccolti: {len(jobs)}")
            all_jobs.extend(jobs)
        except Exception as exc:
            print(f"SOURCE_ERROR {source_id}: {exc}")

    included = 0
    excluded = 0
    for job in all_jobs:
        filter_result = evaluate_filters(job, filters_config)
        score_result = score_job(job, scoring_config, settings) if filter_result.included else None
        included += int(filter_result.included)
        excluded += int(not filter_result.included)

        print(format_console_audit(job, filter_result, score_result))
        audit_file = write_audit(job, filter_result, score_result, output_dir)

    print("\n" + "=" * 72)
    print(f"RACCOLTI: {len(all_jobs)} | INCLUSI: {included} | ESCLUSI: {excluded}")
    if all_jobs:
        print(f"Audit JSONL: {audit_file}")
    print("=" * 72)


if __name__ == "__main__":
    main()
