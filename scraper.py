#!/usr/bin/env python3
import json
import os
import smtplib
import sys
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from google import genai
from google.genai import types

STATE_FILE = Path(__file__).parent / "state.json"
TIMEOUT = 45
ND = "n.d."


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


def fetch_with_zenrows(target_url: str) -> str | None:
    """Scarica il contenuto HTML della pagina tramite l'API di ZenRows."""
    zenrows_key = os.environ.get("ZENROWS_KEY", "").strip()
    if not zenrows_key:
        print("ZENROWS_KEY non trovata nei Secret di GitHub!", file=sys.stderr)
        return None

    params = {
        "apikey": zenrows_key,
        "url": target_url,
        "js_render": "true"
    }

    try:
        resp = requests.get("https://api.zenrows.com/v1/", params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"Errore download ZenRows per {target_url}: {e}", file=sys.stderr)
        return None


def analyze_with_gemini(text_content: str, url: str) -> JobListing | None:
    """Usa Gemini per validare se si tratta di una vera offerta ed estrarne i dati."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY non trovata.", file=sys.stderr)
        return None

    client = genai.Client(api_key=api_key)

    prompt = f"""
    Analizza il seguente testo estratto da una pagina web o annuncio di lavoro.

    Testo:
    {text_content[:3500]}

    PASSAGGIO 1 (Filtro):
    Verifica se questo testo rappresenta un'OFFERTA DI LAVORO / SELEZIONE / CONCORSO reale per FISIOTERAPISTA a Roma o provincia.
    Imposta "is_job_offer": false se si tratta di articoli di giornale, cronaca, notizie di infortuni, blog generici o annunci non pertinenti.

    PASSAGGIO 2 (Estrazione):
    Se È una vera offerta di lavoro, imposta "is_job_offer": true ed estrai i dati.
    Non inventare MAI nulla: se un parametro manca, inserisci rigorosamente "n.d.".

    Restituisci un JSON con questa struttura esatta:
    {{
      "is_job_offer": true/false,
      "title": "titolo dell'annuncio",
      "company": "nome azienda o studio",
      "source": "fonte/portale",
      "location": "sede o zona",
      "published_date": "data pubblicazione o n.d.",
      "piva_required": "Richiesta/Non richiesta/n.d.",
      "employment_time": "Full-time/Part-time/Flessibile/n.d.",
      "contract_duration": "Determinato/Indeterminato/Libera professione/Stage/n.d.",
      "deadline": "scadenza o n.d.",
      "company_type": "Cooperativa/Azienda/Società/Studio privato/Pubblico/SSN/n.d.",
      "is_adi": "Sì/No/n.d.",
      "salary": "retribuzione o n.d.",
      "experience_required": "esperienza richiesta o n.d.",
      "albo_required": "Richiesta/Non richiesta/n.d."
    }}
    """

    try:
        response = client.models.generate_content(
            model="gemini-1.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.0,
            ),
        )
        data = json.loads(response.text)

        if not data.get("is_job_offer", False):
            return None

        data.pop("is_job_offer", None)
        data["url"] = url
        return JobListing(**data)
    except Exception as e:
        print(f"Errore Gemini per {url}: {e}", file=sys.stderr)
        return None


def extract_job_urls(html_content: str, base_url: str) -> list[tuple[str, str]]:
    """Estrae i link specifici degli annunci in base al portale."""
    soup = BeautifulSoup(html_content, "html.parser")
    candidates = []

    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        href = a["href"]

        if href.startswith("/"):
            parsed = urlparse(base_url)
            href = f"{parsed.scheme}://{parsed.netloc}{href}"

        href_lower = href.lower()

        if "bakeca.it" in base_url:
            if "/dettaglio/" in href_lower or "fisioterapista" in href_lower:
                candidates.append((text, href))
        elif "lavoro.it" in base_url:
            if ("/offerta/" in href_lower or "/annuncio/" in href_lower or "fisioterapista" in href_lower) and not href_lower.endswith(".html"):
                candidates.append((text, href))
        else:
            if "fisioterapista" in href_lower or "fisioterapista" in text.lower():
                candidates.append((text, href))

    return candidates


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
    print("Avvio ricerca annunci tramite ZenRows + Gemini...")
    seen_urls = load_seen_urls()
    is_first_run = len(seen_urls) == 0

    target_urls = [
        "https://www.bakeca.it/offerte-lavoro/roma/keyword/fisioterapista/",
        "https://it.lavoro.it/offerte-lavoro-fisioterapista-roma.html"
    ]

    all_listings = []

    for target in target_urls:
        print(f"\nScansione portale: {target}")
        html = fetch_with_zenrows(target)
        if not html:
            continue

        candidates = extract_job_urls(html, target)
        print(f"Estratti {len(candidates)} link totali. Filtraggio dei link pertinenti...")

        valid_candidates = []
        seen_candidate_urls = set()

        for text, url in candidates:
            if url in seen_urls or any(l.url == url for l in all_listings):
                continue

            url_lower = url.lower()
            if "keyword/fisioterapista" in url_lower or "page=" in url_lower:
                continue

            if url not in seen_candidate_urls:
                seen_candidate_urls.add(url)
                valid_candidates.append((text, url))

        print(f"Trovati {len(valid_candidates)} link di annunci potenziali non ancora visti.")

        for text, url in valid_candidates[:10]:
            print(f"\n  Scarico dettagli per: {text[:40]}... -> {url}")
            detail_html = fetch_with_zenrows(url)

            if not detail_html:
                print("   [ERRORE]: Impossibile scaricare la pagina dell'annuncio.")
                continue

            soup = BeautifulSoup(detail_html, "html.parser")
            for element in soup(["script", "style", "nav", "footer", "header"]):
                element.decompose()

            page_text = soup.get_text(separator=" ", strip=True)

            print("   Analisi con Gemini in corso...")
            listing = analyze_with_gemini(page_text, url)

            if listing:
                print(f"   [OFFERTA CONFERMATA]: {listing.title} ({listing.company})")
                all_listings.append(listing)
            else:
                print("   [SCARTATA]: Non è un'offerta di lavoro valida secondo Gemini.")

    print(f"\nTotale nuove offerte valide trovate: {len(all_listings)}")

    if is_first_run:
        send_baseline_email = os.environ.get("SEND_BASELINE_EMAIL", "false").lower() == "true"
        if send_baseline_email and all_listings:
            print(f"Primo avvio (Test): invio e-mail con i {len(all_listings)} annunci trovati.")
            send_email(f"🧪 TEST ZenRows — Baseline: {len(all_listings)} annunci", all_listings)
        else:
            print("Primo avvio: registro gli annunci attuali come baseline.")

        save_seen_urls({j.key() for j in all_listings})
        return 0

    new_listings = [j for j in all_listings if j.key() not in seen_urls]

    if new_listings:
        print(f"Trovati {len(new_listings)} nuovi annunci! Invio e-mail...")
        send_email(f"🔎 {len(new_listings)} Nuove Offerte Fisioterapista Roma", new_listings)
    else:
        print("Nessuna novità rispetto all'ultima scansione.")

    save_seen_urls(seen_urls | {j.key() for j in all_listings})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
