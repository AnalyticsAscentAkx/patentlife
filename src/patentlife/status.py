"""Derive US in-force status from maintenance-fee events.

What this computes, and what it deliberately does not:

  computed   - statutory term expiry from filing/grant dates
             - lapse for non-payment, and revival, from the event stream
             - the resulting state today, and the date it actually ended
             - which maintenance-fee window was last paid, when the next one
               falls due, and whether that deadline has passed

  NOT        - Patent Term Adjustment (PTA) or Extension (PTE). Both push the
               real expiry later, sometimes by years. The USPTO maintenance
               fee file does not carry them.
             - terminal disclaimers, which pull expiry earlier.
             - anything about validity. An in-force patent may still be
               invalid.

So `expiry_estimated` is a floor, not a date to rely on. Every consumer of
this module should carry that caveat through to the user.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import pathlib
from collections import defaultdict
from collections.abc import Iterable

import yaml

from .maintfee import MaintFeeEvent

# Applications filed on or after this date get a 20-years-from-filing term.
# Earlier ones get the greater of 17 from grant and 20 from filing.
URAA_DATE = dt.date(1995, 6, 8)

_EFFECT_ORDER = ("EXPIRED", "REINSTATED", "FEE_PAID", "NEUTRAL", "UNKNOWN")


@dataclasses.dataclass(frozen=True, slots=True)
class PatentStatus:
    patent_number: str
    application_number: str
    filing_date: dt.date | None
    grant_date: dt.date | None
    entity_status: str | None
    state: str  # IN_FORCE | LAPSED | EXPIRED_TERM | UNKNOWN
    expiry_estimated: dt.date | None
    lapse_date: dt.date | None
    effective_end: dt.date | None
    last_fee_stage: int | None
    next_fee_due: dt.date | None
    fee_status: str | None  # CURRENT | DUE_SOON | OVERDUE | COMPLETE
    last_event_code: str | None
    last_event_date: dt.date | None
    fee_payments: int
    unmapped_codes: tuple[str, ...]


class CodeMap:
    """Maps a maintenance-fee event code to its effect on in-force state."""

    def __init__(self, spec: dict):
        self.codes: dict[str, str] = dict(spec.get("codes") or {})
        self.fee_stage: dict[str, int] = {
            str(k): int(v) for k, v in (spec.get("fee_stage") or {}).items()
        }
        prefixes = spec.get("prefixes") or {}
        # Longest prefix wins, so sort descending by length once.
        self.prefixes: list[tuple[str, str]] = sorted(
            prefixes.items(), key=lambda kv: -len(kv[0])
        )
        self.seen_unmapped: set[str] = set()

    @classmethod
    def load(cls, path: pathlib.Path | None = None) -> "CodeMap":
        path = path or pathlib.Path(__file__).with_name("codes.yaml")
        with path.open("rt", encoding="utf-8") as fh:
            return cls(yaml.safe_load(fh) or {})

    def effect(self, code: str) -> str:
        if code in self.codes:
            return self.codes[code]
        for prefix, effect in self.prefixes:
            if code.startswith(prefix):
                return effect
        self.seen_unmapped.add(code)
        return "UNKNOWN"

    def stage(self, code: str) -> int | None:
        """Which maintenance window (4, 8 or 12 years) this payment settles."""
        return self.fee_stage.get(code)


def statutory_expiry(
    filing_date: dt.date | None, grant_date: dt.date | None
) -> dt.date | None:
    """Statutory term expiry, ignoring PTA/PTE and terminal disclaimers."""
    if filing_date is None:
        return None
    twenty = _add_years(filing_date, 20)
    if filing_date >= URAA_DATE or grant_date is None:
        return twenty
    seventeen = _add_years(grant_date, 17)
    return max(twenty, seventeen)


def _add_years(date: dt.date, years: int) -> dt.date:
    try:
        return date.replace(year=date.year + years)
    except ValueError:  # 29 February
        return date.replace(year=date.year + years, day=28)


def _add_months(date: dt.date, months: int) -> dt.date:
    """Shift by whole months, clamping to the end of a shorter month."""
    total = date.month - 1 + months
    year = date.year + total // 12
    month = total % 12 + 1
    day = min(date.day, _DAYS_IN_MONTH[month] + (month == 2 and _is_leap(year)))
    return dt.date(year, month, day)


_DAYS_IN_MONTH = {1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30,
                  7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


# Maintenance fees fall due at 3.5, 7.5 and 11.5 years from grant, each with a
# six-month grace period. `codes.yaml` names the windows by the end of their
# grace period (4, 8, 12), so map that name to the months at which the fee for
# the NEXT window falls due.
_NEXT_FEE_MONTHS = {None: 42, 4: 90, 8: 138, 12: None}

# A fee deadline this close counts as imminent. Twelve months, because the
# window for paying opens six months before the due date and a docketing
# review that only surfaced fees already inside that window would be useless.
DUE_SOON = dt.timedelta(days=365)


def next_fee_due(grant_date: dt.date | None, last_stage: int | None) -> dt.date | None:
    """When the next maintenance fee falls due, or None if none remains.

    After the 12-year fee no further maintenance fees are payable, so a patent
    that has paid it runs to its statutory term untouched by this file.
    """
    if grant_date is None:
        return None
    months = _NEXT_FEE_MONTHS.get(last_stage, None)
    if months is None:
        return None
    return _add_months(grant_date, months)


def fee_status(
    next_due: dt.date | None, last_stage: int | None, as_of: dt.date
) -> str | None:
    """Label the next fee deadline, so a date in the past reads correctly.

    `next_due` is derived purely from what the file records as paid, so it
    can sit in the past: either the file predates a payment USPTO has since
    recorded, or the patent has missed a window and an EXP. is coming. Both
    are worth seeing, and neither is served by printing a bare stale date.
    """
    if last_stage == 12:
        return "COMPLETE"
    if next_due is None:
        return None
    if next_due < as_of:
        return "OVERDUE"
    if next_due - as_of <= DUE_SOON:
        return "DUE_SOON"
    return "CURRENT"


def derive(
    events: Iterable[MaintFeeEvent],
    code_map: CodeMap,
    as_of: dt.date | None = None,
) -> list[PatentStatus]:
    """Collapse an event stream into one status row per patent."""
    as_of = as_of or dt.date.today()
    by_patent: dict[str, list[MaintFeeEvent]] = defaultdict(list)
    for event in events:
        by_patent[event.patent_number].append(event)

    out: list[PatentStatus] = []
    for patent_number, patent_events in by_patent.items():
        patent_events.sort(key=lambda e: (e.event_date or dt.date.min))

        filing = next((e.filing_date for e in patent_events if e.filing_date), None)
        grant = next((e.grant_date for e in patent_events if e.grant_date), None)
        entity = next((e.entity_status for e in patent_events if e.entity_status), None)
        app_no = next((e.application_number for e in patent_events if e.application_number), "")

        lapsed = False
        lapse_date: dt.date | None = None
        fee_payments = 0
        last_stage: int | None = None
        unmapped: set[str] = set()
        for event in patent_events:
            effect = code_map.effect(event.event_code)
            if effect == "EXPIRED":
                lapsed = True
                lapse_date = event.event_date
            elif effect in ("REINSTATED", "FEE_PAID"):
                # A later payment or revival undoes an earlier lapse, so the
                # lapse date is cleared with it rather than left to leak into
                # effective_end.
                lapsed = False
                lapse_date = None
                if effect == "FEE_PAID":
                    fee_payments += 1
                    stage = code_map.stage(event.event_code)
                    if stage is not None and (last_stage is None or stage > last_stage):
                        last_stage = stage
            elif effect == "UNKNOWN":
                unmapped.add(event.event_code)

        expiry = statutory_expiry(filing, grant)

        # A patent that lapsed before its term ran out died on the lapse, not
        # on the term. Report whichever came first rather than letting the
        # statutory date mask an earlier death.
        if lapsed and lapse_date is not None and (expiry is None or lapse_date <= expiry):
            state = "LAPSED"
        elif expiry is not None and as_of >= expiry:
            state = "EXPIRED_TERM"
        elif lapsed:
            state = "LAPSED"
        elif filing is None:
            state = "UNKNOWN"
        else:
            state = "IN_FORCE"

        ends = [d for d in (expiry, lapse_date if lapsed else None) if d is not None]
        effective_end = min(ends) if ends else None

        if state == "IN_FORCE":
            upcoming = next_fee_due(grant, last_stage)
            fees = fee_status(upcoming, last_stage, as_of)
        else:
            upcoming, fees = None, None

        last = patent_events[-1]
        out.append(
            PatentStatus(
                patent_number=patent_number,
                application_number=app_no,
                filing_date=filing,
                grant_date=grant,
                entity_status=entity,
                state=state,
                expiry_estimated=expiry,
                lapse_date=lapse_date if lapsed else None,
                effective_end=effective_end,
                last_fee_stage=last_stage,
                next_fee_due=upcoming,
                fee_status=fees,
                last_event_code=last.event_code,
                last_event_date=last.event_date,
                fee_payments=fee_payments,
                unmapped_codes=tuple(sorted(unmapped)),
            )
        )
    return out
