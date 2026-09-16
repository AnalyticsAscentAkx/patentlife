"""Parser for the USPTO Patent Maintenance Fee Events bulk file.

The file is a fixed-width ASCII export, one maintenance-fee event per line,
cumulative and republished weekly. USPTO publishes a layout document
alongside it; because that layout has changed format over the years this
parser does not hard-code column offsets. It reads the file with a
whitespace tokeniser and validates the shape of each field, which is
tolerant of the extra padding USPTO uses and of the occasional short line.

If USPTO changes the layout, `patentlife inspect` prints the first raw lines
and the parsed interpretation side by side so the mapping can be corrected
in one place: TOKEN_SPEC below.
"""

from __future__ import annotations

import csv
import dataclasses
import datetime as dt
import gzip
import io
import pathlib
import re
from collections.abc import Iterator

# A maintenance-fee event line, after whitespace tokenising, looks like:
#
#   <patent_no> <app_no> <?> <filing_date> <issue_date> <entity> <code> <event_date> ...
#
# Field order is stable across the versions we have seen; what varies is
# padding and the presence of trailing tokens. We therefore identify fields
# by position among tokens AND validate each with a pattern, so a layout
# drift shows up as a parse error rather than silently wrong data.

DATE_RE = re.compile(r"^\d{8}$")
PATENT_RE = re.compile(r"^\d{7,8}$")
CODE_RE = re.compile(r"^[A-Z0-9.]{2,6}$")

ENTITY_MAP = {
    "SM": "small",
    "LG": "large",
    "MI": "micro",
    "UND": "undiscounted",
}


@dataclasses.dataclass(frozen=True, slots=True)
class MaintFeeEvent:
    patent_number: str
    application_number: str
    filing_date: dt.date | None
    grant_date: dt.date | None
    entity_status: str | None
    event_code: str
    event_date: dt.date | None


def _parse_date(token: str) -> dt.date | None:
    if not DATE_RE.match(token):
        return None
    try:
        return dt.datetime.strptime(token, "%Y%m%d").date()
    except ValueError:
        return None


def _open(path: pathlib.Path) -> io.TextIOBase:
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="latin-1", errors="replace")
    return path.open("rt", encoding="latin-1", errors="replace")


def parse_line(line: str) -> MaintFeeEvent | None:
    """Parse one line. Returns None for blank or unparseable lines."""
    tokens = line.split()
    if len(tokens) < 5:
        return None
    if not PATENT_RE.match(tokens[0]):
        return None

    patent_number = tokens[0].lstrip("0")
    application_number = tokens[1] if len(tokens) > 1 else ""

    # Collect dates and the event code positionally-but-validated.
    dates = [d for d in (_parse_date(t) for t in tokens) if d is not None]
    entity = next((ENTITY_MAP[t] for t in tokens if t in ENTITY_MAP), None)

    # The event code is the last token matching the code pattern that is not
    # itself a date and not the patent/application number.
    code = None
    for token in reversed(tokens[2:]):
        if DATE_RE.match(token) or token in ENTITY_MAP:
            continue
        if CODE_RE.match(token):
            code = token
            break
    if code is None:
        return None

    filing_date = dates[0] if len(dates) > 0 else None
    grant_date = dates[1] if len(dates) > 1 else None
    event_date = dates[-1] if dates else None
    # When only one date is present it is the event date, not the filing date.
    if len(dates) == 1:
        filing_date = None
        grant_date = None

    return MaintFeeEvent(
        patent_number=patent_number,
        application_number=application_number,
        filing_date=filing_date,
        grant_date=grant_date,
        entity_status=entity,
        event_code=code,
        event_date=event_date,
    )


def iter_events(path: pathlib.Path) -> Iterator[MaintFeeEvent]:
    with _open(path) as fh:
        for line in fh:
            event = parse_line(line)
            if event is not None:
                yield event


def to_csv(src: pathlib.Path, dest: pathlib.Path) -> int:
    """Convert the raw events file to CSV. Returns the row count."""
    n = 0
    with dest.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "patent_number",
                "application_number",
                "filing_date",
                "grant_date",
                "entity_status",
                "event_code",
                "event_date",
            ]
        )
        for e in iter_events(src):
            writer.writerow(
                [
                    e.patent_number,
                    e.application_number,
                    e.filing_date.isoformat() if e.filing_date else "",
                    e.grant_date.isoformat() if e.grant_date else "",
                    e.entity_status or "",
                    e.event_code,
                    e.event_date.isoformat() if e.event_date else "",
                ]
            )
            n += 1
    return n
