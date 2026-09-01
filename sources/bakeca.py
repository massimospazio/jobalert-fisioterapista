import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from core.models import JobListing


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/151.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
}


def _value(text: str, label: str) -> str:
    match = re.search(rf"{re.escape(label)}\s*:?\s*(.+)", text, re.IGNORECASE)
    return match.group(1).splitlines()[0].strip() if match else ""


def _contract(value: str) -> str:
    lowered = value.lower()
    if "indeterminato" in lowered:
        return "tempo_indeterminato"
    if "determinato" in lowered:
        return "tempo_determinato"
    if "partita iva" in lowered or "libero professionista" in lowered:
        return "partita_iva"
    if "collabor" in lowered:
        return "collaborazione"
    return "non_specificato"


def _employment(value: str) -> str:
    lowered = value.lower()
    if "full time" in lowered:
        return "full_time"
    if "part time" in lowered:
        return "part_time"
    if "turn" in lowered:
        return "turni"
    return "non_specificato"


def _extract_urls(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    urls: list[str] = []
    for anchor in soup.select('a[href*="/dettaglio/"]'):
        url = urljoin(base_url, anchor.get("href", ""))
        if url and "bakeca.it/dettaglio/" in url and url not in urls:
            urls.append(url)
    return urls


def _job_from_html(url: str, html: str, source_config: dict, locations: dict) -> JobListing:
    page = BeautifulSoup(html, "lxml")
    title_node = page.find("h1")
    title = title_node.get_text(" ", strip=True) if title_node else ""
    text = page.get_text("\n", strip=True)

    company = _value(text, "Azienda")
    contract_raw = _value(text, "Contratto")
    employment_raw = _value(text, "Disponibilità")
    location = _value(text, "Sede di lavoro")

    coords = locations.get(location.lower(), {}) if location else {}
    combined = f"{title}\n{text}".lower()
    contract_type = _contract(contract_raw)
    piva = contract_type == "partita_iva" or "partita iva" in combined

    return JobListing(
        source=source_config.get("name", "Bakeca"),
        url=url,
        title=title,
        company=company,
        location=location,
        text=text,
        latitude=coords.get("latitude"),
        longitude=coords.get("longitude"),
        contract_type=contract_type,
        employment_type=_employment(employment_raw),
        piva_required=piva,
        adi="adi" in combined or "assistenza domiciliare integrata" in combined,
        salary_present=bool(re.search(r"\b(ral|retribuzione|compenso|stipendio|€|euro)\b", combined)),
    )


def _collect_requests(source_config: dict, locations: dict) -> list[JobListing]:
    search_url = source_config["search_url"]
    response = requests.get(search_url, headers=HEADERS, timeout=25)
    response.raise_for_status()
    urls = _extract_urls(response.text, search_url)

    limit = int(source_config.get("max_results", 30))
    jobs: list[JobListing] = []
    for url in urls[:limit]:
        detail = requests.get(url, headers=HEADERS, timeout=25)
        detail.raise_for_status()
        jobs.append(_job_from_html(url, detail.text, source_config, locations))
    return jobs


def _collect_browser(source_config: dict, locations: dict) -> list[JobListing]:
    search_url = source_config["search_url"]
    limit = int(source_config.get("max_results", 30))
    jobs: list[JobListing] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            locale="it-IT",
            viewport={"width": 1440, "height": 1200},
        )
        page = context.new_page()
        page.goto(search_url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(1500)
        urls = _extract_urls(page.content(), search_url)

        for url in urls[:limit]:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(500)
            jobs.append(_job_from_html(url, page.content(), source_config, locations))

        context.close()
        browser.close()

    return jobs


def collect(source_config: dict, locations: dict) -> list[JobListing]:
    try:
        return _collect_requests(source_config, locations)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status != 403:
            raise
        print("SOURCE_FALLBACK bakeca: HTTP 403, provo browser Playwright")
        return _collect_browser(source_config, locations)
