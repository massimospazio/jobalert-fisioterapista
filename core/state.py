import hashlib
import json
from pathlib import Path


def stable_job_id(item: dict) -> str:
    basis = item.get("url") or item.get("dedup_key") or "|".join(
        str(item.get(key) or "") for key in ("source", "title", "company", "location")
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]


def build_state(items: list[dict]) -> dict:
    jobs = {}
    for item in items:
        job_id = stable_job_id(item)
        jobs[job_id] = {
            "source": item.get("source"),
            "url": item.get("url"),
            "title": item.get("title"),
            "company": item.get("company"),
            "location": item.get("location"),
            "province": item.get("province"),
            "published_at": item.get("published_at"),
        }
    return {"version": 1, "jobs": jobs}


def known_job_ids(state: dict) -> set[str]:
    return set((state or {}).get("jobs", {}).keys())


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


def merge_state(state: dict, items: list[dict]) -> dict:
    merged = {"version": 1, "jobs": dict((state or {}).get("jobs", {}))}
    merged["jobs"].update(build_state(items)["jobs"])
    return merged


def save_state(items: list[dict], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(build_state(items), ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def save_state_dict(state: dict, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def load_state(path: str | Path) -> dict:
    target = Path(path)
    if not target.exists():
        return {"version": 1, "jobs": {}}
    return json.loads(target.read_text(encoding="utf-8"))
