import os
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


ZENROWS_ENDPOINT = "https://api.zenrows.com/v1/"
BAKECA_URL = "https://www.bakeca.it/annunci/medicina-salute-assistenza/luogo/lazio/?keyword=fisioterapista"


def extract_links(html: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    links: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "").strip()
        if not href:
            continue
        url = urljoin(BAKECA_URL, href)
        if "bakeca.it" in url.lower() and url not in links:
            links.append(url)
    return links


def main() -> None:
    apikey = os.environ.get("ZENROWS_KEY")
    if not apikey:
        raise SystemExit("ZENROWS_KEY non configurata")

    params = {
        "url": BAKECA_URL,
        "apikey": apikey,
        "js_render": "true",
        "premium_proxy": "true",
    }

    response = requests.get(ZENROWS_ENDPOINT, params=params, timeout=120)
    html = response.text or ""
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    links = extract_links(html)
    probable_jobs = [
        url for url in links
        if any(token in url.lower() for token in ["/dettaglio/", "fisioterap", "medicina-salute-assistenza"])
    ]

    diagnostics = Path("diagnostics_zenrows")
    diagnostics.mkdir(parents=True, exist_ok=True)
    (diagnostics / "bakeca.html").write_text(html, encoding="utf-8")
    (diagnostics / "bakeca-links.txt").write_text("\n".join(links), encoding="utf-8")

    print(
        "ZENROWS_PROBE bakeca "
        f"status={response.status_code} bytes={len(response.content)} "
        f"title={title!r} links={len(links)} probable_jobs={len(probable_jobs)} "
        f"contains_fisio={'fisioterap' in html.lower()}"
    )
    for url in probable_jobs[:30]:
        print(f"ZENROWS_JOB_LINK {url}")

    if response.status_code >= 400:
        raise SystemExit(f"ZenRows ha restituito HTTP {response.status_code}")


if __name__ == "__main__":
    main()
