"""Rule checks for one (job, contractor) pair.

A failed check returns its reason, and the match goes to matches_flagged.csv for review
instead of matches.csv. A flag is information only and never moves or drops a match.
Unknown data always passes (recall first): only a clear mismatch fails.
"""

from jobmatch.config import MatchingSettings
from jobmatch.normalize.seniority import LEVELS
from jobmatch.schemas import PROVINCE_CODES, ContractorProfile, Job, Language, PayUnit, RemoteType


def seniority_check(
    job_level: str | None, contractor_levels: list[str], max_gap: int
) -> str | None:
    """Fails when the job's level is more than max_gap levels from every level the
    contractor holds, in either direction. The reason names both sides."""
    if job_level is None or not contractor_levels:
        return None
    job_index = LEVELS.index(job_level)
    held = [LEVELS.index(level) for level in contractor_levels]
    if min(abs(job_index - i) for i in held) <= max_gap:
        return None
    if max(held) < job_index:
        kind = "under-qualified"
    elif min(held) > job_index:
        kind = "over-qualified"
    else:
        kind = "levels far apart"
    return f"seniority: {kind} (job {job_level}, contractor {'/'.join(contractor_levels)})"


def contractor_province(location: str | None) -> str | None:
    """ "Toronto, ON" -> "CA-ON". Anything else ("Canada", blank) is unknown."""
    if not location:
        return None
    code = location.rsplit(",", 1)[-1].strip().upper()
    return f"CA-{code}" if code in PROVINCE_CODES else None


def location_check(job: Job, profile: ContractorProfile, settings: MatchingSettings) -> str | None:
    """Off while assume_all_remote is true (user decision 2026-10-26). Otherwise an on-site
    or hybrid job in another province than the contractor's fails."""
    if settings.assume_all_remote or job.remote_type == RemoteType.REMOTE:
        return None
    home = contractor_province(profile.location)
    if job.province is None or home is None or job.province == home:
        return None
    return f"location: {job.remote_type.value} job in {job.province}, contractor in {home}"


def hourly_pay_max(job: Job, hours_per_year: float) -> float | None:
    """The job's top pay as an hourly figure (pay_max, else pay_min), or None."""
    pay = job.pay_max if job.pay_max is not None else job.pay_min
    if pay is None or job.pay_unit is None:
        return None
    week = hours_per_year / 52
    hours = {
        PayUnit.HOUR: 1.0,
        PayUnit.DAY: week / 5,
        PayUnit.WEEK: week,
        PayUnit.MONTH: hours_per_year / 12,
        PayUnit.YEAR: hours_per_year,
    }[job.pay_unit]
    return pay / hours


def contractor_rate(profile: ContractorProfile) -> float | None:
    """The hourly rate, or None for the default text ("Industry Standard")."""
    try:
        return float(profile.rate_expectation)
    except ValueError:
        return None


def is_thin(profile: ContractorProfile) -> bool:
    """No summary and no core strengths: the profile is little more than a title."""
    return profile.executive_summary is None and not profile.core_strengths


def flags(job: Job, profile: ContractorProfile, settings: MatchingSettings) -> list[str]:
    """Information for the reviewer. Hours and full-/part-time are shown, never checked."""
    notes: list[str] = []
    rate = contractor_rate(profile)
    top = hourly_pay_max(job, settings.hours_per_year)
    if rate is not None and top is not None and rate > top * settings.rate_tolerance:
        notes.append(f"rate above posted pay (contractor ${rate:g}/h, job up to ${top:.0f}/h)")
    if job.language == Language.FR:
        notes.append("posting in French")
    elif job.language == Language.BILINGUAL:
        notes.append("posting bilingual (EN/FR)")
    if is_thin(profile):
        notes.append("thin profile (no summary or strengths)")
    return notes
