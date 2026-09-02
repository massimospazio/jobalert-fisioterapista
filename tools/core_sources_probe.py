from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/151.0.0.0 Safari/537.36"
)

SOURCES = {
    "indeed": "https://it.indeed.com/q-fisioterapista-l-lazio-offerte-lavoro.html",
    "ofi_lazio": "https://www.ofilazio.it/offerte-di-lavoro/",
    "linkedin": "https://it.linkedin.com/jobs/fisioterapista-roma-rome-offerte-di-lavoro-roma-lz",
}


def probe(name: str, url: str, browser) -> None:
    diagnostics = Path("diagnostics_core_sources")
    diagnostics.mkdir(parents=True, exist_ok=True)

    context = browser.new_context(
        user_agent=USER_AGENT,
        locale="it-IT",
        viewport={"width": 1440, "height": 1200},
    )
    page = context.new_page()
    response = None
    timed_out = False

    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except PlaywrightTimeoutError:
        timed_out = True

    page.wait_for_timeout(2500)
    html = page.content()
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)
    lowered = text.lower()
    title = page.title()

    links = []
    job_like_links = []
    for anchor in soup.find_all("a", href=True):
        resolved = urljoin(page.url, anchor.get("href", ""))
        if resolved not in links:
            links.append(resolved)
        marker = (anchor.get_text(" ", strip=True) + " " + resolved).lower()
        if "fisioterap" in marker or "job" in marker or "offert" in marker:
            if resolved not in job_like_links:
                job_like_links.append(resolved)

    (diagnostics / f"{name}.html").write_text(html, encoding="utf-8")
    (diagnostics / f"{name}-links.txt").write_text("\n".join(job_like_links), encoding="utf-8")

    print(
        f"CORE_SOURCE_PROBE {name} status={response.status if response else 'n/a'} "
        f"timeout={timed_out} bytes={len(html)} links={len(links)} "
        f"job_like_links={len(job_like_links)} contains_fisio={'fisioterap' in lowered} "
        f"title={title!r} current_url={page.url}"
    )

    for candidate in job_like_links[:15]:
        print(f"CORE_SOURCE_LINK {name} {candidate}")

    context.close()


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for name, url in SOURCES.items():
            try:
                probe(name, url, browser)
            except Exception as exc:
                print(f"CORE_SOURCE_PROBE_ERROR {name}: {type(exc).__name__}: {exc}")
        browser.close()


if __name__ == "__main__":
    main()
