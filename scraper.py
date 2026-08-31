#!/usr/bin/env python3
"""
Job alert per "fisioterapista" a Roma e provincia.
Utilizza Google News RSS per evitare blocchi IP e Gemini per estrarre
un elenco strutturato di informazioni da ciascun annuncio.
"""

import json
import os
import smtplib
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup
from google import genai
from google.genai import types

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
    title: str = ND
    company: str = ND
    source: str = ND
    location: str = ND
    published_date: str = ND
    piva_required: str = ND
    employment_time: str = ND
    contract_duration: str = ND
    deadline: str = ND
    company_type: str = ND
    is_adi: str = ND
    salary: str = ND
    experience_required: str = ND
    albo_required: str = ND
    url: str = ""

    def key(self) -> str:
        return self.url


def analyze_with_gemini(title: str, snippet: str, link: str, pub_date: str) -> JobListing:
    """Usa Gemini per estrarre dati strutturati dal testo dell'annuncio."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY non trovata, imposto campi su n.d.", file=sys.stderr)
        return JobListing(title=title, url=link, published_date=pub_date)

    client = genai.Client(api_key=api_key)
    
    prompt = f"""
    Analizza il seguente annuncio di lavoro per fisioterapista ed estrai le informazioni richieste in formato JSON.
    Non inventare MAI alcuna informazione: se un dato non è espressamente specificato nel testo, inserisci rigorosamente "n.d.".

    Testo Annuncio:
    Titolo: {title}
    Descrizione/Snippet: {snippet}
    Data pubblicazione: {pub_date}

    Restituisci un oggetto JSON con i seguenti campi esatti:
    - title: titolo dell'annuncio
    - company: nome dell'azienda/struttura che cerca
    - source: fonte o portale originale (se deducibile)
    - location: sede/zona di lavoro (es. Roma, Albano Laziale, ecc.)
    - published_date: data pubblicazione (usa {pub_date} se non specificata diversamente)
    - piva_required: "Richiesta", "Non richiesta" oppure "n.d."
    - employment_time: "Full-time", "Part-time", "Flessibile" oppure "n.d."
    - contract_duration: "Determinato", "Indeterminato", "Libera professione", "Stage" oppure "n.d."
    - deadline: data di scadenza della candidatura o "n.d."
    - company_type: "Cooperativa", "Azienda/Società", "Studio privato", "Pubblico/SSN" oppure "n.d."
    - is_adi: "Sì" se riguarda Assistenza Domiciliare Integrata (ADI), altrimenti "No" o "n.d."
    - salary: retribuzione indicata o "n.d."
    - experience_required: esperienza richiesta in anni/livello o "n.d."
    - albo_required: "Richiesta", "Non richiesta" oppure "n.d."
    """

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )
        data = json.loads(response.text)
        data["url"] = link
        return JobListing(**data)
    except Exception as e:
        print(f"Errore analisi Gemini per {link}: {e}", file=sys.stderr)
        return JobListing(title=title, url=link, published_date=pub_date)


def fetch_google_news_jobs() -> list[JobListing]:
    """Cerca annunci tramite il feed RSS di Google News e li analizza."""
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
        
        # Estrazione dello snippet HTML all'interno del feed
        desc_html = item.findtext("description", default="")
        snippet = BeautifulSoup(desc_html, "html.parser").get_text(strip=True) if desc_html else ""
        
        if "fisioterapista" in title.lower() or "fisioterapista" in snippet.lower():
            print(f"Analisi annuncio: {title[:50]}...")
            listing = analyze_with_gemini(title, snippet, link, pub_date)
            results.append(listing)
            
    return results


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
        <tr style="border-bottom: 1px solid #ddd;">
          <td style="padding:8px;border:1px solid #ddd;"><a href="{job.url}" target="_blank"><b>{job.title}</b></a></td>
          <td style="padding:8px;border:1px solid #ddd;">{job.company}</td>
          <td style="padding:8px;border:1px solid #ddd;">{job.source}</td>
          <td style="padding:8px;border:1px solid #ddd;">{job.location}</td>
          <td style="padding:8px;border:1px solid #ddd;">{job.published_date}</td>
          <td style="padding:8px;border:1px solid #ddd;">{job.piva_required}</td>
          <td style="padding:8px;border:1px solid #ddd;">{job.employment_time}</td>
          <td style="padding:8px;border:1px solid #ddd;">{job.contract_duration}</td>
          <td style="padding:8px;border:1px solid #ddd;">{job.deadline}</td>
          <td style="padding:8px;border:1px solid #ddd;">{job.company_type}</td>
          <td style="padding:8px;border:1px solid #ddd;">{job.is_adi}</td>
          <td style="padding:8px;border:1px solid #ddd;">{job.salary}</td>
          <td style="padding:8px;border:1px solid #ddd;">{job.experience_required}</td>
          <td style="padding:8px;border:1px solid #ddd;">{job.albo_required}</td>
        </tr>""")

    headers = [
        "Titolo", "Azienda", "Fonte", "Sede", "Pubblicato", 
        "P.IVA", "Orario", "Contratto", "Scadenza", "Tipo Società", 
        "ADI", "Retribuzione", "Esperienza", "Albo TSRM/PSTRP"
    ]
    header_html = "".join(f'<th style="padding:8px;border:1px solid #ddd;background:#2c3e50;color:#fff;font-size:11px;">{h}</th>' for h in headers)

    return f"""
    <html><body style="font-family:Arial,sans-serif;font-size:12px;">
      <h2>🔎 {len(new_listings)} Nuove Offerte per Fisioterapista (Roma e Provincia)</h2>
      <div style="overflow-x:auto;">
        <table style="border-collapse:collapse;width:100%;font-size:12px;text-align:left;">
          <thead><tr>{header_html}</tr></thead>
          <tbody>{"".join(rows)}</tbody>
        </table>
      </div>
      <p style="color:#777;margin-top:15px;font-size:11px;">Le informazioni mancanti nell'annuncio originale sono indicate con "n.d." (nessun dato inventato).</p>
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
    print("Avvio ricerca e analisi annunci con Gemini...")
    seen_urls = load_seen_urls()
    is_first_run = len(seen_urls) == 0

    all_listings = fetch_google_news_jobs()
    print(f"Totale annunci individuati: {len(all_listings)}")

    if is_first_run:
        send_baseline_email = os.environ.get("SEND_BASELINE_EMAIL", "false").lower() == "true"

        if send_baseline_email and all_listings:
            print(f"Primo avvio con test attivo: invio e-mail con i {len(all_listings)} annunci strutturati.")
            subject = f"🧪 TEST — Baseline Struct: {len(all_listings)} annunci trovati"
            send_email(subject, all_listings)
            print("E-mail di test inviata con successo.")
        else:
            print("Primo avvio: registro gli annunci attuali come baseline. Nessuna e-mail inviata.")

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
