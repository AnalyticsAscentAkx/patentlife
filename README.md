# patentlife

Which patents a company still holds, where, and until when — from public data.

Existing tools either cost five figures a year or require you to upload your
own portfolio first. This one works from public bulk data, on any company,
for free.

**Status: v0.1, United States only.** See [Scope](#scope) for what that means
and why the rest is harder.

## What it does

Reads USPTO's Patent Maintenance Fee Events bulk file and produces one row
per US patent: statutory expiry, whether it lapsed for non-payment, whether
it was revived, and its state today.

```bash
pip install patentlife

patentlife fetch                          # download the USPTO events file
patentlife inspect MaintFeeEvents.txt     # sanity-check the parse
patentlife codes  MaintFeeEvents.txt      # every event code, with counts
patentlife status MaintFeeEvents.txt -o us_status.csv
```

Output columns: `patent_number, application_number, filing_date, grant_date,
entity_status, state, expiry_estimated, last_event_code, last_event_date,
fee_payments, unmapped_codes`.

`state` is one of `IN_FORCE`, `LAPSED`, `EXPIRED_TERM`, `UNKNOWN`.

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
| Does this patent exist, who owns it | yes | yes, via Google Patents Public Data on BigQuery |
| Is it in force today | yes | needs EPO INPADOC legal event data |
| When does it expire | statutory term | statutory term only |
| What renewals cost | not yet | not yet |

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
(`EXP*` expires, `M*` pays a fee) plus a short list of confirmed exact codes.

**Anything unmapped is reported, never guessed.** `patentlife codes` lists
every code present in your file with a count and its current mapping, and
`patentlife status` warns when unmapped codes were encountered. Extend
`codes.yaml` and send a pull request — that file is the part of this project
worth contributing to.

## Accuracy

The parser is tolerant by design: it tokenises on whitespace and validates
each field by shape rather than hard-coding column offsets, because USPTO's
layout has drifted between versions. The included tests exercise the
tokeniser against synthetic lines in the expected shapes — they do **not**
validate against the real file. Run `patentlife inspect` on your download
first and check that the parsed output matches the raw lines.

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
