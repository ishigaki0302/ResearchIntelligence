"""Gap Detection Pipeline.

Combines tag_patterns.json (blank spots in the research landscape) with
personalized_top30.json (user-relevant papers) to produce the Top10 research
gaps — areas no one has tried yet, that the user is best positioned to fill.

For each gap, an LLM generates a concrete experiment proposal.

Output: data/corpus/gaps_top10.json
Format:
[
  {
    "rank": 1,
    "gap_type": "tag_pair",
    "description": "...",
    "tag1": "corpus_task:ner", "tag2": "corpus_dataset:wikidata",
    "freq1": 12, "freq2": 8,
    "experiment": "..."  # LLM-generated experiment proposal
  },
  ...
]
"""

import json
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import get_config, resolve_path

logger = logging.getLogger(__name__)


def _corpus_dir() -> Path:
    cfg = get_config()
    d = resolve_path(cfg["storage"]["base_dir"]) / "corpus"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _gaps_path() -> Path:
    return _corpus_dir() / "gaps_top10.json"


def _llm_experiment_proposal(description: str, top30_context: str) -> str:
    """Ask LLM to generate a concrete experiment proposal for a gap."""
    try:
        from app.gpu.llm import generate_single  # type: ignore

        prompt = (
            "あなたは NLP 研究者のアシスタントです。\n"
            "以下の「研究の空白地帯」について、具体的な実験案を日本語で3〜5文で提案してください。\n"
            "実験案には: 背景・具体的な手順・期待される貢献 を含めてください。\n\n"
            f"空白地帯: {description}\n\n"
            f"関連する研究者の近傍論文（参考）:\n{top30_context[:600]}"
        )
        return generate_single(prompt, max_new_tokens=250, temperature=0.3).strip()
    except Exception as e:
        logger.debug(f"LLM experiment proposal skipped: {e}")
        return ""


def detect_gaps(session: Session, top_n: int = 10) -> list[dict]:
    """Detect Top-N research gaps and generate experiment proposals.

    Reads tag_patterns.json and personalized_top30.json (if available).
    Writes gaps_top10.json.

    Returns list of gap dicts.
    """
    corpus_dir = _corpus_dir()
    patterns_path = corpus_dir / "tag_patterns.json"
    top30_path = corpus_dir / "personalized_top30.json"

    if not patterns_path.exists():
        logger.warning("tag_patterns.json not found — run 'ri corpus normalize-tags' first.")
        return []

    patterns = json.loads(patterns_path.read_text())
    gaps_raw = patterns.get("gaps", [])

    # Load user's top30 for context
    top30_context = ""
    if top30_path.exists():
        top30 = json.loads(top30_path.read_text())
        top30_context = "\n".join(f"- {r['title']}: {r['abstract'][:150]}" for r in top30[:5])

    results = []
    for i, gap in enumerate(gaps_raw[:top_n]):
        tag1 = gap.get("tag1", "")
        tag2 = gap.get("tag2", "")
        freq1 = gap.get("freq1", 0)
        freq2 = gap.get("freq2", 0)

        # Human-readable description
        t1_display = tag1.split(":", 1)[-1] if ":" in tag1 else tag1
        t2_display = tag2.split(":", 1)[-1] if ":" in tag2 else tag2
        description = (
            f"{t1_display} と {t2_display} の組み合わせ"
            f"（それぞれ {freq1} 件・{freq2} 件あるが、同時適用した論文がない）"
        )

        experiment = _llm_experiment_proposal(description, top30_context)

        results.append(
            {
                "rank": i + 1,
                "gap_type": "tag_pair",
                "description": description,
                "tag1": tag1,
                "tag2": tag2,
                "freq1": freq1,
                "freq2": freq2,
                "experiment": experiment,
            }
        )

    _gaps_path().write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Gap detection: {len(results)} gaps written to {_gaps_path()}")
    return results
