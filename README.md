# Bluebird Lookup — Property Address Lookup Automation

Takes a CSV of pre-foreclosure leads (owner name + county + state), searches
each county's property appraiser website, scrapes the matching property
record, fuzzy-matches the owner name to avoid attaching the wrong person's
address to a lead, and writes a clean CSV ready for skip tracing / CRM
import — one row at a time, so a mid-run crash never loses progress.

## Setup

```bash
cd bluebird_lookup
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

The `selenium` / `webdriver-manager` lines in `requirements.txt` are only
needed for counties whose search tool is a JavaScript-rendered single-page
app (config `search_type: "selenium"`). Skip installing them if every
county you use is `html_get` or `aspnet_postback`.

## Input CSV format

```csv
owner_name,county,state
JOHN SMITH,Duval,FL
MARIA GARCIA,Hillsborough,FL
```

Column names are matched case-insensitively and `state` accepts either the
2-letter abbreviation or the full state name.

## CLI usage

```bash
python main.py --input leads.csv --output results.csv
python main.py --input leads.csv --output results.csv --county "Duval FL" --delay 3
python main.py --input leads.csv --output results.csv --resume
```

| Flag | Default | Meaning |
|---|---|---|
| `--input` | *(required)* | Input leads CSV |
| `--output` | *(required)* | Output CSV path (written row-by-row) |
| `--county "County ST"` | all counties | Only process rows for this county; omit to process every county present in the input |
| `--delay` | `3` | Center of the randomized per-request delay, in seconds (jittered ~2–4s at the default) |
| `--resume` | off | Skip input rows whose `(owner_name, county, state)` already appear in the output CSV, so a crashed run can pick back up |
| `--verbose` | off | DEBUG-level console logging (the log file always has full detail) |
| `--inspect-county "County ST"` | — | Diagnostic mode, see below — doesn't touch the input/output CSV |

## Output

`results.csv`:

```
owner_name_input, owner_name_found, property_address, mailing_address, parcel_id, county, state, status, match_score, source_url
```

`status` is one of:

- **FOUND** — one result, owner name matched ≥85% fuzzy similarity
- **LOW CONFIDENCE** — one result, name similarity 60–84% (included, but flagged — eyeball it before using)
- **MULTIPLE MATCHES** — the site returned more than one result for this owner name. The output row shows the best-scoring candidate; **every** candidate (name, address, parcel, score) is written to `results_multiple_matches.csv` alongside the main output, and logged to `run.log`.
- **NOT FOUND** — the site returned zero results, *or* it returned exactly one result but the owner name was too dissimilar (<60%) to trust — in that case the scraped fields are deliberately left blank rather than risk attaching a stranger's address to this lead.
- **ERROR** — a technical failure (network error, HTTP error after retries, unknown county, parsing failure). Check `run.log` for the reason.

A `run.log` file is written next to the output CSV with a timestamped
record of every request, retry, match decision, and error.

## Rate limiting & retries

- Each county gets its own `requests.Session` (keep-alive) reused across
  all rows for that county.
- A random 2–4s delay (centered on `--delay`) is applied before every
  request, per county.
- The `User-Agent` header is randomized per request from a small pool of
  realistic browser strings.
- HTTP 429 / 503 responses (and connection errors) trigger a 30s wait and
  retry, up to 3 attempts, before the row is marked `ERROR`.

## Fuzzy name matching

`name_matcher.py` normalizes both the input name and the scraped name
(uppercase, punctuation stripped, suffixes like `JR`/`SR`/`III`/`TRUSTEE`
removed) and compares them with `rapidfuzz`'s `token_sort_ratio` (word-order
independent, so `"SMITH JOHN"` vs `"JOHN SMITH"` still matches — useful
since counties often record `LAST FIRST`). Thresholds: ≥85 → match, 60–84 →
low confidence, <60 → rejected.

## Status on Duval County, FL

`county_configs.py` ships a config for Duval County (`paopropertysearch.coj.net/Basic/Search.aspx`),
built as a standard ASP.NET WebForms search (GET the form to harvest
`__VIEWSTATE`/`__EVENTVALIDATION`, then POST the search — that's the
correct pattern for how this class of site works). **It is marked
`verified: False`**: the environment this was built in could not reach
`paopropertysearch.coj.net` (connections were refused, likely blocking
datacenter/automated traffic), so the exact form field name for the owner
box and the results-table CSS selectors are structurally-correct
placeholders, not confirmed against the live HTML.

Before running it against real leads, verify it from a machine that can
reach the site:

```bash
python main.py --inspect-county "Duval FL"
```

This prints every `<input>` field on the search form (name/id/value),
which tells you the real `name_param`. Then do one manual search in a
browser, right-click a result row → Inspect, and update `row_selector` /
`fields` in `county_configs.py` to match. You can sanity-check your fix
without touching any lead data:

```bash
python main.py --inspect-county "Duval FL" --inspect-sample-name "SMITH JOHN"
```

That runs one live search with the current config and reports how many
result rows it parsed — 0 rows almost always means `row_selector` needs
adjusting. Once confirmed, flip `"verified": False` to `"verified": True`.

## Adding a new county

Adding a county is just adding one dictionary entry to `COUNTY_CONFIGS` in
`county_configs.py`. No coding required beyond filling in a template —
here's the non-technical walkthrough:

1. **Find the search page.** Go to the county property appraiser's
   website and find the "search by owner name" tool. Copy its URL.

2. **Do one manual search** for any common name (e.g. "SMITH JOHN") and
   watch what happens:
   - **Does the URL in your address bar change** to include the name
     you typed (e.g. `...?owner=SMITH+JOHN`)? → this is an **`html_get`**
     site. Copy that URL pattern.
   - **Does the URL stay the same**, but a results table appears below
     the form? → open your browser's DevTools (F12) → **Network** tab,
     redo the search, and click the request that fired. If its form data
     includes a field called `__VIEWSTATE` → this is an
     **`aspnet_postback`** site.
   - **No new network request appears at all** (or it returns JSON, not
     HTML) and the page just re-renders? → this is a **`selenium`** site
     (JavaScript-rendered).

3. **Copy the matching template** from the bottom of
   `county_configs.py` — `TEMPLATE_HTML_GET`, `TEMPLATE_ASPNET_POSTBACK`,
   or `TEMPLATE_SELENIUM` — and paste it as a new entry in
   `COUNTY_CONFIGS`, keyed `"<county lowercase>|<ST>"`, e.g.
   `"hillsborough|fl"`.

4. **Fill in the URL(s)** from step 1/2.

5. **Find the field names / selectors:**
   - `html_get`: the query parameter name that held the owner name (from
     the changed URL in step 2).
   - `aspnet_postback`: run `python main.py --inspect-county "Your County ST"`
     after adding a bare-bones entry — it prints every form field's
     `name` attribute so you can find the owner-name box and the submit
     button.
   - Any type: right-click one result row in the browser → **Inspect**.
     Find the repeating element that wraps one property result (a `<tr>`,
     a `<div class="result-card">`, etc.) — that's your `row_selector`.
     Then find the specific `<td>`/`<span>`/`<div>` inside it that holds
     the owner name, address, mailing address, and parcel ID, and write a
     CSS selector for each (or just use `{"index": N}` to grab the Nth
     `<td>` if it's a plain table).

6. **Test on a tiny sample** before running it on real leads:

   ```bash
   python main.py --inspect-county "Your County ST" --inspect-sample-name "SMITH JOHN"
   ```

   Fix `row_selector`/`fields` until it reports a sensible number of
   parsed rows, then run a 1–2 row CSV through the full pipeline and
   check `results.csv` and `run.log`.

7. **Flip `"verified": True`** once you've confirmed real results look
   right, so the tool stops warning about it.

## Legal / ethical notes

- Every field this tool reads (owner name, property address, mailing
  address, parcel ID) is public record, published by the county itself.
- This tool only targets sites without CAPTCHAs and does not attempt to
  bypass any bot-detection or access controls — respect each site's
  Terms of Service and `robots.txt`, and keep the built-in rate limiting
  in place. Don't lower `--delay` to hammer a county server.
- Use scraped results for legitimate purposes (skip tracing your own
  leads, CRM import) — not for harassment or mass unsolicited contact
  campaigns.
