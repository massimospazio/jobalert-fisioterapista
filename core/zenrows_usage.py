import json
from calendar import monthrange
from datetime import date, datetime, timezone
from pathlib import Path

STATE_PATH = Path("state/zenrows_usage.json")
MONTHLY_LIMIT = 5000
DEFAULT_CREDITS_PER_REQUEST = 25


def _default_state() -> dict:
    return {
        "month": "2026-09",
        "monthly_limit": MONTHLY_LIMIT,
        "consumed": 1281,
        "snapshot_consumed": 1281,
        "snapshot_date": "2026-09-05",
        "requests": [],
    }


def load_state() -> dict:
    if not STATE_PATH.exists():
        return _default_state()
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return _default_state()

    current_month = date.today().strftime("%Y-%m")
    if data.get("month") != current_month:
        return {
            "month": current_month,
            "monthly_limit": int(data.get("monthly_limit") or MONTHLY_LIMIT),
            "consumed": 0,
            "snapshot_consumed": 0,
            "snapshot_date": date.today().isoformat(),
            "requests": [],
        }
    return data


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def record_success(source: str, credits: int = DEFAULT_CREDITS_PER_REQUEST, request_cost=None, request_id=None) -> dict:
    state = load_state()
    state["consumed"] = int(state.get("consumed") or 0) + int(credits)
    state.setdefault("requests", []).append({
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "credits": int(credits),
        "request_cost": request_cost,
        "request_id": request_id,
    })
    # Keep the state compact while preserving enough history for diagnostics.
    state["requests"] = state["requests"][-200:]
    save_state(state)
    return state


def forecast(state: dict | None = None, credits_per_run: int = 50) -> dict:
    state = state or load_state()
    today = date.today()
    limit = int(state.get("monthly_limit") or MONTHLY_LIMIT)
    consumed = int(state.get("consumed") or 0)
    remaining = max(0, limit - consumed)
    days_in_month = monthrange(today.year, today.month)[1]
    days_remaining = max(0, days_in_month - today.day)
    projected_additional = days_remaining * credits_per_run
    projected_consumed = consumed + projected_additional
    projected_remaining = limit - projected_consumed
    projected_pct = (projected_consumed / limit * 100) if limit else 100.0

    if projected_consumed > limit:
        risk = "RISK"
    elif projected_pct >= 90:
        risk = "WARNING"
    else:
        risk = "SAFE"

    recommended_every_days = 1
    if days_remaining > 0 and credits_per_run > 0 and remaining > 0:
        max_runs = remaining // credits_per_run
        if max_runs < days_remaining:
            recommended_every_days = max(2, (days_remaining + max_runs - 1) // max_runs) if max_runs else days_remaining + 1

    return {
        "month": state.get("month"),
        "monthly_limit": limit,
        "consumed": consumed,
        "remaining": remaining,
        "days_remaining": days_remaining,
        "credits_per_run": credits_per_run,
        "projected_additional": projected_additional,
        "projected_consumed": projected_consumed,
        "projected_remaining": projected_remaining,
        "projected_pct": round(projected_pct, 1),
        "risk": risk,
        "recommended_every_days": recommended_every_days,
    }
