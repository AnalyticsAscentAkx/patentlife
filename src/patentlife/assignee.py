"""Assignee -> patent index, built from a local PatentsView bulk table.

The maintenance fee file answers "is this patent alive". It cannot answer
"what does this company hold", because it carries no owner. That join is the
job of this module, and it is the harder half: company names in patent data
are not identifiers. The same owner appears as "Shimano Inc.", "SHIMANO
INC", "Shimano Kabushiki Kaisha" and "Shimano Singapore Pte. Ltd." — the
first three are the same legal entity spelled three ways, the fourth is a
different company that you may or may not want folded in.

So this module separates two things that are often conflated:

  normalise_name   cheap, mechanical, safe. Case, punctuation, and corporate
                   suffixes only. "Shimano Inc." and "SHIMANO, INC" collapse;
                   "Shimano Singapore" stays distinct. No judgement calls.

  search           deliberately returns candidates with their patent counts
                   rather than picking one, because deciding whether a
                   subsidiary belongs in a portfolio is the user's call and
                   not something to guess silently.

Nothing here reads the network. Point it at a file you already downloaded.
"""

from __future__ import annotations

import csv
import dataclasses
import gzip
import io
import pathlib
import re
import zipfile
from collections import defaultdict
from collections.abc import Iterable, Iterator

# Column names differ between PatentsView schema versions, and between the
# "disambiguated" and raw tables. Resolve by header name rather than by
# position so a schema change is an explicit error, not a silent mis-read.
PATENT_COLUMNS = ("patent_id", "patent_number", "patent")
ORG_COLUMNS = (
    "disambig_assignee_organization",
    "assignee_organization",
    "organization",
)
FIRST_NAME_COLUMNS = (
    "disambig_assignee_individual_name_first",
    "assignee_individual_name_first",
    "name_first",
)
LAST_NAME_COLUMNS = (
    "disambig_assignee_individual_name_last",
    "assignee_individual_name_last",
    "name_last",
)

# Corporate form suffixes, stripped from the end of a name for matching.
# Multi-word forms must be tried before single words ("kabushiki kaisha"
# before "kaisha"), so this is ordered longest-first at module load.
_SUFFIXES = (
    "kabushiki kaisha",
    "kabushiki gaisha",
    "public limited company",
    "limited liability company",
    "incorporated",
    "corporation",
    "corporativa",
    "aktiengesellschaft",
    "naamloze vennootschap",
    "besloten vennootschap",
    "societe anonyme",
    "sendirian berhad",
    "proprietary limited",
    "company limited",
    "and company",
    "limited",
    "company",
    "holdings",
    "holding",
    "group",
    "inc",
    "corp",
    "co",
    "ltd",
    "llc",
    "llp",
    "lp",
    "plc",
    "gmbh",
    "mbh",
    "ag",
    "kgaa",
    "kg",
    "ohg",
    "bv",
    "nv",
    "sa",
    "sas",
    "sarl",
    "spa",
    "srl",
    "ab",
    "as",
    "asa",
    "oy",
    "oyj",
    "aps",
    "kk",
    "pte",
    "pty",
    "sdn",
    "bhd",
    "kft",
    "doo",
    "sp z oo",
    "zoo",
)
_SUFFIXES_ORDERED = tuple(sorted(_SUFFIXES, key=lambda s: -len(s.split())))

# Dots are deleted rather than turned into spaces, so "N.V." collapses to
# "nv" and matches the suffix list. Every other punctuation mark becomes a
# space, so "SHIMANO, INC" splits into words correctly.
_DOT_RE = re.compile(r"\.")
_PUNCT_RE = re.compile(r"[^\w\s&]+", re.UNICODE)
_WS_RE = re.compile(r"\s+")


@dataclasses.dataclass(frozen=True, slots=True)
class AssigneeRecord:
    patent_number: str
    name: str
    is_organization: bool


@dataclasses.dataclass(frozen=True, slots=True)
class AssigneeMatch:
    """One candidate owner, with enough context for a human to judge it."""

    normalised: str
    patent_count: int
    variants: tuple[str, ...]  # the raw spellings that collapsed to this key


def normalise_name(name: str) -> str:
    """Canonical form for matching. Mechanical only - no entity resolution.

    Lowercases, drops punctuation, expands '&', and strips trailing corporate
    form suffixes. Repeats the suffix strip because names like "Foo Co Ltd"
    carry two. Returns "" for input that is empty or entirely punctuation.
    """
    text = name.strip().lower()
    if not text:
        return ""
    text = text.replace("&", " and ")
    text = _DOT_RE.sub("", text)
    text = _PUNCT_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()

    # "The Boeing Company" -> "boeing"
    if text.startswith("the "):
        text = text[4:]

    # Strip suffixes repeatedly: "foo co ltd" needs two passes. Guard against
    # eating the whole name - "Limited Brands" must not become "".
    changed = True
    while changed:
        changed = False
        for suffix in _SUFFIXES_ORDERED:
            if text.endswith(" " + suffix):
                stripped = text[: -(len(suffix) + 1)].strip()
                if stripped:
                    text = stripped
                    changed = True
                    break
    return text


def _pick_column(header: list[str], candidates: Iterable[str]) -> str | None:
    lowered = {h.strip().lower(): h for h in header}
    for candidate in candidates:
        if candidate in lowered:
            return lowered[candidate]
    return None


def _open_table(path: pathlib.Path) -> io.TextIOBase:
    """Open a PatentsView table: plain .tsv, .tsv.gz, or a .zip holding one."""
    if path.suffix == ".zip":
        archive = zipfile.ZipFile(path)
        names = [n for n in archive.namelist() if n.lower().endswith((".tsv", ".csv"))]
        if not names:
            raise ValueError(f"{path.name} contains no .tsv or .csv member")
        if len(names) > 1:
            raise ValueError(
                f"{path.name} holds {len(names)} tables; unzip it and pass one: {names}"
            )
        return io.TextIOWrapper(archive.open(names[0]), encoding="utf-8", errors="replace")
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("rt", encoding="utf-8", errors="replace")


def iter_assignees(path: pathlib.Path) -> Iterator[AssigneeRecord]:
    """Yield one record per assignee row in a PatentsView assignee table.

    Individuals are yielded too, flagged, because a patent held by a named
    inventor rather than a company is a real and common case - but they are
    kept distinguishable so a portfolio search can exclude them.
    """
    with _open_table(path) as fh:
        reader = csv.reader(fh, delimiter="\t", quoting=csv.QUOTE_NONE)
        try:
            header = next(reader)
        except StopIteration:
            return
        header = [h.lstrip("﻿") for h in header]

        patent_col = _pick_column(header, PATENT_COLUMNS)
        org_col = _pick_column(header, ORG_COLUMNS)
        first_col = _pick_column(header, FIRST_NAME_COLUMNS)
        last_col = _pick_column(header, LAST_NAME_COLUMNS)

        if patent_col is None:
            raise ValueError(
                f"no patent id column in {path.name}. Looked for {PATENT_COLUMNS}, "
                f"found columns: {header}"
            )
        if org_col is None and last_col is None:
            raise ValueError(
                f"no assignee name column in {path.name}. Looked for {ORG_COLUMNS} "
                f"and {LAST_NAME_COLUMNS}, found columns: {header}"
            )

        index = {name: i for i, name in enumerate(header)}
        p_i = index[patent_col]
        o_i = index[org_col] if org_col else None
        f_i = index[first_col] if first_col else None
        l_i = index[last_col] if last_col else None

        def cell(row: list[str], i: int | None) -> str:
            if i is None or i >= len(row):
                return ""
            return row[i].strip()

        for row in reader:
            if not row:
                continue
            patent_number = cell(row, p_i).lstrip("0")
            if not patent_number:
                continue
            org = cell(row, o_i)
            if org:
                yield AssigneeRecord(patent_number, org, True)
                continue
            first, last = cell(row, f_i), cell(row, l_i)
            person = " ".join(part for part in (first, last) if part)
            if person:
                yield AssigneeRecord(patent_number, person, False)


class AssigneeIndex:
    """Normalised owner name -> the patents recorded against it."""

    def __init__(self) -> None:
        self.by_name: dict[str, set[str]] = defaultdict(set)
        self.variants: dict[str, set[str]] = defaultdict(set)
        self.by_patent: dict[str, set[str]] = defaultdict(set)
        self.individuals: set[str] = set()

    def add(self, record: AssigneeRecord) -> None:
        key = normalise_name(record.name)
        if not key:
            return
        self.by_name[key].add(record.patent_number)
        self.variants[key].add(record.name)
        self.by_patent[record.patent_number].add(key)
        if not record.is_organization:
            self.individuals.add(key)

    @classmethod
    def build(cls, records: Iterable[AssigneeRecord]) -> "AssigneeIndex":
        index = cls()
        for record in records:
            index.add(record)
        return index

    def search(
        self, query: str, organisations_only: bool = True, limit: int = 25
    ) -> list[AssigneeMatch]:
        """Candidate owners matching `query`, largest portfolio first.

        Matches an exact normalised hit first, then any name containing every
        token of the query. Returns candidates rather than one answer: only
        you know whether "Shimano Singapore" belongs in Shimano's portfolio.
        """
        key = normalise_name(query)
        if not key:
            return []
        tokens = key.split()

        matches: list[AssigneeMatch] = []
        for name, patents in self.by_name.items():
            if organisations_only and name in self.individuals:
                continue
            if name == key or all(token in name.split() for token in tokens):
                matches.append(
                    AssigneeMatch(
                        normalised=name,
                        patent_count=len(patents),
                        variants=tuple(sorted(self.variants[name])),
                    )
                )
        matches.sort(key=lambda m: (-m.patent_count, m.normalised))
        return matches[:limit]

    def patents_for(self, names: Iterable[str]) -> set[str]:
        """Union of patent numbers across the given normalised owner names."""
        out: set[str] = set()
        for name in names:
            out |= self.by_name.get(normalise_name(name), set())
        return out
