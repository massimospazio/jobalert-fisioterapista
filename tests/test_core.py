import unittest

from core.config import load_all
from core.dedup import deduplicate_jobs, opportunity_key
from core.filters import evaluate_filters
from core.models import JobListing
from core.normalizer import enrich_job, extract_contract, extract_deadline, extract_location, extract_salary
from core.scoring import haversine_km, score_job
from core.state import build_state, merge_state, split_new_jobs


class CoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_all()

    def test_positive_job_is_included(self):
        job = JobListing(source="test", url="https://example.test/1", title="Fisioterapista", text="Cerchiamo fisioterapista per centro di riabilitazione")
        result = evaluate_filters(job, self.config["filters"])
        self.assertTrue(result.included)
        self.assertIn("fisioterapista", result.positive_matches)

    def test_plural_physiotherapist_title_is_included(self):
        job = JobListing(source="test", url="https://example.test/plural", title="Cercasi Fisioterapisti", text="Studio sanitario ricerca professionisti per la propria sede")
        result = evaluate_filters(job, self.config["filters"])
        self.assertTrue(result.included)
        self.assertIn("fisioterapisti", result.positive_matches)

    def test_service_offer_is_excluded(self):
        job = JobListing(source="test", url="https://example.test/2", title="Fisioterapista a domicilio", text="Fisioterapista offre trattamenti a domicilio")
        result = evaluate_filters(job, self.config["filters"])
        self.assertFalse(result.included)
        self.assertIn("service_offer", result.exclusion_rules)

    def test_homecare_only_is_excluded(self):
        job = JobListing(source="test", url="https://example.test/homecare", title="Fisioterapista ADI", text="Ricerca fisioterapista per sola assistenza domiciliare integrata", adi=True, homecare_only=True)
        result = evaluate_filters(job, self.config["filters"])
        self.assertFalse(result.included)
        self.assertIn("homecare_only", result.exclusion_rules)

    def test_mixed_ambulatory_homecare_is_included(self):
        job = JobListing(source="test", url="https://example.test/mixed", title="Fisioterapista", text="Ricerca fisioterapista per ambulatorio e interventi domiciliari", adi=True, homecare_only=False)
        result = evaluate_filters(job, self.config["filters"])
        self.assertTrue(result.included)

    def test_non_rome_province_is_excluded(self):
        job = JobListing(source="test", url="https://example.test/latina", title="Fisioterapista", text="Offerta di lavoro", location="Latina", province="LT")
        result = evaluate_filters(job, self.config["filters"])
        self.assertFalse(result.included)
        self.assertIn("province_not_allowed", result.exclusion_rules)

    def test_rome_province_is_included(self):
        job = JobListing(source="test", url="https://example.test/anzio", title="Fisioterapista", text="Offerta di lavoro", location="Anzio", province="RM")
        result = evaluate_filters(job, self.config["filters"])
        self.assertTrue(result.included)

    def test_reposts_same_company_location_are_deduplicated(self):
        first = JobListing(source="Bakeca", url="https://example.test/repost-1", title="Fisioterapista - Latina (LT)", text="S.M.E.C. ricerca fisioterapista", company="S.M.E.C. SRLS", location="Latina", province="LT", published_at="2026-09-01")
        second = JobListing(source="Bakeca", url="https://example.test/repost-2", title="Fisioterapista - Latina (LT)", text="S.M.E.C. ricerca fisioterapista a tempo indeterminato", company="S.M.E.C. SRLS", location="Latina", province="LT", published_at="2026-09-02", contract_type="tempo_indeterminato")
        self.assertEqual(opportunity_key(first), opportunity_key(second))
        unique, removed = deduplicate_jobs([first, second])
        self.assertEqual(len(unique), 1)
        self.assertEqual(removed, 1)
        self.assertEqual(unique[0].contract_type, "tempo_indeterminato")

    def test_same_company_different_locations_are_distinct(self):
        latina = JobListing(source="Bakeca", url="https://example.test/latina", title="Fisioterapista", text="", company="S.M.E.C. SRLS", location="Latina", province="LT")
        anzio = JobListing(source="Bakeca", url="https://example.test/anzio", title="Fisioterapista", text="", company="S.M.E.C. SRLS", location="Anzio", province="RM")
        self.assertNotEqual(opportunity_key(latina), opportunity_key(anzio))
        unique, removed = deduplicate_jobs([latina, anzio])
        self.assertEqual(len(unique), 2)
        self.assertEqual(removed, 0)

    def test_indefinite_contract_scores_better_than_piva(self):
        common = dict(source="test", title="Fisioterapista", text="Offerta di lavoro per fisioterapista", location="Albano Laziale", latitude=41.7318, longitude=12.6583, employment_type="full_time")
        employee = JobListing(url="https://example.test/3", contract_type="tempo_indeterminato", piva_required=False, **common)
        piva = JobListing(url="https://example.test/4", contract_type="collaborazione", piva_required=True, **common)
        employee_score = score_job(employee, self.config["scoring"], self.config["settings"])
        piva_score = score_job(piva, self.config["scoring"], self.config["settings"])
        self.assertGreater(employee_score.raw_score, piva_score.raw_score)

    def test_distance_from_albano(self):
        km = haversine_km(41.7318, 12.6583, 41.8067, 12.6813)
        self.assertGreater(km, 5)
        self.assertLess(km, 10)

    def test_state_detects_only_new_urls(self):
        baseline = [{"source": "test", "url": "https://example.test/known", "title": "Fisioterapista"}]
        current = [
            {"source": "test", "url": "https://example.test/known", "title": "Fisioterapista"},
            {"source": "test", "url": "https://example.test/new", "title": "Fisioterapista"},
        ]
        new_items, existing_items = split_new_jobs(current, build_state(baseline))
        self.assertEqual([item["url"] for item in new_items], ["https://example.test/new"])
        self.assertEqual([item["url"] for item in existing_items], ["https://example.test/known"])

    def test_merge_state_preserves_old_and_adds_opportunity(self):
        baseline = build_state([{"source": "test", "url": "https://example.test/known", "title": "Fisioterapista"}])
        current = [{"source": "test", "url": "https://example.test/new", "title": "Fisioterapista", "company": "Centro X", "location": "Roma"}]
        merged = merge_state(baseline, current, ["opportunity-123"])
        self.assertEqual(len(merged["jobs"]), 2)
        self.assertIn("opportunity-123", merged["opportunities"])
        self.assertEqual(merged["version"], 2)

    def test_normalizer_extracts_contract_variants(self):
        self.assertEqual(extract_contract("Collaborazione coordinata e continuativa"), "cococo")
        self.assertEqual(extract_contract("Rapporto in libera professione"), "partita_iva")
        self.assertEqual(extract_contract("Contratto a tempo indeterminato"), "tempo_indeterminato")

    def test_normalizer_extracts_salary_and_deadline(self):
        text = "RAL: 32.000. Candidature entro il 15/09/2026"
        self.assertEqual(extract_salary(text), "RAL: 32.000")
        self.assertEqual(extract_deadline(text), "2026-09-15")
        self.assertEqual(extract_deadline("termine ultimo 30 settembre 2026"), "2026-09-30")

    def test_normalizer_extracts_location_and_coordinates(self):
        self.assertEqual(extract_location("Sede di lavoro: Ciampino"), ("Ciampino", "RM"))
        job = JobListing(source="test", url="https://example.test/norm", title="Fisioterapista - Frascati (RM)", text="Contratto a tempo determinato")
        enriched = enrich_job(job, self.config["locations"]["locations"])
        self.assertEqual(enriched.location, "Frascati")
        self.assertEqual(enriched.province, "RM")
        self.assertIsNotNone(enriched.latitude)
        self.assertEqual(enriched.contract_type, "tempo_determinato")


if __name__ == "__main__":
    unittest.main()
