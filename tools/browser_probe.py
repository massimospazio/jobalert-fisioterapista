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
    "subito": "https://www.subito.it/annunci-lazio/vendita/offerte-lavoro/roma/?q=fisioterapista",
    "lavoro_it": "https://www.lavoro.it/",
}


def probe(name: str, url: str, browser) -> None:
    diagnostics = Path("diagnostics_browser")
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

    page.wait_for_timeout(3000)
    html = page.content()
    text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
    title = page.title()

    links = []
    soup = BeautifulSoup(html, "lxml")
    for anchor in soup.find_all("a", href=True):
        resolved = urljoin(page.url, anchor.get("href", ""))
        if resolved not in links:
            links.append(resolved)

    (diagnostics / f"{name}.html").write_text(html, encoding="utf-8")
    (diagnostics / f"{name}-links.txt").write_text("\n".join(links), encoding="utf-8")

    lowered = text.lower()
    print(
        f"BROWSER_PROBE {name} status={response.status if response else 'n/a'} "
        f"timeout={timed_out} title={title!r} bytes={len(html)} links={len(links)} "
        f"contains_fisio={'fisioterap' in lowered} current_url={page.url}"
    )
    for candidate in links[:20]:
        print(f"BROWSER_LINK {name} {candidate}")

    context.close()


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for name, url in SOURCES.items():
            try:
                probe(name, url, browser)
            except Exception as exc:
                print(f"BROWSER_PROBE_ERROR {name}: {type(exc).__name__}: {exc}")
        browser.close()


if __name__ == "__main__":
    main()
