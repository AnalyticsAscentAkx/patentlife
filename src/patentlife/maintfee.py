"""Parser for the USPTO Patent Maintenance Fee Events bulk file.

The file is a fixed-width ASCII export, one maintenance-fee event per line,
cumulative and republished weekly. Column slicing is therefore the correct
way to read it; a whitespace tokeniser is only a fallback, because a blank
field silently shifts every later field and yields confident wrong dates.

Layout provenance
-----------------
The current layout is USPTO's own, from `MaintFeeEventsFileDocumentation.doc`
(June 2018), which describes 1-based inclusive columns:

    1-13 patent number . 15-22 application number . 24 entity status .
    26-33 filing date . 35-42 grant date . 44-51 event date . 53-57 event code

It is corroborated by two independent working implementations that agree on
the same offsets: `iamlemec/fastpat` (`read_fwf` colspecs) and
`matusfaro/invented` (string slices). See LAYOUTS below.

Files published before roughly 2014 use a narrower 7-character patent-number
field, which shifts every subsequent field left by exactly 6. That variant is
corroborated by `aniemerg/Patent-Tools`, written against a January 2012 file.
Rather than guess which one a given file is, `detect_layout` scores both
against real lines and picks the one that actually parses.
"""

from __future__ import annotations

import csv
import dataclasses
import datetime as dt
import gzip
import io
import pathlib
import re
import zipfile
from collections.abc import Iterator

DATE_RE = re.compile(r"^\d{8}$")
PATENT_RE = re.compile(r"^\d{7,8}$")
CODE_RE = re.compile(r"^[A-Z0-9.]{2,6}$")

# Grant numbers in this file are not always plain utility numbers: reissues
# (RE), designs (D), plant patents (PP) and a few historical kinds carry a
# letter prefix inside the same fixed-width field. The field is zero-padded
# to its full width, and the padding sits BEFORE the letter prefix, so
# 'RE35000' arrives as '000000RE35000'.
DOC_NUMBER_RE = re.compile(r"^0*[A-Z]{0,2}\d{4,13}$")

# Entity status is a single character in the current layout. USPTO records
# only the small-entity flag here; micro entity is visible in the M2xxx event
# codes, not in this column. Anything else is passed through unchanged rather
# than mapped to a guess.
ENTITY_MAP = {
    "Y": "small",
    "N": "large",
    # Token forms, kept for the whitespace fallback and older exports.
    "SM": "small",
    "LG": "large",
    "MI": "micro",
    "UND": "undiscounted",
}

# 0-based [start, end) slices, matching Python. See "Layout provenance" above.
LAYOUT_2018: dict[str, tuple[int, int]] = {
    "patent_number": (0, 13),
    "application_number": (14, 22),
    "entity_status": (23, 24),
    "filing_date": (25, 33),
    "grant_date": (34, 42),
    "event_date": (43, 51),
    "event_code": (52, 57),
}

# Pre-~2014 files: patent number occupies 7 characters instead of 13, so
# everything after it sits 6 columns to the left. The patent, entity, event
# date and event code offsets are confirmed against a 2012-era implementation;
# the filing and grant date offsets are the same uniform -6 shift and are the
# one part of this variant not independently confirmed.
LAYOUT_LEGACY: dict[str, tuple[int, int]] = {
    "patent_number": (0, 7),
    "application_number": (8, 16),
    "entity_status": (17, 18),
    "filing_date": (19, 27),
    "grant_date": (28, 36),
    "event_date": (37, 45),
    "event_code": (46, 51),
}

LAYOUTS: dict[str, dict[str, tuple[int, int]]] = {
    "2018": LAYOUT_2018,
    "legacy": LAYOUT_LEGACY,
}

# The layout applied when none is specified. Kept under the old name so
# callers that imported LAYOUT keep working.
LAYOUT = LAYOUT_2018

# True once the offsets have been read off a real USPTO download. They are
# currently taken from USPTO's own layout document and match two independent
# implementations, which is strong but is not the same as having seen the
# file. `detect_layout` is what actually protects the output: a file that
# matches neither layout raises rather than parsing into plausible nonsense.
LAYOUT_VERIFIED = False


@dataclasses.dataclass(frozen=True, slots=True)
class MaintFeeEvent:
    patent_number: str
    application_number: str
    filing_date: dt.date | None
    grant_date: dt.date | None
    entity_status: str | None
    event_code: str
    event_date: dt.date | None


class LayoutError(ValueError):
    """Raised when a file matches no known column layout."""


def _parse_date(token: str) -> dt.date | None:
    if not DATE_RE.match(token):
        return None
    try:
        return dt.datetime.strptime(token, "%Y%m%d").date()
    except ValueError:
        return None


def normalise_doc_number(raw: str) -> str:
    """Strip the zero padding without destroying a kind-code prefix.

    '00000RE35000' -> 'RE35000', '0004593999' -> '4593999'.
    """
    token = raw.strip().upper()
    if not token:
        return ""
    prefix = ""
    body = token
    match = re.match(r"^(\d*)([A-Z]{1,2})(\d+)$", token)
    if match:
        prefix, body = match.group(2), match.group(3)
    elif token[:2].isalpha():
        prefix, body = token[:2], token[2:]
    elif token[:1].isalpha():
        prefix, body = token[:1], token[1:]
    return prefix + (body.lstrip("0") or "0")


def _open(path: pathlib.Path) -> io.TextIOBase:
    """Open a events file as text, whether it is raw, gzipped or zipped.

    USPTO ships the file inside a .zip, so reading the archive directly is
    the normal case and unpacking by hand should never be required.
    """
    suffix = path.suffix.lower()
    if suffix == ".gz":
        return gzip.open(path, "rt", encoding="latin-1", errors="replace")
    if suffix == ".zip":
        archive = zipfile.ZipFile(path)
        names = [n for n in archive.namelist() if not n.endswith("/")]
        if not names:
            archive.close()
            raise LayoutError(f"{path} is an empty zip archive")
        # Prefer the events file if the archive carries more than one member.
        name = next((n for n in names if "MaintFee" in n), None)
        if name is None:
            name = next((n for n in names if n.lower().endswith(".txt")), names[0])
        return _ZipTextReader(archive, name)
    return path.open("rt", encoding="latin-1", errors="replace")


class _ZipTextReader(io.TextIOWrapper):
    """Text view over one zip member that closes the archive with itself."""

    def __init__(self, archive: zipfile.ZipFile, name: str):
        self._archive = archive
        super().__init__(archive.open(name), encoding="latin-1", errors="replace")

    def close(self) -> None:
        try:
            super().close()
        finally:
            self._archive.close()


def _sample_lines(path: pathlib.Path, limit: int = 200) -> list[str]:
    lines: list[str] = []
    with _open(path) as fh:
        for line in fh:
            if line.strip():
                lines.append(line)
            if len(lines) >= limit:
                break
    return lines


def score_layout(lines: list[str], layout: dict[str, tuple[int, int]]) -> float:
    """Fraction of sample lines that parse cleanly under `layout`.

    A line counts only if the patent number, the event code and the event
    date all read as valid values. Those three are the fields every
    downstream answer depends on, and under a wrong layout at least one of
    them lands on a space or on half of its neighbour.
    """
    if not lines:
        return 0.0
    good = 0
    for line in lines:
        stripped = line.rstrip("\r\n")

        def field(name: str) -> str:
            start, end = layout[name]
            return stripped[start:end].strip()

        if not DOC_NUMBER_RE.match(field("patent_number")):
            continue
        if not CODE_RE.match(field("event_code")):
            continue
        if _parse_date(field("event_date")) is None:
            continue
        good += 1
    return good / len(lines)


def detect_layout(
    lines: list[str], threshold: float = 0.9
) -> tuple[str, dict[str, tuple[int, int]], float]:
    """Pick the layout that actually fits these lines.

    Raises LayoutError rather than returning a poor match: parsing this file
    under the wrong offsets produces confident wrong expiry dates, which is
    worse than refusing to parse it at all.
    """
    scored = sorted(
        ((score_layout(lines, lay), name, lay) for name, lay in LAYOUTS.items()),
        key=lambda item: -item[0],
    )
    score, name, layout = scored[0]
    if score < threshold:
        detail = ", ".join(f"{n}={s:.0%}" for s, n, _ in scored)
        raise LayoutError(
            f"no known column layout fits this file (best match {detail}). "
            "Run `patentlife inspect FILE --ruler` and read the real offsets "
            "off the ruler, then add them to maintfee.LAYOUTS."
        )
    return name, layout, score


def parse_line(line: str) -> MaintFeeEvent | None:
    """Parse one line with a whitespace tokeniser. Fallback only.

    Prefer `parse_line_fixed`. This exists for hand-typed samples and for
    diffing against the column parser in `patentlife inspect`; on the real
    file a blank field shifts every later token.
    """
    tokens = line.split()
    if len(tokens) < 5:
        return None
    if not PATENT_RE.match(tokens[0]):
        return None

    patent_number = tokens[0].lstrip("0")
    application_number = tokens[1] if len(tokens) > 1 else ""

    dates = [d for d in (_parse_date(t) for t in tokens) if d is not None]
    entity = next((ENTITY_MAP[t] for t in tokens if t in ENTITY_MAP), None)

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


def parse_line_fixed(
    line: str, layout: dict[str, tuple[int, int]] | None = None
) -> MaintFeeEvent | None:
    """Parse one line by column offsets. Returns None if the slices don't fit.

    Unlike the tokeniser this cannot silently shift fields when one is blank,
    which is exactly the failure mode that would produce confident wrong
    expiry dates. It fails loudly instead: a bad layout yields None, not
    plausible-looking nonsense.
    """
    layout = layout or LAYOUT
    stripped = line.rstrip("\r\n")
    if not stripped.strip():
        return None

    def field(name: str) -> str:
        start, end = layout[name]
        return stripped[start:end].strip()

    raw_number = field("patent_number")
    if not DOC_NUMBER_RE.match(raw_number):
        return None

    code = field("event_code")
    if not code:
        return None

    entity_raw = field("entity_status")
    return MaintFeeEvent(
        patent_number=normalise_doc_number(raw_number),
        application_number=field("application_number"),
        filing_date=_parse_date(field("filing_date")),
        grant_date=_parse_date(field("grant_date")),
        entity_status=ENTITY_MAP.get(entity_raw, entity_raw or None),
        event_code=code,
        event_date=_parse_date(field("event_date")),
    )


def ruler(width: int = 90) -> str:
    """A character-position ruler, to read column offsets off a real line.

    Prints tens on the first row and units on the second, 0-based, so the
    number under a field's first character is its slice start.
    """
    tens = "".join(str((i // 10) % 10) for i in range(width))
    units = "".join(str(i % 10) for i in range(width))
    return f"{tens}\n{units}"


def iter_events(
    path: pathlib.Path, layout: dict[str, tuple[int, int]] | None = None
) -> Iterator[MaintFeeEvent]:
    """Yield every parseable event, using the layout the file actually fits.

    Detection happens once, on a sample from the head of the file, and the
    chosen layout is then applied to every line.
    """
    if layout is None:
        _, layout, _ = detect_layout(_sample_lines(path))
    with _open(path) as fh:
        for line in fh:
            event = parse_line_fixed(line, layout)
            if event is not None:
                yield event


CSV_COLUMNS = [
    "patent_number",
    "application_number",
    "filing_date",
    "grant_date",
    "entity_status",
    "event_code",
    "event_date",
]


def to_csv(src: pathlib.Path, dest: pathlib.Path) -> int:
    """Convert the raw events file to CSV. Returns the row count."""
    n = 0
    with dest.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(CSV_COLUMNS)
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
