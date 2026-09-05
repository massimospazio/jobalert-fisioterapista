import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml

from core.dedup import deduplicate_jobs, opportunity_key
from core.filters import evaluate_filters
from core.normalizer import enrich_job
from sources import indeed


def load_yaml(path: str):
    with open(ROOT / path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_locations():
    data = load_yaml("config/locations.yaml")
    raw = data.get("locations", data)
    return {str(k).lower(): v for k, v in raw.items()}


def main():
    sources_cfg = load_yaml("config/sources.yaml")["sources"]
    source_cfg = next(item for item in sources_cfg if item["id"] == "indeed").copy()
    source_cfg["search_url"] = "https://it.indeed.com/jobs?q=fisioterapista&l=Roma%2C+Lazio&radius=50&sort=date"
    source_cfg["max_results"] = 30

    filters_cfg = load_yaml("config/filters.yaml")
    locations = load_locations()
    baseline = json.loads((ROOT / "data/baseline_jobs.json").read_text(encoding="utf-8"))
    baseline_keys = {item.get("opportunity_id") for item in baseline if item.get("opportunity_id")}

    jobs, usage = indeed.collect(source_cfg, locations)
    enriched = [enrich_job(job, locations) for job in jobs]
    unique_jobs, duplicates_internal = deduplicate_jobs(enriched)

    rows = []
    counts = {
        "raw": len(jobs),
        "unique": len(unique_jobs),
        "duplicates_internal": duplicates_internal,
        "included": 0,
        "excluded": 0,
        "duplicates_baseline": 0,
        "new_incremental": 0,
        "homecare_only_excluded": 0,
        "province_excluded": 0,
    }

    for job in unique_jobs:
        result = evaluate_filters(job, filters_cfg)
        key = opportunity_key(job)
        duplicate = key in baseline_keys
        if result.included:
            counts["included"] += 1
            if duplicate:
                counts["duplicates_baseline"] += 1
            else:
                counts["new_incremental"] += 1
        else:
            counts["excluded"] += 1
            if "homecare_only" in result.exclusion_rules:
                counts["homecare_only_excluded"] += 1
            if "province_not_allowed" in result.exclusion_rules:
                counts["province_excluded"] += 1

        rows.append({
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "province": job.province,
            "contract_type": job.contract_type,
            "salary": job.salary,
            "homecare_only": job.homecare_only,
            "included": result.included,
            "duplicate_baseline": duplicate,
            "opportunity_id": key,
            "url": job.url,
            "reason": result.reason,
        })

    payload = {"usage": usage, "counts": counts, "jobs": rows}
    out = ROOT / "logs" / "indeed_benchmark.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        "INDEED_BENCHMARK "
        f"raw={counts['raw']} unique={counts['unique']} duplicates_internal={counts['duplicates_internal']} "
        f"included={counts['included']} excluded={counts['excluded']} "
        f"duplicates_baseline={counts['duplicates_baseline']} new_incremental={counts['new_incremental']} "
        f"homecare_only_excluded={counts['homecare_only_excluded']} province_excluded={counts['province_excluded']}"
    )
    for row in rows:
        if row["included"] and not row["duplicate_baseline"]:
            print(
                "INDEED_NEW "
                f"company={row['company']!r} location={row['location']!r} "
                f"contract={row['contract_type']} salary={row['salary']!r} title={row['title']!r} url={row['url']}"
            )


if __name__ == "__main__":
    main()
