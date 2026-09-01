import unittest

from core.config import load_all
from core.filters import evaluate_filters
from core.models import JobListing
from core.scoring import haversine_km, score_job


class CoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_all()

    def test_positive_job_is_included(self):
        job = JobListing(
            source="test",
            url="https://example.test/1",
            title="Fisioterapista",
            text="Cerchiamo fisioterapista per centro di riabilitazione",
        )
        result = evaluate_filters(job, self.config["filters"])
        self.assertTrue(result.included)
        self.assertIn("fisioterapista", result.positive_matches)

    def test_service_offer_is_excluded(self):
        job = JobListing(
            source="test",
            url="https://example.test/2",
            title="Fisioterapista a domicilio",
            text="Fisioterapista offre trattamenti a domicilio",
        )
        result = evaluate_filters(job, self.config["filters"])
        self.assertFalse(result.included)
        self.assertIn("service_offer", result.exclusion_rules)

    def test_indefinite_contract_scores_better_than_piva(self):
        common = dict(
            source="test",
            title="Fisioterapista",
            text="Offerta di lavoro per fisioterapista",
            location="Albano Laziale",
            latitude=41.7318,
            longitude=12.6583,
            employment_type="full_time",
        )
        employee = JobListing(
            url="https://example.test/3",
            contract_type="tempo_indeterminato",
            piva_required=False,
            **common,
        )
        piva = JobListing(
            url="https://example.test/4",
            contract_type="collaborazione",
            piva_required=True,
            **common,
        )
        employee_score = score_job(employee, self.config["scoring"], self.config["settings"])
        piva_score = score_job(piva, self.config["scoring"], self.config["settings"])
        self.assertGreater(employee_score.raw_score, piva_score.raw_score)

    def test_distance_from_albano(self):
        km = haversine_km(41.7318, 12.6583, 41.8067, 12.6813)
        self.assertGreater(km, 5)
        self.assertLess(km, 10)


if __name__ == "__main__":
    unittest.main()
