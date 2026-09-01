import os
import sys
import json
import time
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dataclasses import dataclass, asdict
from typing import List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# Importazione SDK Google GenAI
try:
    from google import genai
    from google.genai import types
    HAS_GEMINI_SDK = True
except ImportError:
    HAS_GEMINI_SDK = False

# ==========================================
# 1. MODELLO DATI E GESTIONE STATO
# ==========================================

@dataclass
class JobListing:
    title: str = "n.d."
    company: str = "n.d."
    source: str = "n.d."
    location: str = "Roma"
    published_date: str = "n.d."
    piva_required: str = "n.d."
    employment_time: str = "n.d."
    contract_duration: str = "n.d."
    company_type: str = "n.d."
    is_adi: str = "n.d."
    albo_required: str = "n.d."
    url: str = ""

STATE_FILE = "state.json"
STATE_TMP_FILE = "state.tmp"

def load_state() -> set:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data.get("seen_urls", []))
        except Exception as e:
            print(f"[WARN] Impossibile leggere {STATE_FILE}: {e}")
    return set()

def save_state_atomic(seen_urls: set):
    try:
        with open(STATE_TMP_FILE, "w", encoding="utf-8") as f:
            json.dump({"seen_urls": list(seen_urls)}, f, indent=2, ensure_ascii=False)
        os.replace(STATE_TMP_FILE, STATE_FILE)
        print(f" Stato salvato correttamente in {STATE_FILE} ({len(seen_urls)} URL registrati).")
    except Exception as e:
        print(f"[ERRORE] Impossibile salvare lo stato: {e}")

# ==========================================
# 2. SCRAPING WEB (Playwright + ZenRows)
# ==========================================

def fetch_with_playwright(url: str) -> Optional[str]:
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                locale="it-IT",
                viewport={"width": 1280, "height": 800}
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2500)  # Attesa per rendering JS
            content = page.content()
            browser.close()
            return content
    except Exception as e:
        print(f"  [PLAYWRIGHT ERRORE] {url}: {e}")
        return None

def fetch_with_zenrows(url: str) -> Optional[str]:
    zenrows_key = os.getenv("ZENROWS_KEY")
    if not zenrows_key:
        print("  [ZENROWS] Key non presente nelle variabili di ambiente.")
        return None
    try:
        endpoint = "https://api.zenrows.com/v1/"
        params = {
            "api_key": zenrows_key,
            "url": url,
            "js_render": "true",
            "premium_proxy": "true"
        }
        res = requests.get(endpoint, params=params, timeout=30)
        if res.status_code == 200:
            return res.text
        else:
            print(f"  [ZENROWS ERRORE] Status {res.status_code}: {res.text[:200]}")
    except Exception as e:
        print(f"  [ZENROWS ECCEZIONE] {e}")
    return None

def fetch_page(url: str) -> Optional[str]:
    html = fetch_with_playwright(url)
    use_zenrows = os.getenv("USE_ZENROWS", "false").lower() == "true"
    
    is_blocked = False
    if html:
        if len(html) < 3000 or "Cloudflare" in html or "Access Denied" in html:
            is_blocked = True

    if (not html or is_blocked) and use_zenrows:
        print(f"  [FALLBACK] Playwright non sufficiente su {url}. Attivazione ZenRows API...")
        html = fetch_with_zenrows(url)

    return html

def extract_job_urls(base_url: str, html_content: str) -> List[Tuple[str, str]]:
    soup = BeautifulSoup(html_content, "html.parser")
    candidates = []
    
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        text = a_tag.get_text(strip=True)
        
        if href.startswith("/"):
            href = urljoin(base_url, href)
            
        href_lower = href.lower()
        text_lower = text.lower()
        
        # 1. LOGICA BAKECA.IT
        if "bakeca.it" in base_url:
            if ("/dettaglio/" in href_lower or "/offerta/" in href_lower or "fisioterap" in href_lower or "fisioterap" in text_lower):
                if not href_lower.rstrip("/").endswith("/roma"):
                    candidates.append((text, href))
                    
        # 2. LOGICA SUBITO.IT
        elif "subito.it" in base_url:
            if "/offerte-lavoro/" in href_lower and href_lower.endswith(".htm"):
                keywords = ["fisioterap", "riabilitaz", "sanitar", "studio", "clinica", "assistenza"]
                if any(kw in href_lower or kw in text_lower for kw in keywords):
                    candidates.append((text, href))

    # Deduplica mantenendo l'ordine
    seen = set()
    unique_candidates = []
    for text, url in candidates:
        if url not in seen:
            seen.add(url)
            unique_candidates.append((text, url))
            
    return unique_candidates

def clean_html_content(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for element in soup(["script", "style", "nav", "footer", "header", "iframe", "noscript", "svg"]):
        element.decompose()
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)

# ==========================================
# 3. ANALISI LLM GEMINI & FALLBACK EURISTICO
# ==========================================

def analyze_with_gemini(text_content: str, url: str) -> Optional[JobListing]:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key or not HAS_GEMINI_SDK:
        print("  [GEMINI] API Key o SDK non presente. Passo all'euristica.")
        return None

    client = genai.Client(api_key=api_key)
    model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    prompt = f"""
Sei un analista esperto di offerte di lavoro.
Analizza il seguente testo estratto da una pagina web e determina se si tratta di un'OFFERTA DI LAVORO VALIDA per FISIOTERAPISTA a Roma o provincia.

Istruzioni:
1. Se il testo NON è un'offerta di lavoro (es. è una domanda/candidatura "cerco lavoro", un articolo, o riguarda una figura diversa senza riferimento a fisioterapia), imposta "is_job_offer": false.
2. Se si tratta di un'offerta di lavoro per FISIOTERAPISTA (o studio/centro che cerca fisioterapisti), imposta "is_job_offer": true ed estrai i dettagli.
3. Rispondi TASSATIVAMENTE in formato JSON coerente con questa struttura:

{{
  "is_job_offer": true|false,
  "title": "Titolo chiaro della posizione",
  "company": "Nome dell'azienda/studio o n.d.",
  "location": "Sede specifica (es. Roma EUR, Nettuno) o Roma",
  "published_date": "Data o n.d.",
  "piva_required": "Richiesta / Non richiesta / n.d.",
  "employment_time": "Full-time / Part-time / Flessibile / n.d.",
  "contract_duration": "Determinato / Indeterminato / Libera professione / n.d.",
  "company_type": "Studio privato / Clinica / Cooperativa / RSA / n.d.",
  "is_adi": "Sì / No / n.d.",
  "albo_required": "Richiesta / Non richiesta / n.d."
}}

Testo da analizzare:
\"\"\"
{text_content[:6000]}
\"\"\"
"""

    max_retries = 2
    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.0
                )
            )
            
            res_json = json.loads(response.text)
            if not res_json.get("is_job_offer", False):
                print("   [SCARTATA GEMINI]: Annuncio valutato come non valido.")
                return None
            
            domain = urlparse(url).netloc.replace("www.", "")

            return JobListing(
                title=res_json.get("title", "n.d."),
                company=res_json.get("company", "n.d."),
                source=domain,
                location=res_json.get("location", "Roma"),
                published_date=res_json.get("published_date", "n.d."),
                piva_required=res_json.get("piva_required", "n.d."),
                employment_time=res_json.get("employment_time", "n.d."),
                contract_duration=res_json.get("contract_duration", "n.d."),
                company_type=res_json.get("company_type", "n.d."),
                is_adi=res_json.get("is_adi", "n.d."),
                albo_required=res_json.get("albo_required", "n.d."),
                url=url
            )

        except Exception as e:
            err_str = str(e)
            print(f"   [ERRORE DETTAGLIATO GEMINI - Tentativo {attempt}/{max_retries}]: {err_str}")
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                wait_sec = attempt * 5
                print(f"   [RATE LIMIT Gemini]: Quota superata. Attesa {wait_sec}s...")
                time.sleep(wait_sec)
            else:
                break

    print("   [FALLBACK]: Gemini non disponibile. Attivazione euristica locale...")
    return None

def analyze_with_heuristics(text_content: str, url: str) -> Optional[JobListing]:
    text_lower = text_content.lower()

    # Pattern negativi (candidature spontanee o altre figure)
    neg_patterns = [
        r"\bcerco lavoro come\b",
        r"\boffro servizio\b",
        r"\boffro ripetizioni\b",
        r"\bbadante\b",
        r"\bpulizie\b",
        r"\bcolf\b"
    ]
    
    for pat in neg_patterns:
        if re.search(pat, text_lower):
            print(f"   [SCARTATA EURISTICA]: Trovato pattern negativo '{pat}'.")
            return None

    # Verifica parole chiave
    keywords_job = ["fisioterapista", "fisioterapia", "riabilitazione", "riabilitativo"]
    if not any(kw in text_lower for kw in keywords_job):
        print("   [SCARTATA EURISTICA]: Nessuna parola chiave di fisioterapia trovata.")
        return None

    # Esclusione mansioni puramente amministrative
    if "segretaria" in text_lower and "fisioterapista" not in text_lower:
        print("   [SCARTATA EURISTICA]: Offerta per sola segreteria.")
        return None

    # Estrazione titolo dalle prime righe
    lines = [l.strip() for l in text_content.splitlines() if len(l.strip()) > 5]
    title = lines[0] if lines else "Fisioterapista"
    if len(title) > 80:
        title = title[:77] + "..."

    piva_req = "Richiesta" if ("p.iva" in text_lower or "partita iva" in text_lower) else "n.d."
    is_adi = "Sì" if ("adi" in text_lower or "domiciliare" in text_lower) else "No"
    albo_req = "Richiesta" if ("albo" in text_lower or "tsrm" in text_lower) else "n.d."

    domain = urlparse(url).netloc.replace("www.", "")

    return JobListing(
        title=title,
        company="n.d.",
        source=domain,
        location="Roma/Provincia",
        published_date="Oggi",
        piva_required=piva_req,
        employment_time="n.d.",
        contract_duration="n.d.",
        company_type="n.d.",
        is_adi=is_adi,
        albo_required=albo_req,
        url=url
    )

# ==========================================
# 4. GENERAZIONE REPORT & NOTIFICHE
# ==========================================

def generate_html_report(listings: List[JobListing]) -> str:
    rows_html = ""
    for item in listings:
        rows_html += f"""
        <tr>
            <td><b><a href="{item.url}" target="_blank">{item.title}</a></b></td>
            <td>{item.company}</td>
            <td>{item.source}</td>
            <td>{item.location}</td>
            <td>{item.piva_required}</td>
            <td>{item.is_adi}</td>
            <td>{item.employment_time}</td>
        </tr>
        """

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; color: #333; }}
            h2 {{ color: #2c3e50; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 15px; }}
            th, td {{ border: 1px solid #ddd; padding: 10px; text-align: left; }}
            th {{ background-color: #f4f6f7; }}
            tr:nth-child(even) {{ background-color: #f9f9f9; }}
            a {{ color: #2980b9; text-decoration: none; }}
        </style>
    </head>
    <body>
        <h2> Report Nuove Offerte Fisioterapista Roma</h2>
        <p>Trovate <b>{len(listings)}</b> nuove offerte pertinenti:</p>
        <table>
            <thead>
                <tr>
                    <th>Titolo Offerta</th>
                    <th>Azienda/Studio</th>
                    <th>Fonte</th>
                    <th>Sede</th>
                    <th>P.IVA</th>
                    <th>ADI</th>
                    <th>Orario</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
            </tbody>
        </table>
    </body>
    </html>
    """

def send_email_notification(html_content: str, count: int):
    smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    email_to = os.getenv("EMAIL_TO")

    if not smtp_user or not smtp_password or not email_to:
        print("  [EMAIL] Credenziali SMTP non configurate. Notifica email saltata.")
        return

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f" JobAlert: {count} Nuove Offerte Fisioterapista Roma"
        msg["From"] = smtp_user
        msg["To"] = email_to

        msg.attach(MIMEText(html_content, "html", "utf-8"))

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, [email_to], msg.as_string())
        print(f" E-mail inviata con successo a {email_to}")
    except Exception as e:
        print(f" [ERRORE INVIO EMAIL]: {e}")

# ==========================================
# 5. FLUSSO PRINCIPALE (MAIN)
# ==========================================

def main():
    print("Avvio ricerca annunci tramite Strategia Ibrida (Playwright + Gemini/Regex)...")

    seen_urls = load_state()
    target_urls = [
        "https://www.bakeca.it/offerte-lavoro/roma/?q=fisioterapista",
        "https://www.subito.it/annunci-lazio/vendita/offerte-lavoro/roma/?q=fisioterapista"
    ]

    all_candidate_links = []

    # 1. Scansione Listing
    for target_url in target_urls:
        print(f"\nScansione portale: {target_url}")
        html = fetch_page(target_url)
        if not html:
            print(f" Impossibile recuperare {target_url}")
            continue

        extracted = extract_job_urls(target_url, html)
        print(f"Estratti {len(extracted)} link totali dal listing.")

        new_links = [(title, url) for title, url in extracted if url not in seen_urls]
        print(f"Trovati {len(new_links)} link non ancora visti.")
        all_candidate_links.extend(new_links)

    max_process = int(os.getenv("MAX_PROCESS_PER_RUN", "10"))
    candidates_to_process = all_candidate_links[:max_process]

    valid_listings: List[JobListing] = []

    # 2. Dettaglio e Analisi
    for idx, (link_title, detail_url) in enumerate(candidates_to_process, start=1):
        print(f"\n[{idx}/{len(candidates_to_process)}] Scarico dettaglio: {detail_url}")
        detail_html = fetch_page(detail_url)
        
        # Aggiungiamo l'URL allo stato per non riprocessarlo al ciclo successivo
        seen_urls.add(detail_url)

        if not detail_html:
            print("   Impossibile scaricare l'HTML dell'annuncio.")
            continue

        cleaned_text = clean_html_content(detail_html)
        
        # Analisi Gemini
        listing = analyze_with_gemini(cleaned_text, detail_url)
        
        # Fallback Euristico
        if not listing:
            listing = analyze_with_heuristics(cleaned_text, detail_url)

        if listing:
            print(f"   [OFFERTA CONFERMATA]: {listing.title} ({listing.company})")
            valid_listings.append(listing)

        # Pause di 4 secondi per evitare Rate Limit (15 RPM)
        time.sleep(4)

    # 3. Risultati e Notifiche
    print(f"\nTotale nuove offerte valide trovate: {len(valid_listings)}")

    if valid_listings:
        html_report = generate_html_report(valid_listings)
        
        preview_path = os.path.join(os.getcwd(), "preview.html")
        with open(preview_path, "w", encoding="utf-8") as f:
            f.write(html_report)
        print(f" Anteprima HTML salvata in: {preview_path}")

        print("\n--- RISULTATI TROVATI ---")
        for i, item in enumerate(valid_listings, 1):
            print(f"[{i}] {item.title}")
            print(f"    Azienda: {item.company} | Sede: {item.location}")
            print(f"    P.IVA: {item.piva_required} | ADI: {item.is_adi}")
            print(f"    URL: {item.url}\n")

        send_email_notification(html_report, len(valid_listings))
    else:
        print(" Nessun nuovo annuncio valido trovato in questo ciclo.")

    # 4. Salvataggio Atomico dello Stato
    save_state_atomic(seen_urls)

if __name__ == "__main__":
    main()
