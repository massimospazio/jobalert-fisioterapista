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

# Importazione SDK Groq
try:
    from groq import Groq
    HAS_GROQ_SDK = True
except ImportError:
    HAS_GROQ_SDK = False

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
            page.wait_for_timeout(2500)
            content = page.content()
            browser.close()
            return content
    except Exception as e:
        print(f"  [PLAYWRIGHT ERRORE] {url}: {e}")
        return None

def fetch_with_zenrows(url: str) -> Optional[str]:
    zenrows_key = os.getenv("ZENROWS_KEY")
    if not zenrows_key:
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
        
        if "bakeca.it" in base_url:
            if ("/dettaglio/" in href_lower or "/offerta/" in href_lower or "fisioterap" in href_lower or "fisioterap" in text_lower):
                if not href_lower.rstrip("/").endswith("/roma"):
                    candidates.append((text, href))
                    
        elif "subito.it" in base_url:
            if "/offerte-lavoro/" in href_lower and href_lower.endswith(".htm"):
                keywords = ["fisioterap", "riabilitaz", "sanitar", "studio", "clinica", "assistenza"]
                if any(kw in href_lower or kw in text_lower for kw in keywords):
                    candidates.append((text, href))

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
# 3. ANALISI LLM (GROQ / GEMINI / EURISTICA)
# ==========================================

def analyze_with_groq(text_content: str, url: str) -> Optional[JobListing]:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key or not HAS_GROQ_SDK:
        return None

    client = Groq(api_key=api_key)
    model_name = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

    prompt = f"""
Sei un analista esperto di offerte di lavoro.
Analizza il seguente testo ed estrai se si tratta di un'OFFERTA DI LAVORO VALIDA per FISIOTERAPISTA a Roma o provincia.

Istruzioni:
1. Se il testo NON è un'offerta di lavoro (es. candidatura "cerco lavoro", offerta per badante/segretaria), imposta "is_job_offer": false.
2. Se è un'offerta valida per FISIOTERAPISTA, imposta "is_job_offer": true ed estrai i dati.

Rispondi TASSATIVAMENTE in formato JSON con questa struttura:
{{
  "is_job_offer": true|false,
  "title": "Titolo chiaro della posizione",
  "company": "Nome dell'azienda/studio o n.d.",
  "location": "Sede specifica o Roma",
  "published_date": "Data o n.d.",
  "piva_required": "Richiesta / Non richiesta / n.d.",
  "employment_time": "Full-time / Part-time / Flessibile / n.d.",
  "contract_duration": "Determinato / Indeterminato / Libera professione / n.d.",
  "company_type": "Studio privato / Clinica / Cooperativa / RSA / n.d.",
  "is_adi": "Sì / No / n.d.",
  "albo_required": "Richiesta / Non richiesta / n.d."
}}

Testo:
\"\"\"
{text_content[:5000]}
\"\"\"
"""
    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": "Sei un assistente che risponde esclusivamente in formato JSON valido."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.0
        )
        
        res_json = json.loads(response.choices[0].message.content)
        if not res_json.get("is_job_offer", False):
            print("   [SCARTATA GROQ]: Annuncio valutato come non valido.")
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
        print(f"   [ERRORE GROQ]: {e}")
        return None

def analyze_with_gemini(text_content: str, url: str) -> Optional[JobListing]:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key or not HAS_GEMINI_SDK:
        return None

    client = genai.Client(api_key=api_key)
    model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    prompt = f"""
Sei un analista di offerte di lavoro. Determina se si tratta di un'offerta per FISIOTERAPISTA a Roma.
Rispondi in JSON:
{{
  "is_job_offer": true|false,
  "title": "Titolo",
  "company": "Azienda/Studio",
  "location": "Sede",
  "piva_required": "Richiesta / Non richiesta / n.d.",
  "is_adi": "Sì / No / n.d."
}}
Testo:
{text_content[:5000]}
"""
    try:
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.0)
        )
        res_json = json.loads(response.text)
        if not res_json.get("is_job_offer", False):
            print("   [SCARTATA GEMINI]: Annuncio non valido.")
            return None
            
        domain = urlparse(url).netloc.replace("www.", "")
        return JobListing(
            title=res_json.get("title", "n.d."),
            company=res_json.get("company", "n.d."),
            source=domain,
            location=res_json.get("location", "Roma"),
            piva_required=res_json.get("piva_required", "n.d."),
            is_adi=res_json.get("is_adi", "n.d."),
            url=url
        )
    except Exception as e:
        print(f"   [ERRORE GEMINI]: {e}")
        return None

def analyze_with_heuristics(text_content: str, url: str) -> Optional[JobListing]:
    text_lower = text_content.lower()

    neg_patterns = [
        r"\bcerco lavoro come\b",
        r"\boffro servizio\b",
        r"\bbadante\b",
        r"\bpulizie\b"
    ]
    
    for pat in neg_patterns:
        if re.search(pat, text_lower):
            print(f"   [SCARTATA EURISTICA]: Trovato pattern '{pat}'.")
            return None

    if not any(kw in text_lower for kw in ["fisioterapista", "fisioterapia", "riabilitazione"]):
        print("   [SCARTATA EURISTICA]: Nessuna parola chiave valida.")
        return None

    lines = [l.strip() for l in text_content.splitlines() if len(l.strip()) > 5]
    title = lines[0][:77] + "..." if lines and len(lines[0]) > 80 else (lines[0] if lines else "Fisioterapista")

    domain = urlparse(url).netloc.replace("www.", "")
    return JobListing(
        title=title,
        company="n.d.",
        source=domain,
        location="Roma/Provincia",
        published_date="Oggi",
        piva_required="Richiesta" if "p.iva" in text_lower or "partita iva" in text_lower else "n.d.",
        is_adi="Sì" if "adi" in text_lower or "domiciliare" in text_lower else "No",
        url=url
    )

# ==========================================
# 4. REPORT E NOTIFICHE
# ==========================================

def generate_html_report(listings: List[JobListing]) -> str:
    rows_html = "".join([
        f"<tr><td><b><a href='{i.url}' target='_blank'>{i.title}</a></b></td><td>{i.company}</td><td>{i.source}</td><td>{i.location}</td><td>{i.piva_required}</td><td>{i.is_adi}</td></tr>"
        for i in listings
    ])
    return f"""
    <html><body style="font-family: Arial, sans-serif; margin: 20px;">
    <h2> JobAlert: Nuove Offerte Fisioterapista Roma</h2>
    <table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; width: 100%;">
        <tr style="background-color: #f2f2f2;"><th>Titolo</th><th>Azienda</th><th>Fonte</th><th>Sede</th><th>P.IVA</th><th>ADI</th></tr>
        {rows_html}
    </table>
    </body></html>
    """

def send_email_notification(html_content: str, count: int):
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    email_to = os.getenv("EMAIL_TO")

    if not smtp_user or not smtp_password or not email_to:
        print("  [EMAIL] Credenziali non presenti. Invio saltato.")
        return

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f" JobAlert: {count} Nuove Offerte Fisioterapista Roma"
        msg["From"] = smtp_user
        msg["To"] = email_to
        msg.attach(MIMEText(html_content, "html", "utf-8"))

        with smtplib.SMTP(os.getenv("SMTP_SERVER", "smtp.gmail.com"), int(os.getenv("SMTP_PORT", "587"))) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, [email_to], msg.as_string())
        print(f" E-mail inviata con successo a {email_to}")
    except Exception as e:
        print(f" [ERRORE EMAIL]: {e}")

# ==========================================
# 5. MAIN
# ==========================================

def main():
    print("Avvio ricerca annunci tramite Strategia Ibrida (Groq / Gemini / Euristica)...")

    seen_urls = load_state()
    target_urls = [
        "https://www.bakeca.it/offerte-lavoro/roma/?q=fisioterapista",
        "https://www.subito.it/annunci-lazio/vendita/offerte-lavoro/roma/?q=fisioterapista"
    ]

    all_candidate_links = []

    for target_url in target_urls:
        print(f"\nScansione portale: {target_url}")
        html = fetch_page(target_url)
        if not html:
            continue

        extracted = extract_job_urls(target_url, html)
        new_links = [(title, url) for title, url in extracted if url not in seen_urls]
        print(f"Estratti {len(extracted)} link, {len(new_links)} nuovi.")
        all_candidate_links.extend(new_links)

    max_process = int(os.getenv("MAX_PROCESS_PER_RUN", "10"))
    candidates_to_process = all_candidate_links[:max_process]
    valid_listings: List[JobListing] = []

    for idx, (link_title, detail_url) in enumerate(candidates_to_process, start=1):
        print(f"\n[{idx}/{len(candidates_to_process)}] Scarico dettaglio: {detail_url}")
        detail_html = fetch_page(detail_url)
        seen_urls.add(detail_url)

        if not detail_html:
            continue

        cleaned_text = clean_html_content(detail_html)
        
        # Sequenza Fallback: Groq -> Gemini -> Euristica
        listing = analyze_with_groq(cleaned_text, detail_url)
        if not listing:
            listing = analyze_with_gemini(cleaned_text, detail_url)
        if not listing:
            listing = analyze_with_heuristics(cleaned_text, detail_url)

        if listing:
            print(f"   [OFFERTA CONFERMATA]: {listing.title} ({listing.company})")
            valid_listings.append(listing)

        time.sleep(1)  # Pausa breve (Groq supporta fino a 30 RPM)

    print(f"\nTotale nuove offerte valide trovate: {len(valid_listings)}")

    if valid_listings:
        html_report = generate_html_report(valid_listings)
        with open("preview.html", "w", encoding="utf-8") as f:
            f.write(html_report)
        send_email_notification(html_report, len(valid_listings))

    save_state_atomic(seen_urls)

if __name__ == "__main__":
    main()
