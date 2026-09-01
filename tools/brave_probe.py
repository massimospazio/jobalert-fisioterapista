import json
import os
from pathlib import Path

import requests


BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
QUERY = "site:bakeca.it fisioterapista Lazio"


def main() -> None:
    api_key = os.environ.get("BRAVE_API_KEY")
    if not api_key:
        raise SystemExit("BRAVE_API_KEY non configurata")

    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": api_key,
    }
    params = {
        "q": QUERY,
        "count": 20,
        "country": "IT",
        "search_lang": "it",
        "ui_lang": "it-IT",
    }

    response = requests.get(BRAVE_ENDPOINT, headers=headers, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()
    results = data.get("web", {}).get("results", [])

    bakeca_results = []
    for item in results:
        url = item.get("url", "")
        if "bakeca.it" not in url.lower():
            continue
        bakeca_results.append({
            "title": item.get("title", ""),
            "url": url,
            "description": item.get("description", ""),
            "age": item.get("age", ""),
        })

    diagnostics = Path("diagnostics_brave")
    diagnostics.mkdir(parents=True, exist_ok=True)
    (diagnostics / "bakeca-search.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (diagnostics / "bakeca-results.txt").write_text(
        "\n\n".join(
            f"TITLE: {item['title']}\nURL: {item['url']}\nAGE: {item['age']}\nDESCRIPTION: {item['description']}"
            for item in bakeca_results
        ),
        encoding="utf-8",
    )

    print(
        "BRAVE_PROBE bakeca "
        f"status={response.status_code} total_results={len(results)} "
        f"bakeca_results={len(bakeca_results)} query={QUERY!r}"
    )
    for item in bakeca_results:
        print(
            f"BRAVE_RESULT title={item['title']!r} age={item['age']!r} url={item['url']}"
        )


if __name__ == "__main__":
    main()
