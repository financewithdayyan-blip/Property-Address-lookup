# Deploying the web version

Two pieces, two hosts:

- **`web/`** (Next.js) → Vercel. Upload page, status page, download links.
- **`worker.py`** (Python) → Railway (or Render, Fly.io, etc. - anything that runs a
  long-lived process, unlike Vercel). Does the actual county-site searching.

Both talk to the same Turso database - that's the hand-off point between them.

This has already been done once during development (this repo's Vercel project
`property-address-lookup` is linked to `web/`, and the Turso database is
already connected to it) - these steps are for redeploying or setting up
fresh.

## 1. Database (Turso)

Already set up if you're working from this repo: `TURSO_DATABASE_URL` and
`TURSO_AUTH_TOKEN` are connected to the Vercel project (Storage tab). If you
need the raw values for the worker deployment (step 3), get them from Vercel's
dashboard: project → Storage tab → the Turso database → **.env.local** tab
(these are marked "sensitive" so `vercel env pull` returns them blank - the
dashboard's own reveal is the only way to get the real values back out).

Schema is applied by running `schema.sql` against the database once
(already done during development). To reapply from scratch, either use the
Turso dashboard's SQL console, the Turso CLI (`turso db shell <db-name> < schema.sql`),
or adapt the small Python script pattern used during development (connect
with the `libsql` package, split `schema.sql` on `;`, execute each
statement).

## 2. Web app (Vercel)

From the `web/` directory:

```
cd web
vercel link          # first time only - links this folder to the Vercel project
vercel --prod
```

No git repo is required for this - `vercel --prod` deploys whatever's in the
current directory directly. (If you'd rather use git-based auto-deploy on
push instead, connect a GitHub repo in the Vercel dashboard and set the
project's **Root Directory** to `web`.)

Make sure `TURSO_DATABASE_URL` / `TURSO_AUTH_TOKEN` are set for the
Production environment in the Vercel project's env vars (Settings →
Environment Variables) - they already are if you're working from this repo.

## 3. Worker (Railway)

The worker needs a host that runs a persistent process with no execution
time limit - Railway's free/hobby tier works fine for this workload.

1. Create a new Railway project, deploy from this repo (or `railway up`
   from the `bluebird_lookup/` root directory via the Railway CLI if you'd
   rather not push to git).
2. Set the start command to:
   ```
   pip install -r requirements.txt && python worker.py
   ```
3. Set environment variables `TURSO_DATABASE_URL` and `TURSO_AUTH_TOKEN` to
   the same values as the Vercel project (get them from Vercel's Storage tab
   as in step 1, or directly from the Turso dashboard).
4. Deploy. Check the logs - you should see:
   ```
   Worker starting. Supported search_types: {'arcgis_query'}
   ```
   and then a line per row it processes once a job is uploaded through the
   web app.

The worker reconnects automatically on transient database errors (verified
during development - Turso's remote connection can drop after a few minutes
idle, and the worker handles that without dying).

## Known limitations (by design, see the project plan)

- **Duval County (and any non-ArcGIS county) isn't supported in the web
  version.** It needs Selenium/a real browser, which doesn't run on
  Railway's default Python environment. Rows for unsupported counties come
  back as a clear `ERROR` with an explanation, not a crash. Use the CLI
  (`main.py`) for those counties instead.
- **No login.** A job's status/download page is only protected by its URL
  containing an unguessable UUID. Fine for a single-operator tool holding
  your own lead data; add a shared password in front of it if that
  changes.
- **Single worker only.** The row-claiming logic in `db.py` is not safe
  against two workers running concurrently (documented in
  `claim_next_pending_row()`'s docstring) - don't scale the worker to
  multiple instances without adding a proper claim-token guard first.
