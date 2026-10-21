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
from jobmatch.normalize.commitment import CommitmentDetector
from jobmatch.normalize.company import CompanyNormalizer
from jobmatch.normalize.contract import ContractAssessment, ContractDetector
from jobmatch.normalize.description import compose_description
from jobmatch.normalize.experience import ExperienceReader
from jobmatch.normalize.fingerprint import job_fingerprint
from jobmatch.normalize.language import LanguageDetector
from jobmatch.normalize.location import LocationInfo, LocationParser
from jobmatch.normalize.pay import PayInfo, PayParser
from jobmatch.normalize.sections import PostingSections, SectionKind, SectionSplitter
from jobmatch.normalize.seniority import SeniorityDetector
from jobmatch.normalize.skills import SkillExtractor
from jobmatch.normalize.text import clean_text, to_plain_text
from jobmatch.normalize.title import TitleNormalizer
from jobmatch.normalize.work_mode import resolve
from jobmatch.schemas import (
    EmploymentType,
    FilterReason,
    Job,
    JobStatus,
    Language,
    RawJob,
    RemoteType,
)

# Lines up to this many words (or "Label: value" with a value this short) are read for
# work-mode terms; longer sentences only through the hybrid patterns.
_MAX_MODE_LINE_WORDS = 6


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
        self._contract = ContractDetector(
            phrases, settings.contract_score_threshold, rules.number_words
        )
        self._experience = ExperienceReader(rules.number_words)
        self._skills = SkillExtractor(taxonomy)
        self._seniority = SeniorityDetector(rules.seniority)
        self._pay = PayParser(rules.pay)
        self._commitment = CommitmentDetector(phrases.commitment)

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
        sections = self._sections.parse(description)
        # job_text: the job itself (intro and kept sections, metadata included). Skills,
        # language, contract wording and work mode are read from it, never from the employer
        # pitch, benefits or pay notes.
        job_text = sections.clean_text()
        limit = self._settings.max_section_items
        responsibilities = sections.items(
            SectionKind.RESPONSIBILITIES, self._settings.max_responsibilities
        )
        requirements = sections.items(SectionKind.REQUIREMENTS, limit)
        nice_to_have = sections.items(SectionKind.NICE_TO_HAVE, limit)
        body = "\n".join(part for part in (title, job_text) if part)
        pay = self._pay_from(raw, sections.text({SectionKind.PAY}), job_text)
        # description_clean: the pruned, fixed-order description (name, responsibilities,
        # requirements, pay) without intro prose, reporting lines, metadata or application
        # steps. Prose is kept only for postings with no lists at all.
        description_clean = (
            compose_description(
                title,
                responsibilities,
                requirements,
                nice_to_have,
                pay,
                fallback=sections.role_lines(),
            )
            if job_text
            else ""
        )

        title_norm = self._titles.normalize(title)
        company_norm = self._companies.normalize(raw.company)
        location = self._locations.parse(raw.location)
        remote_type = self._work_mode(raw.location, title, sections)
        language = self._language.detect(body)
        # The source's own employment-type text ("Contract, full-time") is read for contract
        # wording too; an exact structured value ("Permanent") still wins inside assess().
        contract_text = "\n".join(p for p in (raw.employment_type, body) if p)
        contract = self._contract.assess(contract_text, raw.employment_type)
        # Full-/part-time never filters a posting; it is recorded next to the employment type.
        commitment = self._commitment.detect(raw.employment_type, title, job_text)
        status, filter_reason = self._status(raw, now, location, contract, language, job_text)

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
            commitment=commitment,
            contract_signals=contract.signals,
            duration_months=contract.duration_months,
            language=language,
            description_raw=description or None,
            description_clean=description_clean or None,
            responsibilities=responsibilities,
            requirements=requirements,
            nice_to_have=nice_to_have,
            skills=self._skills.extract(self._skills_text(title, sections, body)),
            seniority=self._seniority.detect(title),
            # every requirement line, including ones after a list ends or past the cap
            years_experience_min=self._experience.years_min(
                sections.lines({SectionKind.REQUIREMENTS, SectionKind.NICE_TO_HAVE})
            ),
            pay_min=pay.pay_min if pay else None,
            pay_max=pay.pay_max if pay else None,
            pay_unit=pay.unit if pay else None,
            pay_currency=pay.currency if pay else None,
            noc_code=raw.noc_code,
            posted_at=raw.posted_at,
            first_seen_at=raw.fetched_at,
            fingerprint=job_fingerprint(company_norm, title_norm, location.city),
            status=status,
            filter_reason=filter_reason,
        )

    @staticmethod
    def _skills_text(title: str, sections: PostingSections, body: str) -> str:
        """Skills come from the job itself. When the posting has lists, that is the title,
        the overview and the bullets, so a company-intro sentence ("...Commerce, CRM, and
        Data") adds nothing; a posting without lists falls back to all of its job text."""
        lists = [
            *sections.items(SectionKind.RESPONSIBILITIES, None),
            *sections.items(SectionKind.REQUIREMENTS, None),
            *sections.items(SectionKind.NICE_TO_HAVE, None),
        ]
        if not lists:
            return body
        return "\n".join([title, sections.text({SectionKind.OVERVIEW}), *lists])

    def _work_mode(self, location: str | None, title: str, sections: PostingSections) -> RemoteType:
        """Modes from the location, the title, then the posting's own short lines
        ("Workplace Type: Hybrid", or "Hybrid" under a "Workplace Type:" line), resolved
        with the precedence rule in work_mode.py. Long sentences only count through the
        hybrid patterns ("3 days a week in the office"); bullets of duties never count
        ("Remote monitoring of servers")."""
        detector = self._locations.work_mode
        signals = [*detector.signals(location), *detector.signals(title)]
        info_kinds = {SectionKind.INTRO, SectionKind.OVERVIEW, SectionKind.OTHER}
        for line in sections.lines(info_kinds):
            value = line.split(":", 1)[1] if ":" in line else line
            short = len(line.split()) <= _MAX_MODE_LINE_WORDS or (
                ":" in line and len(value.split()) <= _MAX_MODE_LINE_WORDS
            )
            signals += detector.signals(value if ":" in line else line, terms=short)
        return resolve(signals)

    def _pay_from(self, raw: RawJob, pay_text: str, job_text: str) -> PayInfo | None:
        """The source's structured pay wins; otherwise read it from the posting text."""
        given = [v for v in (raw.pay_min, raw.pay_max) if v is not None]
        if given:
            return PayInfo.of(given, raw.pay_unit, raw.pay_currency)
        return self._pay.parse(pay_text, job_text)

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
