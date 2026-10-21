from pathlib import Path

import pytest
from pydantic import ValidationError

from conftest import CONFIG_DIR
from jobmatch.config import (
    ContractPhrases,
    NormalizationRules,
    SkillsTaxonomy,
    load_canada_locations,
    load_company_aliases,
    load_contract_phrases,
    load_normalization_rules,
    load_settings,
    load_skills_taxonomy,
)


def test_repo_settings_load_and_paths_resolve() -> None:
    settings = load_settings(CONFIG_DIR)
    assert settings.paths.data_dir == CONFIG_DIR.parent / "data"
    assert settings.embedding.model_name == "intfloat/multilingual-e5-small"
    assert settings.matching.bands.medium <= settings.matching.bands.high


def test_unknown_setting_is_rejected(tmp_path: Path) -> None:
    cdir = tmp_path / "config"
    cdir.mkdir()
    text = (CONFIG_DIR / "settings.yaml").read_text(encoding="utf-8")
    (cdir / "settings.yaml").write_text(text + "\nsurprise: true\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_settings(cdir)


def test_repo_taxonomies_load() -> None:
    taxonomy = load_skills_taxonomy(CONFIG_DIR)
    assert "gestion de projet" in taxonomy.skills["project management"].aliases
    phrases = load_contract_phrases(CONFIG_DIR)
    assert {p.lang for p in phrases.contract} == {"en", "fr"}
    aliases = load_company_aliases(CONFIG_DIR)
    assert "ltée" in aliases.legal_suffixes


def test_v1_config_files_load() -> None:
    taxonomy = load_skills_taxonomy(CONFIG_DIR)
    assert len(taxonomy.skills) >= 150
    assert len({entry.category for entry in taxonomy.skills.values()}) >= 10
    rules = load_normalization_rules(CONFIG_DIR)
    assert "about us" in rules.sections["employer"].headers.en
    assert list(rules.sections)[:4] == [
        "overview",
        "responsibilities",
        "nice_to_have",
        "requirements",
    ]
    assert set(rules.pay.units) == {"hour", "day", "week", "month", "year"}
    locations = load_canada_locations(CONFIG_DIR)
    assert len(locations.provinces) == 13
    assert "ON" in locations.provinces["CA-ON"].abbreviations  # not YAML's boolean "on"


def test_duplicate_contract_phrase_is_rejected() -> None:
    data = load_contract_phrases(CONFIG_DIR).model_dump()
    data["permanent"].append({"phrase": "Contrat", "lang": "fr", "weight": 1.0})
    with pytest.raises(ValidationError, match="more than once"):
        ContractPhrases.model_validate(data)


def test_language_words_must_not_overlap() -> None:
    data = load_normalization_rules(CONFIG_DIR).model_dump()
    data["language_words"]["fr"].append("The")
    with pytest.raises(ValidationError, match="both en and fr"):
        NormalizationRules.model_validate(data)


def test_bad_title_pattern_is_rejected() -> None:
    data = load_normalization_rules(CONFIG_DIR).model_dump()
    data["title_noise_patterns"].append("(unclosed")
    with pytest.raises(ValidationError, match="invalid title noise pattern"):
        NormalizationRules.model_validate(data)


def test_skill_alias_collision_is_rejected() -> None:
    with pytest.raises(ValidationError, match="listed under both"):
        SkillsTaxonomy.model_validate(
            {
                "version": 1,
                "skills": {
                    "java": {"aliases": ["jvm"]},
                    "kotlin": {"aliases": ["JVM"]},  # same alias, different case
                },
            }
        )
