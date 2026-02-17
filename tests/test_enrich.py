"""Tests for enrichment pipeline."""

from unittest.mock import patch, MagicMock

from sqlalchemy import select

from app.core.models import Item, ItemId
from app.pipelines.enricher import enrich_item, _get_item_ext_ids


S2_RESPONSE = {
    "paperId": "abc123",
    "title": "Attention Is All You Need",
    "year": 2017,
    "externalIds": {
        "DOI": "10.5555/3295222.3295349",
        "ArXiv": "1706.03762",
        "CorpusId": "13756489",
    },
    "authors": [{"authorId": "1", "name": "Ashish Vaswani"}],
}

OPENALEX_RESPONSE = [
    {
        "id": "https://openalex.org/W2963403868",
        "title": "Attention Is All You Need",
        "publication_year": 2017,
        "doi": "https://doi.org/10.5555/3295222.3295349",
        "ids": {
            "openalex": "https://openalex.org/W2963403868",
            "doi": "https://doi.org/10.5555/3295222.3295349",
        },
        "authorships": [
            {"author": {"display_name": "Ashish Vaswani"}},
        ],
    }
]


def _enable_external(test_config):
    """Enable external APIs in test config."""
    import app.core.config as config_mod
    test_config.setdefault("external", {})
    test_config["external"]["semantic_scholar"] = {"enabled": True, "api_key": ""}
    test_config["external"]["openalex"] = {"enabled": True, "email": ""}
    test_config["external"]["enrich"] = {"match_threshold": 0.5, "sleep_sec": 0}
    config_mod.get_config._cache = test_config


def test_enrich_by_doi(tmp_db):
    """Item with DOI should get S2 ID via Semantic Scholar lookup."""
    import app.core.config as config_mod
    _enable_external(config_mod.get_config._cache)

    item = Item(title="Attention Is All You Need", year=2017)
    tmp_db.add(item)
    tmp_db.flush()
    tmp_db.add(ItemId(item_id=item.id, id_type="doi", id_value="10.5555/3295222.3295349"))
    tmp_db.flush()

    with patch("app.connectors.semantic_scholar.lookup_s2_by_doi", return_value=S2_RESPONSE):
        result = enrich_item(tmp_db, item)

    assert result["source"] == "s2"
    assert any("s2:" in x for x in result["ids_added"])


def test_enrich_by_title(tmp_db):
    """Item without DOI should fall back to OpenAlex title search."""
    import app.core.config as config_mod
    cfg = config_mod.get_config._cache
    _enable_external(cfg)
    # Disable S2 to force OpenAlex fallback
    cfg["external"]["semantic_scholar"]["enabled"] = False

    item = Item(title="Attention Is All You Need", year=2017)
    tmp_db.add(item)
    tmp_db.flush()

    with patch("app.connectors.openalex.search_openalex", return_value=OPENALEX_RESPONSE):
        result = enrich_item(tmp_db, item)

    assert result["source"] == "openalex"
    assert any("openalex:" in x for x in result["ids_added"])


def test_enrich_no_match(tmp_db):
    """Low-scoring match should not add any IDs."""
    import app.core.config as config_mod
    cfg = config_mod.get_config._cache
    _enable_external(cfg)
    cfg["external"]["semantic_scholar"]["enabled"] = False
    cfg["external"]["enrich"]["match_threshold"] = 0.99  # very high threshold

    item = Item(title="Completely Unrelated Paper Title", year=2024)
    tmp_db.add(item)
    tmp_db.flush()

    with patch("app.connectors.openalex.search_openalex", return_value=OPENALEX_RESPONSE):
        result = enrich_item(tmp_db, item)

    assert result["source"] is None
    assert result["ids_added"] == []


def test_enrich_idempotent(tmp_db):
    """Enriching twice should not create duplicate IDs."""
    import app.core.config as config_mod
    _enable_external(config_mod.get_config._cache)

    item = Item(title="Attention Is All You Need", year=2017)
    tmp_db.add(item)
    tmp_db.flush()
    tmp_db.add(ItemId(item_id=item.id, id_type="doi", id_value="10.5555/3295222.3295349"))
    tmp_db.flush()

    with patch("app.connectors.semantic_scholar.lookup_s2_by_doi", return_value=S2_RESPONSE):
        result1 = enrich_item(tmp_db, item)
        result2 = enrich_item(tmp_db, item)

    assert len(result1["ids_added"]) > 0
    assert len(result2["ids_added"]) == 0  # no new IDs second time

    # Verify no duplicates
    all_ids = tmp_db.execute(
        select(ItemId).where(ItemId.item_id == item.id)
    ).scalars().all()
    type_values = [(i.id_type, i.id_value) for i in all_ids]
    assert len(type_values) == len(set(type_values))
