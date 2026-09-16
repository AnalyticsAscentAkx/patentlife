import datetime as dt

import pytest

from patentlife.maintfee import parse_line
from patentlife.status import CodeMap, derive, statutory_expiry

# These are SYNTHETIC lines in the shapes we expect. They exercise the
# tokeniser, not USPTO's real file. Run `patentlife inspect` against the
# real download before trusting any of this.
LINES = [
    "04593999 06537062 19830929 19860610 SM   M170 19900208",
    "5123456  07123456 19900101 19920714 LG   M1551 19960201",
    "5123456  07123456 19900101 19920714 LG   EXP. 20001015",
    "6543210  09543210 19990601 20010508 SM   M2551 20050110",
    "9999999  14999999 20140301 20170620 MI   M3551 20210405",
]


def test_parse_basic():
    e = parse_line(LINES[0])
    assert e is not None
    assert e.patent_number == "4593999"
    assert e.filing_date == dt.date(1983, 9, 29)
    assert e.grant_date == dt.date(1986, 6, 10)
    assert e.entity_status == "small"
    assert e.event_code == "M170"
    assert e.event_date == dt.date(1990, 2, 8)


def test_parse_rejects_junk():
    assert parse_line("") is None
    assert parse_line("# comment line") is None
    assert parse_line("not a patent row at all") is None


@pytest.mark.parametrize(
    "filing,grant,expected",
    [
        # Post-URAA: 20 years from filing.
        (dt.date(1999, 6, 1), dt.date(2001, 5, 8), dt.date(2019, 6, 1)),
        # Pre-URAA: greater of 17-from-grant and 20-from-filing.
        (dt.date(1983, 9, 29), dt.date(1986, 6, 10), dt.date(2003, 9, 29)),
        (dt.date(1990, 1, 1), dt.date(1992, 7, 14), dt.date(2010, 1, 1)),
    ],
)
def test_statutory_expiry(filing, grant, expected):
    assert statutory_expiry(filing, grant) == expected


def test_leap_day_filing():
    # 2020 is itself a leap year, so the date survives intact.
    assert statutory_expiry(dt.date(2000, 2, 29), None) == dt.date(2020, 2, 29)
    # 1900 was not a leap year (century rule), so this must fall back to the
    # 28th rather than raising.
    assert statutory_expiry(dt.date(1880, 2, 29), None) == dt.date(1900, 2, 28)


def test_derive_states():
    events = [parse_line(line) for line in LINES]
    events = [e for e in events if e is not None]
    rows = {r.patent_number: r for r in derive(events, CodeMap.load(), as_of=dt.date(2026, 9, 16))}

    # Expired on term (filed 1990, 20 years) AND has an EXP event.
    assert rows["5123456"].state == "EXPIRED_TERM"
    # Filed 2014, term runs to 2034; one fee paid, no expiry event.
    assert rows["9999999"].state == "IN_FORCE"
    assert rows["9999999"].fee_payments == 1
    # Filed 1999, term ran out 2019.
    assert rows["6543210"].state == "EXPIRED_TERM"


def test_lapse_then_reinstatement():
    lines = [
        "8000001  13000001 20120301 20150106 LG   M1551 20180901",
        "8000001  13000001 20120301 20150106 LG   EXP. 20230301",
    ]
    events = [parse_line(line) for line in lines]
    rows = derive(events, CodeMap.load(), as_of=dt.date(2026, 9, 16))
    assert rows[0].state == "LAPSED"

    revived = lines + ["8000001  13000001 20120301 20150106 LG   RMPN 20230801"]
    events = [parse_line(line) for line in revived]
    rows = derive(events, CodeMap.load(), as_of=dt.date(2026, 9, 16))
    assert rows[0].state == "IN_FORCE"


def test_unmapped_codes_are_reported_not_guessed():
    code_map = CodeMap.load()
    assert code_map.effect("ZZZZ") == "UNKNOWN"
    assert "ZZZZ" in code_map.seen_unmapped
