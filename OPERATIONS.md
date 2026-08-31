# Operations

Running AI Value Flywheel for someone other than yourself: how to apply a schema change,
read the logs, diagnose a report, and answer the questions that come up first.

- [Applying migrations](#applying-migrations)
- [Reading the logs](#reading-the-logs)
- [Diagnosing a user report](#diagnosing-a-user-report)
- [Rate limits](#rate-limits)
- [Health check](#health-check)
- [Upgrading a deployed instance](#upgrading-a-deployed-instance)
- [Common failures](#common-failures)

---

## Applying migrations

Schema changes are an **explicit step**, run by you as the Lakebase owner. The app's
service principal holds DML but deliberately not DDL, so the running app cannot
apply a migration even if it wanted to.

```bash
# What would change — reads only, changes nothing
python3 scripts/migrate.py --profile <cli-profile> --project <lakebase-project> --status

# Apply what's pending
python3 scripts/migrate.py --profile <cli-profile> --project <lakebase-project>

# After adding migrations that CREATE tables, re-grant the app SP
python3 scripts/migrate.py --profile <p> --project <proj> --grant-app-sp <sp-client-id>
```

### Why not on startup

Startup used to concatenate every `server/migrations/*.sql` and execute the lot on
every boot. That is safe only while every statement is idempotent. The first
migration containing a backfill `UPDATE`, a seed `INSERT`, or a `DROP` of a
superseded column would silently corrupt or destroy data on the next restart —
with no error anywhere, because the previous run "succeeded" too.

Now each file is recorded in `schema_migrations` with a content checksum and
applied **at most once**, inside a transaction that also writes its ledger row. A
failure therefore leaves neither a half-applied file nor a lying ledger, and a
fixed migration can simply be re-run.

### Drift

If a file that was already applied has **changed**, the database no longer matches
the code. Both automatic responses are wrong — re-applying may destroy data,
ignoring it leaves the schema silently incorrect — so the tool refuses and tells
you which file.

The right fix is almost always to **write a new migration** for the change. Use
`--allow-drift` only when you have confirmed the edit was cosmetic (a comment, a
reformat); it means *carry on*, never *replay*.

Checksums normalize line endings and trailing whitespace, so a CRLF checkout or a
reformat is not drift. Whitespace *inside* a statement is not normalized, because
it can be inside a string literal.

### What the app does at startup

Reports, never applies. A pending migration is logged with the exact command to
run, and the app still serves every table that does exist — a schema check failing
is not a reason to refuse a portfolio that works.

---

## Reading the logs

JSON, one object per line, in Databricks Apps:

```json
{"ts":"2026-08-07T13:37:50.816Z","level":"INFO","logger":"grid_atlas.request",
 "msg":"POST /api/research/company -> 200 in 8423ms","request_id":"6df5bca0374248da"}
```

Locally the same records render as readable single lines.

| Field | Use |
|---|---|
| `request_id` | **The one that matters.** Every line emitted while handling a request carries it, and it is returned to the browser as `X-Request-Id`. |
| `level` | `WARNING` covers the things worth knowing: an LLM fallback, a rate limit, a failed audit write, schema drift. |
| `logger` | `grid_atlas.request` for the one-line-per-request summary; `server.*` for module detail. |

Turn up detail without a redeploy by setting `LOG_LEVEL=DEBUG` in `app.yaml`.

Health checks and static assets are excluded from request logging, or the
platform's probe would bury everything else.

### Secrets

Redaction happens in the **formatter**, not at call sites, because the realistic
leak is a token inside an exception nobody inspected — asyncpg's
`InvalidPasswordError` carries the Lakebase OAuth JWT verbatim. Bearer tokens,
JWTs, `password=` in a DSN, and `token:` keys in JSON are all replaced before
anything is written.

---

## Diagnosing a user report

"It didn't work" is answerable without reproducing it:

1. **Get the request id.** The browser has it on the response (`X-Request-Id`). If
   the user only has a timestamp, the request line gives you method, path, status
   and duration.
2. **Grep the app logs for that id.** Every line from that request, in order.
3. **Read the status.** `429` is a rate limit (see below). `502` from a chat or
   research endpoint is nearly always the serving endpoint — the message says
   whether it was a permission problem. `500` will have a redacted traceback.

The one-line-per-request summary also answers "was it slow or did it fail?" — the
question that otherwise costs a reproduction attempt.

---

## Rate limits

Per-actor token buckets on the endpoints that cost money or warehouse time.

| Class | Burst | Per minute | Applies to |
|---|---|---|---|
| `chat` | 6 | 20 | A conversational turn (up to 7 model calls each) |
| `research` | 3 | 6 | Company research — the most expensive single call in the app |
| `generate` | 4 | 12 | Use-case generation, classification, canonicalization |
| `sweep` | 2 | 4 | Warehouse sweeps: bootstrap, enrich, artifact sync, live sync |
| `write` | 30 | 120 | Ordinary writes |
| `confirm` | 10 | 60 | Confirm attempts |

These are **guard rails against accidental load** — a workshop room clicking at
once, a UI retry loop — not a quota system and not a security control. The numbers
are what the UI legitimately does, roughly doubled: no honest user should see a 429.

A 429 response includes `Retry-After` and says explicitly that it is a guard rail,
so it does not read as a ban.

To disable for a demo where fast clicking is deliberate:

```yaml
- name: RATE_LIMITS
  value: "off"
```

Accounting is per-worker and in memory. With N workers the effective ceiling is
N x the limit — still bounded, and it avoids adding a database round-trip to every
request in order to protect the database.

### Query budget

One chat turn is bounded to 120 database queries. `MAX_TOOL_ROUNDS` bounds the
turn's *model* calls, not the queries underneath them: one round can carry several
tool calls, each running its own queries. Exceeding the budget returns 429 with the
count, because the request was too expensive rather than malformed. The user's
message is already saved, so the conversation stays coherent on retry.

---

## Health check

`GET /api/health` — no auth beyond the app's own, safe to poll.

```json
{"status":"healthy","db_connected":true,"demo_mode":false,
 "serving_endpoint":"databricks-claude-sonnet-5","genie_space_configured":true,
 "counts":{"lobs":6,"data_assets":146,"use_cases":240},
 "rate_limits":{"enabled":true,"limits":{"chat":{"burst":6,"per_minute":20,
                                                 "tracked_actors":3}}}}
```

`counts` carrying an `error` instead of numbers means the pool connected but a query
failed — usually a missing grant or an unapplied migration. `rate_limits` reports
counts only, never who used them, since this endpoint is widely readable.

A **503 with `"status":"unhealthy"`** means Lakebase is configured (`PGHOST` is set)
but unreachable — a stale OAuth token, DNS, or an outage. Reads return empty and
writes 503 until it recovers. This is deliberately *not* reported as `demo_mode`:
demo mode means nobody configured Lakebase at all, and conflating the two made a
full outage render as "no data yet" over a populated database while the health check
still said healthy.

---

## Account administration

Destructive and cross-tenant account operations require an administrator:

| Operation | Endpoint |
|---|---|
| Create an account | `POST /api/accounts` |
| Rename / archive / make default | `PATCH /api/accounts/{id}` |
| Archive or hard-delete | `DELETE /api/accounts/{id}[?hard=true]` |
| List archived accounts | `GET /api/accounts?include_inactive=true` |

Set **`GRID_ATLAS_ADMINS`** to a comma-separated list of Databricks emails to permit
them:

```yaml
- name: GRID_ATLAS_ADMINS
  value: "ops@utility.com,platform@utility.com"
```

The identity is taken from the `X-Forwarded-Email` header the Databricks Apps proxy
injects, which the browser cannot forge. Matching is case-insensitive.

**`scripts/deploy.py` sets this for you.** Every deploy writes the deploying user's
Databricks email into `GRID_ATLAS_ADMINS`, so the operator who installs the app is an
admin by default — no separate bootstrap step. Pass `--admins a@x.com,b@x.com` to set
a different allowlist instead; the flag wins over the resolved deployer email. The
shipped `app.yaml` template keeps this empty (fail-closed) — deploy fills it per-deploy.

**Unset means nobody is an admin** and these operations return 403. That is the
intended default: hard-deleting an account cascades away a customer's calibrated
assumptions, research and proposals, so it fails closed rather than being available
to every authenticated user of the instance. A 403 naming `GRID_ATLAS_ADMINS` is the
signal to set it.

Reading and switching between *active* accounts is not gated — the deployment model
is one instance per customer, and every authenticated user of an instance is trusted
with that instance's data. There is no per-account membership model yet; see the
`TODO(multi-tenant)` notes in `server/accounts.py` for what would change.

---

## Upgrading a deployed instance

```bash
python3 scripts/check.py                                    # gates pass locally
python3 scripts/migrate.py --profile <p> --project <proj> --status   # what's pending
python3 scripts/migrate.py --profile <p> --project <proj>            # apply
python3 scripts/deploy.py                                   # deploy the code
```

Migrate **before** deploying code that depends on the new schema. The reverse order
leaves the app serving errors for the interval between the two, since it cannot
apply the migration itself.

The reference library (catalog, domains, assumptions) is seed data, not migrations.
Re-seeding is a separate, explicit choice — see `scripts/seed_clean.py`.

---

## Common failures

| Symptom | Cause | Fix |
|---|---|---|
| `password authentication failed for user <uuid>` | The SP's Postgres role is not identity-linked. Plain SQL `CREATE ROLE` does not link it. | Register via the roles API with `auth_method=LAKEBASE_OAUTH_V1` and `identity_type=SERVICE_PRINCIPAL`. See [`INSTALL.md`](INSTALL.md). |
| `External authorization failed` | The app is bound to a database *instance* but connects to a *project*. | Remove the `database` app resource; the app connects via `PGHOST`/`PGUSER`. |
| Everything reads empty, `demo_mode: true` | `PGHOST` unset, or the pool failed to connect. | Check the startup log — the failure is logged with its cause. |
| Agents return heuristic results | No serving endpoint, no `CAN_QUERY`, or the endpoint rejected a parameter. | The log line names the exception. Parameter rejection is negotiated automatically; a permission error is not. |
| `SCHEMA DRIFT` at startup | An applied migration file changed. | See [drift](#drift). |
| Discovery tabs disabled | `ATLAS_CATALOG` blank. | Set it, then run the in-app setup probes for the exact GRANTs. |
| A 429 nobody expected | Several people on one shared identity, or a UI retry loop. | `/api/health` shows the class and tracked actors; the log line names the actor and class. |
