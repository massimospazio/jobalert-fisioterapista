import re
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

from core.models import JobListing


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/151.0.0.0 Safari/537.36"
)


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


def _candidate_links(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    urls: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "").strip()
        if not href:
            continue
        url = urljoin(base_url, href)
        lowered = url.lower()
        if "bakeca.it" not in lowered:
            continue
        if any(token in lowered for token in ["/annunci/", "/offerte-lavoro/", "/dettaglio/"]):
            if url not in urls and url.rstrip("/") != base_url.rstrip("/"):
                urls.append(url)
    return urls


def _is_probable_job_url(url: str) -> bool:
    lowered = url.lower()
    blocked_fragments = [
        "?keyword=",
        "/luogo/",
        "/categoria/",
    ]
    if any(fragment in lowered for fragment in blocked_fragments):
        return False
    return "bakeca.it" in lowered


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


def _write_diagnostics(html: str, links: list[str], current_url: str) -> None:
    diagnostics = Path("diagnostics")
    diagnostics.mkdir(parents=True, exist_ok=True)
    (diagnostics / "bakeca-search.html").write_text(html, encoding="utf-8")
    (diagnostics / "bakeca-links.txt").write_text(
        "CURRENT_URL: " + current_url + "\n\n" + "\n".join(links),
        encoding="utf-8",
    )
    print(f"BAKECA_DIAGNOSTIC current_url={current_url} links_found={len(links)}")
    for url in links[:30]:
        print(f"BAKECA_LINK {url}")


def collect(source_config: dict, locations: dict) -> list[JobListing]:
    search_url = source_config["search_url"]
    limit = int(source_config.get("max_results", 30))
    jobs: list[JobListing] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=USER_AGENT,
            locale="it-IT",
            viewport={"width": 1440, "height": 1200},
        )
        page = context.new_page()
        response = None
        try:
            response = page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        except PlaywrightTimeoutError:
            print("BAKECA_NAVIGATION_TIMEOUT: uso comunque il contenuto parzialmente caricato")

        page.wait_for_timeout(3000)
        html = page.content()
        candidates = _candidate_links(html, page.url)
        _write_diagnostics(html, candidates, page.url)

        urls = [url for url in candidates if _is_probable_job_url(url)]
        print(
            f"BAKECA_BROWSER status={response.status if response else 'n/a'} "
            f"candidate_links={len(candidates)} probable_jobs={len(urls)}"
        )

        for url in urls[:limit]:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except PlaywrightTimeoutError:
                print(f"BAKECA_DETAIL_TIMEOUT {url}")
            page.wait_for_timeout(750)
            job = _job_from_html(url, page.content(), source_config, locations)
            if job.title and "fisioterap" in f"{job.title}\n{job.text}".lower():
                jobs.append(job)

        context.close()
        browser.close()

    return jobs
