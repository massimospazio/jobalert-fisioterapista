import re
from dataclasses import replace
from datetime import datetime

from core.models import JobListing


PROVINCE_BY_PLACE = {
    "albano laziale": "RM", "aprilia": "LT", "anzio": "RM", "ariccia": "RM",
    "civitavecchia": "RM", "colleferro": "RM", "fiano romano": "RM", "fiumicino": "RM",
    "fondi": "LT", "formia": "LT", "frascati": "RM", "gaeta": "LT", "genzano": "RM",
    "genzano di roma": "RM", "grottaferrata": "RM", "latina": "LT", "marino": "RM",
    "nettuno": "RM", "pomezia": "RM", "roma": "RM", "velletri": "RM", "viterbo": "VT",
    "cori": "LT", "frosinone": "FR", "rieti": "RI", "ciampino": "RM", "lariano": "RM",
    "castel gandolfo": "RM", "rocca di papa": "RM", "monte porzio catone": "RM",
    "monte compatri": "RM", "palestrina": "RM", "valmontone": "RM", "ardea": "RM",
}


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def extract_contract(text: str) -> str:
    lowered = clean(text).lower()
    rules = [
        ("tempo_indeterminato", ["tempo indeterminato", "contratto a tempo indeterminato"]),
        ("tempo_determinato", ["tempo determinato", "contratto a tempo determinato"]),
        ("cococo", ["co.co.co", "co co co", "cococo", "collaborazione coordinata e continuativa"]),
        ("partita_iva", ["partita iva", "p. iva", "p.iva", "libero professionista", "libera professione"]),
        ("collaborazione", ["collaborazione", "contratto di collaborazione"]),
    ]
    for value, tokens in rules:
        if any(token in lowered for token in tokens):
            return value
    return "non_specificato"


def extract_salary(text: str) -> str:
    value = clean(text)
    patterns = [
        r"\bRAL\s*(?:di|pari a|:)?\s*(?:€\s*)?\d{2,3}(?:[\.\s]\d{3})?(?:,\d{1,2})?",
        r"(?:€|EUR\s*)\s*\d{1,3}(?:[\.\s]\d{3})*(?:,\d{1,2})?(?:\s*(?:-|–|a)\s*(?:€|EUR\s*)?\s*\d{1,3}(?:[\.\s]\d{3})*(?:,\d{1,2})?)?(?:\s*(?:lord[ioae]*|netti?)?\s*(?:al mese|mensili|annui|annuali|l'anno|ora|orari)?)?",
        r"\b\d{1,3}(?:[\.,]\d{1,2})?\s*(?:€|euro)\s*(?:/\s*h|all['’]?ora|ora)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, value, re.IGNORECASE)
        if match:
            return clean(match.group(0))
    return ""


def _iso_date(day: str, month: str, year: str) -> str | None:
    if len(year) == 2:
        year = "20" + year
    try:
        return datetime(int(year), int(month), int(day)).date().isoformat()
    except ValueError:
        return None


def extract_deadline(text: str) -> str | None:
    value = clean(text)
    numeric = re.search(
        r"(?:scadenza|entro(?:\s+e\s+non\s+oltre)?\s+il|candidature\s+entro|termine(?:\s+ultimo)?)[^\d]{0,50}(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})",
        value, re.IGNORECASE,
    )
    if numeric:
        return _iso_date(*numeric.groups())

    named = re.search(
        r"(?:scadenza|entro(?:\s+e\s+non\s+oltre)?\s+il|candidature\s+entro|termine(?:\s+ultimo)?)[^\d]{0,50}(\d{1,2})\s+(gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre)\s+(20\d{2})",
        value, re.IGNORECASE,
    )
    if named:
        months = {name: index for index, name in enumerate([
            "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
            "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre",
        ], 1)}
        day, month_name, year = named.groups()
        return _iso_date(day, str(months[month_name.lower()]), year)
    return None


def extract_location(text: str, title: str = "") -> tuple[str, str]:
    combined = clean(f"{title} {text}")
    explicit = re.search(r"\b([A-Za-zÀ-ÿ'’\- ]{2,45}?)\s*\((RM|LT|FR|VT|RI)\)\b", combined)
    if explicit:
        candidate = clean(explicit.group(1))
        candidate = re.sub(r"^(?:fisioterapista|fisioterapisti|cercasi|ricerca)\s*[-:]?\s*", "", candidate, flags=re.IGNORECASE)
        words = candidate.split()
        if len(words) > 5:
            candidate = " ".join(words[-5:])
        return candidate.title(), explicit.group(2).upper()

    lowered = combined.lower()
    for place in sorted(PROVINCE_BY_PLACE, key=len, reverse=True):
        if re.search(rf"(?<!\w){re.escape(place)}(?!\w)", lowered):
            label = " ".join(word.capitalize() for word in place.split())
            if place == "genzano":
                label = "Genzano di Roma"
            return label, PROVINCE_BY_PLACE[place]
    return "", ""


def enrich_job(job: JobListing, locations: dict) -> JobListing:
    combined = clean(f"{job.title} {job.company} {job.text}")
    contract = job.contract_type if job.contract_type != "non_specificato" else extract_contract(combined)
    salary = job.salary or extract_salary(combined)
    deadline = job.application_deadline or extract_deadline(combined)

    location = job.location
    province = job.province
    if not location or not province:
        parsed_location, parsed_province = extract_location(job.text, job.title)
        location = location or parsed_location
        province = province or parsed_province

    coords = locations.get((location or "").lower(), {})
    latitude = job.latitude if job.latitude is not None else coords.get("latitude")
    longitude = job.longitude if job.longitude is not None else coords.get("longitude")

    return replace(
        job,
        location=location,
        province=province,
        latitude=latitude,
        longitude=longitude,
        contract_type=contract,
        piva_required=job.piva_required or contract == "partita_iva",
        salary=salary,
        salary_present=job.salary_present or bool(salary),
        application_deadline=deadline,
    )
