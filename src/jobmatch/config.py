"""Load settings.yaml and the YAML taxonomies into validated pydantic models."""

import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from jobmatch.schemas import CountryCode, Province

CONFIG_DIR_ENV = "JOBMATCH_CONFIG_DIR"
DEFAULT_CONFIG_DIR = Path("config")
SETTINGS_FILE = "settings.yaml"
INDUSTRIES_FILE = "industries.yaml"
SKILLS_TAXONOMY_FILE = "skills_taxonomy.yaml"
CONTRACT_PHRASES_FILE = "contract_phrases.yaml"
COMPANY_ALIASES_FILE = "company_aliases.yaml"
NORMALIZATION_FILE = "normalization.yaml"
CANADA_LOCATIONS_FILE = "canada_locations.yaml"
SOURCES_FILE = "sources.yaml"


def _fold(text: str) -> str:
    """Casefold and strip accents (used to compare config terms)."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c)).strip()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ---- settings.yaml ----


class PathSettings(StrictModel):
    data_dir: Path
    exports_dir: Path
    contractors_csv: Path
    learned_skills: Path


class StorageSettings(StrictModel):
    lock_timeout_seconds: float = Field(gt=0)


class EmbeddingSettings(StrictModel):
    model_name: str = Field(min_length=1)
    model_revision: str | None = None
    batch_size: int = Field(gt=0)
    max_seq_length: int = Field(gt=0)
    normalize_embeddings: bool
    job_prefix: str
    facet_prefix: str


class NormalizeSettings(StrictModel):
    freshness_days: int = Field(gt=0)
    contract_score_threshold: float = Field(gt=0)
    language_min_hits: int = Field(ge=1)
    bilingual_min_share: float = Field(gt=0, le=0.5)
    language_filter_min_words: int = Field(ge=1)
    max_section_items: int = Field(ge=1)
    max_responsibilities: int = Field(ge=1)
    fuzzy_title_threshold: float = Field(gt=0, le=100)


class MatchingWeights(StrictModel):
    cosine: float = Field(ge=0)
    title: float = Field(default=0, ge=0)
    skill_overlap: float = Field(ge=0)
    field: float = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _not_all_zero(self) -> "MatchingWeights":
        if self.cosine + self.title + self.skill_overlap + self.field <= 0:
            raise ValueError("at least one matching weight must be positive")
        return self


class ConfidenceBands(StrictModel):
    high: float = Field(ge=0, le=1)
    medium: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _ordered(self) -> "ConfidenceBands":
        if self.medium > self.high:
            raise ValueError("bands.medium must not exceed bands.high")
        return self


class MatchingSettings(StrictModel):
    top_k: int = Field(gt=0)
    min_score: float = Field(ge=0, le=1)
    cosine_floor: float = Field(ge=-1, le=1)
    cosine_ceiling: float = Field(ge=-1, le=1)
    title_floor: float = Field(ge=-1, le=1)
    title_ceiling: float = Field(ge=-1, le=1)
    weights: MatchingWeights
    bands: ConfidenceBands
    missing_must_have_lowers_band: bool
    skill_overlap_cap: int = Field(ge=1)
    field_ignore_categories: list[str] = []
    thin_profiles_skip_text: bool
    seniority_max_gap: int = Field(ge=0)
    assume_all_remote: bool
    hours_per_year: float = Field(gt=0)
    rate_tolerance: float = Field(gt=0)

    @model_validator(mode="after")
    def _cosine_range(self) -> "MatchingSettings":
        if self.cosine_floor >= self.cosine_ceiling:
            raise ValueError("matching.cosine_floor must be below cosine_ceiling")
        if self.title_floor >= self.title_ceiling:
            raise ValueError("matching.title_floor must be below title_ceiling")
        return self


class LoggingSettings(StrictModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


class Settings(StrictModel):
    schema_version: Literal[1]
    config_dir: Path
    paths: PathSettings
    storage: StorageSettings
    embedding: EmbeddingSettings
    normalize: NormalizeSettings
    matching: MatchingSettings
    logging: LoggingSettings


# ---- taxonomies ----


class SkillEntry(StrictModel):
    category: str | None = None
    aliases: list[str] = []
    # Longer phrases holding a term that do not mean the skill ("excel at"): where one
    # matches, that text yields no skill.
    not_when: list[str] = []


class SkillsTaxonomy(StrictModel):
    version: int
    skills: dict[str, SkillEntry]

    @model_validator(mode="after")
    def _no_collisions(self) -> "SkillsTaxonomy":
        owners: dict[str, str] = {}
        for canonical, entry in self.skills.items():
            if canonical != canonical.strip().casefold():
                raise ValueError(f"skill key {canonical!r} must be lowercase and trimmed")
            for term in (canonical, *entry.aliases, *entry.not_when):
                key = _fold(term)
                owner = owners.setdefault(key, canonical)
                if owner != canonical:
                    raise ValueError(f"{term!r} is listed under both {owner!r} and {canonical!r}")
        return self


class WeightedPhrase(StrictModel):
    phrase: str = Field(min_length=1)
    lang: Literal["en", "fr"]
    weight: float = Field(gt=0)


class PlainPhrase(StrictModel):
    phrase: str = Field(min_length=1)
    lang: Literal["en", "fr"]


class StructuredTypes(StrictModel):
    """Values a source's own employment-type field may take, per employment type."""

    contract: list[str]
    temporary: list[str]
    permanent: list[str]


class CommitmentRules(StrictModel):
    full_time: list[str]
    part_time: list[str]
    full_time_min_hours: float = Field(gt=0)


class ContractPhrases(StrictModel):
    version: int
    contract: list[WeightedPhrase]
    permanent: list[WeightedPhrase]
    false_positives: list[PlainPhrase]
    duration_weight: float = Field(ge=0)
    structured_types: StructuredTypes
    commitment: CommitmentRules

    @model_validator(mode="after")
    def _unique_phrases(self) -> "ContractPhrases":
        seen: set[str] = set()
        for phrase in [*self.contract, *self.permanent, *self.false_positives]:
            key = _fold(phrase.phrase)
            if key in seen:
                raise ValueError(f"phrase {phrase.phrase!r} is listed more than once")
            seen.add(key)
        return self


class CompanyAliases(StrictModel):
    version: int
    legal_suffixes: list[str]
    aliases: dict[str, list[str]]


class BilingualList(StrictModel):
    en: list[str]
    fr: list[str]

    def all(self) -> list[str]:
        return [*self.en, *self.fr]


class SeniorityLevels(StrictModel):
    junior: list[str]
    intermediate: list[str]
    senior: list[str]
    lead: list[str]
    executive: list[str]


SectionName = Literal[
    "overview", "responsibilities", "nice_to_have", "requirements", "benefits", "pay",
    "employer", "equal_opportunity", "application", "other",
]  # fmt: skip


def _check_patterns(patterns: list[str], what: str) -> list[str]:
    for pattern in patterns:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"invalid {what} {pattern!r}: {exc}") from exc
    return patterns


class SectionRule(StrictModel):
    headers: BilingualList
    patterns: list[str] = []

    @field_validator("patterns")
    @classmethod
    def _patterns_compile(cls, patterns: list[str]) -> list[str]:
        return _check_patterns(patterns, "section pattern")


class PayUnitRule(StrictModel):
    terms: list[str]
    min: float = Field(gt=0)
    max: float = Field(gt=0)

    @model_validator(mode="after")
    def _ordered(self) -> "PayUnitRule":
        if self.min > self.max:
            raise ValueError("pay unit min must not exceed max")
        return self


class PayRules(StrictModel):
    units: dict[Literal["hour", "day", "week", "month", "year"], PayUnitRule]
    context_terms: list[str]
    usd_markers: list[str]
    cad_markers: list[str]

    @model_validator(mode="after")
    def _all_units(self) -> "PayRules":
        missing = {"hour", "day", "week", "month", "year"} - set(self.units)
        if missing:
            raise ValueError(f"pay units missing: {', '.join(sorted(missing))}")
        return self


class FlattenedTextRules(StrictModel):
    min_chars: int = Field(ge=0)
    min_chars_per_line: int = Field(gt=0)
    item_starters: BilingualList
    ing_nouns: list[str] = []


class NormalizationRules(StrictModel):
    version: int
    sections: dict[SectionName, SectionRule]  # tried in this order
    kept_sections: list[SectionName]
    pay: PayRules
    title_noise_patterns: list[str]
    title_abbreviations: dict[str, str] = {}
    number_words: dict[str, int] = {}
    seniority: SeniorityLevels
    language_words: BilingualList
    must_have_soft_markers: BilingualList = BilingualList(en=[], fr=[])
    flattened_text: FlattenedTextRules | None = None

    @field_validator("number_words")
    @classmethod
    def _numbers_positive(cls, words: dict[str, int]) -> dict[str, int]:
        bad = [w for w, n in words.items() if not 0 < n < 100]
        if bad:
            raise ValueError(f"number words must map to 1-99: {', '.join(bad)}")
        return {_fold(w): n for w, n in words.items()}

    @field_validator("title_noise_patterns")
    @classmethod
    def _patterns_compile(cls, patterns: list[str]) -> list[str]:
        return _check_patterns(patterns, "title noise pattern")

    @model_validator(mode="after")
    def _language_words_disjoint(self) -> "NormalizationRules":
        overlap = {_fold(w) for w in self.language_words.en} & {
            _fold(w) for w in self.language_words.fr
        }
        if overlap:
            raise ValueError(f"words listed for both en and fr: {', '.join(sorted(overlap))}")
        return self


class ProvinceEntry(StrictModel):
    names: list[str]  # matched case- and accent-insensitively
    abbreviations: list[str]  # matched case-sensitively ("ON", not "on")


class CanadaLocations(StrictModel):
    version: int
    provinces: dict[Province, ProvinceEntry]
    cities: dict[Province, list[str]]
    canada_terms: list[str]
    foreign_countries: dict[CountryCode, list[str]]
    us_states: list[str]
    us_state_codes: list[str]
    remote_terms: list[str]
    hybrid_terms: list[str]
    onsite_terms: list[str]
    hybrid_patterns: list[str] = []

    @field_validator("hybrid_patterns")
    @classmethod
    def _hybrid_patterns_compile(cls, patterns: list[str]) -> list[str]:
        return _check_patterns(patterns, "hybrid pattern")

    @model_validator(mode="after")
    def _cities_unique(self) -> "CanadaLocations":
        owners: dict[str, str] = {}
        for province, cities in self.cities.items():
            for city in cities:
                owner = owners.setdefault(_fold(city), province)
                if owner != province:
                    raise ValueError(f"city {city!r} is listed under {owner} and {province}")
        if "CA" in self.us_state_codes:
            raise ValueError("'CA' must not be a US state code here: it means Canada")
        return self


# ---- loaders ----


def resolve_config_dir(config_dir: Path | None = None) -> Path:
    if config_dir is None:
        env_value = os.environ.get(CONFIG_DIR_ENV)
        config_dir = Path(env_value) if env_value else DEFAULT_CONFIG_DIR
    return config_dir.resolve()


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping at the top level")
    return data


def load_settings(config_dir: Path | None = None) -> Settings:
    cdir = resolve_config_dir(config_dir)
    raw = _read_yaml(cdir / SETTINGS_FILE)
    root = cdir.parent
    paths = dict(raw.get("paths") or {})
    for key, value in paths.items():
        path = Path(value)
        paths[key] = path if path.is_absolute() else root / path
    raw["paths"] = paths
    raw["config_dir"] = cdir
    return Settings.model_validate(raw)


def load_skills_taxonomy(config_dir: Path, learned: Path | None = None) -> SkillsTaxonomy:
    """The curated taxonomy, plus the skills learned from the contractor list when `learned`
    exists. A learned skill whose name or alias is already a curated term is dropped:
    curated always wins."""
    curated = SkillsTaxonomy.model_validate(_read_yaml(config_dir / SKILLS_TAXONOMY_FILE))
    if learned is None or not learned.exists():
        return curated
    extra = SkillsTaxonomy.model_validate(_read_yaml(learned))
    taken = {_fold(t) for name, entry in curated.skills.items() for t in (name, *entry.aliases)}
    merged = dict(curated.skills)
    for name, entry in extra.skills.items():
        terms = {_fold(t) for t in (name, *entry.aliases)}
        if name in merged or terms & taken:
            continue
        merged[name] = entry
        taken |= terms
    return SkillsTaxonomy(version=curated.version, skills=merged)


def load_contract_phrases(config_dir: Path) -> ContractPhrases:
    return ContractPhrases.model_validate(_read_yaml(config_dir / CONTRACT_PHRASES_FILE))


def load_company_aliases(config_dir: Path) -> CompanyAliases:
    return CompanyAliases.model_validate(_read_yaml(config_dir / COMPANY_ALIASES_FILE))


def load_normalization_rules(config_dir: Path) -> NormalizationRules:
    return NormalizationRules.model_validate(_read_yaml(config_dir / NORMALIZATION_FILE))


class IndustryEntry(StrictModel):
    aliases: list[str] = []
    contractor_aliases: list[str] = []  # matched in contractor industry lists only


class Industries(StrictModel):
    version: int
    industries: dict[str, IndustryEntry]

    @model_validator(mode="after")
    def _no_collisions(self) -> "Industries":
        owners: dict[str, str] = {}
        for name, entry in self.industries.items():
            for term in (name, *entry.aliases, *entry.contractor_aliases):
                owner = owners.setdefault(_fold(term), name)
                if owner != name:
                    raise ValueError(f"{term!r} is listed under both {owner!r} and {name!r}")
        return self


def load_industries(config_dir: Path) -> Industries:
    return Industries.model_validate(_read_yaml(config_dir / INDUSTRIES_FILE))


def load_canada_locations(config_dir: Path) -> CanadaLocations:
    return CanadaLocations.model_validate(_read_yaml(config_dir / CANADA_LOCATIONS_FILE))


class WebSourceSettings(StrictModel):
    user_agent: str = Field(min_length=1)
    delay_seconds: float = Field(ge=0)
    timeout_seconds: float = Field(gt=0)
    max_bytes: int = Field(gt=0)
    max_redirects: int = Field(ge=0)
    respect_robots_txt: bool
    blocked_host_labels: list[str]


class SourcesConfig(StrictModel):
    version: int
    web: WebSourceSettings


def load_sources(config_dir: Path) -> SourcesConfig:
    return SourcesConfig.model_validate(_read_yaml(config_dir / SOURCES_FILE))
