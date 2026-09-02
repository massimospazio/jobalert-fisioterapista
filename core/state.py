import hashlib
import json
from pathlib import Path


def stable_job_id(item: dict) -> str:
    basis = item.get("url") or item.get("dedup_key") or "|".join(
        str(item.get(key) or "") for key in ("source", "title", "company", "location")
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]


def _job_record(item: dict) -> dict:
    return {
        "source": item.get("source"),
        "url": item.get("url"),
        "title": item.get("title"),
        "company": item.get("company"),
        "location": item.get("location"),
        "province": item.get("province"),
        "published_at": item.get("published_at"),
    }


def build_state(items: list[dict]) -> dict:
    jobs = {}
    for item in items:
        jobs[stable_job_id(item)] = _job_record(item)
    return {"version": 2, "jobs": jobs, "opportunities": {}}


def known_job_ids(state: dict) -> set[str]:
    return set((state or {}).get("jobs", {}).keys())


def known_opportunity_ids(state: dict) -> set[str]:
    return set((state or {}).get("opportunities", {}).keys())


def split_new_jobs(items: list[dict], state: dict) -> tuple[list[dict], list[dict]]:
    known = known_job_ids(state)
    new_items = []
    existing_items = []
    for item in items:
        if stable_job_id(item) in known:
            existing_items.append(item)
        else:
            new_items.append(item)
    return new_items, existing_items


def merge_state(state: dict, items: list[dict], opportunity_ids: list[str] | None = None) -> dict:
    merged = {
        "version": 2,
        "jobs": dict((state or {}).get("jobs", {})),
        "opportunities": dict((state or {}).get("opportunities", {})),
    }
    for item in items:
        merged["jobs"][stable_job_id(item)] = _job_record(item)
    for opportunity_id in opportunity_ids or []:
        merged["opportunities"].setdefault(opportunity_id, {})
    return merged


def save_state(items: list[dict], path: str | Path) -> Path:
    return save_state_dict(build_state(items), path)


def save_state_dict(state: dict, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(target)
    return target


def load_state(path: str | Path) -> dict:
    target = Path(path)
    if not target.exists():
        return {"version": 2, "jobs": {}, "opportunities": {}}
    state = json.loads(target.read_text(encoding="utf-8"))
    state.setdefault("version", 2)
    state.setdefault("jobs", {})
    state.setdefault("opportunities", {})
    return state
