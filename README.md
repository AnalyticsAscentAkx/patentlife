# patentlife

Which patents a company still holds, where, and until when — from public data.

Existing tools either cost five figures a year or require you to upload your
own portfolio first. This one works from public bulk data, on any company,
for free.

**Status: v0.2, United States only. The column layout now comes from USPTO's
own layout document and matches two independent implementations, but nobody
here has yet run it against a real download.** See [Accuracy](#accuracy)
before trusting any number it prints, and [Scope](#scope) for what is and is
not built.

## What it does

Reads USPTO's Patent Maintenance Fee Events bulk file and produces one row
per US patent: statutory expiry, whether it lapsed for non-payment, whether
it was revived, its state today, and when its next maintenance fee falls
due.

Not on PyPI yet — see [Accuracy](#accuracy) for why.

```bash
git clone https://github.com/AnalyticsAscentAkx/patentlife
cd patentlife && pip install -e .         # needs Python 3.10+

# Download MaintFeeEvents.zip by hand first - see below. The .zip is read
# in place; there is no need to unpack it.
patentlife inspect MaintFeeEvents.zip --ruler   # check the parse FIRST
patentlife codes  MaintFeeEvents.zip      # every event code, with counts
patentlife status MaintFeeEvents.zip -o us_status.csv
```

### Getting the data

`patentlife fetch` no longer works out of the box, and that is USPTO's doing
rather than a bug here. The anonymous bulk host `bulkdata.uspto.gov` was
retired. There are now three routes, and **two of them need a browser**:

- **USPTO Open Data Portal** —
  [the dataset page](https://data.uspto.gov/bulkdata/datasets/ptmnfee2)
  works, but only in a browser signed in to a free USPTO.gov account
  (required since 18 June 2026). The page is a JavaScript app: its HTML
  carries no dataset list at all, and the list plus every download link are
  fetched at runtime from `api.uspto.gov`, which answers
  `{"message":"Unauthorized"}` without a key. So `curl` gets an empty shell,
  not a gate you can talk your way past.
- **The Reed Tech mirror** —
  [`MaintFeeEvents.zip`](https://patents.reedtech.com/downloads/PatentMaintFeeEvents/1981-present/MaintFeeEvents.zip)
  has carried the same weekly file for years and needs **no USPTO account**.
  It sits behind a Cloudflare challenge, so scripted requests get HTTP 403
  while a real browser is served normally. This is the shortest path if you
  do not already have an account.
- **The ODP API** needs an API key, which additionally requires a validated
  **ID.me** identity linked to your USPTO.gov account. Outside the US that
  means a video call with an agent. If you have a key, `patentlife fetch
  --api-key ...` (or `$USPTO_API_KEY`) will use it.

So "public data" is still true in the legal sense — it is a US government
work with no licence restrictions — but no longer in the practical sense of
being downloadable unattended. The download is a one-off; everything after
it is local.

### Whose patents are these?

The maintenance fee file is keyed on patent number and carries no owner, so
it cannot answer "what does this company hold" on its own. Pair it with a
[PatentsView](https://patentsview.org) assignee table, downloaded separately:

```bash
patentlife assignees g_assignee_disambiguated.tsv --search shimano
#      412  shimano
#               spelled: SHIMANO INC., Shimano Inc., SHIMANO, INC
#       18  shimano singapore
#               spelled: Shimano Singapore Pte. Ltd.

patentlife company g_assignee_disambiguated.tsv \
    --name "Shimano Inc." --status us_status.csv -o shimano.csv
```

`assignees` returns **candidates, not an answer.** Spelling variants of one
name are collapsed; separate legal entities are not. Whether a subsidiary
belongs in a portfolio is a judgement about corporate structure, and the
tool will not make it for you silently. Pass `--name` more than once to
include several.

Output columns: `patent_number, application_number, filing_date, grant_date,
entity_status, state, expiry_estimated, lapse_date, effective_end,
last_fee_stage, next_fee_due, fee_status, last_event_code, last_event_date,
fee_payments, unmapped_codes`.

`state` is one of `IN_FORCE`, `LAPSED`, `EXPIRED_TERM`, `UNKNOWN`.

A patent that stopped paying before its term ran out died on the lapse, not
on the term, so `state` reports whichever came first and `effective_end`
carries that date. `expiry_estimated` always holds the statutory term
regardless, so nothing is lost.

### Maintenance fee schedule

US maintenance fees fall due 3.5, 7.5 and 11.5 years after grant, each with
a six-month grace period. `last_fee_stage` names the last window paid (4, 8
or 12, after the end of its grace period), `next_fee_due` is the next
deadline, and `fee_status` labels it:

| `fee_status` | meaning |
|---|---|
| `CURRENT` | next fee is more than a year away |
| `DUE_SOON` | next fee falls due within a year |
| `OVERDUE` | the deadline has passed with no payment in this file |
| `COMPLETE` | the 12-year fee was paid; no maintenance fees remain |

`OVERDUE` is not proof of a lapse. The file is a weekly snapshot, so it also
appears when a payment was made more recently than your copy. Treat it as
"look at this one", not as an answer.

## What it does not do, and you should read this part

`expiry_estimated` is **statutory term only**. It does not apply:

- **Patent Term Adjustment (PTA)** — added for USPTO delay, often months,
  sometimes years. Pushes real expiry *later*.
- **Patent Term Extension (PTE)** — regulatory review, mainly pharma. Later
  again.
- **Terminal disclaimers** — pull expiry *earlier*.

None of these are in the maintenance fee file. So treat `expiry_estimated`
as a floor, never as the answer. It is good enough to rank a portfolio by
urgency; it is not good enough to decide whether to file.

It also says nothing about validity. An `IN_FORCE` patent can still be
invalid, under opposition, or subject to an inter partes review.

**Not legal advice.** Verify anything that matters against USPTO Patent
Center before acting on it.

## Scope

| Question | US | Elsewhere |
|---|---|---|
| Does this patent exist | yes | not built |
| Who owns it | yes, via a PatentsView table you supply | not built |
| Is it in force today | yes | needs EPO INPADOC legal event data |
| When does it expire | statutory term only, see below | not built |
| What renewals cost | not built | not built |

The non-US half needs EPO's worldwide legal event data (INPADOC), which
became free to download in February 2025. It is free but **licensed**: EPO's
terms permit building your own product on it and selling that product
royalty-free, but forbid republishing the data itself or making it publicly
accessible. So this project will never ship EPO-derived data files — that
half will read from a copy you download yourself.

USPTO data is a US government work and carries no such restriction, which is
why the US half comes first.

## Event code mapping

USPTO event codes are mapped to state effects in
[`codes.yaml`](src/patentlife/codes.yaml). The defaults are prefix rules
(`EXP*` expires, `M*` pays a fee) plus a short list of confirmed exact codes
that override them, and a `fee_stage` table mapping each payment code to the
window it settles (4, 8 or 12 years).

**Anything unmapped is reported, never guessed.** `patentlife codes` lists
every code present in your file with a count and its current mapping, and
`patentlife status` warns when unmapped codes were encountered. Extend
`codes.yaml` and send a pull request — that file is the part of this project
worth contributing to.

## Accuracy

**The parser has still never been run against the real file.** Nobody here
has been able to download one — see [Getting the data](#getting-the-data).
That is the reason this is not on PyPI: a tool that hands a patent attorney
a confidently wrong expiry date once is finished, and `pip install` is a
promise that something works.

What changed in v0.2 is that the column offsets are no longer a guess.

### Where the layout comes from

The current layout is USPTO's own, from `MaintFeeEventsFileDocumentation.doc`
(June 2018), which gives 1-based inclusive columns:

```
1-13 patent number . 15-22 application number . 24 entity status .
26-33 filing date . 35-42 grant date . 44-51 event date . 53-57 event code
```

Two independent implementations agree with it exactly:
[`iamlemec/fastpat`](https://github.com/iamlemec/fastpat) (pandas `read_fwf`
colspecs) and [`matusfaro/invented`](https://github.com/matusfaro/invented)
(string slices). A third,
[`aniemerg/Patent-Tools`](https://github.com/aniemerg/Patent-Tools), was
written against a January 2012 file and uses offsets shifted left by exactly
six — a 7-character patent number field instead of 13 — which is where the
`legacy` layout in `maintfee.LAYOUTS` comes from.

Corroboration is not verification. Read the offsets off a real line before
relying on them.

### What protects the output meanwhile

- **Layout detection.** `iter_events` scores every known layout against a
  sample from the head of your file and uses the one that fits. A file
  matching neither raises `LayoutError` rather than parsing into plausible
  nonsense, so a future USPTO layout change is a crash, not a silent
  corruption.
- **Column slicing, not tokenising.** `parse_line_fixed` is now the parser
  every command uses. The old whitespace tokeniser survives only as the
  `TOKEN` line in `inspect`, for comparison — it shifts every following
  field when one is blank, and on a real file it does not parse the
  zero-padded patent number at all.
- **Unmapped codes are reported, never guessed.**

```bash
patentlife inspect MaintFeeEvents.zip --ruler -n 20
```

prints the fit of each candidate layout, then a ruler above each raw line
with both parsers below it. If the fields line up, set
`maintfee.LAYOUT_VERIFIED = True`. If they do not, correct
`maintfee.LAYOUTS` and re-run.

### Known fix since v0.1

`EXPX` was mapped to `EXPIRED`. It means *expiration cancelled* — it is
emitted when a lapsed patent is reinstated and always follows the `EXP.` it
undoes. Every revived patent was therefore reported as dead. It now maps to
`REINSTATED`.

The assignee layer is a different story: name normalisation and table
reading are pure logic with no hidden data dependency, and are covered by
tests that mean something.

## Attribution

If you build on the non-US half, EPO's terms require this statement on your
product and any interface to it:

> This product contains data sourced from EPO databases,
> © [European Patent Organisation](https://www.epo.org)

## Licence

MIT for the code. `codes.yaml` and any fee schedules added later are CC BY 4.0.

## Development

```bash
pip install -e ".[dev]"
pytest
```
