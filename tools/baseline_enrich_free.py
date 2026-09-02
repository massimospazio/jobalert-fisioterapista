import csv
import io
import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from pypdf import PdfReader


BASELINE_CSV = Path("baseline/baseline_jobs.csv")
LAZIO_PLACES = [
    ("Albano Laziale", "RM"), ("Aprilia", "LT"), ("Anzio", "RM"), ("Ariccia", "RM"),
    ("Civitavecchia", "RM"), ("Colleferro", "RM"), ("Fiano Romano", "RM"), ("Fiumicino", "RM"),
    ("Fondi", "LT"), ("Formia", "LT"), ("Frascati", "RM"), ("Gaeta", "LT"), ("Genzano", "RM"),
    ("Grottaferrata", "RM"), ("Latina", "LT"), ("Marino", "RM"), ("Nettuno", "RM"),
    ("Pomezia", "RM"), ("Roma", "RM"), ("Velletri", "RM"), ("Viterbo", "VT"), ("Cori", "LT"),
]


def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def extract_contract(text: str):
    lowered = (text or "").lower()
    if "tempo indeterminato" in lowered:
        return "tempo_indeterminato"
    if "tempo determinato" in lowered:
        return "tempo_determinato"
    if any(token in lowered for token in ["partita iva", "p.iva", "p. iva", "libero professionista"]):
        return "partita_iva"
    if any(token in lowered for token in ["co.co.co", "cococo", "collaborazione coordinata"]):
        return "cococo"
    if "collaborazione" in lowered:
        return "collaborazione"
    if "prestazione occasionale" in lowered:
        return "occasionale"
    return None


def extract_salary(text: str):
    value = clean(text)
    patterns = [
        r"(?:€|EUR\s*)(\d{1,3}(?:[\.\s]\d{3})*(?:,\d{1,2})?)(?:\s*[-–]\s*(?:€|EUR\s*)?(\d{1,3}(?:[\.\s]\d{3})*(?:,\d{1,2})?))?",
        r"(\d{1,3}(?:[\.\s]\d{3})*(?:,\d{1,2})?)\s*(?:€|euro)(?:\s*[-–]\s*(\d{1,3}(?:[\.\s]\d{3})*(?:,\d{1,2})?)\s*(?:€|euro))?",
        r"\bRAL\s*(?:di|:)?\s*(\d{2,3}(?:[\.]\d{3})?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, value, re.IGNORECASE)
        if match:
            return clean(match.group(0))
    return None


def extract_deadline(text: str):
    patterns = [
        r"(?:scadenza|entro il|candidature entro|termine)[^\d]{0,40}(\d{1,2}/\d{1,2}/\d{2,4})",
        r"(?:scadenza|entro il|candidature entro|termine)[^\d]{0,40}(\d{1,2}/\d{1,2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text or "", re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def extract_location(text: str):
    lowered = (text or "").lower()
    for place, province in LAZIO_PLACES:
        if place.lower() in lowered:
            return place, province
    match = re.search(r"\b([A-Za-zÀ-ÿ'’\- ]{2,40})\s*\((RM|LT|FR|VT|RI)\)", text or "")
    if match:
        return clean(match.group(1)), match.group(2)
    return None, None


def is_homecare(text: str):
    lowered = (text or "").lower()
    return any(token in lowered for token in ["adi", "domiciliar", "a domicilio", "assistenza domiciliare"])


def is_cooperative(text: str):
    lowered = (text or "").lower()
    return any(token in lowered for token in ["cooperativa", "soc. coop", "società cooperativa", "coop. sociale"])


def enrich_from_text(item: dict, detail_text: str):
    detail_text = clean(detail_text)
    if not detail_text:
        return item
    current_raw = clean(item.get("raw_text", ""))
    item["raw_text"] = clean(f"{current_raw} {detail_text}")
    if not item.get("contract_type"):
        item["contract_type"] = extract_contract(detail_text)
    if not item.get("salary"):
        item["salary"] = extract_salary(detail_text)
    if not item.get("application_deadline"):
        item["application_deadline"] = extract_deadline(detail_text)
    if not item.get("location"):
        location, province = extract_location(detail_text)
        item["location"] = location
        item["province"] = province
    item["homecare"] = str(item.get("homecare", "")).lower() == "true" or is_homecare(detail_text)
    item["cooperative"] = str(item.get("cooperative", "")).lower() == "true" or is_cooperative(detail_text)
    return item


def enrich_linkedin(rows: list[dict]) -> None:
    targets = [item for item in rows if item.get("source") == "linkedin"]
    if not targets:
        return
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(locale="it-IT")
        for item in targets:
            try:
                response = page.goto(item["url"], wait_until="domcontentloaded", timeout=35000)
                page.wait_for_timeout(700)
                html = page.content()
                soup = BeautifulSoup(html, "lxml")
                main = soup.select_one("main") or soup
                detail = main.get_text(" ", strip=True)
                enrich_from_text(item, detail)
                print(
                    f"BASELINE_DETAIL linkedin status={response.status if response else 'n/a'} "
                    f"title={item.get('title')!r} contract={item.get('contract_type')!r} "
                    f"salary={item.get('salary')!r}"
                )
            except Exception as exc:
                print(f"BASELINE_DETAIL_ERROR linkedin url={item.get('url')} error={exc}")
        browser.close()


def enrich_ofi(rows: list[dict]) -> None:
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    for item in rows:
        if item.get("source") != "ofi_lazio":
            continue
        try:
            response = session.get(item["url"], timeout=30)
            response.raise_for_status()
            reader = PdfReader(io.BytesIO(response.content))
            detail = " ".join(page.extract_text() or "" for page in reader.pages)
            enrich_from_text(item, detail)
            print(
                f"BASELINE_DETAIL ofi bytes={len(response.content)} pages={len(reader.pages)} "
                f"company={item.get('company')!r} location={item.get('location')!r} "
                f"contract={item.get('contract_type')!r} salary={item.get('salary')!r}"
            )
        except Exception as exc:
            print(f"BASELINE_DETAIL_ERROR ofi url={item.get('url')} error={exc}")


def main() -> None:
    if not BASELINE_CSV.exists():
        raise SystemExit(f"Baseline non trovata: {BASELINE_CSV}")
    with BASELINE_CSV.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
        fieldnames = list(rows[0].keys()) if rows else []

    enrich_linkedin(rows)
    enrich_ofi(rows)

    with BASELINE_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"BASELINE_ENRICH_FREE rows={len(rows)}")


if __name__ == "__main__":
    main()
