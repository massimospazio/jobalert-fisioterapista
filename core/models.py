from dataclasses import dataclass, field
from typing import Optional


@dataclass
class JobListing:
    source: str
    url: str
    title: str
    text: str
    company: str = ""
    location: str = ""
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    contract_type: str = "non_specificato"
    employment_type: str = "non_specificato"
    piva_required: bool = False
    adi: bool = False
    salary_present: bool = False


@dataclass
class FilterResult:
    included: bool
    positive_matches: list[str] = field(default_factory=list)
    negative_matches: list[str] = field(default_factory=list)
    exclusion_rules: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class ScoreComponent:
    name: str
    value: int
    detail: str = ""


@dataclass
class ScoreResult:
    raw_score: int
    normalized_score: int
    components: list[ScoreComponent] = field(default_factory=list)
    distance_km: Optional[float] = None
