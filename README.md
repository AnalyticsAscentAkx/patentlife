# patentlife

Which patents a company still holds, where, and until when — from public data.

Existing tools either cost five figures a year or require you to upload your
own portfolio first. This one works from public bulk data, on any company,
for free.

**Status: v0.1, United States only, and not yet verified against the real
data file.** See [Accuracy](#accuracy) before trusting any number it prints,
and [Scope](#scope) for what is and is not built.

## What it does

Reads USPTO's Patent Maintenance Fee Events bulk file and produces one row
per US patent: statutory expiry, whether it lapsed for non-payment, whether
it was revived, and its state today.

Not on PyPI yet — see [Accuracy](#accuracy) for why.

```bash
git clone https://github.com/AnalyticsAscentAkx/patentlife
cd patentlife && pip install -e .         # needs Python 3.10+

patentlife fetch                          # needs a USPTO API key, see below
patentlife inspect MaintFeeEvents.txt --ruler   # check the parse FIRST
patentlife codes  MaintFeeEvents.txt      # every event code, with counts
patentlife status MaintFeeEvents.txt -o us_status.csv
```

### Getting the data

`patentlife fetch` no longer works out of the box, and that is USPTO's doing
rather than a bug here. The anonymous bulk host `bulkdata.uspto.gov` was
retired. What replaced it:

- **The web download** needs a free USPTO.gov account — sign-in has been
  required since 18 June 2026. This is the path most people should take:
  download the file from
  [the dataset page](https://data.uspto.gov/bulkdata/datasets/ptmnfee2)
  and point the commands at it directly.
- **The ODP API** needs an API key, which additionally requires a validated
  **ID.me** identity linked to your USPTO.gov account. Outside the US that
  means a video call with an agent. If you have a key, `patentlife fetch
  --api-key ...` (or `$USPTO_API_KEY`) will use it.

So "public data" is still true in the legal sense — it is a US government
work with no licence restrictions — but no longer in the practical sense of
being downloadable without an account.

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
(`EXP*` expires, `M*` pays a fee) plus a short list of confirmed exact codes.

**Anything unmapped is reported, never guessed.** `patentlife codes` lists
every code present in your file with a count and its current mapping, and
`patentlife status` warns when unmapped codes were encountered. Extend
`codes.yaml` and send a pull request — that file is the part of this project
worth contributing to.

## Accuracy

**The maintenance-fee parser has never been run against the real file.** The
author could not obtain a copy — see [Getting the data](#getting-the-data).
Everything in this section follows from that, and it is the reason this is
not on PyPI: a tool that hands a patent attorney a confidently wrong expiry
date once is finished, and `pip install` is a promise that something works.

Two parsers ship, and `patentlife inspect` shows both against each raw line:

- `TOKEN` — splits on whitespace and validates each field by shape. Tolerant
  of padding, but it **shifts every following field when one is blank**,
  which is exactly how you get wrong dates that look right.
- `FIXED` — slices by column offsets, which is how a fixed-width file should
  be read. Cannot shift. Its offsets in `maintfee.LAYOUT` are a **starting
  guess**, so it fails loudly rather than producing plausible nonsense.

The intended fix, once you have a real file:

```bash
patentlife inspect MaintFeeEvents.txt --ruler -n 20
```

Read each field's start column off the ruler, correct `maintfee.LAYOUT`,
then set `LAYOUT_VERIFIED = True`. Until someone does that, treat every
number this tool prints as unverified.

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
