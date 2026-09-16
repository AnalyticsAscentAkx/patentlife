"""Tests for the assignee layer.

Unlike the maintenance-fee parser, everything here is verifiable without the
real data: name normalisation is pure string logic, and the table reader is
driven by header names we can construct. These tests are worth something.
"""

from __future__ import annotations

import gzip
import io
import zipfile

import pytest

from patentlife.assignee import (
    AssigneeIndex,
    AssigneeRecord,
    iter_assignees,
    normalise_name,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Shimano Inc.", "shimano"),
        ("SHIMANO, INC", "shimano"),
        ("shimano inc", "shimano"),
        ("Shimano Kabushiki Kaisha", "shimano"),
        ("The Boeing Company", "boeing"),
        ("Koninklijke Philips N.V.", "koninklijke philips"),
        ("Robert Bosch GmbH", "robert bosch"),
        ("Foo Co Ltd", "foo"),  # two suffixes, needs repeated stripping
        ("Procter & Gamble", "procter and gamble"),
        ("  Acme   Corp.  ", "acme"),
    ],
)
def test_normalise_collapses_spelling_variants(raw, expected):
    assert normalise_name(raw) == expected


def test_normalise_keeps_distinct_companies_distinct():
    """A subsidiary must not silently collapse into its parent."""
    assert normalise_name("Shimano Inc.") != normalise_name("Shimano Singapore Pte Ltd")


def test_normalise_does_not_eat_the_whole_name():
    """'Limited Brands' is a company, not an empty string."""
    assert normalise_name("Limited Brands") == "limited brands"
    assert normalise_name("Group 1 Automotive") == "group 1 automotive"


@pytest.mark.parametrize("raw", ["", "   ", ",,,", "."])
def test_normalise_empty_input(raw):
    assert normalise_name(raw) == ""


def _write_tsv(tmp_path, header, rows, name="assignee.tsv"):
    path = tmp_path / name
    lines = ["\t".join(header)] + ["\t".join(r) for r in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_iter_assignees_current_schema(tmp_path):
    path = _write_tsv(
        tmp_path,
        ["patent_id", "disambig_assignee_organization", "location_id"],
        [["10000001", "Shimano Inc.", "x"], ["10000002", "Robert Bosch GmbH", "y"]],
    )
    records = list(iter_assignees(path))
    assert [r.patent_number for r in records] == ["10000001", "10000002"]
    assert all(r.is_organization for r in records)


def test_iter_assignees_older_schema(tmp_path):
    """Column names differ between PatentsView versions; both must work."""
    path = _write_tsv(
        tmp_path,
        ["patent_number", "organization", "name_first", "name_last"],
        [["7000001", "Acme Corp", "", ""], ["7000002", "", "Ada", "Lovelace"]],
    )
    records = list(iter_assignees(path))
    assert records[0].name == "Acme Corp" and records[0].is_organization
    assert records[1].name == "Ada Lovelace" and not records[1].is_organization


def test_iter_assignees_unknown_schema_raises(tmp_path):
    """A schema change must fail loudly, not read the wrong column."""
    path = _write_tsv(tmp_path, ["id", "who"], [["1", "Acme"]])
    with pytest.raises(ValueError, match="no patent id column"):
        list(iter_assignees(path))


def test_iter_assignees_strips_leading_zeros(tmp_path):
    """Patent numbers must match the maintenance-fee side, which strips them."""
    path = _write_tsv(
        tmp_path,
        ["patent_id", "disambig_assignee_organization"],
        [["0123456", "Acme Corp"]],
    )
    assert list(iter_assignees(path))[0].patent_number == "123456"


def test_iter_assignees_gzip(tmp_path):
    path = tmp_path / "assignee.tsv.gz"
    body = "patent_id\tdisambig_assignee_organization\n10000001\tShimano Inc.\n"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(body)
    assert list(iter_assignees(path))[0].name == "Shimano Inc."


def test_iter_assignees_zip(tmp_path):
    path = tmp_path / "assignee.tsv.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "g_assignee_disambiguated.tsv",
            "patent_id\tdisambig_assignee_organization\n10000001\tShimano Inc.\n",
        )
    assert list(iter_assignees(path))[0].patent_number == "10000001"


def _index(*pairs):
    return AssigneeIndex.build(
        AssigneeRecord(patent, name, True) for patent, name in pairs
    )


def test_index_collapses_spellings_of_one_owner():
    index = _index(("1", "Shimano Inc."), ("2", "SHIMANO, INC"), ("3", "Shimano"))
    assert index.patents_for(["Shimano Inc"]) == {"1", "2", "3"}


def test_search_ranks_by_portfolio_size():
    index = _index(
        ("1", "Shimano Inc."),
        ("2", "Shimano Inc."),
        ("3", "Shimano Singapore Pte Ltd"),
    )
    matches = index.search("shimano")
    assert matches[0].normalised == "shimano"
    assert matches[0].patent_count == 2
    # The subsidiary is surfaced as a separate candidate, not folded in.
    assert [m.normalised for m in matches] == ["shimano", "shimano singapore"]


def test_search_reports_the_raw_spellings():
    index = _index(("1", "Shimano Inc."), ("2", "SHIMANO, INC"))
    assert index.search("shimano")[0].variants == ("SHIMANO, INC", "Shimano Inc.")


def test_search_excludes_individuals_by_default():
    index = AssigneeIndex.build(
        [
            AssigneeRecord("1", "Acme Corp", True),
            AssigneeRecord("2", "Acme Person", False),
        ]
    )
    assert [m.normalised for m in index.search("acme")] == ["acme"]
    assert len(index.search("acme", organisations_only=False)) == 2


def test_search_unknown_name_returns_nothing():
    assert _index(("1", "Acme Corp")).search("nonesuch") == []
