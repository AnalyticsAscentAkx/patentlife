import datetime as dt
import zipfile

import pytest

from patentlife import maintfee
from patentlife.maintfee import (
    LayoutError,
    detect_layout,
    iter_events,
    normalise_doc_number,
    parse_line,
    parse_line_fixed,
    score_layout,
)
from patentlife.status import (
    CodeMap,
    derive,
    fee_status,
    next_fee_due,
    statutory_expiry,
)

# Fixed-width fixtures written out at USPTO's documented columns
# (MaintFeeEventsFileDocumentation.doc, June 2018):
#
#   1-13 patent no . 15-22 application no . 24 entity . 26-33 filing .
#   35-42 grant . 44-51 event date . 53-57 event code
#
# They are literal strings on purpose. If someone edits maintfee.LAYOUTS the
# fixtures stay where they are and these tests break, which is the point.
# Note that the documented offsets leave exactly one space between every
# field across all 57 characters - a wrong offset would show up here as a
# doubled space or a clipped field.
LINES_2018 = [
    "0000004593999 06537062 N 19830929 19860610 19900208 M170 ",
    "0000005123456 07123456 Y 19900101 19920714 19960201 M1551",
    "0000005123456 07123456 Y 19900101 19920714 20001015 EXP. ",
    "0000006543210 09543210 N 19990601 20010508 20050110 M171 ",
    "0000009999999 14999999 Y 20140301 20170620 20210405 M1551",
    "000000RE35000 07999999 N 19910215 19950620 19990101 M184 ",
]

# Pre-~2014 files: 7-character patent number, so every later field sits six
# columns to the left.
LINES_LEGACY = [
    "4593999 06537062 N 19830929 19860610 19900208 M170 ",
    "5123456 07123456 Y 19900101 19920714 19960201 M1551",
    "5123456 07123456 Y 19900101 19920714 20001015 EXP. ",
]

AS_OF = dt.date(2026, 9, 16)


# --------------------------------------------------------------------------
# Column parsing
# --------------------------------------------------------------------------

def test_parse_fixed_reads_every_field():
    e = parse_line_fixed(LINES_2018[0], maintfee.LAYOUT_2018)
    assert e is not None
    assert e.patent_number == "4593999"
    assert e.application_number == "06537062"
    assert e.entity_status == "large"
    assert e.filing_date == dt.date(1983, 9, 29)
    assert e.grant_date == dt.date(1986, 6, 10)
    assert e.event_date == dt.date(1990, 2, 8)
    assert e.event_code == "M170"


def test_parse_fixed_reads_five_character_codes():
    # A four-character code field would clip 'M1551' to 'M155' and quietly
    # lose the fee stage, so the width of this field is worth pinning.
    e = parse_line_fixed(LINES_2018[1], maintfee.LAYOUT_2018)
    assert e.event_code == "M1551"


def test_parse_fixed_legacy_layout():
    e = parse_line_fixed(LINES_LEGACY[0], maintfee.LAYOUT_LEGACY)
    assert e is not None
    assert e.patent_number == "4593999"
    assert e.entity_status == "large"
    assert e.event_code == "M170"
    assert e.event_date == dt.date(1990, 2, 8)


def test_wrong_layout_does_not_silently_succeed():
    # The whole point of detection: reading a current file with the legacy
    # offsets must fail, not produce shifted-but-plausible fields.
    assert score_layout(LINES_2018, maintfee.LAYOUT_LEGACY) < 0.5
    assert score_layout(LINES_LEGACY, maintfee.LAYOUT_2018) < 0.5


@pytest.mark.parametrize(
    "lines,expected",
    [(LINES_2018, "2018"), (LINES_LEGACY, "legacy")],
)
def test_detect_layout(lines, expected):
    name, _, score = detect_layout(lines)
    assert name == expected
    assert score == 1.0


def test_detect_layout_refuses_an_unknown_shape():
    junk = ["this is not a maintenance fee record at all"] * 20
    with pytest.raises(LayoutError):
        detect_layout(junk)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("0000004593999", "4593999"),
        ("000000RE35000", "RE35000"),
        ("0000000D123456", "D123456"),
        ("  0000005123456  ", "5123456"),
    ],
)
def test_normalise_doc_number(raw, expected):
    assert normalise_doc_number(raw) == expected


def test_reissue_numbers_survive_parsing():
    e = parse_line_fixed(LINES_2018[5], maintfee.LAYOUT_2018)
    assert e is not None
    assert e.patent_number == "RE35000"


def test_parse_rejects_junk():
    assert parse_line("") is None
    assert parse_line("# comment line") is None
    assert parse_line("not a patent row at all") is None
    assert parse_line_fixed("", maintfee.LAYOUT_2018) is None
    assert parse_line_fixed("# comment", maintfee.LAYOUT_2018) is None


# --------------------------------------------------------------------------
# Reading the shipped archive
# --------------------------------------------------------------------------

def test_reads_a_zip_in_place(tmp_path):
    # USPTO ships a .zip, so requiring a manual unzip would be a papercut on
    # a 2 GB file.
    archive = tmp_path / "MaintFeeEvents.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("MaintFeeEvents_20260101.txt", "\n".join(LINES_2018))
    events = list(iter_events(archive))
    assert len(events) == len(LINES_2018)
    assert events[0].patent_number == "4593999"


def test_reads_plain_text(tmp_path):
    path = tmp_path / "MaintFeeEvents.txt"
    path.write_text("\n".join(LINES_2018), encoding="latin-1")
    assert len(list(iter_events(path))) == len(LINES_2018)


def test_iter_events_detects_layout_per_file(tmp_path):
    path = tmp_path / "legacy.txt"
    path.write_text("\n".join(LINES_LEGACY), encoding="latin-1")
    events = list(iter_events(path))
    assert [e.event_code for e in events] == ["M170", "M1551", "EXP."]


# --------------------------------------------------------------------------
# Term arithmetic
# --------------------------------------------------------------------------

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


@pytest.mark.parametrize(
    "stage,expected",
    [
        (None, dt.date(2020, 12, 20)),  # first fee, 3.5 years from grant
        (4, dt.date(2024, 12, 20)),     # second fee, 7.5 years
        (8, dt.date(2028, 12, 20)),     # third fee, 11.5 years
        (12, None),                     # no maintenance fees remain
    ],
)
def test_next_fee_due(stage, expected):
    assert next_fee_due(dt.date(2017, 6, 20), stage) == expected


def test_next_fee_due_needs_a_grant_date():
    assert next_fee_due(None, 4) is None


# --------------------------------------------------------------------------
# Status derivation
# --------------------------------------------------------------------------

def _rows(lines, as_of=AS_OF):
    events = [e for e in (parse_line_fixed(l, maintfee.LAYOUT_2018) for l in lines) if e]
    return {r.patent_number: r for r in derive(events, CodeMap.load(), as_of=as_of)}


def test_derive_states():
    rows = _rows(LINES_2018)
    # Ran its full term out, no lapse.
    assert rows["4593999"].state == "EXPIRED_TERM"
    # Filed 2014, term runs to 2034; one fee paid, no expiry event.
    assert rows["9999999"].state == "IN_FORCE"
    assert rows["9999999"].fee_payments == 1
    # Filed 1999, term ran out 2019.
    assert rows["6543210"].state == "EXPIRED_TERM"


def test_lapse_before_term_is_reported_as_the_lapse():
    # 5123456 was filed in 1990 (term to 2010) but stopped paying and expired
    # in 2000. Reporting EXPIRED_TERM here would hide the ten years in which
    # it was already dead, and hide the date anyone reviving it needs.
    row = _rows(LINES_2018)["5123456"]
    assert row.state == "LAPSED"
    assert row.lapse_date == dt.date(2000, 10, 15)
    assert row.expiry_estimated == dt.date(2010, 1, 1)
    assert row.effective_end == dt.date(2000, 10, 15)


def test_in_force_patent_reports_its_next_fee():
    row = _rows(LINES_2018)["9999999"]
    assert row.last_fee_stage == 4
    # Granted 2017-06-20, four-year fee paid, so the eight-year fee falls due
    # 7.5 years after grant.
    assert row.next_fee_due == dt.date(2024, 12, 20)


def test_expx_cancels_an_expiry_rather_than_causing_one():
    # EXPX means "expiration cancelled". Treating it as an expiry - as this
    # project previously did - reported every revived patent as dead.
    lapsed = [
        "0000008000001 13000001 N 20120301 20150106 20230301 EXP. ",
    ]
    revived = lapsed + [
        "0000008000001 13000001 N 20120301 20150106 20230801 EXPX ",
    ]
    assert _rows(lapsed)["8000001"].state == "LAPSED"
    row = _rows(revived)["8000001"]
    assert row.state == "IN_FORCE"
    assert row.lapse_date is None
    assert row.effective_end == row.expiry_estimated


def test_lapse_then_payment_clears_the_lapse():
    lines = [
        "0000008000001 13000001 N 20120301 20150106 20230301 EXP. ",
        "0000008000001 13000001 N 20120301 20150106 20230901 M1551",
    ]
    row = _rows(lines)["8000001"]
    assert row.state == "IN_FORCE"
    assert row.lapse_date is None


def test_unmapped_codes_are_reported_not_guessed():
    code_map = CodeMap.load()
    assert code_map.effect("ZZZZ") == "UNKNOWN"
    assert "ZZZZ" in code_map.seen_unmapped


def test_fee_stages_are_loaded():
    code_map = CodeMap.load()
    assert code_map.stage("M1551") == 4
    assert code_map.stage("M171") == 8
    assert code_map.stage("M285") == 12
    assert code_map.stage("EXP.") is None


@pytest.mark.parametrize(
    "due,stage,expected",
    [
        (dt.date(2030, 1, 1), 4, "CURRENT"),
        (dt.date(2027, 1, 1), 4, "DUE_SOON"),
        # A deadline already behind us: either the file predates the payment
        # or an EXP. is coming. Printing the bare date would read as a bug.
        (dt.date(2020, 1, 1), 4, "OVERDUE"),
        (None, 12, "COMPLETE"),
        (None, None, None),
    ],
)
def test_fee_status(due, stage, expected):
    assert fee_status(due, stage, AS_OF) == expected


def test_overdue_is_surfaced_not_hidden():
    # Granted 2017, four-year fee paid in 2021, nothing since. The eight-year
    # fee fell due 2024-12-20, before our as_of date.
    row = _rows(LINES_2018)["9999999"]
    assert row.next_fee_due == dt.date(2024, 12, 20)
    assert row.fee_status == "OVERDUE"


def test_fee_status_is_blank_once_the_patent_is_dead():
    row = _rows(LINES_2018)["5123456"]
    assert row.state == "LAPSED"
    assert row.fee_status is None
    assert row.next_fee_due is None
