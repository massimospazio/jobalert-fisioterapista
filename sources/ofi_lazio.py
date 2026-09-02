import io
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

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


def _date(text: str):
    match = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](20\d{2})\b", text or "")
    if not match:
        return None
    day, month, year = match.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}"


def _deadline(text: str):
    match = re.search(
        r"(?:scadenza|entro il|candidature entro|termine)[^\d]{0,40}(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        text,
        re.IGNORECASE,
    )
    return match.group(1) if match else None


def _location(text: str):
    lowered = (text or "").lower()
    for place, province in LAZIO_PLACES:
        if place.lower() in lowered:
            return place, province
    match = re.search(r"\b([A-Za-zÀ-ÿ'’\- ]{2,40})\s*\((RM|LT|FR|VT|RI)\)", text or "")
    if match:
        return _clean(match.group(1)), match.group(2)
    return "", ""


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
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    response = session.get(search_url, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "lxml")

    offers = []
    seen = set()
    for block in soup.select("div.fusion-text"):
        anchor = block.find("a", href=True)
        if not anchor:
            continue
        url = urljoin(search_url, anchor["href"])
        if "/wp-content/uploads/20" not in url or url in seen:
            continue
        text = _clean(block.get_text(" ", strip=True))
        company = re.sub(r"\s+Leggi\s+tutto.*$", "", text, flags=re.IGNORECASE).strip()
        if not company:
            continue
        offers.append((company, url, text))
        seen.add(url)
        if len(offers) >= limit:
            break

    print(f"OFI_COLLECT index_status={response.status_code} offers={len(offers)}")
    jobs = []
    for company, url, index_text in offers:
        detail_text = index_text
        try:
            pdf_response = session.get(url, timeout=30)
            pdf_response.raise_for_status()
            reader = PdfReader(io.BytesIO(pdf_response.content))
            extracted = " ".join(page.extract_text() or "" for page in reader.pages)
            if extracted.strip():
                detail_text = _clean(f"{index_text} {extracted}")
            print(f"OFI_DETAIL status={pdf_response.status_code} pages={len(reader.pages)} company={company!r}")
        except Exception as exc:
            print(f"OFI_DETAIL_ERROR company={company!r} error={exc}")

        location, province = _location(detail_text)
        coords = locations.get(location.lower(), {}) if location else {}
        contract = _contract(detail_text)
        salary = _salary(detail_text)
        homecare = _homecare(detail_text)
        jobs.append(JobListing(
            source=source_config.get("name", "OFI Lazio"),
            url=url,
            title="Fisioterapista",
            text=detail_text,
            company=company,
            location=location,
            province=province,
            latitude=coords.get("latitude"),
            longitude=coords.get("longitude"),
            published_at=_date(detail_text),
            application_deadline=_deadline(detail_text),
            contract_type=contract,
            piva_required=contract == "partita_iva",
            adi="adi" in detail_text.lower(),
            homecare=homecare,
            homecare_only=_homecare_only(detail_text),
            cooperative="cooperativa" in detail_text.lower(),
            salary=salary,
            salary_present=bool(salary),
        ))

    return jobs
