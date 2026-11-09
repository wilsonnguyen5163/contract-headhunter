from pathlib import Path

import pytest
from pydantic import ValidationError

from conftest import CONFIG_DIR
from jobmatch.config import (
    ContractPhrases,
    LlmSettings,
    NormalizationRules,
    ProfessionTitles,
    ScoreBands,
    SkillsTaxonomy,
    load_canada_locations,
    load_company_aliases,
    load_contract_phrases,
    load_normalization_rules,
    load_profession_titles,
    load_professions,
    load_settings,
    load_skills_taxonomy,
)


def test_repo_settings_load_and_paths_resolve() -> None:
    settings = load_settings(CONFIG_DIR)
    assert settings.paths.data_dir == CONFIG_DIR.parent / "data"
    assert settings.llm is not None and settings.llm.provider == "gemini"


def test_unknown_setting_is_rejected(tmp_path: Path) -> None:
    cdir = tmp_path / "config"
    cdir.mkdir()
    text = (CONFIG_DIR / "settings.yaml").read_text(encoding="utf-8")
    (cdir / "settings.yaml").write_text(text + "\nsurprise: true\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_settings(cdir)


def test_llm_settings_load_without_any_key() -> None:
    llm = load_settings(CONFIG_DIR).llm
    assert llm is not None
    assert llm.provider == "gemini"
    assert llm.api_key_env == "GEMINI_API_KEY"  # the variable's name, never a key
    assert llm.temperature == 1  # Google's default for Gemini 3 (user decision 2026-11-08)
    assert isinstance(llm.seed, int)  # fixed seed, picked once
    assert llm.prefilter.enabled is False  # off until the recall check passes


def test_score_bands_load_and_must_descend() -> None:
    bands = load_settings(CONFIG_DIR).app.score_bands
    assert (bands.strong, bands.good, bands.some) == (90, 70, 40)
    with pytest.raises(ValidationError, match="strong > good > some"):
        ScoreBands(strong=70, good=90, some=40)


def test_both_thinking_settings_are_rejected() -> None:
    llm = load_settings(CONFIG_DIR).llm
    assert llm is not None
    data = llm.model_dump()
    data.update(thinking_budget=0, thinking_level="low")
    with pytest.raises(ValidationError, match="not both"):
        LlmSettings.model_validate(data)


def test_profession_titles_name_real_professions() -> None:
    titles = load_profession_titles(CONFIG_DIR)
    professions = load_professions(CONFIG_DIR)
    assert professions is not None
    assert set(titles.titles) <= set(professions.professions)
    assert "rédacteur" in titles.titles["content writing and editing"]


def test_title_phrase_under_two_professions_is_rejected() -> None:
    with pytest.raises(ValidationError, match="both"):
        ProfessionTitles.model_validate(
            {"version": 1, "titles": {"a": ["Producer"], "b": ["producer"]}}
        )


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


def test_skill_not_when_phrase_cannot_be_a_skill_term() -> None:
    with pytest.raises(ValidationError, match="listed under both"):
        SkillsTaxonomy.model_validate(
            {
                "version": 1,
                "skills": {
                    "excel": {"not_when": ["excel at"]},
                    "excellence": {"aliases": ["Excel at"]},
                },
            }
        )
