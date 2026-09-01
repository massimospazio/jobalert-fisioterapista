from math import atan2, cos, radians, sin, sqrt

from core.models import JobListing, ScoreComponent, ScoreResult


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return radius * 2 * atan2(sqrt(a), sqrt(1 - a))


def _distance_score(distance_km: float, rules: list[dict]) -> int:
    for rule in rules:
        maximum = rule.get("max")
        if maximum is None or distance_km <= float(maximum):
            return int(rule.get("score", 0))
    return 0


def score_job(job: JobListing, scoring: dict, settings: dict) -> ScoreResult:
    components: list[ScoreComponent] = []

    base = int(scoring.get("base_score", 0))
    components.append(ScoreComponent("base", base, "Punteggio base"))

    contract_key = "partita_iva" if job.piva_required else job.contract_type
    contract_score = int(scoring.get("contract", {}).get(contract_key, 0))
    components.append(ScoreComponent("contract", contract_score, contract_key))

    employment_score = int(scoring.get("employment", {}).get(job.employment_type, 0))
    components.append(ScoreComponent("employment", employment_score, job.employment_type))

    adi_score = int(scoring.get("adi", {}).get(str(job.adi).lower(), 0))
    components.append(ScoreComponent("adi", adi_score, str(job.adi)))

    salary_key = "present" if job.salary_present else "absent"
    salary_score = int(scoring.get("salary", {}).get(salary_key, 0))
    components.append(ScoreComponent("salary", salary_score, salary_key))

    distance = None
    home = settings.get("home_location", {})
    if job.latitude is not None and job.longitude is not None and home.get("latitude") is not None and home.get("longitude") is not None:
        distance = haversine_km(
            float(home["latitude"]),
            float(home["longitude"]),
            float(job.latitude),
            float(job.longitude),
        )
        value = _distance_score(distance, scoring.get("distance_km", []))
        components.append(ScoreComponent("distance", value, f"{distance:.1f} km da {home.get('name', 'residenza')}"))
    else:
        components.append(ScoreComponent("distance", 0, "Coordinate non disponibili"))

    raw = sum(component.value for component in components)
    normalized = max(0, min(100, raw))
    return ScoreResult(raw_score=raw, normalized_score=normalized, components=components, distance_km=distance)
