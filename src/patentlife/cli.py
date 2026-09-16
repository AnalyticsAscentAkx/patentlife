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
import pathlib
import sys
import urllib.request

from . import maintfee
from .status import CodeMap, derive

# USPTO republishes this weekly, cumulative. If the URL moves, the dataset
# landing page is https://data.uspto.gov/bulkdata/datasets/ptmnfee2
EVENTS_URL = "https://bulkdata.uspto.gov/data/patent/maintenancefee/MaintFeeEvents.zip"

DISCLAIMER = (
    "Derived from USPTO public-domain bulk data. Statutory term only: Patent "
    "Term Adjustment, Patent Term Extension and terminal disclaimers are NOT "
    "applied, so expiry dates are a floor and real expiry may be later. Not "
    "legal advice; verify against USPTO Patent Center before acting."
)


def cmd_fetch(args: argparse.Namespace) -> int:
    dest = pathlib.Path(args.out)
    print(f"downloading {EVENTS_URL}\n  -> {dest}", file=sys.stderr)
    try:
        urllib.request.urlretrieve(EVENTS_URL, dest)
    except Exception as exc:  # noqa: BLE001 - surface the real reason
        print(
            f"download failed: {exc}\n"
            f"Fetch it manually from https://data.uspto.gov/bulkdata/datasets/ptmnfee2",
            file=sys.stderr,
        )
        return 1
    print(f"saved {dest.stat().st_size / 1e6:.0f} MB", file=sys.stderr)
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    path = pathlib.Path(args.file)
    shown = 0
    with maintfee._open(path) as fh:  # noqa: SLF001 - intentional, same package
        for line in fh:
            if not line.strip():
                continue
            parsed = maintfee.parse_line(line)
            print(f"RAW   {line.rstrip()}")
            print(f"PARSE {parsed}\n")
            shown += 1
            if shown >= args.n:
                break
    print(
        "If PARSE lines look wrong, the USPTO layout has drifted; fix the "
        "tokeniser in maintfee.parse_line.",
        file=sys.stderr,
    )
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
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("inspect", help="show raw and parsed lines, to check the layout")
    p.add_argument("file")
    p.add_argument("-n", type=int, default=5)
    p.set_defaults(func=cmd_inspect)

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
