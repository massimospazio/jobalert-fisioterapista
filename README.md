# Job Alert – Fisioterapista Roma e provincia

Controlla ogni giorno automaticamente Bakeca e Lavoro.it per nuovi annunci
"fisioterapista" a Roma e provincia, e ti manda un'email di riepilogo **solo
quando trova novità**. Costo: €0/mese.

## Come funziona

1. Ogni giorno GitHub esegue `scraper.py` (gratis, GitHub Actions).
2. Lo script cerca gli annunci sulle fonti configurate.
3. Confronta i link trovati con quelli già visti (salvati in `state.json`).
4. Se ci sono annunci nuovi, invia un'email con titolo, azienda e link diretto.
5. Aggiorna `state.json` così domani non ricevi gli stessi annunci due volte.

## Setup (10 minuti, una tantum)

### 1. Crea un repository GitHub

- Vai su [github.com/new](https://github.com/new), crea un repo (può essere
  **privato**, va bene lo stesso — resta comunque gratuito).
- Carica tutti i file di questa cartella nel repo (puoi trascinarli
  dall'interfaccia web di GitHub, oppure via `git push` se hai già Git).

### 2. Crea una "Password per le app" di Gmail

Gmail non permette di usare la password normale per l'invio via script.
Serve una password dedicata:

1. Vai su [myaccount.google.com/security](https://myaccount.google.com/security)
2. Attiva la **verifica in due passaggi** se non è già attiva (obbligatoria
   per il passo successivo)
3. Cerca "Password per le app" (o vai direttamente su
   [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords))
4. Crea una nuova password per l'app, dai un nome tipo "Job Alert"
5. Copia la password di 16 caratteri che ti viene mostrata (la vedrai una
   sola volta)

### 3. Configura i "Secrets" nel repository GitHub

Nel tuo repo su GitHub: **Settings → Secrets and variables → Actions →
New repository secret**. Crea questi tre secret:

| Nome | Valore |
|---|---|
| `GMAIL_USER` | il tuo indirizzo Gmail, es. `tuonome@gmail.com` |
| `GMAIL_APP_PASSWORD` | la password a 16 caratteri del punto 2 (senza spazi) |
| `EMAIL_TO` | l'indirizzo dove vuoi ricevere gli alert (può essere uguale a GMAIL_USER) |

### 4. Attiva e testa il workflow

- Vai nella tab **Actions** del repo, seleziona "Job Alert Fisioterapista"
- Clicca **Run workflow**. Vedrai un'opzione **"Invia comunque un'email di
  test con tutti gli annunci trovati"** (checkbox, spuntala per il test)
  - **Spuntata**: se è il primo avvio in assoluto, ricevi subito un'email
    con tutti gli annunci di baseline attualmente esistenti (utile per
    controllare che il formato/tabella sia quello che ti aspetti). Il
    soggetto dell'email sarà preceduto da "🧪 TEST"
  - **Non spuntata (default)**: comportamento normale, nessuna email al
    primo giro (vedi sotto)
  - Nota: questa opzione ha effetto **solo** al primissimo avvio (quando
    `state.json` è vuoto o assente). Nelle esecuzioni successive viene
    ignorata: la logica di deduplica prende sempre il sopravvento, così non
    rischi di romperla per errore rilanciando il workflow con la casella
    spuntata
- Se non spunti il test: alla **prima esecuzione** non riceverai email — lo
  script registra tutti gli annunci trovati in quel momento come "baseline"
  (li considera già noti), così da non mandarti in un colpo solo 15-20
  annunci già esistenti da settimane
- Verifica che dopo questa run sia comparso/aggiornato il file `state.json`
  nel repo (commit automatico "Aggiorna elenco annunci visti")
- Dalla **seconda esecuzione in poi** (il giorno dopo, o rilanciandola
  manualmente) riceverai email solo per gli annunci realmente nuovi rispetto
  al baseline

Da qui in avanti gira da solo, una volta al giorno, senza che tu debba fare
nulla. Se in futuro vuoi "resettare" e ripartire da un nuovo baseline (ad
esempio dopo una lunga pausa), basta svuotare il contenuto di `state.json` a
`{"seen_urls": []}` e la prossima esecuzione ripartirà come un primo giro
(a quel punto puoi anche rilanciarla con la casella di test spuntata).

## Fonti configurate

Le fonti sono elencate esplicitamente in cima a `scraper.py`, nel blocco
`SOURCES_CONFIG`:

| Fonte | Stato di default | Note |
|---|---|---|
| Bakeca | ✅ Attiva | copre bene Roma e provincia (Marino, Anzio, Colleferro, Civitavecchia, Velletri...) |
| Lavoro.it | ✅ Attiva | include anche comuni minori (Guidonia, Pomezia) |
| Jobeka | ⛔ Disattivata | selettori non ancora verificati — vedi istruzioni nel codice prima di attivarla |
| Indeed | Non incluso | blocca lo scraping automatico in modo aggressivo; servirebbe un servizio a pagamento per aggirarlo |

**Per forzare l'inclusione di una fonte disattivata** (es. Jobeka): apri
`scraper.py`, trova `SOURCES_CONFIG` e cambia `"enabled": False` in
`"enabled": True` per quella fonte. Testala prima in locale (istruzioni nel
commento della funzione `fetch_jobeka`) perché potrebbe richiedere una
correzione dei selettori CSS.

**Per aggiungere un sito nuovo** (es. Glassdoor, Subito.it):
1. Scrivi una funzione `fetch_nomesito()` seguendo lo schema di
   `fetch_bakeca()` — apre la pagina di ricerca, estrae titolo/link/azienda
   per ogni annuncio
2. Aggiungila al dizionario `_FETCHERS`
3. Aggiungi una riga a `SOURCES_CONFIG` con `"enabled": True`

Ogni esecuzione stampa nei log (tab "Actions" su GitHub) quali fonti sono
attive e quanti annunci ha trovato ciascuna, così sai subito se una fonte
smette di funzionare.

## Personalizzazioni comuni

- **Cambiare l'orario**: modifica la riga `cron: "0 7 * * *"` nel file
  `.github/workflows/job-alert.yml` (l'orario è in UTC).
- **Cambiare parola chiave o città**: modifica l'URL dentro `fetch_bakeca()`
  in `scraper.py` (es. cambia `keyword=fisioterapista` o il sottodominio
  `roma.` con un'altra città).
- **Aggiungere altre fonti**: scrivi una nuova funzione simile a
  `fetch_bakeca()` che ritorna una lista di `JobListing`, e aggiungila alla
  lista `SOURCES` in fondo al file. Indeed è volutamente escluso perché
  blocca aggressivamente lo scraping automatico e richiederebbe soluzioni
  più complesse (e a pagamento) per aggirarlo in modo affidabile.

## Nota su manutenzione

I siti web cambiano struttura di tanto in tanto: se uno scraper smette di
trovare annunci, quasi sempre basta aggiornare i selettori CSS nella
funzione corrispondente. Controlla ogni tanto la tab "Actions" per
assicurarti che le esecuzioni vadano a buon fine (icona verde ✓).

## Alternativa a costo zero e zero manutenzione

Se in futuro preferisci non gestire nemmeno questo, ricorda che Bakeca offre
già un alert email gratuito nativo, attivabile qui:
https://roma.bakeca.it/annunci/medicina-salute-assistenza/?keyword=fisioterapista
(pulsante "Attiva l'alert" in fondo alla pagina risultati).
