"""RawJob -> Job: clean, extract fields, apply hard filters (recall first).

Pure and deterministic: the same RawJob and `now` always give the same Job, and nothing here
touches the network or the disk. Hard filters drop a posting only on a clear signal and
always record why (Job.filter_reason).
"""

from datetime import datetime, timedelta

from jobmatch.config import (
    CanadaLocations,
    CompanyAliases,
    ContractPhrases,
    NormalizationRules,
    NormalizeSettings,
    Settings,
    SkillsTaxonomy,
    load_canada_locations,
    load_company_aliases,
    load_contract_phrases,
    load_normalization_rules,
    load_skills_taxonomy,
)
from jobmatch.normalize.company import CompanyNormalizer
from jobmatch.normalize.contract import ContractAssessment, ContractDetector
from jobmatch.normalize.fingerprint import job_fingerprint
from jobmatch.normalize.language import LanguageDetector
from jobmatch.normalize.location import LocationInfo, LocationParser
from jobmatch.normalize.sections import SectionSplitter
from jobmatch.normalize.seniority import SeniorityDetector
from jobmatch.normalize.skills import SkillExtractor
from jobmatch.normalize.text import clean_text, to_plain_text
from jobmatch.normalize.title import TitleNormalizer
from jobmatch.schemas import (
    EmploymentType,
    FilterReason,
    Job,
    JobStatus,
    Language,
    RawJob,
    RemoteType,
)


def job_id_for(source: str, source_id: str) -> str:
    return f"{source}:{source_id}"


class Normalizer:
    def __init__(
        self,
        settings: NormalizeSettings,
        *,
        rules: NormalizationRules,
        taxonomy: SkillsTaxonomy,
        phrases: ContractPhrases,
        aliases: CompanyAliases,
        locations: CanadaLocations,
    ) -> None:
        self._settings = settings
        self._locations = LocationParser(locations)
        self._sections = SectionSplitter(rules)
        self._titles = TitleNormalizer(rules, self._locations.looks_like_location)
        self._companies = CompanyNormalizer(aliases)
        self._language = LanguageDetector(
            rules.language_words, settings.language_min_hits, settings.bilingual_min_share
        )
        self._contract = ContractDetector(phrases, settings.contract_score_threshold)
        self._skills = SkillExtractor(taxonomy)
        self._seniority = SeniorityDetector(rules.seniority)

    @classmethod
    def from_settings(cls, settings: Settings) -> "Normalizer":
        config_dir = settings.config_dir
        return cls(
            settings.normalize,
            rules=load_normalization_rules(config_dir),
            taxonomy=load_skills_taxonomy(config_dir),
            phrases=load_contract_phrases(config_dir),
            aliases=load_company_aliases(config_dir),
            locations=load_canada_locations(config_dir),
        )

    def normalize(self, raw: RawJob, *, now: datetime) -> Job:
        title = clean_text(raw.title).replace("\n", " ")
        description = to_plain_text(raw.description) if raw.description else ""
        description_clean = self._sections.strip_boilerplate(description) if description else ""
        requirements = (
            self._sections.requirements(description, self._settings.max_requirements)
            if description
            else []
        )
        body = "\n".join(part for part in (title, description_clean) if part)

        title_norm = self._titles.normalize(title)
        company_norm = self._companies.normalize(raw.company)
        location = self._locations.parse(raw.location)
        remote_type = location.remote_type
        if remote_type is RemoteType.UNKNOWN:
            remote_type = self._locations.remote_type(title)
        language = self._language.detect(body)
        contract = self._contract.assess(body, raw.employment_type)
        status, filter_reason = self._status(
            raw, now, location, contract, language, description_clean
        )

        return Job(
            job_id=job_id_for(raw.source, raw.source_id),
            source=raw.source,
            source_id=raw.source_id,
            url=raw.url,
            title_raw=title,
            title_norm=title_norm,
            company_raw=clean_text(raw.company) if raw.company else None,
            company_norm=company_norm,
            location_raw=clean_text(raw.location) if raw.location else None,
            city=location.city,
            province=location.province,
            country=location.country,
            remote_type=remote_type,
            employment_type=contract.employment_type,
            contract_signals=contract.signals,
            duration_months=contract.duration_months,
            language=language,
            description_raw=description or None,
            description_clean=description_clean or None,
            requirements=requirements,
            skills=self._skills.extract(body),
            seniority=self._seniority.detect(title),
            pay_min=raw.pay_min,
            pay_max=raw.pay_max,
            pay_unit=raw.pay_unit,
            pay_currency=raw.pay_currency,
            noc_code=raw.noc_code,
            posted_at=raw.posted_at,
            first_seen_at=raw.fetched_at,
            fingerprint=job_fingerprint(company_norm, title_norm, location.city),
            status=status,
            filter_reason=filter_reason,
        )

    def _status(
        self,
        raw: RawJob,
        now: datetime,
        location: LocationInfo,
        contract: ContractAssessment,
        language: Language | None,
        description_clean: str,
    ) -> tuple[JobStatus, str | None]:
        """First failing hard filter wins; otherwise new, or needs_details for stubs."""
        if location.country is not None and location.country != "CA":
            return JobStatus.FILTERED_OUT, FilterReason.NOT_CANADA.value
        if contract.employment_type is EmploymentType.PERMANENT:
            return JobStatus.FILTERED_OUT, FilterReason.NOT_CONTRACT.value
        if raw.posted_at is not None and now - raw.posted_at > timedelta(
            days=self._settings.freshness_days
        ):
            return JobStatus.FILTERED_OUT, FilterReason.STALE.value
        if (
            language is None
            and self._language.word_count(description_clean)
            >= self._settings.language_filter_min_words
        ):
            return JobStatus.FILTERED_OUT, FilterReason.UNSUPPORTED_LANGUAGE.value
        if not description_clean:
            return JobStatus.NEEDS_DETAILS, None
        return JobStatus.NEW, None
