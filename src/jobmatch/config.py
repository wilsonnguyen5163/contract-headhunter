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
DATA_DIR_ENV = "JOBMATCH_DATA_DIR"  # root that settings.yaml's relative paths resolve against
DEFAULT_CONFIG_DIR = Path("config")
SETTINGS_FILE = "settings.yaml"
INDUSTRIES_FILE = "industries.yaml"
SKILLS_TAXONOMY_FILE = "skills_taxonomy.yaml"
CONTRACT_PHRASES_FILE = "contract_phrases.yaml"
COMPANY_ALIASES_FILE = "company_aliases.yaml"
NORMALIZATION_FILE = "normalization.yaml"
CANADA_LOCATIONS_FILE = "canada_locations.yaml"
SOURCES_FILE = "sources.yaml"
PROFESSIONS_FILE = "professions.yaml"
PROFESSION_TITLES_FILE = "profession_titles.yaml"


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
    # Where records are kept (storage/loader.py create_loader). "mongodb" is a placeholder
    # until hosting is decided; connection details will come from .env, never from here.
    backend: Literal["csv", "mongodb"] = "csv"


class NormalizeSettings(StrictModel):
    freshness_days: int = Field(gt=0)
    contract_score_threshold: float = Field(gt=0)
    language_min_hits: int = Field(ge=1)
    full_time_hours_per_week: float = Field(gt=0)
    bilingual_min_share: float = Field(gt=0, le=0.5)
    language_filter_min_words: int = Field(ge=1)
    max_section_items: int = Field(ge=1)
    max_responsibilities: int = Field(ge=1)
    fuzzy_title_threshold: float = Field(gt=0, le=100)


class LoggingSettings(StrictModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


# ---- v1: LLM matching (doc/V1_PLAN.md) ----


class LlmLimits(StrictModel):
    """Free-tier limits for our project and model, copied from AI Studio. Never assumed in code."""

    rpm: int = Field(gt=0)  # requests per minute
    tpm: int = Field(gt=0)  # tokens per minute
    rpd: int = Field(gt=0)  # requests per day (resets at midnight Pacific time)
    tpd: int | None = Field(default=None, gt=0)  # tokens per day, when the model has one


class LlmBudget(StrictModel):
    # Stop a run that would use more than this share of what is left today.
    drain_warn_share: float = Field(gt=0, le=1)
    # Do not send a single call estimated above this share of the per-minute token limit.
    call_max_tpm_share: float = Field(gt=0, le=1)


class LlmPayloadSettings(StrictModel):
    chars_per_token: float = Field(gt=0)  # local token estimate before a call
    max_job_tokens: int = Field(gt=0)  # job text cap; lists are cut from the end past it
    summary_max_words: int = Field(gt=0)  # contractor summary sent to the API
    max_list_items: int = Field(gt=0)  # per contractor list (skills, keywords, titles)
    expected_output_tokens: int = Field(gt=0)  # for budget estimates only


class LlmShortlistSettings(StrictModel):
    """What a job's stored shortlist keeps from Gemini's verdicts (user rule, 2026-11-06)."""

    max_fits: int = Field(ge=0)  # jobs with at least one "fit": fits only, up to this many
    partials_when_no_fit: int = Field(ge=0)  # jobs with no fit: this many best partials


class LlmPrefilterSettings(StrictModel):
    enabled: bool
    min_roster: int = Field(ge=1)


class LlmSettings(StrictModel):
    """v12 (2026-11-08, user design): two calls per job. `model` screens every contractor and
    shortlists up to top_k; the first model in `review_models` with quota left reviews that
    shortlist in depth, falling back down the list when one reaches its daily limit."""

    provider: Literal["gemini"]
    model: str = Field(min_length=1)  # the screening model (the one with the largest quota)
    # Deep-review models, strongest first. Empty: `model` reviews too.
    review_models: list[str] = []
    # Jobs reviewed per review call (v15). The strong models' daily limits count requests, so
    # 2 jobs per call doubles the jobs reviewed per day.
    review_batch_size: int = Field(default=1, ge=1, le=6)
    # Thinking level for review calls only (Gemini 3); null: the same as thinking_level.
    review_thinking_level: Literal["minimal", "low", "medium", "high"] | None = None
    # Thinking level per model, overriding thinking_level for that model wherever it runs
    # (screening or review). User decision 2026-11-09: gemini-3.5-flash-lite at "high".
    model_thinking: dict[str, Literal["minimal", "low", "medium", "high"]] = {}
    # Free-tier limits per model; a model not listed uses `limits`.
    model_limits: dict[str, LlmLimits] = {}
    base_url: str = Field(min_length=1)
    api_key_env: str = Field(min_length=1)  # name of the variable in .env, never the key
    temperature: float = Field(ge=0, le=2)
    # A fixed seed makes answers more repeatable (Google: best effort, not guaranteed).
    # null sends none.
    seed: int | None = None
    max_output_tokens: int = Field(gt=0)
    # Gemini 2.5 models take a thinking budget (0 = off); Gemini 3 models a thinking level.
    # Leave both null to use the model's default.
    thinking_budget: int | None = Field(default=None, ge=0)
    thinking_level: Literal["minimal", "low", "medium", "high"] | None = None
    timeout_seconds: float = Field(gt=0)
    max_retries: int = Field(ge=0)  # per call, on per-minute 429s, 5xx and network errors
    retry_base_seconds: float = Field(ge=0)
    top_k: int = Field(gt=0)
    jobs_per_call: int = Field(gt=0)
    limits: LlmLimits
    budget: LlmBudget
    payload: LlmPayloadSettings
    shortlist: LlmShortlistSettings
    prefilter: LlmPrefilterSettings

    @model_validator(mode="after")
    def _one_thinking_setting(self) -> "LlmSettings":
        if self.thinking_budget is not None and self.thinking_level is not None:
            raise ValueError("set llm.thinking_budget or llm.thinking_level, not both")
        return self

    @property
    def review_chain(self) -> list[str]:
        return list(self.review_models) or [self.model]

    def for_model(self, model: str) -> "LlmSettings":
        """These settings for one model: its name, its own limits and its own thinking level
        (model_thinking), when set."""
        update: dict = {"model": model, "limits": self.model_limits.get(model, self.limits)}
        if model in self.model_thinking:
            update |= {"thinking_level": self.model_thinking[model], "thinking_budget": None}
        return self.model_copy(update=update)


class ScoreBands(StrictModel):
    """Lowest fit_score of each colour band on the results screen (user decision
    2026-11-08): strong = darker green, good = green, some = yellow, below = light yellow."""

    strong: int = Field(default=90, ge=0, le=100)
    good: int = Field(default=70, ge=0, le=100)
    some: int = Field(default=40, ge=0, le=100)

    @model_validator(mode="after")
    def _descending(self) -> "ScoreBands":
        if not self.strong > self.good > self.some:
            raise ValueError("app.score_bands: need strong > good > some")
        return self


class AppSettings(StrictModel):
    score_bands: ScoreBands = ScoreBands()


class Settings(StrictModel):
    schema_version: Literal[1]
    config_dir: Path
    paths: PathSettings
    storage: StorageSettings
    normalize: NormalizeSettings
    logging: LoggingSettings
    llm: LlmSettings | None = None  # v1; None when settings.yaml has no llm section
    app: AppSettings = AppSettings()  # the desktop app's screens


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
    # Words about how someone is engaged ("fractional", "interim"), not the role: removed from
    # titles before they are embedded, like the seniority words.
    title_engagement_words: list[str] = []
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


def load_settings(config_dir: Path | None = None, data_root: Path | None = None) -> Settings:
    """Relative paths in settings.yaml resolve against `data_root`, else $JOBMATCH_DATA_DIR,
    else the config folder's parent (the repo, or /app in Docker). The desktop app passes its
    user data folder, so the read-only bundled config never decides where data is written."""
    cdir = resolve_config_dir(config_dir)
    raw = _read_yaml(cdir / SETTINGS_FILE)
    env_root = os.environ.get(DATA_DIR_ENV)
    root = data_root or (Path(env_root) if env_root else cdir.parent)
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


class Professions(StrictModel):
    """config/professions.yaml: what each profession does, which ones are close enough to
    accept each other, and how clear an assignment must be to count."""

    version: int
    min_margin: float = Field(ge=0, lt=1)
    close: list[list[str]] = []
    professions: dict[str, str] = Field(min_length=2)

    @model_validator(mode="after")
    def _close_names_exist(self) -> "Professions":
        unknown = sorted({n for group in self.close for n in group} - set(self.professions))
        if unknown:
            raise ValueError(f"close groups name unknown profession(s): {', '.join(unknown)}")
        return self


def load_professions(config_dir: Path) -> Professions | None:
    """None when the file is missing: profession notes are optional."""
    path = config_dir / PROFESSIONS_FILE
    return Professions.model_validate(_read_yaml(path)) if path.exists() else None


class ProfessionTitles(StrictModel):
    """config/profession_titles.yaml (v1): title phrases that place a job in a profession
    without any model. Keys must be professions.yaml names."""

    version: int
    titles: dict[str, list[str]]

    @model_validator(mode="after")
    def _phrases_unique(self) -> "ProfessionTitles":
        owners: dict[str, str] = {}
        for profession, phrases in self.titles.items():
            for phrase in phrases:
                owner = owners.setdefault(_fold(phrase), profession)
                if owner != profession:
                    raise ValueError(
                        f"title phrase {phrase!r} is listed under both {owner!r} and {profession!r}"
                    )
        return self


def load_profession_titles(config_dir: Path) -> ProfessionTitles:
    """The title phrases, checked against the profession names in professions.yaml."""
    titles = ProfessionTitles.model_validate(_read_yaml(config_dir / PROFESSION_TITLES_FILE))
    professions = load_professions(config_dir)
    if professions is None:
        raise ValueError(f"{PROFESSION_TITLES_FILE} needs {PROFESSIONS_FILE}")
    unknown = sorted(set(titles.titles) - set(professions.professions))
    if unknown:
        raise ValueError(
            f"{PROFESSION_TITLES_FILE} names unknown profession(s): {', '.join(unknown)}"
        )
    return titles


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
