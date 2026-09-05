import json
import re
from pathlib import Path

RUN_LOG = Path("logs/run.log")
HEALTH_JSON = Path("logs/run_health.json")


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def main() -> None:
    text = RUN_LOG.read_text(encoding="utf-8", errors="replace") if RUN_LOG.exists() else ""
    warnings = []
    source_errors = []
    zenrows_by_source = {}

    for match in re.finditer(r"SOURCE_ERROR\s+([^:]+):\s*(.+)", text):
        source_errors.append({"source": match.group(1).strip(), "message": match.group(2).strip()})

    linkedin_429 = len(re.findall(r"LINKEDIN_DETAIL\s+status=429", text))
    linkedin_detail_errors = len(re.findall(r"LINKEDIN_DETAIL_ERROR", text))
    if linkedin_429:
        warnings.append(f"LinkedIn detail rate limit 429 ({linkedin_429})")
    if linkedin_detail_errors:
        warnings.append(f"LinkedIn detail errors ({linkedin_detail_errors})")

    bakeca = re.findall(r"ZENROWS_USAGE\s+request_cost=([^\s]+).*?credits=(\d+).*?request_id=([^\s]+)", text)
    if bakeca:
        cost, credits, request_id = bakeca[-1]
        zenrows_by_source["Bakeca"] = {"credits": int(credits), "request_cost": _float(cost), "request_id": request_id}

    indeed = re.findall(r"INDEED_ZENROWS_USAGE\s+request_cost=([^\s]+).*?request_id=([^\s]+)", text)
    if indeed:
        cost, request_id = indeed[-1]
        zenrows_by_source["Indeed"] = {"credits": 25, "request_cost": _float(cost), "request_id": request_id}

    total_credits = sum(item.get("credits", 0) for item in zenrows_by_source.values())
    total_request_cost = sum(item.get("request_cost") or 0 for item in zenrows_by_source.values())

    if source_errors:
        status = "DEGRADED"
    elif warnings:
        status = "DEGRADED"
    else:
        status = "OK"

    health = {
        "status": status,
        "warnings": warnings,
        "source_errors": source_errors,
        "linkedin_429": linkedin_429,
        "linkedin_detail_errors": linkedin_detail_errors,
        "zenrows_by_source": zenrows_by_source,
        "zenrows_run_credits": total_credits,
        "zenrows_run_request_cost": round(total_request_cost, 3),
    }
    HEALTH_JSON.parent.mkdir(parents=True, exist_ok=True)
    HEALTH_JSON.write_text(json.dumps(health, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"RUN_HEALTH status={status} warnings={len(warnings)} source_errors={len(source_errors)} "
        f"zenrows_credits={total_credits}"
    )


if __name__ == "__main__":
    main()
