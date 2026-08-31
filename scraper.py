#!/usr/bin/env python3
"""
Job alert per "fisioterapista" a Roma e provincia.

Cerca nuovi annunci, li arricchisce con i dettagli utili (P.IVA, tipo
contratto, full/part time, scadenza, sede, tipo di società, ADI o meno,
retribuzione, esperienza richiesta, iscrizione albo) e invia un'email
di riepilogo in formato tabellare — solo se ci sono novità.

Pensato per girare una volta al giorno tramite GitHub Actions,
ma funziona identico in locale con `python scraper.py`.
"""

import json
import os
import re
import smtplib
import sys
import time
from dataclasses import dataclass, field
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

STATE_FILE = Path(__file__).parent / "state.json"
TIMEOUT = 20
REQUEST_DELAY = 1.5  # secondi tra una richiesta di dettaglio e l'altra, per non martellare il sito
ND = "n.d."  # valore mostrato quando un'informazione non è stata trovata nell'annuncio

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "max-age=0",
    "Upgrade-Insecure-Requests": "1",
}

# =============================================================================
# FONTI DI RICERCA — configurazione esplicita
#
# Ogni fonte è: nome visualizzato, funzione di scraping, e un flag "enabled".
# Per FORZARE l'inclusione di una fonte già presente ma disattivata: metti
# enabled=True (dopo averla testata, vedi nota su Jobeka più sotto).
# Per aggiungere un sito nuovo: scrivi una funzione fetch_<nome>() più in
# basso nel file (segui lo schema di fetch_bakeca) e aggiungila qui.
#
# Ad ogni esecuzione, lo script stampa nei log quali fonti sono attive e
# quanti annunci ha trovato ciascuna — controllalo nella tab "Actions" di
# GitHub per verificare che tutto funzioni come atteso.
# =============================================================================

SOURCES_CONFIG = [
    {"name": "Bakeca", "fetch": "fetch_bakeca", "enabled": True},
    {"name": "Lavoro.it", "fetch": "fetch_lavoro_it", "enabled": True},
    {
        "name": "Jobeka",
        "fetch": "fetch_jobeka",
        "enabled": False,  # <-- disattivata: selettori scritti "a stima", da verificare
                            #     prima di attivare (vedi commento nella funzione).
    },
    # Indeed volutamente NON incluso: blocca lo scraping automatico in modo
    # aggressivo (richiede soluzioni a pagamento per essere aggirato in modo
    # affidabile), incompatibile con l'obiettivo "costo minimo".
]


@dataclass
class JobListing:
    source: str
    title: str
    url: str
    company: str = ""
    location: str = ""
    published_date: str = ""

    # Campi arricchiti dalla pagina di dettaglio (compilati da enrich_listing)
    piva_required: str = ND          # "Richiesta" / "Non richiesta (dipendente)" / "n.d."
    employment_time: str = ND        # "Full-time" / "Part-time" / "n.d."
    contract_duration: str = ND      # "Indeterminato" / "Determinato" / "Libera professione" / "Stage/Tirocinio" / "n.d."
    deadline: str = ND
    company_type: str = ND          # "Cooperativa" / "Azienda/Società" / "Studio privato" / "Pubblico/SSN" / "n.d."
    is_adi: str = "No"              # "Sì" / "No"
    salary: str = ND
    experience_required: str = ND
    albo_required: str = ND         # "Richiesta" / "n.d."

    def key(self) -> str:
        return self.url


# ---------------------------------------------------------------------------
# Sorgenti: scraping delle pagine di ricerca (titolo, azienda, link, data)
# ---------------------------------------------------------------------------

def fetch_bakeca() -> list[JobListing]:
    url = "https://roma.bakeca.it/annunci/medicina-salute-assistenza/?keyword=fisioterapista"
    session = requests.Session()
    session.headers.update(HEADERS)
    resp = session.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    # ... resto del codice invariato


    soup = BeautifulSoup(resp.text, "lxml")

    results: list[JobListing] = []
    seen_urls = set()

    for a in soup.select('a[href*="/dettaglio/"]'):
        href = a.get("href", "")
        if not href or "?click=open-form" in href:
            continue
        if href in seen_urls:
            continue
        seen_urls.add(href)

        title = a.get_text(strip=True)
        if not title:
            continue

        company_match = re.search(r"Azienda:\s*(.+)$", title)
        company = company_match.group(1).strip() if company_match else ""
        title_clean = re.split(r"Azienda:", title)[0]
        half = len(title_clean) // 2
        if title_clean[:half] == title_clean[half:]:
            title_clean = title_clean[:half]

        # Prova a recuperare data e località dal contenitore genitore dell'annuncio
        container = a.find_parent("li") or a.find_parent("article") or a.parent
        pub_date, location = ND, ""
        if container:
            container_text = container.get_text(" ", strip=True)
            date_match = re.search(r"\b(\d{2}/\d{2}/\d{4})\b", container_text)
            if date_match:
                pub_date = date_match.group(1)
            bold = container.find("b") or container.find("strong")
            if bold:
                location = bold.get_text(strip=True)

        results.append(
            JobListing(
                source="Bakeca",
                title=title_clean.strip()[:200],
                url=href if href.startswith("http") else f"https://roma.bakeca.it{href}",
                company=company,
                location=location,
                published_date=pub_date,
            )
        )

    return results


def fetch_lavoro_it() -> list[JobListing]:
    url = "https://www.lavoro.it/roma/fisioterapista/"
    session = requests.Session()
    session.headers.update(HEADERS)
    resp = session.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    # ... resto del codice invariato
    
    soup = BeautifulSoup(resp.text, "lxml")

    results: list[JobListing] = []
    for a in soup.select('a[href*="/annuncio/"], a[href*="/offerta/"]'):
        href = a.get("href", "")
        title = a.get_text(strip=True)
        if not href or not title or len(title) < 5:
            continue
        results.append(
            JobListing(
                source="Lavoro.it",
                title=title[:200],
                url=href if href.startswith("http") else f"https://www.lavoro.it{href}",
            )
        )
    return results


def fetch_jobeka() -> list[JobListing]:
    """
    ESEMPIO DI FONTE NON ANCORA VERIFICATA.

    Selettori scritti sulla base della struttura tipica del sito ma senza
    accesso diretto per testarli. Prima di mettere enabled=True in
    SOURCES_CONFIG:
      1. esegui questa funzione da sola in locale (`python -c "from scraper
         import fetch_jobeka; print(fetch_jobeka())"`)
      2. se ritorna una lista vuota o dà errore, apri la pagina nel browser,
         ispeziona l'HTML reale e correggi il selettore CSS qui sotto
    """
    url = "https://it.jobeka.com/lavoro-fisioterapisti-roma"
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    results: list[JobListing] = []
    for a in soup.select('a[href*="/offerte-di-lavoro/"], a.job-title, a[href*="/annuncio/"]'):
        href = a.get("href", "")
        title = a.get_text(strip=True)
        if not href or not title or len(title) < 5:
            continue
        results.append(
            JobListing(
                source="Jobeka",
                title=title[:200],
                url=href if href.startswith("http") else f"https://it.jobeka.com{href}",
            )
        )
    return results


# Mappa nome funzione -> funzione reale, usata da SOURCES_CONFIG sopra
_FETCHERS = {
    "fetch_bakeca": fetch_bakeca,
    "fetch_lavoro_it": fetch_lavoro_it,
    "fetch_jobeka": fetch_jobeka,
}

# Lista effettiva delle fonti attive, calcolata da SOURCES_CONFIG
SOURCES = [
    (src["name"], _FETCHERS[src["fetch"]])
    for src in SOURCES_CONFIG
    if src["enabled"]
]


# ---------------------------------------------------------------------------
# Arricchimento: apre la pagina di dettaglio e ne estrae le info utili
# ---------------------------------------------------------------------------

def _search(pattern: str, text: str, flags=re.IGNORECASE) -> Optional[re.Match]:
    return re.search(pattern, text, flags)


def enrich_listing(job: JobListing) -> None:
    """Scarica la pagina di dettaglio e compila i campi euristici di `job` in place."""
    try:
        resp = requests.get(job.url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
    except Exception as exc:
        print(f"  [!] impossibile aprire il dettaglio di {job.url}: {exc}", file=sys.stderr)
        return

    soup = BeautifulSoup(resp.text, "lxml")
    text = soup.get_text(" ", strip=True)
    text_lower = text.lower()
    haystack = f"{job.title} {text}".lower()

    # --- ADI (assistenza domiciliare integrata) ---
    if re.search(r"\badi\b", haystack) or "assistenza domiciliare integrata" in haystack:
        job.is_adi = "Sì"

    # --- P.IVA richiesta / dipendente ---
    if re.search(r"partita\s*iva|p\.\s*iva|libera professione|libero professionista", text_lower):
        job.piva_required = "Richiesta"
    elif re.search(r"tempo indeterminato|tempo determinato|contratto (di lavoro )?subordinato|assunzione diretta|ccnl", text_lower):
        job.piva_required = "Non richiesta (dipendente)"

    # --- Full-time / part-time ---
    ft = re.search(r"full\s*time|tempo pieno", text_lower)
    pt = re.search(r"part\s*time|tempo parziale", text_lower)
    if ft and pt:
        job.employment_time = "Full-time / Part-time (flessibile)"
    elif ft:
        job.employment_time = "Full-time"
    elif pt:
        job.employment_time = "Part-time"

    # --- Tipo di contratto / durata ---
    if re.search(r"tempo indeterminato", text_lower):
        job.contract_duration = "Indeterminato"
    elif re.search(r"tempo determinato", text_lower):
        job.contract_duration = "Determinato"
    elif re.search(r"stage|tirocinio", text_lower):
        job.contract_duration = "Stage/Tirocinio"
    elif re.search(r"libera professione|libero professionista|collaborazione|partita\s*iva", text_lower):
        job.contract_duration = "Libera professione/P.IVA"

    # --- Scadenza candidatura ---
    deadline_match = _search(
        r"(?:scadenza|entro il|candidature? entro il|si prega di candidarsi entro)\D{0,10}(\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4})",
        text,
    )
    if deadline_match:
        job.deadline = deadline_match.group(1)

    # --- Retribuzione ---
    salary_match = _search(
        r"(?:€\s?\d[\d.,]*(?:\s?/\s?(?:ora|h|mese|anno))?|\d[\d.,]*\s?€(?:\s?/\s?(?:ora|h|mese|anno))?)",
        text,
    )
    if salary_match:
        job.salary = salary_match.group(0).strip()
    elif re.search(r"retribuzione (commisurata|da definire|in base)", text_lower):
        job.salary = "Commisurata all'esperienza (importo non indicato)"

    # --- Tipo di società ---
    combined = f"{job.company} {text}".lower()
    if re.search(r"cooperativa|coop\.", combined):
        job.company_type = "Cooperativa"
    elif re.search(r"\basl\b|ssn|servizio sanitario|struttura pubblica", combined):
        job.company_type = "Pubblico/SSN"
    elif re.search(r"studio (fisioterapico|medico|associato)", combined):
        job.company_type = "Studio privato/associato"
    elif re.search(r"\bs\.?r\.?l\.?\b|\bs\.?p\.?a\.?\b|\bsrls\b", combined):
        job.company_type = "Azienda/Società"

    # --- Esperienza richiesta ---
    exp_match = _search(r"esperienza\s*(?:minima\s*)?(?:di\s*)?(\d+)\s*ann", text_lower)
    if exp_match:
        job.experience_required = f"{exp_match.group(1)} anni"
    elif re.search(r"no esperienza|anche prima esperienza|neolaureat", text_lower):
        job.experience_required = "Non richiesta / anche neolaureati"

    # --- Iscrizione Albo ---
    if re.search(r"iscrizion[ei] all'?albo|iscritt[oa] all'?albo|albo dei fisioterapisti|albo tsrm|pstrp", text_lower):
        job.albo_required = "Richiesta"

    # --- Data pubblicazione (fallback se non trovata in pagina di ricerca) ---
    if job.published_date == ND:
        date_match = re.search(r"pubblicat[oa]?\D{0,10}(\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4})", text_lower)
        if date_match:
            job.published_date = date_match.group(1)


# ---------------------------------------------------------------------------
# Stato / deduplica
# ---------------------------------------------------------------------------

def load_seen_urls() -> set:
    if STATE_FILE.exists():
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return set(data.get("seen_urls", []))
    return set()


def save_seen_urls(urls: set) -> None:
    STATE_FILE.write_text(
        json.dumps({"seen_urls": sorted(urls)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Email — versione HTML (tabella "a colpo d'occhio") + fallback testo semplice
# ---------------------------------------------------------------------------

def build_email_html(new_listings: list[JobListing]) -> str:
    def esc(s: str) -> str:
        return (s or ND).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    rows = []
    for job in new_listings:
        rows.append(f"""
        <tr>
          <td style="padding:8px;border:1px solid #ddd;"><a href="{esc(job.url)}">{esc(job.title)}</a><br>
              <span style="color:#666;font-size:12px;">{esc(job.source)}</span></td>
          <td style="padding:8px;border:1px solid #ddd;">{esc(job.company)}<br>
              <span style="color:#666;font-size:12px;">{esc(job.company_type)}</span></td>
          <td style="padding:8px;border:1px solid #ddd;text-align:center;">{esc(job.is_adi)}</td>
          <td style="padding:8px;border:1px solid #ddd;">{esc(job.piva_required)}</td>
          <td style="padding:8px;border:1px solid #ddd;">{esc(job.contract_duration)}</td>
          <td style="padding:8px;border:1px solid #ddd;">{esc(job.employment_time)}</td>
          <td style="padding:8px;border:1px solid #ddd;">{esc(job.location)}</td>
          <td style="padding:8px;border:1px solid #ddd;">{esc(job.published_date)}</td>
          <td style="padding:8px;border:1px solid #ddd;">{esc(job.deadline)}</td>
          <td style="padding:8px;border:1px solid #ddd;">{esc(job.salary)}</td>
          <td style="padding:8px;border:1px solid #ddd;">{esc(job.experience_required)}</td>
          <td style="padding:8px;border:1px solid #ddd;">{esc(job.albo_required)}</td>
        </tr>""")

    headers = [
        "Annuncio", "Azienda / Tipo", "ADI", "P.IVA", "Contratto",
        "Orario", "Sede", "Pubblicato", "Scadenza", "Retribuzione",
        "Esperienza", "Albo",
    ]
    header_html = "".join(
        f'<th style="padding:8px;border:1px solid #ddd;background:#2c3e50;color:#fff;text-align:left;">{h}</th>'
        for h in headers
    )

    return f"""
    <html><body style="font-family:Arial,Helvetica,sans-serif;">
      <p>Trovati <b>{len(new_listings)}</b> nuovi annunci per "fisioterapista" (Roma e provincia).
      Le informazioni mancanti sono segnate come "n.d." perché l'annuncio non le specifica.</p>
      <table style="border-collapse:collapse;width:100%;font-size:13px;">
        <tr>{header_html}</tr>
        {"".join(rows)}
      </table>
      <p style="color:#888;font-size:12px;margin-top:16px;">Alert automatico generato da GitHub Actions.</p>
    </body></html>
    """


def build_email_text(new_listings: list[JobListing]) -> str:
    lines = [f"Trovati {len(new_listings)} nuovi annunci per 'fisioterapista' (Roma e provincia):", ""]
    for job in new_listings:
        lines += [
            f"• [{job.source}] {job.title}",
            f"  Azienda: {job.company or ND} ({job.company_type}) | ADI: {job.is_adi}",
            f"  P.IVA: {job.piva_required} | Contratto: {job.contract_duration} | Orario: {job.employment_time}",
            f"  Sede: {job.location or ND} | Pubblicato: {job.published_date} | Scadenza: {job.deadline}",
            f"  Retribuzione: {job.salary} | Esperienza: {job.experience_required} | Albo: {job.albo_required}",
            f"  Link: {job.url}",
            "",
        ]
    lines.append("--- Alert automatico generato da GitHub Actions.")
    return "\n".join(lines)


def send_email(subject: str, new_listings: list[JobListing]) -> None:
    gmail_user = os.environ["GMAIL_USER"]
    gmail_app_password = os.environ["GMAIL_APP_PASSWORD"]
    email_to = os.environ.get("EMAIL_TO", gmail_user)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = gmail_user
    msg["To"] = email_to
    msg.attach(MIMEText(build_email_text(new_listings), "plain", "utf-8"))
    msg.attach(MIMEText(build_email_html(new_listings), "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_user, gmail_app_password)
        server.sendmail(gmail_user, [email_to], msg.as_string())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    active = [s["name"] for s in SOURCES_CONFIG if s["enabled"]]
    inactive = [s["name"] for s in SOURCES_CONFIG if not s["enabled"]]
    print(f"Fonti attive: {', '.join(active) if active else '(nessuna!)'}")
    if inactive:
        print(f"Fonti disponibili ma disattivate: {', '.join(inactive)}")

    seen_urls = load_seen_urls()
    # "Primo giro" = non abbiamo ancora nessun URL registrato, sia perché
    # state.json non esiste ancora sia perché esiste ma è vuoto (es. appena
    # clonato dal repository template).
    is_first_run = len(seen_urls) == 0

    all_listings: list[JobListing] = []
    for name, fetch_fn in SOURCES:
        try:
            listings = fetch_fn()
            print(f"[{name}] trovati {len(listings)} annunci")
            all_listings.extend(listings)
        except Exception as exc:
            print(f"[{name}] errore durante lo scraping: {exc}", file=sys.stderr)

    if is_first_run:
        send_baseline_email = os.environ.get("SEND_BASELINE_EMAIL", "false").lower() == "true"

        if send_baseline_email and all_listings:
            print(
                f"Prima esecuzione con test attivo: invio comunque un'email con "
                f"tutti i {len(all_listings)} annunci di baseline (per verificare il formato)."
            )
            for job in all_listings:
                enrich_listing(job)
                time.sleep(REQUEST_DELAY)
            subject = f"🧪 TEST — Baseline: {len(all_listings)} annunci esistenti al primo avvio"
            send_email(subject, all_listings)
            print("Email di test inviata.")
        else:
            # Comportamento di default: registriamo tutti gli annunci trovati
            # come "baseline" SENZA inviare email. Altrimenti riceveresti in
            # un colpo solo tutti gli annunci già esistenti da settimane/mesi,
            # non le vere novità.
            print(
                f"Prima esecuzione: registro {len(all_listings)} annunci come baseline. "
                "Nessuna email inviata (verrà inviata solo per i prossimi annunci nuovi)."
            )

        save_seen_urls({j.key() for j in all_listings})
        return 0

    new_listings = [j for j in all_listings if j.key() not in seen_urls]

    if new_listings:
        print(f"Nuovi annunci trovati: {len(new_listings)}. Recupero i dettagli...")
        for job in new_listings:
            enrich_listing(job)
            time.sleep(REQUEST_DELAY)  # cortesia verso il sito, evita di sembrare un bot aggressivo

        subject = f"🔎 {len(new_listings)} nuove offerte per fisioterapista (Roma)"
        send_email(subject, new_listings)
        print("Email inviata.")
    else:
        print("Nessun nuovo annuncio rispetto all'ultima esecuzione.")

    updated_seen = seen_urls | {j.key() for j in all_listings}
    save_seen_urls(updated_seen)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
