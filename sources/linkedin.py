import math
import re
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from core.dedup import opportunity_key
from core.models import JobListing


LAZIO_PLACES = [
    ("Albano Laziale", "RM"), ("Aprilia", "LT"), ("Anzio", "RM"), ("Ariccia", "RM"),
    ("Civitavecchia", "RM"), ("Colleferro", "RM"), ("Fiano Romano", "RM"), ("Fiumicino", "RM"),
    ("Fondi", "LT"), ("Formia", "LT"), ("Frascati", "RM"), ("Gaeta", "LT"), ("Genzano", "RM"),
    ("Grottaferrata", "RM"), ("Latina", "LT"), ("Marino", "RM"), ("Nettuno", "RM"),
    ("Pomezia", "RM"), ("Roma", "RM"), ("Velletri", "RM"), ("Viterbo", "VT"), ("Cori", "LT"),
]

ALBANO_LAT = 41.72748
ALBANO_LON = 12.65900


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
    return (
        bool(re.search(r"\badi\b", lowered))
        or "domiciliar" in lowered
        or "a domicilio" in lowered
        or "assistenza domiciliare" in lowered
    )


def _homecare_only(title: str, text: str) -> bool:
    title_lower = title.lower()
    lowered = text.lower()
    if not _homecare(f"{title} {text}"):
        return False
    mixed = ["ambulator", "poliambulator", "residenzial", "ospedal", "clinica", "studio", "rsa", "reparto"]
    if any(token in lowered for token in mixed):
        return False
    if "domiciliar" in title_lower or re.search(r"\badi\b", title_lower):
        return True
    exclusive = ["assistenza domiciliare", "servizio adi", "prestazioni domiciliari", "pazienti a domicilio"]
    return any(token in lowered for token in exclusive)


def _distance_km(location: str, locations: dict) -> float:
    coords = locations.get((location or "").lower(), {})
    lat = coords.get("latitude")
    lon = coords.get("longitude")
    if lat is None or lon is None:
        return 9999.0
    radius = 6371.0
    phi1 = math.radians(ALBANO_LAT)
    phi2 = math.radians(float(lat))
    dphi = math.radians(float(lat) - ALBANO_LAT)
    dlambda = math.radians(float(lon) - ALBANO_LON)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _candidate_priority(candidate: tuple, locations: dict) -> tuple:
    title, _company, location, province, published, _url, is_known = candidate
    known_priority = 1 if is_known else 0
    province_priority = 0 if province == "RM" else 1
    homecare_priority = 1 if _homecare_only(title, title) else 0
    distance = _distance_km(location, locations)
    try:
        recent_priority = -int((published or "0000-00-00")[:10].replace("-", ""))
    except ValueError:
        recent_priority = 0
    return known_priority, province_priority, homecare_priority, distance, recent_priority


def _description_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    selectors = [
        ".show-more-less-html__markup",
        ".description__text",
        ".show-more-less-html",
        "section.show-more-less-html",
    ]
    for selector in selectors:
        node = soup.select_one(selector)
        if node:
            text = _clean(node.get_text(" ", strip=True))
            if text:
                return text
    return ""


def _card_opportunity_id(source_name: str, title: str, company: str, location: str, province: str, published, url: str) -> str:
    combined = _clean(f"{title} {company}")
    provisional = JobListing(
        source=source_name,
        url=url,
        title=title,
        text=combined,
        company=company,
        location=location,
        province=province,
        published_at=published,
        homecare=_homecare(combined),
        homecare_only=_homecare_only(title, combined),
    )
    return opportunity_key(provisional)


def collect(source_config: dict, locations: dict, known_opportunities: set[str] | None = None) -> list[JobListing]:
    search_url = source_config["search_url"]
    limit = int(source_config.get("max_results", 30))
    source_name = source_config.get("name", "LinkedIn")
    known_opportunities = known_opportunities or set()
    jobs = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(locale="it-IT")
        response = page.goto(search_url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2000)
        soup = BeautifulSoup(page.content(), "lxml")
        cards = soup.select(".base-search-card")

        candidates = []
        skipped_outside_rm = 0
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
            if province and province != "RM":
                skipped_outside_rm += 1
                continue
            published = time_node.get("datetime") if time_node and time_node.get("datetime") else None
            card_opp_id = _card_opportunity_id(source_name, title, company, location, province, published, url)
            is_known = card_opp_id in known_opportunities
            candidates.append((title, company, location, province, published, url, is_known))
            if len(candidates) >= limit:
                break

        candidates.sort(key=lambda item: _candidate_priority(item, locations))
        new_candidates = sum(1 for candidate in candidates if not candidate[6])
        known_candidates = len(candidates) - new_candidates
        print(
            f"LINKEDIN_COLLECT status={response.status if response else 'n/a'} "
            f"cards={len(cards)} candidates={len(candidates)} new_opportunities={new_candidates} "
            f"known_opportunities={known_candidates} skipped_outside_rm={skipped_outside_rm}"
        )
        for rank, candidate in enumerate(candidates[:5], 1):
            title, _company, location, province, _published, _url, is_known = candidate
            print(
                f"LINKEDIN_PRIORITY rank={rank} state={'KNOWN' if is_known else 'NEW'} province={province or 'unknown'} "
                f"location={location!r} distance_km={_distance_km(location, locations):.1f} title={title!r}"
            )

        detail_blocked = False
        detail_attempted = 0
        detail_success = 0
        detail_429 = 0
        detail_errors = 0
        skipped_known = 0
        unattempted_after_block = 0

        for title, company, location, province, published, url, is_known in candidates:
            detail_text = ""
            if is_known:
                skipped_known += 1
            elif detail_blocked:
                unattempted_after_block += 1
            else:
                detail_attempted += 1
                try:
                    detail_response = page.goto(url, wait_until="domcontentloaded", timeout=35000)
                    status = detail_response.status if detail_response else None
                    if status == 200:
                        page.wait_for_timeout(400)
                        detail_text = _description_text(page.content())
                        detail_success += 1
                    elif status == 429:
                        detail_429 += 1
                        detail_blocked = True
                    print(f"LINKEDIN_DETAIL status={status or 'n/a'} description_bytes={len(detail_text)} url={url}")
                except Exception as exc:
                    detail_errors += 1
                    print(f"LINKEDIN_DETAIL_ERROR url={url} error={exc}")

            combined = _clean(f"{title} {company} {detail_text}")
            coords = locations.get(location.lower(), {}) if location else {}
            salary = _salary(combined)
            contract = _contract(combined)
            homecare = _homecare(combined)
            jobs.append(JobListing(
                source=source_name,
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
                adi=bool(re.search(r"\badi\b", combined.lower())),
                homecare=homecare,
                homecare_only=_homecare_only(title, combined),
                cooperative="cooperativa" in f"{company} {detail_text}".lower(),
                salary=salary,
                salary_present=bool(salary),
            ))

        detail_impacted = max(0, new_candidates - detail_success)
        impact_pct = round((detail_impacted / new_candidates * 100), 1) if new_candidates else 0.0
        print(
            f"LINKEDIN_DETAIL_SUMMARY candidates={len(candidates)} new_opportunities={new_candidates} "
            f"known_skipped={skipped_known} attempted={detail_attempted} success={detail_success} "
            f"rate_limited_429={detail_429} errors={detail_errors} unattempted_after_block={unattempted_after_block} "
            f"detail_impacted={detail_impacted} impact_pct={impact_pct}"
        )
        browser.close()

    return jobs
