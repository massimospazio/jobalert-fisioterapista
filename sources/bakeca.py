import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from core.models import JobListing


HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; JobAlertFisioterapista/2.0)"}


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


def collect(source_config: dict, locations: dict) -> list[JobListing]:
    response = requests.get(source_config["search_url"], headers=HEADERS, timeout=25)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "lxml")

    urls = []
    for anchor in soup.select('a[href*="/dettaglio/"]'):
        url = urljoin(source_config["search_url"], anchor.get("href", ""))
        if url and url not in urls:
            urls.append(url)

    limit = int(source_config.get("max_results", 30))
    jobs = []
    for url in urls[:limit]:
        detail = requests.get(url, headers=HEADERS, timeout=25)
        detail.raise_for_status()
        page = BeautifulSoup(detail.text, "lxml")
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

        jobs.append(JobListing(
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
        ))

    return jobs
