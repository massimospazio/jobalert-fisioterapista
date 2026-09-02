import csv
import json
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright


OUT_DIR = Path("baseline")
OUT_DIR.mkdir(parents=True, exist_ok=True)

OFI_URL = "https://www.ofilazio.it/offerte-di-lavoro/"
LINKEDIN_URL = "https://it.linkedin.com/jobs/fisioterapista-roma-rome-offerte-di-lavoro-roma-lz?position=1&pageNum=0"
BAKECA_URL = "https://www.bakeca.it/annunci/medicina-salute-assistenza/luogo/lazio/?keyword=fisioterapista"
ZENROWS_ENDPOINT = "https://api.zenrows.com/v1/"
LAZIO_PROVINCES = {"RM", "LT", "FR", "VT", "RI"}
PROVINCE_NAMES = {
    "RM": "Roma",
    "LT": "Latina",
    "FR": "Frosinone",
    "VT": "Viterbo",
    "RI": "Rieti",
}


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def canonical_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def norm_key(title: str, company: str, location: str) -> str:
    raw = "|".join([title, company, location]).lower()
    raw = re.sub(r"[^a-z0-9àèéìòù]+", " ", raw)
    return clean(raw)


def parse_date(text: str):
    for pattern in [r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", r"\b(\d{1,2})/(\d{1,2})\b"]:
        m = re.search(pattern, text or "")
        if not m:
            continue
        try:
            if len(m.groups()) == 3:
                day, month, year = map(int, m.groups())
            else:
                day, month = map(int, m.groups())
                year = datetime.now().year
            return datetime(year, month, day).date().isoformat()
        except ValueError:
            pass
    return None


def extract_deadline(text: str):
    patterns = [
        r"(?:scadenza|entro il|candidature entro|termine)[^\d]{0,30}(\d{1,2}/\d{1,2}/\d{2,4})",
        r"(?:scadenza|entro il|candidature entro|termine)[^\d]{0,30}(\d{1,2}/\d{1,2})",
    ]
    for pattern in patterns:
        m = re.search(pattern, text or "", re.IGNORECASE)
        if m:
            return parse_date(m.group(1))
    return None


def extract_contract(text: str):
    lowered = (text or "").lower()
    if "tempo indeterminato" in lowered:
        return "tempo_indeterminato"
    if "tempo determinato" in lowered:
        return "tempo_determinato"
    if "partita iva" in lowered or "p.iva" in lowered or "libero professionista" in lowered:
        return "partita_iva"
    if "collaborazione" in lowered or "collaboratore" in lowered:
        return "collaborazione"
    if "prestazione occasionale" in lowered:
        return "occasionale"
    return None


def extract_salary(text: str):
    value = clean(text or "")
    patterns = [
        r"(?:€|eur\s*)(\d{3,5}(?:[\.,]\d{1,2})?)(?:\s*[-–]\s*(?:€|eur\s*)?(\d{3,5}(?:[\.,]\d{1,2})?))?",
        r"(\d{3,5}(?:[\.,]\d{1,2})?)\s*(?:€|euro)(?:\s*[-–]\s*(\d{3,5}(?:[\.,]\d{1,2})?)\s*(?:€|euro))?",
    ]
    for pattern in patterns:
        m = re.search(pattern, value, re.IGNORECASE)
        if m:
            return clean(m.group(0))
    if re.search(r"\b(?:ral|retribuzione|stipendio|compenso)\b", value, re.IGNORECASE):
        return "presente_non_strutturata"
    return None


def extract_location_and_province(text: str, fallback: str = ""):
    blob = clean(text or "")
    # Prefer explicit 'Comune (PR)' patterns, which are common on Bakeca.
    matches = re.findall(r"\b([A-ZÀ-ÖØ-Ý][A-Za-zÀ-ÿ'’\- ]{1,40}?)\s*\(([A-Z]{2})\)", blob)
    for locality, province in matches:
        province = province.upper()
        if province in LAZIO_PROVINCES:
            return clean(locality), province

    # Common Lazio places when no province code is shown.
    known = [
        ("Albano Laziale", "RM"), ("Aprilia", "LT"), ("Anzio", "RM"), ("Ariccia", "RM"),
        ("Civitavecchia", "RM"), ("Colleferro", "RM"), ("Fiano Romano", "RM"), ("Fiumicino", "RM"),
        ("Fondi", "LT"), ("Formia", "LT"), ("Frascati", "RM"), ("Gaeta", "LT"), ("Genzano", "RM"),
        ("Grottaferrata", "RM"), ("Latina", "LT"), ("Marino", "RM"), ("Nettuno", "RM"),
        ("Pomezia", "RM"), ("Roma", "RM"), ("Velletri", "RM"), ("Viterbo", "VT"), ("Cori", "LT"),
    ]
    lowered = blob.lower()
    for locality, province in known:
        if locality.lower() in lowered:
            return locality, province
    return (fallback or None), None


def is_homecare(text: str):
    lowered = (text or "").lower()
    positive = ["domiciliar", "a domicilio", "adi", "assistenza domiciliare"]
    if any(token in lowered for token in positive):
        return True
    return False


def is_cooperative(text: str):
    lowered = (text or "").lower()
    return any(token in lowered for token in ["cooperativa", "soc. coop", "società cooperativa", "coop. sociale", "cooperativa sociale"])


def row(source: str, title: str, company: str, location: str, province: str, url: str, raw_text: str,
        published_at=None, application_deadline=None, contract_type=None, homecare=False,
        cooperative=False, salary=None) -> dict:
    return {
        "source": source,
        "title": clean(title),
        "company": clean(company),
        "location": clean(location) if location else None,
        "province": province,
        "homecare": homecare,
        "published_at": published_at,
        "application_deadline": application_deadline,
        "contract_type": contract_type,
        "cooperative": cooperative,
        "salary": salary,
        "url": canonical_url(url),
        "raw_text": clean(raw_text),
        "dedup_key": norm_key(title, company, location or ""),
    }


def collect_ofi() -> list[dict]:
    response = requests.get(OFI_URL, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "lxml")
    results = []

    heading = soup.find(lambda tag: tag.name in {"h1", "h2", "h3", "h4"} and clean(tag.get_text(" ", strip=True)).lower() == "offerte di lavoro")
    root = heading.parent if heading else soup

    for title_node in root.find_all(["h2", "h3", "h4"]):
        company = clean(title_node.get_text(" ", strip=True))
        if not company or company.lower() == "offerte di lavoro":
            continue
        anchor = title_node.find_next("a", href=True)
        if not anchor:
            continue
        href = urljoin(OFI_URL, anchor["href"])
        if "/wp-content/uploads/" not in href:
            continue
        parent = title_node.find_parent(["article", "div"]) or title_node
        raw = clean(parent.get_text(" ", strip=True))
        location, province = extract_location_and_province(raw)
        results.append(row(
            "ofi_lazio", "Fisioterapista", company, location, province, href, raw,
            published_at=parse_date(raw),
            application_deadline=extract_deadline(raw),
            contract_type=extract_contract(raw),
            homecare=is_homecare(raw),
            cooperative=is_cooperative(f"{company} {raw}"),
            salary=extract_salary(raw),
        ))

    print(f"BASELINE_SOURCE ofi_lazio collected={len(results)}")
    return results


def collect_linkedin() -> list[dict]:
    results = []
    seen = set()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(locale="it-IT")
        page.goto(LINKEDIN_URL, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2500)
        html = page.content()
        browser.close()

    soup = BeautifulSoup(html, "lxml")
    for card in soup.select(".base-search-card"):
        title_node = card.select_one("h3.base-search-card__title")
        company_node = card.select_one("h4.base-search-card__subtitle")
        location_node = card.select_one(".job-search-card__location")
        time_node = card.select_one("time")
        anchor = card.select_one('a[href*="/jobs/view/"]')
        if not title_node or not anchor:
            continue
        title = clean(title_node.get_text(" ", strip=True))
        if "fisioterap" not in title.lower():
            continue
        href = canonical_url(urljoin(LINKEDIN_URL, anchor["href"]))
        if href in seen:
            continue
        company = clean(company_node.get_text(" ", strip=True)) if company_node else ""
        raw_location = clean(location_node.get_text(" ", strip=True)) if location_node else ""
        raw = clean(card.get_text(" ", strip=True))
        location, province = extract_location_and_province(raw_location or raw, raw_location)
        published = time_node.get("datetime") if time_node and time_node.get("datetime") else parse_date(raw)
        results.append(row(
            "linkedin", title, company, location, province, href, raw,
            published_at=published,
            application_deadline=extract_deadline(raw),
            contract_type=extract_contract(raw),
            homecare=is_homecare(f"{title} {raw}"),
            cooperative=is_cooperative(f"{company} {raw}"),
            salary=extract_salary(raw),
        ))
        seen.add(href)

    print(f"BASELINE_SOURCE linkedin collected={len(results)}")
    return results


def _bakeca_title(card, raw: str) -> str:
    node = card.select_one("h2, h3, .title, .titolo")
    if node:
        title = clean(node.get_text(" ", strip=True))
        if "fisioterap" in title.lower():
            return title
    text = re.sub(r"^\d+\s+", "", raw)
    parts = re.split(
        r"\s+(?:Libero professionista \(o Partita IVA\)|Tempo indeterminato|Tempo determinato|Da definire)\b",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )
    return clean(parts[0])


def _is_lazio_bakeca(title: str) -> bool:
    province = re.search(r"\(([A-Z]{2})\)", title)
    return not province or province.group(1) in LAZIO_PROVINCES


def collect_bakeca() -> list[dict]:
    key = os.environ.get("ZENROWS_KEY")
    if not key:
        print("BASELINE_SOURCE bakeca skipped=no_zenrows_key")
        return []

    params = {
        "url": BAKECA_URL,
        "apikey": key,
        "js_render": "true",
        "premium_proxy": "true",
        "wait_for": ".annuncio-in-elenco",
    }
    response = requests.get(ZENROWS_ENDPOINT, params=params, timeout=120)
    html = response.text or ""
    soup = BeautifulSoup(html, "lxml")
    cards = soup.select(".annuncio-in-elenco")
    print(
        "ZENROWS_USAGE "
        f"request_cost={response.headers.get('X-Request-Cost', 'n/a')} "
        f"concurrency_remaining={response.headers.get('Concurrency-Remaining', 'n/a')}"
    )
    if response.status_code >= 400:
        raise RuntimeError(f"ZenRows Bakeca HTTP {response.status_code}")
    if not cards:
        print(f"BASELINE_SOURCE bakeca incomplete_render bytes={len(response.content)}")
        return []

    results = []
    seen = set()
    for card in cards:
        anchor = card.find("a", href=True)
        if not anchor:
            continue
        href = urljoin(BAKECA_URL, anchor["href"])
        if "/dettaglio/medicina-salute-assistenza/" not in href.lower():
            continue
        href = canonical_url(href)
        if href in seen:
            continue
        raw = clean(card.get_text(" ", strip=True))
        title = _bakeca_title(card, raw)
        if "fisioterap" not in f"{title} {raw}".lower():
            continue
        if not _is_lazio_bakeca(title):
            continue
        location, province = extract_location_and_province(f"{title} {raw}")
        results.append(row(
            "bakeca", title, "", location, province, href, raw,
            published_at=parse_date(raw),
            application_deadline=extract_deadline(raw),
            contract_type=extract_contract(raw),
            homecare=is_homecare(f"{title} {raw}"),
            cooperative=is_cooperative(raw),
            salary=extract_salary(raw),
        ))
        seen.add(href)

    print(f"BASELINE_SOURCE bakeca collected={len(results)} cards={len(cards)}")
    return results


def deduplicate(rows: list[dict]) -> list[dict]:
    unique = []
    seen_urls = set()
    seen_keys = set()
    for item in rows:
        if item["url"] in seen_urls:
            continue
        key = item["dedup_key"]
        if key and key in seen_keys:
            continue
        unique.append(item)
        seen_urls.add(item["url"])
        if key:
            seen_keys.add(key)
    return unique


def save(rows: list[dict]) -> None:
    json_path = OUT_DIR / "baseline_jobs.json"
    csv_path = OUT_DIR / "baseline_jobs.csv"
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    fieldnames = [
        "source", "title", "company", "location", "province", "homecare",
        "published_at", "application_deadline", "contract_type", "cooperative",
        "salary", "url", "dedup_key", "raw_text",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"BASELINE_TOTAL unique={len(rows)} json={json_path} csv={csv_path}")


def main() -> None:
    all_rows = []
    all_rows.extend(collect_ofi())
    all_rows.extend(collect_linkedin())
    all_rows.extend(collect_bakeca())
    save(deduplicate(all_rows))


if __name__ == "__main__":
    main()
