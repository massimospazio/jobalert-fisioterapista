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


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def main() -> None:
    text = RUN_LOG.read_text(encoding="utf-8", errors="replace") if RUN_LOG.exists() else ""
    warnings = []
    source_errors = []
    zenrows_by_source = {}

    for match in re.finditer(r"SOURCE_ERROR\s+([^:]+):\s*(.+)", text):
        source_errors.append({"source": match.group(1).strip(), "message": match.group(2).strip()})

    linkedin_429 = len(re.findall(r"LINKEDIN_DETAIL\s+status=429", text))
    linkedin_detail_errors = len(re.findall(r"LINKEDIN_DETAIL_ERROR", text))
    linkedin = {
        "candidates": 0,
        "new_opportunities": 0,
        "known_skipped": 0,
        "attempted": 0,
        "success": 0,
        "rate_limited_429": linkedin_429,
        "errors": linkedin_detail_errors,
        "unattempted_after_block": 0,
        "detail_impacted": 0,
        "impact_pct": 0.0,
        "search_available": bool(re.search(r"LINKEDIN_COLLECT\s+status=200", text)),
    }

    summary_match = re.search(
        r"LINKEDIN_DETAIL_SUMMARY\s+"
        r"candidates=(\d+)\s+new_opportunities=(\d+)\s+known_skipped=(\d+)\s+"
        r"attempted=(\d+)\s+success=(\d+)\s+rate_limited_429=(\d+)\s+errors=(\d+)\s+"
        r"unattempted_after_block=(\d+)\s+detail_impacted=(\d+)\s+impact_pct=([\d.]+)",
        text,
    )
    if summary_match:
        keys = [
            "candidates", "new_opportunities", "known_skipped", "attempted", "success",
            "rate_limited_429", "errors", "unattempted_after_block", "detail_impacted",
        ]
        for key, value in zip(keys, summary_match.groups()[:9]):
            linkedin[key] = _int(value)
        linkedin["impact_pct"] = _float(summary_match.group(10)) or 0.0

    if linkedin["detail_impacted"]:
        warnings.append(
            "LinkedIn: dati base disponibili, ma dettaglio non arricchito per "
            f"{linkedin['detail_impacted']}/{linkedin['new_opportunities']} nuove opportunità "
            f"({linkedin['impact_pct']:.1f}%)"
        )
    elif linkedin_429:
        warnings.append(f"LinkedIn detail rate limit 429 ({linkedin_429})")
    if linkedin_detail_errors and not summary_match:
        warnings.append(f"LinkedIn detail errors ({linkedin_detail_errors})")

    bakeca = re.findall(r"ZENROWS_USAGE\s+request_cost=([^\s]+).*?credits=(\d+).*?request_id=([^\s]+)", text)
    if bakeca:
        cost, credits, request_id = bakeca[-1]
        zenrows_by_source["Bakeca"] = {"credits": int(credits), "request_cost": _float(cost), "request_id": request_id}

    indeed = re.findall(r"INDEED_ZENROWS_USAGE\s+request_cost=([^\s]+).*?credits=(\d+).*?request_id=([^\s]+)", text)
    if indeed:
        cost, credits, request_id = indeed[-1]
        zenrows_by_source["Indeed"] = {"credits": int(credits), "request_cost": _float(cost), "request_id": request_id}
    else:
        indeed = re.findall(r"INDEED_ZENROWS_USAGE\s+request_cost=([^\s]+).*?request_id=([^\s]+)", text)
        if indeed:
            cost, request_id = indeed[-1]
            zenrows_by_source["Indeed"] = {"credits": 25, "request_cost": _float(cost), "request_id": request_id}

    total_credits = sum(item.get("credits", 0) for item in zenrows_by_source.values())
    total_request_cost = sum(item.get("request_cost") or 0 for item in zenrows_by_source.values())
    status = "DEGRADED" if source_errors or warnings else "OK"

    health = {
        "status": status,
        "warnings": warnings,
        "source_errors": source_errors,
        "linkedin": linkedin,
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
        f"linkedin_detail_impact={linkedin['detail_impacted']}/{linkedin['new_opportunities']} "
        f"({linkedin['impact_pct']:.1f}%) zenrows_credits={total_credits}"
    )


if __name__ == "__main__":
    main()
