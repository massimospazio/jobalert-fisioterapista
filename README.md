# Job Alert Fisioterapista — Roma e provincia

Sistema automatico per cercare, normalizzare, filtrare, deduplicare e ordinare offerte di lavoro per fisioterapista nella provincia di Roma, con riferimento geografico ad **Albano Laziale**.

Stato documentazione: **5 settembre 2026**.

## Obiettivo

Il sistema deve produrre ogni giorno una lista affidabile di opportunità lavorative rilevanti, evitando duplicati e annunci non desiderati, mantenendo memoria degli annunci già visti e notificando solo le nuove opportunità.

Priorità principali:

- provincia di Roma (`RM`);
- distanza da Albano Laziale;
- preferenza per rapporti di lavoro stabili;
- esclusione delle offerte esclusivamente domiciliari/ADI;
- esclusione delle cooperative;
- controllo dei costi delle fonti a pagamento;
- monitoraggio esplicito della qualità di ogni esecuzione.

## Architettura V2

La pipeline principale è:

```text
SOURCES
  ↓
COLLECTOR
  ↓
NORMALIZER
  ↓
FILTER
  ↓
DEDUPLICATOR
  ↓
SCORING
  ↓
STATE
  ↓
REPORT / NOTIFIER
```

Il core è volutamente deterministico. L'uso di un LLM è previsto eventualmente in futuro per attività ad alto valore semantico, come interpretazione di descrizioni ambigue o classificazione di campi difficili, ma non per le funzioni meccaniche di stato, deduplica, scoring e monitoraggio.

## Branch e produzione

- `main`: branch di default. Contiene lo **scheduler di produzione**.
- `v2-core`: branch operativo con il codice della pipeline V2, configurazione, stato, baseline e report HTML.

Lo scheduler è:

```text
.github/workflows/job-alert-production-scheduler.yml
```

È presente su `main` perché GitHub Actions esegue i workflow `schedule` dal branch di default.

Lo scheduler effettua checkout di `v2-core`, esegue la pipeline e persiste nuovamente stato, baseline, report e consumo ZenRows su `v2-core`.

### Orario

```yaml
cron: "0 7 * * *"
```

Quindi il run parte ogni giorno alle **07:00 UTC**:

- 09:00 in Italia con ora legale;
- 08:00 in Italia con ora solare.

È disponibile anche l'avvio manuale tramite `workflow_dispatch`.

## Fonti

### Bakeca

**Stato: core / attiva**

Bakeca è una delle fonti principali. Le richieste dirette da GitHub Actions vengono bloccate, quindi viene utilizzato **ZenRows** con rendering JavaScript e premium proxy.

Costo osservato per una richiesta riuscita:

- `X-Request-Cost`: `0.025`;
- equivalente contabilizzato dal progetto: **25 crediti ZenRows**.

### OFI Lazio

**Stato: core / gratuita**

Raccoglie offerte pubblicate dall'Ordine dei Fisioterapisti del Lazio e analizza anche PDF tramite `pypdf`.

È utile soprattutto per opportunità professionali pubblicate direttamente da strutture sanitarie.

### LinkedIn

**Stato: integrativa / gratuita**

La ricerca pubblica restituisce normalmente le card degli annunci. Le pagine di dettaglio possono invece rispondere con HTTP `429`.

Strategia attuale:

1. raccogliere tutte le card rilevanti;
2. identificare subito le opportunità già note nello state;
3. aprire la pagina di dettaglio **solo per opportunità nuove**;
4. interrompere le richieste di dettaglio quando LinkedIn applica rate limiting;
5. continuare a mantenere titolo, azienda, località e data disponibili dalla card.

Il degrado LinkedIn viene quantificato con:

- candidati totali;
- opportunità nuove;
- dettagli tentati;
- dettagli riusciti;
- opportunità già note saltate;
- nuove opportunità rimaste senza dettaglio.

Impatto del degrado:

- **BASSO**: fino al 10% delle nuove opportunità senza dettaglio;
- **MEDIO**: oltre 10% e fino al 35%;
- **ALTO**: oltre 35%.

Un `429` sui dettagli non implica perdita completa della fonte: la ricerca LinkedIn può continuare a funzionare e fornire i dati base.

### Indeed

**Stato: gap-filler / attiva in produzione**

Indeed blocca le richieste dirette da GitHub Actions con HTTP `403`, quindi viene interrogato via ZenRows.

Indeed viene eseguito **dopo Bakeca, OFI e LinkedIn**. La pipeline fa una prima deduplica delle fonti gratuite/core e usa Indeed come fonte incrementale finale.

URL Indeed stabili vengono ricostruiti con l'identificativo `jk`:

```text
https://it.indeed.com/viewjob?jk=<id>
```

Questo evita il precedente problema che faceva collassare annunci distinti negli stessi URL `/viewjob` o `/rc/clk`.

Nel primo run production integrato Indeed ha aggiunto **11 opportunità uniche prima dei filtri**, confermando un valore incrementale reale rispetto alle altre fonti.

Costo osservato per un run Indeed:

- `X-Request-Cost`: `0.025`;
- **25 crediti ZenRows**.

## Normalizzazione dati

Ogni annuncio viene trasformato nel modello comune con questi campi principali:

```text
source
title
company
location
province
homecare
homecare_only
published_at
application_deadline
contract_type
employment_type
cooperative
salary
piva_required
adi
salary_present
latitude
longitude
url
score
distance_km
opportunity_id
job_id
raw_text
```

La località viene normalizzata sul comune reale quando possibile. La provincia è memorizzata separatamente.

Esempi:

- Genzano → `Genzano di Roma` / `RM`;
- Marino → `Marino` / `RM`;
- Latina → `Latina` / `LT`.

## Filtri di inclusione/esclusione

Configurazione principale:

```text
config/filters.yaml
```

### Provincia

Sono ammesse le offerte con provincia conosciuta:

```yaml
allowed_provinces:
  - RM
```

Una provincia conosciuta diversa da `RM` produce:

```text
province_not_allowed
```

Se la provincia non è determinabile, l'offerta viene mantenuta per evitare falsi negativi.

### Domiciliare / ADI

Vengono distinti:

- `homecare=true`: l'annuncio comprende attività domiciliare/ADI;
- `homecare_only=true`: l'annuncio riguarda esclusivamente attività domiciliare/ADI.

Regola:

```yaml
exclude_homecare_only: true
```

Quindi:

- solo ADI/domiciliare → escluso;
- ambulatorio + domicilio → può essere incluso.

### Cooperative

Dal 5 settembre 2026 le cooperative sono criterio di esclusione:

```yaml
exclude_cooperatives: true
```

Se `job.cooperative == true`, il motivo di esclusione è:

```text
cooperative
```

### Altri filtri

Sono esclusi anche annunci di candidati che cercano lavoro, offerte di servizi personali e contenuti che non soddisfano i criteri positivi configurati.

## Deduplica

La deduplica non è basata soltanto sull'URL.

L'identità di una opportunità usa principalmente:

```text
company + locality + homecare mode
```

Principi:

- stessa azienda + stessa località + repost con URL diverso → stessa opportunità;
- stessa azienda + località diversa → opportunità distinta;
- fonti differenti che descrivono la stessa opportunità possono essere unite;
- quando due record vengono uniti, viene conservata la versione più ricca e vengono riempiti i campi mancanti quando possibile.

Il codice è in:

```text
core/dedup.py
```

## Stato persistente

File principale:

```text
state/baseline_state.json
```

Schema corrente:

```json
{
  "version": 2,
  "jobs": {},
  "opportunities": {}
}
```

Lo stato mantiene memoria sia dei singoli annunci sia delle opportunità logiche, così un repost non viene notificato come nuovo solo perché cambia URL.

## Baseline

La baseline completa delle offerte incluse viene esportata in:

```text
data/baseline_jobs.json
data/baseline_jobs.csv
```

Nel primo run production con Indeed integrato, prima dell'introduzione del filtro cooperative, sono stati osservati:

- 53 record raw;
- 44 opportunità uniche;
- 25 incluse;
- 19 escluse;
- 10 nuove incluse.

La baseline del prossimo run verrà ricalcolata applicando anche il nuovo filtro cooperative.

## Scoring

Il ranking usa come riferimento geografico **Albano Laziale**.

La preferenza contrattuale è, in ordine generale:

```text
tempo indeterminato
> tempo determinato
> collaborazione
> co.co.co
> partita IVA
```

La distanza incide significativamente sul punteggio.

Esempi reali osservati:

- S.M.E.C. Marino: score 100, circa 4,2 km;
- S.M.E.C. Anzio: score 92, circa 31,2 km;
- Studio Fisioterapico Morgagni Roma: score 85, circa 21,6 km.

## Output e notifiche

### Email

L'email viene inviata **solo quando esistono nuove offerte incluse**.

Secrets richiesti:

```text
SMTP_HOST
SMTP_PORT
SMTP_USERNAME
SMTP_PASSWORD
ALERT_EMAIL_FROM
ALERT_EMAIL_TO
```

### Telegram

Telegram invia invece un heartbeat a ogni run, anche quando non ci sono nuove offerte.

Secrets:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
```

Stati principali:

- 🟢 `OK`;
- 🟠 `DEGRADED`;
- 🔴 `FAILED`.

Il messaggio include:

- record elaborati;
- inclusi/esclusi;
- nuove opportunità;
- diagnostica delle fonti;
- dettaglio del degrado LinkedIn;
- consumo ZenRows per fonte;
- previsione ZenRows a fine mese;
- top nuove opportunità;
- link GitHub Actions;
- link al report HTML.

## Report HTML

Report pubblico:

```text
https://massimospazio.github.io/jobalert-fisioterapista/latest.html
```

File sorgente:

```text
docs/latest.html
```

Il report mostra:

- stato del run;
- diagnostica;
- numero di incluse, escluse, nuove ed elaborate;
- consumo e previsione ZenRows;
- distribuzione per fonte;
- regole di esclusione;
- tabella ordinata delle offerte incluse;
- evidenza delle nuove opportunità.

## Monitoraggio ZenRows

Stato persistente:

```text
state/zenrows_usage.json
```

Plafond mensile attuale:

```text
5000 crediti
```

Snapshot iniziale comunicato il 5 settembre 2026:

```text
1281 / 5000
```

Il primo run production integrato successivo ha consumato:

```text
Bakeca 25 + Indeed 25 = 50 crediti
```

Portando il consumo contabilizzato a:

```text
1331 / 5000
```

Il sistema calcola automaticamente:

- crediti consumati;
- crediti residui;
- costo del run per fonte;
- consumo medio previsto;
- proiezione a fine mese;
- livello di rischio;
- frequenza consigliata.

Con la situazione osservata il 5 settembre 2026, mantenendo circa 50 crediti al giorno, la previsione era circa:

```text
2581 / 5000 a fine settembre
≈ 51,6% del plafond
```

quindi la frequenza giornaliera è attualmente sostenibile.

La frequenza **non viene modificata automaticamente**: se la previsione diventasse critica, Telegram segnala il rischio e suggerisce di passare, per esempio, a una esecuzione ogni 2 o 3 giorni.

## Health monitoring

Il log completo del run viene salvato in:

```text
logs/run.log
```

L'analisi produce:

```text
logs/run_health.json
```

Il sistema rileva almeno:

- errori di fonte (`SOURCE_ERROR`);
- LinkedIn HTTP 429;
- errori sui dettagli LinkedIn;
- consumo Bakeca via ZenRows;
- consumo Indeed via ZenRows;
- impatto quantitativo del degrado LinkedIn.

Un problema parziale di una fonte produce `DEGRADED`, non necessariamente `FAILED`.

## Artifact GitHub Actions

Ogni run conserva gli output principali come artifact, tra cui:

```text
logs/*.jsonl
logs/run.log
logs/run_health.json
logs/new_jobs.json
logs/run_summary.json
data/baseline_jobs.json
data/baseline_jobs.csv
docs/latest.html
state/zenrows_usage.json
```

## Test

I test principali sono in:

```text
tests/test_core.py
```

Coprono tra l'altro:

- inclusione fisioterapista;
- esclusione domiciliare-only;
- offerte miste ambulatoriali/domiciliari;
- provincia RM/non-RM;
- deduplica repost;
- stessa azienda in località diverse;
- scoring contratti;
- normalizzazione località/contratto/retribuzione/scadenza;
- URL Indeed basati su `jk`;
- esclusione cooperative.

I normali push su `v2-core` eseguono i test gratuiti e **non devono attivare fonti a pagamento**.

## File principali

```text
main_v2.py                         orchestrazione pipeline
config/filters.yaml                criteri di inclusione/esclusione
config/sources.yaml                configurazione fonti
config/scoring.yaml                scoring
core/normalizer.py                 normalizzazione
core/filters.py                    filtri
core/dedup.py                      deduplica opportunità
core/scoring.py                    ranking
core/state.py                      stato persistente
core/zenrows_usage.py              contabilità e forecast ZenRows
sources/bakeca.py                  collector Bakeca
sources/ofi_lazio.py               collector OFI Lazio
sources/linkedin.py                collector LinkedIn
sources/indeed.py                  collector Indeed
tools/analyze_run_health.py        diagnostica run
tools/generate_report.py           report HTML
tools/notify_email.py              email nuove offerte
tools/notify_telegram.py           heartbeat Telegram
```

## Principi operativi

1. Non fare scraping a pagamento sui normali push di sviluppo.
2. Usare Indeed come gap-filler dopo le fonti principali.
3. Tentare dettagli LinkedIn soltanto sulle opportunità nuove.
4. Non considerare un `429` LinkedIn come perdita completa della fonte.
5. Conservare memoria delle opportunità, non solo degli URL.
6. Rendere visibili nei log costi, degrado e motivi di esclusione.
7. Non ridurre automaticamente la frequenza ZenRows senza una decisione esplicita.
8. Mantenere il codice operativo su `v2-core`; `main` ospita lo scheduler necessario per GitHub Actions.

## Stato attuale

Al 5 settembre 2026:

- pipeline V2 operativa;
- Bakeca attiva via ZenRows;
- OFI Lazio attiva;
- LinkedIn attiva con enrichment state-aware e monitoraggio 429;
- Indeed attiva come gap-filler via ZenRows;
- deduplica cross-source attiva;
- scoring da Albano Laziale attivo;
- esclusione domiciliare-only attiva;
- esclusione provincia diversa da RM attiva;
- esclusione cooperative attiva;
- email nuove offerte attiva;
- Telegram heartbeat attivo;
- report HTML pubblico attivo;
- monitor ZenRows con previsione fine mese attivo;
- scheduler production giornaliero presente su `main` alle 07:00 UTC.
