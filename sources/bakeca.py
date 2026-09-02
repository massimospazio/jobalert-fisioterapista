import os
import re
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from core.models import JobListing


ZENROWS_ENDPOINT = "https://api.zenrows.com/v1/"
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


def _title(card, raw: str) -> str:
    node = card.select_one("h2, h3, .title, .titolo")
    if node:
        text = _clean(node.get_text(" ", strip=True))
        if "fisioterap" in text.lower():
            return text
    text = re.sub(r"^\d+\s+", "", raw)
    parts = re.split(
        r"\s+(?:Libero professionista \(o Partita IVA\)|Tempo indeterminato|Tempo determinato|Da definire)\b",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )
    return _clean(parts[0])


def _company(raw: str) -> str:
    match = re.search(r"\bAzienda:\s*(.+?)\s+\d{1,2}/\d{1,2}/\d{4}\b", raw, re.IGNORECASE)
    return _clean(match.group(1)) if match else ""


def _location(title: str, raw: str):
    match = re.search(r"-\s*([^()]+?)\s*\((RM|LT|FR|VT|RI)\)\s*$", title)
    if match:
        return _clean(match.group(1)), match.group(2)
    lowered = f"{title} {raw}".lower()
    for place, province in LAZIO_PLACES:
        if place.lower() in lowered:
            return place, province
    return "", ""


def _published(raw: str):
    match = re.search(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b", raw)
    if not match:
        return None
    day, month, year = match.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}"


def _contract(text: str) -> str:
    lowered = text.lower()
    if "tempo indeterminato" in lowered:
        return "tempo_indeterminato"
    if "tempo determinato" in lowered:
        return "tempo_determinato"
    if any(token in lowered for token in ["partita iva", "libero professionista"]):
        return "partita_iva"
    if "collaborazione" in lowered:
        return "collaborazione"
    return "non_specificato"


def _employment(text: str) -> str:
    lowered = text.lower()
    if "full time" in lowered:
        return "full_time"
    if "part time" in lowered:
        return "part_time"
    if "turni" in lowered:
        return "turni"
    return "non_specificato"


def _salary(text: str) -> str:
    match = re.search(
        r"(?:€|EUR\s*)\d{1,3}(?:[\.\s]\d{3})*(?:,\d{1,2})?(?:\s*[-–]\s*(?:€|EUR\s*)?\d{1,3}(?:[\.\s]\d{3})*(?:,\d{1,2})?)?",
        text,
        re.IGNORECASE,
    )
    return _clean(match.group(0)) if match else ""


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
    api_key = os.environ.get("ZENROWS_KEY")
    if not api_key:
        raise RuntimeError("ZENROWS_KEY non configurata per Bakeca")

    search_url = source_config["search_url"]
    limit = int(source_config.get("max_results", 30))
    params = {
        "url": search_url,
        "apikey": api_key,
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
        f"concurrency_limit={response.headers.get('Concurrency-Limit', 'n/a')} "
        f"concurrency_remaining={response.headers.get('Concurrency-Remaining', 'n/a')} "
        f"request_id={response.headers.get('X-Request-Id', 'n/a')}"
    )

    if response.status_code >= 400:
        raise RuntimeError(f"ZenRows Bakeca HTTP {response.status_code}")
    if not cards:
        raise RuntimeError(f"ZENROWS_INCOMPLETE_RENDER Bakeca bytes={len(response.content)}")

    jobs = []
    seen = set()
    for card in cards:
        anchor = card.find("a", href=True)
        if not anchor:
            continue
        url = _canonical(urljoin(search_url, anchor["href"]))
        if "/dettaglio/medicina-salute-assistenza/" not in url.lower() or url in seen:
            continue
        raw = _clean(card.get_text(" ", strip=True))
        title = _title(card, raw)
        if "fisioterap" not in f"{title} {raw}".lower():
            continue
        company = _company(raw)
        location, province = _location(title, raw)
        coords = locations.get(location.lower(), {}) if location else {}
        contract = _contract(raw)
        salary = _salary(raw)
        homecare = _homecare(f"{title} {raw}")

        jobs.append(JobListing(
            source=source_config.get("name", "Bakeca"),
            url=url,
            title=title,
            text=raw,
            company=company,
            location=location,
            province=province,
            latitude=coords.get("latitude"),
            longitude=coords.get("longitude"),
            published_at=_published(raw),
            contract_type=contract,
            employment_type=_employment(raw),
            piva_required=contract == "partita_iva",
            adi="adi" in raw.lower(),
            homecare=homecare,
            homecare_only=_homecare_only(f"{title} {raw}"),
            cooperative="cooperativa" in f"{company} {raw}".lower(),
            salary=salary,
            salary_present=bool(salary),
        ))
        seen.add(url)
        if len(jobs) >= limit:
            break

    print(f"BAKECA_COLLECT cards={len(cards)} jobs={len(jobs)}")
    return jobs
