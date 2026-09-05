import os
import re
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from core.models import JobListing


ZENROWS_ENDPOINT = "https://api.zenrows.com/v1/"


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _canonical(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _homecare(text: str) -> bool:
    lowered = text.lower()
    return bool(re.search(r"\badi\b", lowered)) or any(
        token in lowered for token in ["domiciliar", "a domicilio", "assistenza domiciliare"]
    )


def _homecare_only(title: str, text: str) -> bool:
    combined = f"{title} {text}".lower()
    if not _homecare(combined):
        return False
    if any(token in combined for token in ["ambulator", "poliambulator", "studio", "clinica", "rsa", "ospedal", "reparto"]):
        return False
    return (
        "domiciliar" in title.lower()
        or bool(re.search(r"\badi\b", title.lower()))
        or any(token in combined for token in ["assistenza domiciliare", "servizio adi", "pazienti a domicilio", "prestazioni domiciliari"])
    )


def _published(text: str):
    lowered = text.lower()
    match = re.search(r"(\d+)\s+(?:giorno|giorni)\s+fa", lowered)
    if match:
        return f"{match.group(1)} giorni fa"
    if "oggi" in lowered:
        return "oggi"
    return None


def _extract_cards(soup: BeautifulSoup):
    cards = soup.select("div.job_seen_beacon, div.cardOutline, div.resultContent")
    if cards:
        return cards
    return soup.select("li.css-5lfssm, div.slider_container")


def collect(source_config: dict, locations: dict) -> tuple[list[JobListing], dict]:
    api_key = os.environ.get("ZENROWS_KEY")
    if not api_key:
        raise RuntimeError("ZENROWS_KEY non configurata per Indeed")

    search_url = source_config["search_url"]
    limit = int(source_config.get("max_results", 30))
    params = {
        "url": search_url,
        "apikey": api_key,
        "js_render": "true",
        "premium_proxy": "true",
    }
    response = requests.get(ZENROWS_ENDPOINT, params=params, timeout=120)
    html = response.text or ""
    usage = {
        "status": response.status_code,
        "request_cost": response.headers.get("X-Request-Cost", "n/a"),
        "concurrency_limit": response.headers.get("Concurrency-Limit", "n/a"),
        "concurrency_remaining": response.headers.get("Concurrency-Remaining", "n/a"),
        "request_id": response.headers.get("X-Request-Id", "n/a"),
        "bytes": len(response.content),
    }
    print(
        "INDEED_ZENROWS_USAGE "
        f"request_cost={usage['request_cost']} status={usage['status']} bytes={usage['bytes']} "
        f"request_id={usage['request_id']}"
    )
    if response.status_code >= 400:
        raise RuntimeError(f"ZenRows Indeed HTTP {response.status_code}")

    soup = BeautifulSoup(html, "lxml")
    cards = _extract_cards(soup)
    jobs = []
    seen = set()

    for card in cards:
        anchor = card.select_one('h2.jobTitle a[href], a[data-jk][href], a[href*="/viewjob"], a[href*="/rc/clk"]')
        if not anchor:
            continue
        title_node = card.select_one("h2.jobTitle, h2 a, a[data-jk]")
        title = _clean(title_node.get_text(" ", strip=True) if title_node else anchor.get_text(" ", strip=True))
        raw = _clean(card.get_text(" ", strip=True))
        if "fisioterap" not in f"{title} {raw}".lower():
            continue

        href = anchor.get("href", "")
        url = _canonical(urljoin(search_url, href))
        if not url or url in seen:
            continue
        company_node = card.select_one('[data-testid="company-name"], span.companyName, .company_location [data-testid="company-name"]')
        location_node = card.select_one('[data-testid="text-location"], div.companyLocation, .company_location [data-testid="text-location"]')
        company = _clean(company_node.get_text(" ", strip=True)) if company_node else ""
        location = _clean(location_node.get_text(" ", strip=True)) if location_node else ""

        jobs.append(JobListing(
            source=source_config.get("name", "Indeed"),
            url=url,
            title=title,
            text=raw,
            company=company,
            location=location,
            published_at=_published(raw),
            adi=bool(re.search(r"\badi\b", f"{title} {raw}".lower())),
            homecare=_homecare(f"{title} {raw}"),
            homecare_only=_homecare_only(title, raw),
            cooperative="cooperativa" in f"{company} {raw}".lower(),
        ))
        seen.add(url)
        if len(jobs) >= limit:
            break

    print(f"INDEED_COLLECT cards={len(cards)} jobs={len(jobs)}")
    if not cards:
        print(f"INDEED_RENDER_WARNING no_cards bytes={len(html)}")
    return jobs, usage
