import re
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from core.models import JobListing


LAZIO_PLACES = [
    ("Albano Laziale", "RM"), ("Aprilia", "LT"), ("Anzio", "RM"), ("Ariccia", "RM"),
    ("Civitavecchia", "RM"), ("Colleferro", "RM"), ("Fiano Romano", "RM"), ("Fiumicino", "RM"),
    ("Fondi", "LT"), ("Formia", "LT"), ("Frascati", "RM"), ("Gaeta", "LT"), ("Genzano", "RM"),
    ("Grottaferrata", "RM"), ("Latina", "LT"), ("Marino", "RM"), ("Nettuno", "RM"),
    ("Pomezia", "RM"), ("Roma", "RM"), ("Velletri", "RM"), ("Viterbo", "VT"), ("Cori", "LT"),
]


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _canonical(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def _location(text: str):
    lowered = (text or "").lower()
    for place, province in LAZIO_PLACES:
        if place.lower() in lowered:
            return place, province
    return _clean(text), ""


def _contract(text: str) -> str:
    lowered = text.lower()
    if "tempo indeterminato" in lowered:
        return "tempo_indeterminato"
    if "tempo determinato" in lowered:
        return "tempo_determinato"
    if any(token in lowered for token in ["partita iva", "p.iva", "libero professionista"]):
        return "partita_iva"
    if any(token in lowered for token in ["co.co.co", "cococo", "collaborazione coordinata"]):
        return "cococo"
    if "collaborazione" in lowered:
        return "collaborazione"
    return "non_specificato"


def _salary(text: str) -> str:
    patterns = [
        r"(?:€|EUR\s*)\d{1,3}(?:[\.\s]\d{3})*(?:,\d{1,2})?(?:\s*[-–]\s*(?:€|EUR\s*)?\d{1,3}(?:[\.\s]\d{3})*(?:,\d{1,2})?)?",
        r"\bRAL\s*(?:di|:)?\s*\d{2,3}(?:\.\d{3})?",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return _clean(match.group(0))
    return ""


def _deadline(text: str):
    match = re.search(
        r"(?:scadenza|entro il|candidature entro|termine)[^\d]{0,40}(\d{1,2}/\d{1,2}/\d{2,4})",
        text,
        re.IGNORECASE,
    )
    return match.group(1) if match else None


def _homecare(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in ["adi", "domiciliar", "a domicilio", "assistenza domiciliare"])


def _homecare_only(text: str) -> bool:
    lowered = text.lower()
    if not _homecare(text):
        return False
    mixed = ["ambulator", "poliambulator", "struttura", "residenzial", "ospedal", "clinica", "studio", "rsa", "reparto"]
    if any(token in lowered for token in mixed):
        return False
    exclusive = ["fisioterapista domiciliare", "assistenza domiciliare", "servizio adi", "per adi", "adi asl", "prestazioni domiciliari"]
    return any(token in lowered for token in exclusive)


def collect(source_config: dict, locations: dict) -> list[JobListing]:
    search_url = source_config["search_url"]
    limit = int(source_config.get("max_results", 30))
    jobs = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(locale="it-IT")
        response = page.goto(search_url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2000)
        soup = BeautifulSoup(page.content(), "lxml")
        cards = soup.select(".base-search-card")

        candidates = []
        for card in cards:
            title_node = card.select_one("h3.base-search-card__title")
            company_node = card.select_one("h4.base-search-card__subtitle")
            location_node = card.select_one(".job-search-card__location")
            time_node = card.select_one("time")
            anchor = card.select_one('a[href*="/jobs/view/"]')
            if not title_node or not anchor:
                continue
            title = _clean(title_node.get_text(" ", strip=True))
            if "fisioterap" not in title.lower():
                continue
            url = _canonical(urljoin(search_url, anchor["href"]))
            company = _clean(company_node.get_text(" ", strip=True)) if company_node else ""
            raw_location = _clean(location_node.get_text(" ", strip=True)) if location_node else ""
            location, province = _location(raw_location)
            published = time_node.get("datetime") if time_node and time_node.get("datetime") else None
            candidates.append((title, company, location, province, published, url))
            if len(candidates) >= limit:
                break

        print(
            f"LINKEDIN_COLLECT status={response.status if response else 'n/a'} "
            f"cards={len(cards)} candidates={len(candidates)}"
        )

        for title, company, location, province, published, url in candidates:
            detail_text = title
            try:
                detail_response = page.goto(url, wait_until="domcontentloaded", timeout=35000)
                page.wait_for_timeout(500)
                detail_soup = BeautifulSoup(page.content(), "lxml")
                main = detail_soup.select_one("main") or detail_soup
                detail_text = _clean(main.get_text(" ", strip=True))
                print(f"LINKEDIN_DETAIL status={detail_response.status if detail_response else 'n/a'} url={url}")
            except Exception as exc:
                print(f"LINKEDIN_DETAIL_ERROR url={url} error={exc}")

            combined = _clean(f"{title} {company} {detail_text}")
            coords = locations.get(location.lower(), {}) if location else {}
            salary = _salary(combined)
            contract = _contract(combined)
            homecare = _homecare(combined)
            jobs.append(JobListing(
                source=source_config.get("name", "LinkedIn"),
                url=url,
                title=title,
                text=combined,
                company=company,
                location=location,
                province=province,
                latitude=coords.get("latitude"),
                longitude=coords.get("longitude"),
                published_at=published,
                application_deadline=_deadline(combined),
                contract_type=contract,
                piva_required=contract == "partita_iva",
                adi="adi" in combined.lower(),
                homecare=homecare,
                homecare_only=_homecare_only(combined),
                cooperative="cooperativa" in combined.lower(),
                salary=salary,
                salary_present=bool(salary),
            ))

        browser.close()

    return jobs
