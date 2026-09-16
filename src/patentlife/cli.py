"""patentlife command line.

    patentlife fetch                      download the USPTO events file
    patentlife inspect FILE               show raw vs parsed lines
    patentlife codes FILE                 list every event code with counts
    patentlife status FILE -o out.csv     derive per-patent US status
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import pathlib
import shutil
import sys
import urllib.error
import urllib.request

from . import assignee, maintfee
from .status import CodeMap, derive

# USPTO retired bulkdata.uspto.gov. The file now lives behind the Open Data
# Portal API, which requires a free API key: register at https://data.uspto.gov
# then set USPTO_API_KEY (or pass --api-key). The dataset landing page is
# https://data.uspto.gov/bulkdata/datasets/ptmnfee2 - its download links work
# in a browser, so a manual download is always a valid fallback.
PRODUCT_ID = "ptmnfee2"
PRODUCT_URL = f"https://api.uspto.gov/api/v1/datasets/products/{PRODUCT_ID}"
REGISTER_URL = "https://data.uspto.gov/apis/getting-started"
LANDING_URL = f"https://data.uspto.gov/bulkdata/datasets/{PRODUCT_ID}"

DISCLAIMER = (
    "Derived from USPTO public-domain bulk data. Statutory term only: Patent "
    "Term Adjustment, Patent Term Extension and terminal disclaimers are NOT "
    "applied, so expiry dates are a floor and real expiry may be later. Not "
    "legal advice; verify against USPTO Patent Center before acting."
)


def _zip_urls(node: object) -> list[str]:
    """Pull every .zip URL out of the product metadata, whatever it is nested in.

    The ODP response shape is not contractually stable, so rather than depend on
    one field name we walk the whole document and keep anything that looks like
    a download link.
    """
    found: list[str] = []
    if isinstance(node, dict):
        for value in node.values():
            found.extend(_zip_urls(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_zip_urls(value))
    elif isinstance(node, str) and node.startswith("http") and node.endswith(".zip"):
        found.append(node)
    return found


def cmd_fetch(args: argparse.Namespace) -> int:
    dest = pathlib.Path(args.out)
    api_key = args.api_key or os.environ.get("USPTO_API_KEY")
    if not api_key:
        print(
            "no API key. USPTO retired the old anonymous bulk-data host; the\n"
            "maintenance fee file is now behind the Open Data Portal API.\n"
            f"  1. get a free key: {REGISTER_URL}\n"
            "  2. export USPTO_API_KEY=... (or pass --api-key)\n"
            f"Or download it by hand in a browser from {LANDING_URL}\n"
            "and point the other commands at the file directly.",
            file=sys.stderr,
        )
        return 2

    headers = {"X-API-KEY": api_key, "Accept": "application/json"}
    print(f"looking up product {PRODUCT_ID}", file=sys.stderr)
    try:
        request = urllib.request.Request(PRODUCT_URL, headers=headers)
        with urllib.request.urlopen(request, timeout=60) as response:
            product = json.load(response)
    except urllib.error.HTTPError as exc:
        hint = " - check the key is valid and activated" if exc.code in (401, 403) else ""
        print(f"product lookup failed: HTTP {exc.code} {exc.reason}{hint}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - surface the real reason
        print(f"product lookup failed: {exc}", file=sys.stderr)
        return 1

    urls = _zip_urls(product)
    if not urls:
        print(
            f"no .zip download link in the product metadata. Download by hand from {LANDING_URL}",
            file=sys.stderr,
        )
        return 1
    # Prefer the events file if the product carries several archives.
    url = next((u for u in urls if "MaintFeeEvents" in u), urls[0])

    print(f"downloading {url}\n  -> {dest}", file=sys.stderr)
    try:
        request = urllib.request.Request(url, headers={"X-API-KEY": api_key})
        with urllib.request.urlopen(request, timeout=600) as response, dest.open("wb") as fh:
            shutil.copyfileobj(response, fh)
    except Exception as exc:  # noqa: BLE001 - surface the real reason
        print(
            f"download failed: {exc}\nFetch it manually from {LANDING_URL}",
            file=sys.stderr,
        )
        return 1
    print(f"saved {dest.stat().st_size / 1e6:.0f} MB", file=sys.stderr)
    return 0


def _fmt_event(event) -> str:
    if event is None:
        return "<no parse>"
    return (
        f"patent={event.patent_number!r} app={event.application_number!r} "
        f"filed={event.filing_date} granted={event.grant_date} "
        f"entity={event.entity_status!r} code={event.event_code!r} "
        f"event={event.event_date}"
    )


def cmd_inspect(args: argparse.Namespace) -> int:
    """Show raw lines against both parsers, so the layout can be checked.

    This is the command that decides whether anything downstream can be
    trusted. Read it field by field against the raw line above it.
    """
    path = pathlib.Path(args.file)
    shown = 0
    with maintfee._open(path) as fh:  # noqa: SLF001 - intentional, same package
        for line in fh:
            if not line.strip():
                continue
            if args.ruler:
                print(maintfee.ruler(max(len(line.rstrip()), 60)))
            print(f"RAW   {line.rstrip()}")
            print(f"TOKEN {_fmt_event(maintfee.parse_line(line))}")
            print(f"FIXED {_fmt_event(maintfee.parse_line_fixed(line))}\n")
            shown += 1
            if shown >= args.n:
                break

    if not maintfee.LAYOUT_VERIFIED:
        print(
            "NOTE: the fixed-width offsets in maintfee.LAYOUT have never been "
            "checked against a real file.\n"
            "Run with --ruler, read each field's start column off the ruler, "
            "correct maintfee.LAYOUT,\n"
            "then set LAYOUT_VERIFIED = True. Until then no output from this "
            "tool should be relied on.",
            file=sys.stderr,
        )
    return 0


def cmd_assignees(args: argparse.Namespace) -> int:
    """Search a PatentsView assignee table for candidate owners."""
    path = pathlib.Path(args.file)
    try:
        index = assignee.AssigneeIndex.build(assignee.iter_assignees(path))
    except ValueError as exc:
        print(f"could not read {path.name}: {exc}", file=sys.stderr)
        return 1

    if not args.search:
        print(f"{len(index.by_name)} distinct owners, {len(index.by_patent)} patents")
        return 0

    matches = index.search(
        args.search, organisations_only=not args.include_individuals, limit=args.limit
    )
    if not matches:
        print(f"no owner matching {args.search!r}", file=sys.stderr)
        return 1

    for match in matches:
        variants = ", ".join(match.variants[:4])
        if len(match.variants) > 4:
            variants += f", +{len(match.variants) - 4} more"
        print(f"{match.patent_count:>7}  {match.normalised}")
        print(f"         spelled: {variants}")
    print(
        "\nThese are candidates, not an answer. Subsidiaries are listed "
        "separately on purpose -\nwhether they belong in the portfolio is "
        "your call. Pass the ones you want to `company`.",
        file=sys.stderr,
    )
    return 0


def cmd_company(args: argparse.Namespace) -> int:
    """Join owner names against a status CSV to get one company's portfolio."""
    index_path = pathlib.Path(args.file)
    status_path = pathlib.Path(args.status)
    try:
        index = assignee.AssigneeIndex.build(assignee.iter_assignees(index_path))
    except ValueError as exc:
        print(f"could not read {index_path.name}: {exc}", file=sys.stderr)
        return 1

    wanted = index.patents_for(args.name)
    if not wanted:
        print(
            f"no patents for {args.name!r}. Run `patentlife assignees "
            f"{index_path.name} --search ...` to find the spelling in use.",
            file=sys.stderr,
        )
        return 1

    with status_path.open("rt", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None or "patent_number" not in reader.fieldnames:
            print(f"{status_path.name} has no patent_number column", file=sys.stderr)
            return 1
        rows = [r for r in reader if r["patent_number"] in wanted]
        fieldnames = list(reader.fieldnames)

    if args.out:
        with pathlib.Path(args.out).open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        print(f"{len(rows)} patents -> {args.out}", file=sys.stderr)
    else:
        counts = collections.Counter(r.get("state", "UNKNOWN") for r in rows)
        for state, n in counts.most_common():
            print(f"{n:>7}  {state}")

    missing = len(wanted) - len(rows)
    if missing > 0:
        print(
            f"note: {missing} of {len(wanted)} patents for this owner are absent "
            f"from {status_path.name}.\nThe maintenance fee file only covers "
            "patents that have reached a fee event, so pre-1981 grants and very "
            "recent ones are expected to be missing.",
            file=sys.stderr,
        )
    print(DISCLAIMER, file=sys.stderr)
    return 0


def cmd_codes(args: argparse.Namespace) -> int:
    path = pathlib.Path(args.file)
    code_map = CodeMap.load()
    counts: collections.Counter[str] = collections.Counter()
    for event in maintfee.iter_events(path):
        counts[event.event_code] += 1

    writer = csv.writer(sys.stdout)
    writer.writerow(["event_code", "count", "mapped_effect"])
    for code, count in counts.most_common():
        writer.writerow([code, count, code_map.effect(code)])

    unmapped = sorted(code_map.seen_unmapped)
    if unmapped:
        print(
            f"\n{len(unmapped)} codes are unmapped and treated as no-ops: "
            f"{', '.join(unmapped)}\n"
            f"Add them to codes.yaml before trusting status output.",
            file=sys.stderr,
        )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    path = pathlib.Path(args.file)
    code_map = CodeMap.load()
    rows = derive(maintfee.iter_events(path), code_map)

    out = pathlib.Path(args.out).open("w", newline="", encoding="utf-8") if args.out else sys.stdout
    writer = csv.writer(out)
    writer.writerow(
        [
            "patent_number",
            "application_number",
            "filing_date",
            "grant_date",
            "entity_status",
            "state",
            "expiry_estimated",
            "last_event_code",
            "last_event_date",
            "fee_payments",
            "unmapped_codes",
        ]
    )
    tally: collections.Counter[str] = collections.Counter()
    for r in rows:
        tally[r.state] += 1
        writer.writerow(
            [
                r.patent_number,
                r.application_number,
                r.filing_date or "",
                r.grant_date or "",
                r.entity_status or "",
                r.state,
                r.expiry_estimated or "",
                r.last_event_code or "",
                r.last_event_date or "",
                r.fee_payments,
                "|".join(r.unmapped_codes),
            ]
        )
    if args.out:
        out.close()

    print(f"\n{len(rows):,} patents", file=sys.stderr)
    for state, n in tally.most_common():
        print(f"  {state:<14} {n:>9,}", file=sys.stderr)
    if code_map.seen_unmapped:
        print(
            f"\nWARNING: {len(code_map.seen_unmapped)} unmapped event codes were "
            f"ignored. Run `patentlife codes` and extend codes.yaml.",
            file=sys.stderr,
        )
    print(f"\n{DISCLAIMER}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="patentlife", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("fetch", help="download the USPTO maintenance fee events file")
    p.add_argument("-o", "--out", default="MaintFeeEvents.zip")
    p.add_argument("--api-key", default=None, help="USPTO ODP key; defaults to $USPTO_API_KEY")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("inspect", help="show raw and parsed lines, to check the layout")
    p.add_argument("file")
    p.add_argument("-n", type=int, default=5)
    p.add_argument("--ruler", action="store_true", help="print a column-position ruler")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("assignees", help="search a PatentsView assignee table for owners")
    p.add_argument("file", help="PatentsView assignee table (.tsv, .tsv.gz or .zip)")
    p.add_argument("--search", default=None, help="company name to look for")
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--include-individuals", action="store_true")
    p.set_defaults(func=cmd_assignees)

    p = sub.add_parser("company", help="one company's portfolio, with status")
    p.add_argument("file", help="PatentsView assignee table")
    p.add_argument("--name", action="append", required=True, help="repeatable")
    p.add_argument("--status", required=True, help="CSV from `patentlife status`")
    p.add_argument("-o", "--out", default=None)
    p.set_defaults(func=cmd_company)

    p = sub.add_parser("codes", help="list event codes with counts and mapping")
    p.add_argument("file")
    p.set_defaults(func=cmd_codes)

    p = sub.add_parser("status", help="derive per-patent in-force status")
    p.add_argument("file")
    p.add_argument("-o", "--out", default=None)
    p.set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
