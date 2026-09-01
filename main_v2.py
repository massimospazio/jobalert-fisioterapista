from core.config import load_all
from core.filters import evaluate_filters
from core.models import JobListing
from core.scoring import score_job
from reports.audit import format_console_audit, write_audit


def demo_jobs() -> list[JobListing]:
    return [
        JobListing(
            source="DEMO",
            url="https://example.test/job/1",
            title="Fisioterapista - tempo indeterminato",
            company="Centro Riabilitazione Demo",
            location="Frascati",
            text="Cerchiamo fisioterapista per struttura riabilitativa. Contratto a tempo indeterminato, full time. Retribuzione indicata.",
            latitude=41.8067,
            longitude=12.6813,
            contract_type="tempo_indeterminato",
            employment_type="full_time",
            salary_present=True,
        ),
        JobListing(
            source="DEMO",
            url="https://example.test/job/2",
            title="Fisioterapista domiciliare con P.IVA",
            company="Assistenza Demo",
            location="Roma",
            text="Ricerca fisioterapista per assistenza domiciliare ADI con partita IVA.",
            latitude=41.9028,
            longitude=12.4964,
            contract_type="collaborazione",
            employment_type="part_time",
            piva_required=True,
            adi=True,
        ),
        JobListing(
            source="DEMO",
            url="https://example.test/job/3",
            title="Fisioterapista offre trattamenti a domicilio",
            location="Albano Laziale",
            text="Fisioterapista offre trattamenti e servizio di fisioterapia a domicilio.",
            latitude=41.7318,
            longitude=12.6583,
        ),
    ]


def main() -> None:
    config = load_all()
    settings = config["settings"]
    filters_config = config["filters"]
    scoring_config = config["scoring"]
    output_dir = settings.get("audit", {}).get("output_dir", "logs")

    included = 0
    excluded = 0

    for job in demo_jobs():
        filter_result = evaluate_filters(job, filters_config)
        score_result = score_job(job, scoring_config, settings) if filter_result.included else None

        if filter_result.included:
            included += 1
        else:
            excluded += 1

        print(format_console_audit(job, filter_result, score_result))
        audit_file = write_audit(job, filter_result, score_result, output_dir)

    print(f"\nDEMO COMPLETATA: {included} inclusi, {excluded} esclusi")
    print(f"Audit JSONL: {audit_file}")


if __name__ == "__main__":
    main()
