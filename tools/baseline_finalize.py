import csv
import json
import re
from pathlib import Path

from core.state import save_state


BASELINE_DIR = Path("baseline")
INPUT_CSV = BASELINE_DIR / "baseline_jobs.csv"
RAW_JSON = BASELINE_DIR / "baseline_all.json"
ACTIVE_CSV = BASELINE_DIR / "baseline_active.csv"
ACTIVE_JSON = BASELINE_DIR / "baseline_active.json"
STATE_JSON = BASELINE_DIR / "baseline_state.json"


def clean(value):
    if value is None:
        return None
    value = re.sub(r"\s+", " ", str(value)).strip()
    return value or None


def as_bool(value) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "si", "sì"}


def detect_homecare_only(text: str) -> bool:
    lowered = (text or "").lower()
    if not any(token in lowered for token in ["adi", "domiciliar", "a domicilio", "assistenza domiciliare"]):
        return False

    mixed_markers = [
        "ambulator", "poliambulator", "centro di riabilitazione", "centro riabilitativo",
        "struttura", "residenzial", "semiresidenzial", "ospedal", "clinica", "studio",
        "rsa", "reparto", "palestra riabilitativa",
    ]
    if any(marker in lowered for marker in mixed_markers):
        return False

    exclusive_markers = [
        "adi", "assistenza domiciliare integrata", "servizio domiciliare", "attività domiciliare",
        "prestazioni domiciliari", "pazienti a domicilio", "fisioterapia domiciliare",
        "fisioterapista domiciliare", "per assistenza domiciliare", "per servizio adi",
    ]
    return any(marker in lowered for marker in exclusive_markers)


def read_rows() -> list[dict]:
    with INPUT_CSV.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    enriched = []
    for item in rows:
        raw = clean(item.get("raw_text")) or ""
        title = clean(item.get("title")) or ""
        company = clean(item.get("company")) or ""
        combined = " ".join([title, company, raw])
        item["homecare"] = as_bool(item.get("homecare"))
        item["homecare_only"] = detect_homecare_only(combined)
        item["cooperative"] = as_bool(item.get("cooperative"))
        for key in ["location", "province", "published_at", "application_deadline", "contract_type", "salary"]:
            item[key] = clean(item.get(key))
        enriched.append(item)
    return enriched


def write_csv(path: Path, rows: list[dict]) -> None:
    fieldnames = [
        "source", "title", "company", "location", "province", "homecare", "homecare_only",
        "published_at", "application_deadline", "contract_type", "cooperative", "salary",
        "url", "dedup_key", "raw_text",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, rows: list[dict]) -> None:
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    if not INPUT_CSV.exists():
        raise SystemExit(f"Baseline input non trovata: {INPUT_CSV}")

    all_rows = read_rows()
    active = [item for item in all_rows if not item["homecare_only"]]
    excluded = [item for item in all_rows if item["homecare_only"]]

    write_json(RAW_JSON, all_rows)
    write_csv(ACTIVE_CSV, active)
    write_json(ACTIVE_JSON, active)
    save_state(active, STATE_JSON)

    print(
        f"BASELINE_FINAL all={len(all_rows)} active={len(active)} "
        f"excluded_homecare_only={len(excluded)} state_jobs={len(active)}"
    )
    for item in excluded:
        print(
            "BASELINE_EXCLUDED homecare_only "
            f"source={item.get('source')} title={item.get('title')!r} "
            f"location={item.get('location')!r}"
        )


if __name__ == "__main__":
    main()
