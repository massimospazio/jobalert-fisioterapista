import requests

SOURCES = {
    "bakeca": "https://www.bakeca.it/annunci/medicina-salute-assistenza/luogo/lazio/?keyword=fisioterapista",
    "subito": "https://www.subito.it/annunci-lazio/vendita/offerte-lavoro/roma/?q=fisioterapista",
    "lavoro_it": "https://www.lavoro.it/",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
}

for name, url in SOURCES.items():
    try:
        r = requests.get(url, headers=HEADERS, timeout=20, allow_redirects=True)
        text = r.text or ""
        print(
            f"HTTP_PROBE {name} status={r.status_code} bytes={len(r.content)} "
            f"final_url={r.url} contains_fisio={'fisioterap' in text.lower()}"
        )
    except Exception as exc:
        print(f"HTTP_PROBE {name} error={type(exc).__name__}: {exc}")
