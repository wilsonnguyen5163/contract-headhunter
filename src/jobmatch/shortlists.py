"""Each job's recommendations across scans (0.4): who still needs scanning, what was scanned,
and the rounds (llm/rounds.py has the rules). No API call; reads and appends the CSV files:

- data/job_scans.csv: (job, contractor, profile, basis) judged once by Gemini. A rescan sends
  only contractors without a row for the job's current basis and their current profile; a
  contractor with a decision for the job is never sent again (user decisions 2026-11-09).
  Jobs never scanned send everyone, which is the same question as before 0.4, so the cached
  judgments are reused (no call).
- data/job_rounds.csv: who holds a slot in which round.
- data/llm_matches.csv: every fit / partial candidate of every scan (the pool).
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime

from pydantic import BaseModel

from jobmatch.config import LlmSettings, Settings
from jobmatch.llm.anonymize import Roster, build_roster, card_hash
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
from jobmatch.schemas import Job, LlmMatch, ScanRecord
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
    llm: LlmSettings,
    scans: Iterable[ScanRecord],
    states: Mapping[Pair, State],
) -> dict[str, Roster]:
    """job_id -> the contractors to send for it; jobs with nobody to send are left out."""
    judged: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    judged_any: dict[str, set[str]] = defaultdict(set)
    for s in scans:
        judged[s.job_id].add((s.talent_id, s.card_hash, s.basis))
        judged_any[s.job_id].add(s.talent_id)
    hashes = {c.alias: card_hash(c) for c in roster.cards}
    out: dict[str, Roster] = {}
    for job in jobs:
        basis = scan_basis(job, llm)
        keep = []
        for alias, talent_id in roster.aliases.items():
            if (talent_id, hashes[alias], basis) in judged[job.job_id]:
                continue  # judged on this basis, with this profile
            decided = states.get((job.job_id, talent_id), "awaiting") != "awaiting"
            if decided and talent_id in judged_any[job.job_id]:
                continue  # a decision was made on them for this job: never sent again
            keep.append(alias)
        if keep:
            out[job.job_id] = roster.subset(keep)
    return out


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
    roster = build_roster(loader.contractors.list_all(), llm.payload)
    if not roster.cards:
        return 0
    now = now or datetime.now(UTC)
    active = [j for j in loader.jobs.list_all() if is_active(j)]
    states = pair_states(loader)
    rosters = contractors_to_scan(active, roster, llm, loader.job_scans.list_all(), states)
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
    roster = build_roster(profiles, llm.payload)
    to_scan = set(contractors_to_scan(active, roster, llm, scans, states) if roster.cards else {})
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
        pool = {
            t: by_key[(job.job_id, t, s.judge_key)]
            for (j, t), s in counted.items()
            if j == job.job_id and (job.job_id, t, s.judge_key) in by_key
        }
        new = arrange(
            job.job_id, pool, job_states, events_of[job.job_id], llm.shortlist,
            newcomers=job.job_id in to_scan, now=now,
        )  # fmt: skip
        added += new
        held = seats(events_of[job.job_id] + new)
        pictures[job.job_id] = JobPicture(
            shortlist=shortlist(held, job_rows, job_states),
            to_scan=job.job_id in to_scan,
            states=job_states,
            rows=job_rows,
        )
    if added:
        loader.job_rounds.add_many(added)
    return pictures
