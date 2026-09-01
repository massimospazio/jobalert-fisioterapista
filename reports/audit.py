import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from core.models import FilterResult, JobListing, ScoreResult


def write_audit(job: JobListing, filter_result: FilterResult, score_result: ScoreResult | None, output_dir: str = "logs") -> Path:
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    filename = path / f"audit-{datetime.now(timezone.utc).date().isoformat()}.jsonl"

    record = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "job": asdict(job),
        "filter": asdict(filter_result),
        "score": asdict(score_result) if score_result else None,
        "decision": "INCLUDED" if filter_result.included else "EXCLUDED",
    }

    with filename.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    return filename


def format_console_audit(job: JobListing, filter_result: FilterResult, score_result: ScoreResult | None) -> str:
    lines = [
        "=" * 72,
        f"FONTE: {job.source}",
        f"TITOLO: {job.title}",
        f"URL: {job.url}",
        f"LOCALITA: {job.location or 'non indicata'}",
        "-" * 72,
        f"FILTRO: {'INCLUSO' if filter_result.included else 'ESCLUSO'}",
        f"MOTIVO: {filter_result.reason}",
        f"MATCH POSITIVI: {', '.join(filter_result.positive_matches) or '-'}",
        f"MATCH NEGATIVI: {', '.join(filter_result.negative_matches) or '-'}",
        f"REGOLE ESCLUSIONE: {', '.join(filter_result.exclusion_rules) or '-'}",
    ]

    if score_result:
        lines.extend(["-" * 72, "SCORING:"])
        for component in score_result.components:
            sign = "+" if component.value >= 0 else ""
            lines.append(f"  {component.name:12} {sign}{component.value:3}  {component.detail}")
        if score_result.distance_km is not None:
            lines.append(f"DISTANZA: {score_result.distance_km:.1f} km")
        lines.append(f"SCORE FINALE: {score_result.normalized_score}/100 (raw {score_result.raw_score})")

    lines.append("=" * 72)
    return "\n".join(lines)
