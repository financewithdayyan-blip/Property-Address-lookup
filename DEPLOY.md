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

**Important - do not use the Vercel project's auto-connected Turso integration
env vars (`TURSO_DATABASE_URL` / `TURSO_AUTH_TOKEN`) for this.** Discovered
the hard way during development: Vercel's Turso marketplace integration
provisions a **brand-new, isolated database branch on every single
deployment** (its hostname literally starts with `dpl-<deployment-id>-...`,
not the database's real name) and silently resets those two env vars to
point at the new branch on every build. A worker running elsewhere, pinned
to one fixed connection string, would never see anything the web app
writes - jobs would sit at "pending" forever with no error anywhere.

Instead, use a **stable, manually-created Turso database** and plain
(non-integration) env vars named `APP_TURSO_DATABASE_URL` /
`APP_TURSO_AUTH_TOKEN` - see `web/lib/db.ts`, which reads these specifically
instead of the integration-managed ones. Get the real database URL/token
from turso.tech directly (Databases → your database → connection details),
not from Vercel's Storage tab. Set them in the Vercel project (Settings →
Environment Variables, Production + Preview + Development) and in
`web/.env.local` for local dev - both need the exact same values as the
worker (step 3).

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

Make sure `APP_TURSO_DATABASE_URL` / `APP_TURSO_AUTH_TOKEN` (not the
integration-managed `TURSO_*` ones - see above) are set for the Production
environment in the Vercel project's env vars (Settings → Environment
Variables) - they already are if you're working from this repo.

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
3. Set environment variables `TURSO_DATABASE_URL` and `TURSO_AUTH_TOKEN`
   (worker.py's own naming - unrelated to Vercel's integration-managed vars)
   to the same **stable** database credentials from turso.tech directly, as
   described in step 1. Do not copy these from Vercel's Storage tab/Turso
   integration - see the warning in step 1 for why.
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

- **A county whose search_type is `selenium` isn't supported in the web
  version.** That needs a real browser, which doesn't run on Railway's
  default Python environment. Duval County was assumed to need this but
  turned out not to (see its `verification_note` in county_configs.py) -
  it runs here as a plain `aspnet_postback` county like any other. Rows
  for a genuinely unsupported county come back as a clear `ERROR` with an
  explanation, not a crash. Use the CLI (`main.py`, which does support
  `selenium` counties) for those instead.
- **No login.** A job's status/download page is only protected by its URL
  containing an unguessable UUID. Fine for a single-operator tool holding
  your own lead data; add a shared password in front of it if that
  changes.
- **Single worker only.** The row-claiming logic in `db.py` is not safe
  against two workers running concurrently (documented in
  `claim_next_pending_row()`'s docstring) - don't scale the worker to
  multiple instances without adding a proper claim-token guard first.
