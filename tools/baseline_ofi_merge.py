import csv
import re
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


OFI_URL = "https://www.ofilazio.it/offerte-di-lavoro/"
BASELINE_CSV = Path("baseline/baseline_jobs.csv")


def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def parse_date_from_url(url: str):
    match = re.search(r"/uploads/(\d{4})/(\d{2})/", url)
    if not match:
        return None
    year, month = match.groups()
    return f"{year}-{month}-01"


def norm_key(title: str, company: str, location: str) -> str:
    raw = "|".join([title, company, location]).lower()
    raw = re.sub(r"[^a-z0-9àèéìòù]+", " ", raw)
    return clean(raw)


def collect_ofi_rows() -> list[dict]:
    response = requests.get(OFI_URL, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "lxml")
    rows = []
    seen = set()

    for block in soup.select("div.fusion-text"):
        anchor = block.find("a", href=True)
        if not anchor:
            continue
        href = urljoin(OFI_URL, anchor["href"])
        if "/wp-content/uploads/2026/" not in href:
            continue
        if href in seen:
            continue
        text = clean(block.get_text(" ", strip=True))
        company = re.sub(r"\s+Leggi\s+tutto.*$", "", text, flags=re.IGNORECASE).strip()
        if not company:
            continue
        rows.append({
            "source": "ofi_lazio",
            "title": "Fisioterapista",
            "company": company,
            "location": None,
            "province": None,
            "homecare": False,
            "published_at": parse_date_from_url(href),
            "application_deadline": None,
            "contract_type": None,
            "cooperative": "cooperativa" in company.lower() or "soc. coop" in company.lower(),
            "salary": None,
            "url": href,
            "dedup_key": norm_key("Fisioterapista", company, ""),
            "raw_text": text,
        })
        seen.add(href)

    return rows


def main() -> None:
    if not BASELINE_CSV.exists():
        raise SystemExit(f"Baseline non trovata: {BASELINE_CSV}")

    with BASELINE_CSV.open("r", encoding="utf-8", newline="") as handle:
        existing = list(csv.DictReader(handle))
        fieldnames = list(existing[0].keys()) if existing else [
            "source", "title", "company", "location", "province", "homecare",
            "published_at", "application_deadline", "contract_type", "cooperative",
            "salary", "url", "dedup_key", "raw_text",
        ]

    existing = [item for item in existing if item.get("source") != "ofi_lazio"]
    ofi_rows = collect_ofi_rows()
    merged = existing + ofi_rows

    with BASELINE_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(merged)

    print(f"BASELINE_OFI restored={len(ofi_rows)} total_after_merge={len(merged)}")
    for item in ofi_rows:
        print(f"BASELINE_OFI_JOB company={item['company']!r} url={item['url']}")


if __name__ == "__main__":
    main()
