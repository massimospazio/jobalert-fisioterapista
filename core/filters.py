from core.models import FilterResult, JobListing


def _haystack(job: JobListing) -> str:
    return "\n".join(
        part for part in [job.title, job.company, job.location, job.text] if part
    ).lower()


def evaluate_filters(job: JobListing, config: dict) -> FilterResult:
    text = _haystack(job)

    positive = [
        keyword
        for keyword in config.get("positive_keywords", [])
        if keyword.lower() in text
    ]
    negative = [
        keyword
        for keyword in config.get("negative_keywords", [])
        if keyword.lower() in text
    ]

    matched_rules: list[str] = []
    for rule_id, rule in config.get("exclusion_rules", {}).items():
        patterns = rule.get("patterns", [])
        if any(pattern.lower() in text for pattern in patterns):
            matched_rules.append(rule_id)

    if config.get("exclude_homecare_only", False) and job.homecare_only:
        matched_rules.append("homecare_only")

    require_positive = bool(config.get("require_positive_match", True))

    if matched_rules:
        return FilterResult(
            included=False,
            positive_matches=positive,
            negative_matches=negative,
            exclusion_rules=matched_rules,
            reason=f"Escluso da regola: {', '.join(matched_rules)}",
        )

    if negative:
        return FilterResult(
            included=False,
            positive_matches=positive,
            negative_matches=negative,
            exclusion_rules=matched_rules,
            reason=f"Parole chiave negative: {', '.join(negative)}",
        )

    if require_positive and not positive:
        return FilterResult(
            included=False,
            positive_matches=[],
            negative_matches=[],
            exclusion_rules=[],
            reason="Nessun criterio positivo trovato",
        )

    return FilterResult(
        included=True,
        positive_matches=positive,
        negative_matches=[],
        exclusion_rules=[],
        reason="Superati i filtri deterministici",
    )
