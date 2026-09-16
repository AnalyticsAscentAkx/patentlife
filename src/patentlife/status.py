"""Derive US in-force status from maintenance-fee events.

What this computes, and what it deliberately does not:

  computed   - statutory term expiry from filing/grant dates
             - lapse for non-payment, and revival, from the event stream
             - the resulting state today

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
    last_event_code: str | None
    last_event_date: dt.date | None
    fee_payments: int
    unmapped_codes: tuple[str, ...]


class CodeMap:
    """Maps a maintenance-fee event code to its effect on in-force state."""

    def __init__(self, spec: dict):
        self.codes: dict[str, str] = dict(spec.get("codes") or {})
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
        fee_payments = 0
        unmapped: set[str] = set()
        for event in patent_events:
            effect = code_map.effect(event.event_code)
            if effect == "EXPIRED":
                lapsed = True
            elif effect in ("REINSTATED", "FEE_PAID"):
                lapsed = False
                if effect == "FEE_PAID":
                    fee_payments += 1
            elif effect == "UNKNOWN":
                unmapped.add(event.event_code)

        expiry = statutory_expiry(filing, grant)

        if expiry is not None and as_of >= expiry:
            state = "EXPIRED_TERM"
        elif lapsed:
            state = "LAPSED"
        elif filing is None:
            state = "UNKNOWN"
        else:
            state = "IN_FORCE"

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
                last_event_code=last.event_code,
                last_event_date=last.event_date,
                fee_payments=fee_payments,
                unmapped_codes=tuple(sorted(unmapped)),
            )
        )
    return out
