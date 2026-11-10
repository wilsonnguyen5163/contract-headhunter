"""Each job's recommendations across scans (0.4): who still needs scanning, what was scanned,
and the rounds (llm/rounds.py has the rules). No API call; reads and appends the CSV files:

- data/job_scans.csv: (job, contractor, profile, basis) judged once by Gemini. A contractor
  is sent for a job only when they were never judged for it (a new job, a new contractor,
  someone just made available), or when a contractor import changed what Gemini sees about
  them after that judgment (data/contractor_changes.csv). Nothing else resends anyone: not a
  changed job text, model or prompt version (user decisions 2026-11-10). A contractor with a
  decision for the job is never sent again (2026-11-09). Jobs never scanned send everyone,
  which is the same question as before 0.4, so the cached judgments are reused (no call).
- data/job_rounds.csv: who holds a slot in which round.
- data/llm_matches.csv: every fit / partial candidate of every scan (the pool).
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime

from pydantic import BaseModel

from jobmatch.config import LlmPayloadSettings, LlmSettings, Settings
from jobmatch.llm.anonymize import Roster, build_roster, card_hash, contractor_card
from jobmatch.llm.payload import is_active, job_card
from jobmatch.llm.prompt import prepare_call
from jobmatch.llm.rounds import (
    JobShortlist,
    Pair,
    State,
    arrange,
    latest_applications,
    latest_reviews,
    latest_rows,
    seats,
    shortlist,
    state_of,
)
from jobmatch.schemas import (
    ContractorChange,
    ContractorProfile,
    Job,
    LlmMatch,
    ScanRecord,
    TalentAvailability,
)
from jobmatch.sources.contractor_upsert import UpsertReport
from jobmatch.storage.repositories import DataLoader

_EMPTY = Roster(cards=[], aliases={})


def scan_basis(job: Job, llm: LlmSettings) -> str:
    return prepare_call(job_card(job, llm.payload), _EMPTY, llm).basis(llm.model)


def pair_states(loader: DataLoader, rows: Sequence[LlmMatch] | None = None) -> dict[Pair, State]:
    """Every (job, contractor) with a decision or an application status, and its state."""
    rows = loader.llm_matches.list_all() if rows is None else rows
    reviews = latest_reviews(rows, loader.reviews.latest_by_match())
    applications = latest_applications(loader.applications.list_all())
    return {
        pair: state_of(reviews.get(pair), applications.get(pair))
        for pair in set(reviews) | set(applications)
    }


def contractors_to_scan(
    jobs: Iterable[Job],
    roster: Roster,
    scans: Iterable[ScanRecord],
    states: Mapping[Pair, State],
    changes: Mapping[str, datetime],
) -> dict[str, Roster]:
    """job_id -> the contractors to send for it; jobs with nobody to send are left out.
    `changes`: talent_id -> their latest contractor change (latest_changes)."""
    last_scan: dict[Pair, datetime] = {}
    for s in scans:
        pair = (s.job_id, s.talent_id)
        if pair not in last_scan or s.scanned_at > last_scan[pair]:
            last_scan[pair] = s.scanned_at
    out: dict[str, Roster] = {}
    for job in jobs:
        keep = []
        for alias, talent_id in roster.aliases.items():
            pair = (job.job_id, talent_id)
            scanned = last_scan.get(pair)
            if scanned is not None:
                if states.get(pair, "awaiting") != "awaiting":
                    continue  # a decision was made on them for this job: never sent again
                changed = changes.get(talent_id)
                if changed is None or changed <= scanned:
                    continue  # judged since their last change
            keep.append(alias)
        if keep:
            out[job.job_id] = roster.subset(keep)
    return out


def latest_changes(loader: DataLoader) -> dict[str, datetime]:
    """talent_id -> when an import last changed what Gemini sees about them."""
    out: dict[str, datetime] = {}
    for c in loader.contractor_changes.list_all():
        if c.talent_id not in out or c.changed_at > out[c.talent_id]:
            out[c.talent_id] = c.changed_at
    return out


def mark_changes(
    loader: DataLoader,
    before: Sequence[ContractorProfile],
    report: UpsertReport,
    payload: LlmPayloadSettings,
    now: datetime,
) -> int:
    """After a contractor import, record who changed (user decisions 2026-11-10): everyone
    added, and everyone edited whose anonymized card differs (edits Gemini never sees, such
    as name, phone or email, are not a change). Compares the stored rows before the import
    with the rows after it. Returns the rows added to contractor_changes.csv."""
    old = {p.talent_id: p for p in before}
    new = {p.talent_id: p for p in loader.contractors.list_all()}
    rows = [
        ContractorChange(talent_id=t, change="added", changed_at=now)
        for t in report.added
        if t in new
    ]
    for t in report.updated:
        a, b = old.get(t), new.get(t)
        if a is None or b is None:
            continue
        if card_hash(contractor_card(a, "-", payload)) == card_hash(
            contractor_card(b, "-", payload)
        ):
            continue  # nothing Gemini sees has changed
        columns = [f for f in ContractorProfile.model_fields if getattr(a, f) != getattr(b, f)]
        rows.append(
            ContractorChange(talent_id=t, change="updated", columns=columns, changed_at=now)
        )
    if rows:
        loader.contractor_changes.add_many(rows)
    return len(rows)


def scan_records(
    job: Job, roster: Roster, judge_key: str, llm: LlmSettings, now: datetime
) -> list[ScanRecord]:
    """The rows to add once a job's judgment against `roster` is saved."""
    basis = scan_basis(job, llm)
    return [
        ScanRecord(
            job_id=job.job_id,
            talent_id=roster.aliases[c.alias],
            card_hash=card_hash(c),
            basis=basis,
            judge_key=judge_key,
            scanned_at=now,
        )  # fmt: skip
        for c in roster.cards
    ]


def latest_toggles(loader: DataLoader) -> dict[str, TalentAvailability]:
    """The reviewer's latest "Available for matching" toggle per talent ID (Talent screen)."""
    out: dict[str, TalentAvailability] = {}
    for row in loader.talent_availability.list_all():  # file order: later rows win ties
        current = out.get(row.talent_id)
        if current is None or row.changed_at >= current.changed_at:
            out[row.talent_id] = row
    return out


def toggles(loader: DataLoader) -> dict[str, bool]:
    return {t: row.available for t, row in latest_toggles(loader).items()}


IMPORT_REVIEWER = "contractor import"  # the "reviewer" on rows written by an import


def reset_toggles_on_import(loader: DataLoader, talent_ids: list[str], now: datetime) -> int:
    """A contractor list import brought new or changed profiles (the contractor filled the
    intake form again, then the reviewer imported it): their own answer takes over from an
    earlier toggle (user decision 2026-11-09). Written as a new toggle row with the profile's
    value, so the history stays. Contractors imported unchanged keep the reviewer's toggle.
    Returns how many were reset."""
    latest = latest_toggles(loader)
    profiles = {p.talent_id: p for p in loader.contractors.list_all()}
    rows = [
        TalentAvailability(
            talent_id=t,
            available=profiles[t].contract,
            note="Set from the imported profile",
            reviewer=IMPORT_REVIEWER,
            changed_at=now,
        )
        for t in talent_ids
        if t in latest and t in profiles
    ]
    if rows:
        loader.talent_availability.add_many(rows)
    return len(rows)


def is_available(profile: ContractorProfile, toggle: bool | None) -> bool:
    """The reviewer's toggle when set (either way), else the profile's own answer."""
    return toggle if toggle is not None else profile.contract


def absorb_cached(settings: Settings, loader: DataLoader, now: datetime | None = None) -> int:
    """Record every saved judgment of an open job that is not recorded yet: its candidates
    into llm_matches.csv (the pool) and its contractors into job_scans.csv. No API call. This
    is how a run's new judgments are stored, and how 0.3 data (judged before job_scans.csv
    existed) is taken over the first time a screen opens. Returns the match rows added."""
    from jobmatch.llm.judge import judgments_dir, load_judgment, plan_jobs
    from jobmatch.llm.matches import judged_job

    llm = settings.llm
    assert llm is not None
    if not loader.contractors.exists():
        return 0
    roster = build_roster(loader.contractors.list_all(), llm.payload, toggles(loader))
    if not roster.cards:
        return 0
    now = now or datetime.now(UTC)
    active = [j for j in loader.jobs.list_all() if is_active(j)]
    states = pair_states(loader)
    changes = latest_changes(loader)
    rosters = contractors_to_scan(active, roster, loader.job_scans.list_all(), states, changes)
    folder = judgments_dir(settings.paths.data_dir)
    by_id = {j.job_id: j for j in active}
    rows: list[LlmMatch] = []
    scanned: list[ScanRecord] = []
    for item in plan_jobs(active, roster, llm, folder, rosters):
        result = load_judgment(folder, item.key) if item.cached else None
        judged = judged_job(result, llm.shortlist, everyone=True) if result else None
        if judged is None:
            continue
        rows += judged.matches
        scanned += scan_records(by_id[item.job_id], item.roster, item.key, llm, now)
    added = len(loader.llm_matches.add_many(rows)) if rows else 0
    if scanned:
        loader.job_scans.add_many(scanned)  # after the rows: an interrupted save re-adds nothing
    return added


class JobPicture(BaseModel):
    """One job's recommendations as the screens need them."""

    shortlist: JobShortlist  # for Results (active jobs; closed jobs: empty)
    to_scan: bool  # contractors still to send to Gemini
    states: dict[str, State]  # talent_id -> state, for contractors with a decision
    rows: dict[str, LlmMatch]  # talent_id -> their newest row for this job
    unavailable: set[str] = set()  # talent IDs not available for matching (Talent screen)


def sync_rounds(
    settings: Settings, loader: DataLoader, now: datetime | None = None
) -> dict[str, JobPicture]:
    """Bring every active job's rounds up to date (llm/rounds.arrange; adds rows to
    job_rounds.csv only when something changes) and return each job's picture, closed jobs
    included (with an empty shortlist). No API call."""
    llm = settings.llm
    assert llm is not None
    now = now or datetime.now(UTC)
    absorb_cached(settings, loader, now)
    jobs = loader.jobs.list_all()
    active = [j for j in jobs if is_active(j)]
    rows = loader.llm_matches.list_all()
    states = pair_states(loader, rows)
    scans = loader.job_scans.list_all()
    profiles = loader.contractors.list_all() if loader.contractors.exists() else []
    switched = toggles(loader)
    roster = build_roster(profiles, llm.payload, switched)
    unavailable = {p.talent_id for p in profiles if not is_available(p, switched.get(p.talent_id))}
    changes = latest_changes(loader)
    to_scan = set(
        contractors_to_scan(active, roster, scans, states, changes) if roster.cards else {}
    )
    to_scan |= {j.job_id for j in active} - {s.job_id for s in scans}  # never scanned yet

    # The judgment that counts per (job, contractor): their latest scan.
    counted: dict[Pair, ScanRecord] = {}
    for s in scans:
        pair = (s.job_id, s.talent_id)
        if pair not in counted or s.scanned_at >= counted[pair].scanned_at:
            counted[pair] = s
    by_key = {(m.job_id, m.talent_id, m.judge_key): m for m in rows}
    newest = latest_rows(rows)
    events = loader.job_rounds.list_all()
    events_of: dict[str, list] = defaultdict(list)
    for e in events:
        events_of[e.job_id].append(e)

    added = []
    pictures: dict[str, JobPicture] = {}
    for job in jobs:
        job_states = {t: s for (j, t), s in states.items() if j == job.job_id}
        job_rows = {t: m for (j, t), m in newest.items() if j == job.job_id}
        if not is_active(job):
            empty = JobShortlist(shown=[], rejected=[])
            pictures[job.job_id] = JobPicture(
                shortlist=empty, to_scan=False, states=job_states, rows=job_rows
            )
            continue
        # Someone not available for matching who is still awaiting review leaves the pool:
        # their slot goes to the next best. Decided ones stay (user decision 2026-11-09).
        pool = {
            t: by_key[(job.job_id, t, s.judge_key)]
            for (j, t), s in counted.items()
            if j == job.job_id
            and (job.job_id, t, s.judge_key) in by_key
            and (t not in unavailable or job_states.get(t, "awaiting") != "awaiting")
        }
        new = arrange(
            job.job_id, pool, job_states, events_of[job.job_id], llm.shortlist,
            newcomers=job.job_id in to_scan, now=now,
        )  # fmt: skip
        added += new
        held = seats(events_of[job.job_id] + new)
        picked = shortlist(held, job_rows, job_states)
        # An awaiting slot from an earlier round is not bumped by arrange: hide it here.
        picked.shown = [
            m
            for m in picked.shown
            if m.talent_id not in unavailable or job_states.get(m.talent_id) == "approved"
        ]
        pictures[job.job_id] = JobPicture(
            shortlist=picked,
            to_scan=job.job_id in to_scan,
            states=job_states,
            rows=job_rows,
            unavailable=unavailable,
        )
    if added:
        loader.job_rounds.add_many(added)
    return pictures
