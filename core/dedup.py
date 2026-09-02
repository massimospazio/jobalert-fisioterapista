import hashlib
import re
import unicodedata
from dataclasses import replace

from core.models import JobListing


LEGAL_SUFFIXES = {
    "srl", "srls", "spa", "societa", "soc", "cooperativa", "coop", "sociale",
    "society", "benefit", "onlus", "ets",
}
SOURCE_PRIORITY = {"ofi lazio": 3, "linkedin": 2, "bakeca": 1}


def _ascii_words(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", value or "")
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch)).lower()
    return re.findall(r"[a-z0-9]+", normalized)


def normalize_company(value: str) -> str:
    words = [word for word in _ascii_words(value) if word not in LEGAL_SUFFIXES]
    return " ".join(words)


def normalize_location(value: str) -> str:
    return " ".join(_ascii_words(value))


def opportunity_key(job: JobListing) -> str:
    company = normalize_company(job.company)
    location = normalize_location(job.location)
    setting = "homecare_only" if job.homecare_only else "not_homecare_only"

    if company and location:
        basis = f"{company}|{location}|{setting}"
    elif company:
        title = " ".join(_ascii_words(job.title))
        basis = f"{company}|{title}|{setting}"
    else:
        title = " ".join(_ascii_words(job.title))
        basis = f"{job.source.lower()}|{title}|{location}|{setting}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]


def _richness(job: JobListing) -> tuple[int, int, int]:
    populated = sum([
        bool(job.company), bool(job.location), bool(job.province), bool(job.published_at),
        bool(job.application_deadline), job.contract_type not in {"", "non_specificato"},
        job.employment_type not in {"", "non_specificato"}, bool(job.salary),
    ])
    priority = SOURCE_PRIORITY.get(job.source.lower(), 0)
    return populated, priority, len(job.text or "")


def _merge(primary: JobListing, secondary: JobListing) -> JobListing:
    data = primary.__dict__.copy()
    for field in [
        "company", "location", "province", "published_at", "application_deadline",
        "salary",
    ]:
        if not data.get(field) and getattr(secondary, field):
            data[field] = getattr(secondary, field)

    if data.get("contract_type") in {"", "non_specificato"} and secondary.contract_type not in {"", "non_specificato"}:
        data["contract_type"] = secondary.contract_type
    if data.get("employment_type") in {"", "non_specificato"} and secondary.employment_type not in {"", "non_specificato"}:
        data["employment_type"] = secondary.employment_type
    if data.get("latitude") is None and secondary.latitude is not None:
        data["latitude"] = secondary.latitude
        data["longitude"] = secondary.longitude

    data["piva_required"] = bool(data.get("piva_required") or secondary.piva_required)
    data["adi"] = bool(data.get("adi") or secondary.adi)
    data["homecare"] = bool(data.get("homecare") or secondary.homecare)
    data["homecare_only"] = bool(data.get("homecare_only") and secondary.homecare_only)
    data["cooperative"] = bool(data.get("cooperative") or secondary.cooperative)
    data["salary_present"] = bool(data.get("salary_present") or secondary.salary_present)
    return JobListing(**data)


def deduplicate_jobs(jobs: list[JobListing]) -> tuple[list[JobListing], int]:
    by_key: dict[str, JobListing] = {}
    duplicates = 0
    for job in jobs:
        key = opportunity_key(job)
        if key not in by_key:
            by_key[key] = job
            continue
        duplicates += 1
        current = by_key[key]
        if _richness(job) > _richness(current):
            by_key[key] = _merge(job, current)
        else:
            by_key[key] = _merge(current, job)
    return list(by_key.values()), duplicates
