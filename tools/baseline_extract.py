import csv
import json
import os
import re
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


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def canonical_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def norm_key(title: str, company: str, location: str) -> str:
    raw = "|".join([title, company, location]).lower()
    raw = re.sub(r"[^a-z0-9àèéìòù]+", " ", raw)
    return clean(raw)


def row(source: str, title: str, company: str, location: str, url: str, raw_text: str) -> dict:
    return {
        "source": source,
        "title": clean(title),
        "company": clean(company),
        "location": clean(location),
        "url": canonical_url(url),
        "raw_text": clean(raw_text),
        "dedup_key": norm_key(title, company, location),
    }


def collect_ofi() -> list[dict]:
    response = requests.get(OFI_URL, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "lxml")
    results = []

    heading = soup.find(lambda tag: tag.name in {"h1", "h2", "h3", "h4"} and clean(tag.get_text(" ", strip=True)).lower() == "offerte di lavoro")
    root = heading.parent if heading else soup

    for title_node in root.find_all(["h2", "h3", "h4"]):
        title = clean(title_node.get_text(" ", strip=True))
        if not title or title.lower() == "offerte di lavoro":
            continue
        anchor = title_node.find_next("a", href=True)
        if not anchor:
            continue
        href = urljoin(OFI_URL, anchor["href"])
        if "/wp-content/uploads/" not in href:
            continue
        parent = title_node.find_parent(["article", "div"]) or title_node
        raw = clean(parent.get_text(" ", strip=True))
        results.append(row("ofi_lazio", "Fisioterapista", title, "Lazio", href, raw))

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
        location = clean(location_node.get_text(" ", strip=True)) if location_node else ""
        raw = clean(card.get_text(" ", strip=True))
        results.append(row("linkedin", title, company, location, href, raw))
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
        r"\s+(?:Libero professionista \(o Partita IVA\)|Tempo indeterminato|Tempo determinato|Da definire)\s*\.",
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
        results.append(row("bakeca", title, "", "Lazio", href, raw))
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
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["source", "title", "company", "location", "url", "dedup_key", "raw_text"])
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
