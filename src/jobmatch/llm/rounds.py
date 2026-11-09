"""Who each job recommends, in rounds (0.4, user decisions 2026-11-09). Pure functions: no
API call, no file access (pipeline.sync_rounds does the reading and writing).

- **The pool:** every contractor Gemini rated fit or partial for the job, over all its scans
  (llm_matches.csv rows of the judgments recorded in job_scans.csv; the newest row per
  contractor counts).
- **A contractor's state for the job:**
  - "removed": their application was rejected or ghosted (Jobs screen); out of
    consideration for this job, hidden everywhere but the Jobs table, where Undo brings them
    back;
  - "approved" / "rejected": the reviewer's latest decision on Results;
  - "awaiting": no decision, "needs info", or the decision was reset.
- **A round** shows up to llm.shortlist.max_fits fits, or llm.shortlist.partials_when_no_fit
  partials when nobody fits (the same rule as before). In a round:
  - decided contractors keep their slot (a reviewer rejection is hidden behind "Show
    rejected" but still uses it);
  - an awaiting contractor loses the slot to a higher-ranked one from the reserve (a
    newcomer, usually) and goes back to the reserve.
- **When everyone in the round is decided,** the next round opens with the best of the
  reserve, but only once the job has no contractor left to scan (newcomers are scanned
  first, then the round is filled from them and the reserve together).
- **Shown on Results:** everyone approved (any round) and everyone awaiting in a slot, best
  first, whatever their decision; the reviewer's rejections behind a button.
"""

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from jobmatch.config import LlmShortlistSettings
from jobmatch.schemas import (
    Application,
    ApplicationStatus,
    Decision,
    LlmMatch,
    LlmVerdict,
    Review,
    RoundEvent,
    RoundEventKind,
)

State = Literal["awaiting", "approved", "rejected", "removed"]
Pair = tuple[str, str]  # (job_id, talent_id)


def rank(m: LlmMatch) -> tuple[bool, bool, int, int]:
    """Best first: fits, then those reviewed in depth, then the higher score; ties keep the
    judgment's own order."""
    return (m.verdict is not LlmVerdict.FIT, not m.detailed, -(m.fit_score or 0), m.rank)


def latest_rows(rows: Iterable[LlmMatch]) -> dict[Pair, LlmMatch]:
    """The newest row per (job, contractor)."""
    out: dict[Pair, LlmMatch] = {}
    for m in rows:
        key = (m.job_id, m.talent_id)
        if key not in out or m.created_at >= out[key].created_at:
            out[key] = m
    return out


def latest_reviews(rows: Iterable[LlmMatch], by_match: Mapping[str, Review]) -> dict[Pair, Review]:
    """The latest reviewer decision per (job, contractor), whichever scan's row it was made
    on, so a decision survives a rescan."""
    pair_of = {m.match_id: (m.job_id, m.talent_id) for m in rows}
    out: dict[Pair, Review] = {}
    for match_id, review in by_match.items():
        pair = pair_of.get(match_id)
        if pair and (pair not in out or review.reviewed_at >= out[pair].reviewed_at):
            out[pair] = review
    return out


def latest_applications(rows: Iterable[Application]) -> dict[Pair, Application]:
    out: dict[Pair, Application] = {}
    for a in rows:  # file order: later rows win ties
        key = (a.job_id, a.talent_id)
        if key not in out or a.updated_at >= out[key].updated_at:
            out[key] = a
    return out


def state_of(review: Review | None, application: Application | None) -> State:
    if application is not None and application.status is ApplicationStatus.REJECTED:
        return "removed"
    if review is None:
        return "awaiting"
    if review.decision is Decision.APPROVED:
        return "approved"
    if review.decision is Decision.REJECTED:
        return "rejected"
    return "awaiting"  # needs info, or reset


def seats(events: Iterable[RoundEvent]) -> dict[str, int]:
    """talent_id -> the round they hold a slot in (seated and not bumped since)."""
    out: dict[str, int] = {}
    for e in events:  # file order
        if e.event is RoundEventKind.SEATED:
            out[e.talent_id] = e.round
        else:
            out.pop(e.talent_id, None)
    return out


def _pick(
    candidates: list[LlmMatch], decided: list[LlmMatch], rules: LlmShortlistSettings
) -> list[LlmMatch]:
    """The best to fill a round's free slots: fits only when anyone fits (in the round or
    among the candidates), else the best partials."""
    ranked = sorted(candidates, key=rank)
    if any(m.verdict is LlmVerdict.FIT for m in candidates + decided):
        cap, eligible = rules.max_fits, [m for m in ranked if m.verdict is LlmVerdict.FIT]
    else:
        cap = rules.partials_when_no_fit
        eligible = [m for m in ranked if m.verdict is LlmVerdict.PARTIAL]
    return eligible[: max(cap - len(decided), 0)]


def arrange(
    job_id: str,
    pool: Mapping[str, LlmMatch],
    states: Mapping[str, State],
    events: list[RoundEvent],
    rules: LlmShortlistSettings,
    *,
    newcomers: bool,
    now: datetime,
) -> list[RoundEvent]:
    """The round events to add for one job (none when nothing changes). `pool`: talent_id
    -> their newest row; `events`: this job's round events so far, in file order;
    `newcomers`: the job still has contractors to scan."""

    def state(talent_id: str) -> State:
        return states.get(talent_id, "awaiting")

    def event(round_no: int, talent_id: str, kind: RoundEventKind) -> RoundEvent:
        return RoundEvent(job_id=job_id, round=round_no, talent_id=talent_id, event=kind, at=now)

    held = seats(events)
    current = max((e.round for e in events), default=0)
    in_round = [t for t, r in held.items() if r == current]
    if current == 0 or all(state(t) != "awaiting" for t in in_round):
        if newcomers:
            return []  # scan them first, then fill the next round from them and the reserve

        # The reserve: awaiting and not in a slot. The first round also takes contractors
        # decided before rounds existed (0.3 data), so their decisions keep their place.
        def open_to(t: str) -> bool:
            return state(t) == "awaiting" or (current == 0 and state(t) != "removed")

        reserve = [m for t, m in pool.items() if t not in held and open_to(t)]
        picked = _pick(reserve, [], rules)
        return [event(current + 1, m.talent_id, RoundEventKind.SEATED) for m in picked]
    # An open round: decided slots stay; awaiting slots go to the best awaiting candidates.
    decided = [pool[t] for t in in_round if state(t) != "awaiting" and t in pool]
    awaiting = [t for t in in_round if state(t) == "awaiting"]
    candidates = [pool[t] for t in awaiting if t in pool] + [
        m for t, m in pool.items() if t not in held and state(t) == "awaiting"
    ]
    keep = {m.talent_id for m in _pick(candidates, decided, rules)}
    out = [event(current, t, RoundEventKind.BUMPED) for t in awaiting if t not in keep]
    out += [event(current, t, RoundEventKind.SEATED) for t in sorted(keep) if t not in awaiting]
    return out


class JobShortlist(BaseModel):
    shown: list[LlmMatch]  # approved (any round) and awaiting in a slot, best first
    rejected: list[LlmMatch]  # the reviewer's rejections, behind "Show rejected"


def shortlist(
    held: Mapping[str, int],
    rows: Mapping[str, LlmMatch],
    states: Mapping[str, State],
) -> JobShortlist:
    """What Results shows for one job. `held`: seats(); `rows`: talent_id -> newest row."""
    shown: list[LlmMatch] = []
    rejected: list[LlmMatch] = []
    for talent_id in held:
        row = rows.get(talent_id)
        if row is None:
            continue
        state = states.get(talent_id, "awaiting")
        if state in ("awaiting", "approved"):
            shown.append(row)
        elif state == "rejected":
            rejected.append(row)
    return JobShortlist(shown=sorted(shown, key=rank), rejected=sorted(rejected, key=rank))
