#!/usr/bin/env python3
"""
Job alert per "fisioterapista" a Roma e provincia.
Utilizza feed RSS/XML per evitare blocchi 403 da GitHub Actions.
"""

import json
import os
import re
import smtplib
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

STATE_FILE = Path(__file__).parent / "state.json"
TIMEOUT = 20
ND = "n.d."

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    )
}

@dataclass
class JobListing:
    source: str
    title: str
    url: str
    company: str = ""
    location: str = ""
    published_date: str = ""

    # Campi di dettaglio
    piva_required: str = ND
    employment_time: str = ND
    contract_duration: str = ND
    deadline: str = ND
    company_type: str = ND
    is_adi: str = "No"
    salary: str = ND
    experience_required: str = ND
    albo_required: str = ND

    def key(self) -> str:
        return self.url


def fetch_google_news_jobs() -> list[JobListing]:
    """Cerca annunci tramite il feed RSS di Google News per evitare blocchi IP."""
    query = quote('fisioterapista (Roma OR "Castelli Romani" OR "provincia di Roma") (offerte OR concorso OR privato)')
    rss_url = f"https://news.google.com/rss/search?q={query}&hl=it&gl=IT&ceid=IT:it"
    
    resp = requests.get(rss_url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    
    root = ET.fromstring(resp.content)
    results = []
    
    for item in root.findall(".//item"):
        title = item.findtext("title", default="")
        link = item.findtext("link", default="")
        pub_date = item.findtext("pubDate", default="")[:16]
        
        # Filtro per assicurarsi che sia inerente
        if "fisioterapista" in title.lower():
            results.append(
                JobListing(
                    source="Google News / Web",
                    title=title,
                    url=link,
                    published_date=pub_date,
                    location="Roma e provincia"
                )
            )
    return results


def fetch_bakeca_rss() -> list[JobListing]:
    """Legge il feed RSS di Bakeca Roma se disponibile."""
    url = "https://roma.bakeca.it/rss/offerte-di-lavoro.xml"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        results = []
        for item in root.findall(".//item"):
            title = item.findtext("title", default="")
            link = item.findtext("link", default="")
            if "fisioterapista" in title.lower():
                results.append(
                    JobListing(
                        source="Bakeca (RSS)",
                        title=title,
                        url=link,
                        location="Roma"
                    )
                )
        return results
    except Exception as e:
        print(f"[Bakeca RSS] Errore o non disponibile: {e}", file=sys.stderr)
        return []


def load_seen_urls() -> set:
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            return set(data.get("seen_urls", []))
        except Exception:
            return set()
    return set()


def save_seen_urls(urls: set) -> None:
    STATE_FILE.write_text(
        json.dumps({"seen_urls": sorted(urls)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_email_html(new_listings: list[JobListing]) -> str:
    rows = []
    for job in new_listings:
        rows.append(f"""
        <tr>
          <td style="padding:8px;border:1px solid #ddd;"><a href="{job.url}">{job.title}</a><br>
              <span style="color:#666;font-size:12px;">{job.source}</span></td>
          <td style="padding:8px;border:1px solid #ddd;">{job.location}</td>
          <td style="padding:8px;border:1px solid #ddd;">{job.published_date}</td>
        </tr>""")

    return f"""
    <html><body style="font-family:Arial,sans-serif;">
      <h2>🔎 {len(new_listings)} Nuove Offerte per Fisioterapista (Roma)</h2>
      <table style="border-collapse:collapse;width:100%;font-size:13px;">
        <tr style="background:#2c3e50;color:#fff;">
          <th style="padding:8px;">Annuncio</th>
          <th style="padding:8px;">Zona</th>
          <th style="padding:8px;">Data</th>
        </tr>
        {"".join(rows)}
      </table>
    </body></html>
    """


def send_email(subject: str, new_listings: list[JobListing]) -> None:
    gmail_user = os.environ["GMAIL_USER"]
    gmail_app_password = os.environ["GMAIL_APP_PASSWORD"]
    email_to = os.environ.get("EMAIL_TO", gmail_user)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = gmail_user
    msg["To"] = email_to
    msg.attach(MIMEText(build_email_html(new_listings), "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_user, gmail_app_password)
        server.sendmail(gmail_user, [email_to], msg.as_string())


def main() -> int:
    print("Avvio ricerca tramite RSS/Google News...")
    seen_urls = load_seen_urls()
    is_first_run = len(seen_urls) == 0

    all_listings = []
    all_listings.extend(fetch_google_news_jobs())
    all_listings.extend(fetch_bakeca_rss())

    print(f"Totale annunci individuati: {len(all_listings)}")

    if is_first_run:
        print("Primo avvio: registro gli annunci attuali come baseline.")
        save_seen_urls({j.key() for j in all_listings})
        return 0

    new_listings = [j for j in all_listings if j.key() not in seen_urls]

    if new_listings:
        print(f"Trovati {len(new_listings)} nuovi annunci! Invio e-mail...")
        subject = f"🔎 {len(new_listings)} Nuove Offerte Fisioterapista Roma"
        send_email(subject, new_listings)
        print("E-mail inviata con successo.")
    else:
        print("Nessuna novità rispetto all'ultima scansione.")

    save_seen_urls(seen_urls | {j.key() for j in all_listings})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
