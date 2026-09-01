#!/usr/bin/env python3
import json
import os
import re
import smtplib
import sys
import time
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from google import genai
from google.genai import types
from playwright.sync_api import sync_playwright

STATE_FILE = Path(__file__).parent / "state.json"
GEMINI_MODEL = "gemini-3.6-flash"
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


def fetch_with_playwright(target_url: str) -> str | None:
    """Scarica la pagina simulando un browser Chromium reale in modalità Stealth."""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
                locale="it-IT"
            )
            page = context.new_page()
            page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2500)
            content = page.content()
            browser.close()
            return content
    except Exception as e:
        print(f"Errore Playwright per {target_url}: {e}", file=sys.stderr)
        return None


def fetch_page(target_url: str) -> str | None:
    """Strategia di download: Playwright primario -> ZenRows facoltativo se abilitato."""
    html = fetch_with_playwright(target_url)

    if html and len(html) > 3000 and "Access Denied" not in html and "Cloudflare" not in html:
        return html

    use_zenrows = os.environ.get("USE_ZENROWS", "false").lower() == "true"
    if not use_zenrows:
        return html

    print(f"Playwright insufficiente per {target_url}. Attivazione fallback ZenRows...")
    zenrows_key = os.environ.get("ZENROWS_KEY", "").strip()
    if not zenrows_key:
        return html

    params = {
        "apikey": zenrows_key,
        "url": target_url,
        "js_render": "true"
    }

    try:
        response = requests.get("https://api.zenrows.com/v1/", params=params, timeout=TIMEOUT)
        response.raise_for_status()
        return response.text
    except Exception as e:
        print(f"Errore fallback ZenRows per {target_url}: {e}", file=sys.stderr)
        return html


def analyze_with_heuristics(text_content: str, url: str) -> JobListing | None:
    """Fallback locale (Regex/Keyword) quando Gemini non è disponibile o va in errore."""
    text_lower = text_content.lower()

    keywords_job = ["fisioterapista", "fisioterapia", "riabilitazione", "riabilitativo"]
   # Sostituisci keywords_neg con parole meno generiche
    keywords_neg = ["cerco lavoro come", "offro ripetizioni", "badante", "pulizie", "colf", "cerco impiego"]

    if not any(k in text_lower for k in keywords_job):
        return None
    if any(k in text_lower for k in keywords_neg):
        return None

    title_match = re.search(r"(cercasi|selezioniamo|offriamo|ricerca|opportunità)\s+([^\n.]+)", text_content, re.IGNORECASE)
    title = title_match.group(0).strip()[:60] if title_match else "Fisioterapista (Estratto da Filtro Locale)"

    is_adi = "Sì" if any(k in text_lower for k in ["adi", "domiciliare", "assistenza a domicilio"]) else "No"
    albo = "Richiesta" if any(k in text_lower for k in ["albo", "tsrm", "pstrp", "iscrizione"]) else ND

    parsed_url = urlparse(url)
    source = parsed_url.netloc.replace("www.", "")

    return JobListing(
        title=title,
        company="Azienda/Studio (Analisi Locale)",
        source=source,
        location="Roma / Provincia",
        published_date=ND,
        piva_required=ND,
        employment_time=ND,
        contract_duration=ND,
        deadline=ND,
        company_type=ND,
        is_adi=is_adi,
        salary=ND,
        experience_required=ND,
        albo_required=albo,
        url=url
    )


def analyze_with_gemini(text_content: str, url: str) -> JobListing | None:
    """Analisi tramite Gemini con fallback al parser locale se la quota o il servizio falliscono."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY non trovata. Uso analisi locale...", file=sys.stderr)
        return analyze_with_heuristics(text_content, url)

    client = genai.Client(api_key=api_key)

    prompt = f"""
    Analizza il seguente testo estratto da una pagina web o annuncio di lavoro.

    Testo:
    {text_content[:3500]}

    PASSAGGIO 1 (Filtro):
    Verifica se questo testo rappresenta un'OFFERTA DI LAVORO / SELEZIONE / CONCORSO reale per FISIOTERAPISTA a Roma o provincia.
    Imposta "is_job_offer": false se si tratta di articoli, blog, annunci di pulizie, badanti o figure non pertinenti.

    PASSAGGIO 2 (Estrazione):
    Se È una vera offerta per fisioterapista, imposta "is_job_offer": true ed estrai i dati.
    Non inventare nulla: se un parametro manca, inserisci "n.d.".

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

    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
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
            err_msg = str(e)
            if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                print("   [RATE LIMIT Gemini]: Quota superata. Attivazione fallback euristico locale...", file=sys.stderr)
                return analyze_with_heuristics(text_content, url)
            elif "503" in err_msg and attempt < 2:
                time.sleep(3)
                continue

            print(f"Errore Gemini per {url}: {e}. Uso analisi locale...", file=sys.stderr)
            return analyze_with_heuristics(text_content, url)

    return analyze_with_heuristics(text_content, url)


def extract_job_urls(html_content: str, base_url: str) -> list[tuple[str, str]]:
    """Estrae e filtra i link degli annunci."""
    soup = BeautifulSoup(html_content, "html.parser")
    candidates = []

    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        href = a["href"]

        if href.startswith("/"):
            parsed = urlparse(base_url)
            href = f"{parsed.scheme}://{parsed.netloc}{href}"

        href_lower = href.lower()
        text_lower = text.lower()
    if "bakeca.it" in base_url:
    if any(k in href_lower for k in ["/dettaglio/", "/offerta/", "fisioterap"]) or "fisioterap" in text_lower:
        candidates.append((text, href))
    
        elif "subito.it" in base_url:
            if "/offerte-lavoro/" in href_lower and href_lower.endswith(".htm"):
                if any(k in href_lower for k in ["fisioterap", "riabilitaz", "sanitar", "studio", "clinica", "assistenza"]):
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
    """Salvataggio atomico su file temporaneo per evitare corruzioni."""
    temp_file = STATE_FILE.with_suffix(".tmp")
    temp_file.write_text(
        json.dumps({"seen_urls": sorted(urls)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_file.replace(STATE_FILE)


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
    html_content = build_email_html(new_listings)

    # 1. Salvataggio anteprima locale
    preview_path = Path(__file__).parent / "preview.html"
    preview_path.write_text(html_content, encoding="utf-8")
    print(f"\n📄 Anteprima HTML salvata in: {preview_path.resolve()}")

    # 2. Stampa a terminale
    print("\n--- RISULTATI TROVATI ---")
    for idx, job in enumerate(new_listings, 1):
        print(f"[{idx}] {job.title}")
        print(f"    Azienda: {job.company} | Sede: {job.location}")
        print(f"    P.IVA: {job.piva_required} | ADI: {job.is_adi}")
        print(f"    URL: {job.url}\n")

    # 3. Invio email se le credenziali sono configurate
    gmail_user = os.environ.get("GMAIL_USER") or os.environ.get("EMAIL_USER")
    gmail_app_password = os.environ.get("GMAIL_APP_PASSWORD") or os.environ.get("EMAIL_PASS")
    email_to = os.environ.get("EMAIL_TO", gmail_user)

    if not gmail_user or not gmail_app_password:
        print("ℹ️ Credenziali Gmail assenti: invio e-mail saltato (modalità test).")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = gmail_user
    msg["To"] = email_to
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_app_password)
            server.sendmail(gmail_user, [email_to], msg.as_string())
        print(f"E-mail inviata con successo a {email_to}!")
    except Exception as e:
        print(f"Errore durante l'invio dell'e-mail: {e}", file=sys.stderr)


def main() -> int:
    print(f"Avvio ricerca annunci tramite Strategia Ibrida (Playwright + {GEMINI_MODEL}/Regex)...")
    seen_urls = load_seen_urls()
    is_first_run = len(seen_urls) == 0

    target_urls = [
        "https://www.bakeca.it/offerte-lavoro/roma/?q=fisioterapista",
        "https://www.subito.it/annunci-lazio/vendita/offerte-lavoro/roma/?q=fisioterapista"
    ]

    all_listings = []

    for target in target_urls:
        print(f"\nScansione portale: {target}")
        html = fetch_page(target)
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

        for text, url in valid_candidates[:5]:
        # Dentro il ciclo for text, url in valid_candidates[:5]:
        print("   Analisi annuncio in corso...")
        time.sleep(4)  # Mantiene le richieste sotto il limite RPM di Gemini
        listing = analyze_with_gemini(page_text, url)
            print(f"\n  Scarico dettagli per: {text[:40]}... -> {url}")
            detail_html = fetch_page(url)

            if not detail_html:
                print("   [ERRORE]: Impossibile scaricare la pagina dell'annuncio.")
                continue

            soup = BeautifulSoup(detail_html, "html.parser")
            for element in soup(["script", "style", "nav", "footer", "header"]):
                element.decompose()

            page_text = soup.get_text(separator=" ", strip=True)

            time.sleep(3)

            print("   Analisi annuncio in corso...")
            listing = analyze_with_gemini(page_text, url)

            if listing:
                print(f"   [OFFERTA CONFERMATA]: {listing.title} ({listing.company})")
                all_listings.append(listing)
            else:
                print("   [SCARTATA]: Non è un'offerta di lavoro valida.")

    print(f"\nTotale nuove offerte valide trovate: {len(all_listings)}")

    if all_listings:
        preview_path = Path(__file__).parent / "preview.html"
        preview_path.write_text(build_email_html(all_listings), encoding="utf-8")
        print(f"\n📄 Anteprima generata con successo: {preview_path.resolve()}")

    if is_first_run:
        send_baseline_email = os.environ.get("SEND_BASELINE_EMAIL", "false").lower() == "true"
        if send_baseline_email and all_listings:
            print(f"Primo avvio (Test): invio e-mail con i {len(all_listings)} annunci trovati.")
            send_email(f"🧪 TEST Strategia Ibrida — Baseline: {len(all_listings)} annunci", all_listings)
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
