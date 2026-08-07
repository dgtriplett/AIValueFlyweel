/*
 * Grid Atlas — Setup & Discovery Console
 *
 * A dependency-free operator console for the workflows added by the discovery
 * and agent layers. See index.html's comment for why this is plain JS rather
 * than part of the React SPA.
 *
 * Conventions used throughout:
 *   - Every network call goes through `api()`, which surfaces the server's own
 *     error `detail` rather than a generic "request failed". Those details are
 *     written to be actionable (they name the GRANT to run, the flag to pass),
 *     so hiding them would waste the effort spent on them.
 *   - Anything that costs money or mutates state is behind an explicit button,
 *     never fired on page load. AI enrichment on a large estate is real spend.
 *   - `text()` is used for all interpolation of server data. Table comments,
 *     LLM output, and schema names all end up on this page, and none of them
 *     are trustworthy enough to inject as HTML.
 */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const main = $("#main");

  // ---------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------

  /** Escape for safe interpolation into innerHTML. */
  function text(value) {
    if (value === null || value === undefined) return "";
    return String(value).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  function num(value) {
    if (value === null || value === undefined || value === "") return "—";
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed.toLocaleString() : text(value);
  }

  async function api(path, options = {}) {
    const response = await fetch(`/api${path}`, {
      headers: options.body instanceof FormData
        ? {}
        : { "Content-Type": "application/json" },
      ...options,
    });
    let payload = null;
    try {
      payload = await response.json();
    } catch {
      /* a non-JSON body (a proxy error page, say) leaves payload null */
    }
    if (!response.ok) {
      // FastAPI puts the useful message in `detail`; these are written to tell
      // the operator exactly what to fix, so prefer them over the status text.
      const detail = payload && (payload.detail || payload.error);
      throw new Error(detail || `${response.status} ${response.statusText}`);
    }
    return payload;
  }

  function banner(kind, message) {
    return `<div class="banner ${kind}">${text(message)}</div>`;
  }

  /** Render a busy state into a button and return a restore function. */
  function busy(button, label = "Working…") {
    const original = button.innerHTML;
    button.disabled = true;
    button.innerHTML = `<span class="spin"></span> ${text(label)}`;
    return () => {
      button.disabled = false;
      button.innerHTML = original;
    };
  }

  /** Wire every [data-act] button in a container to an async handler. */
  function onActions(container, handlers) {
    container.querySelectorAll("[data-act]").forEach((button) => {
      button.addEventListener("click", async () => {
        const handler = handlers[button.dataset.act];
        if (!handler) return;
        const restore = busy(button, button.dataset.busy || "Working…");
        try {
          await handler(button);
        } catch (error) {
          const slot = $("#result") || container;
          slot.innerHTML = banner("err", error.message);
        } finally {
          restore();
        }
      });
    });
  }

  // ---------------------------------------------------------------------
  // Get started — the SINGLE onboarding surface
  //
  // Merges what used to be three overlapping places: the SPA's "Get Started"
  // tab (Excel round-trip + auto-populate from system tables), the console's
  // "Setup & health" probes, and the console's "Discovery" pipeline. All three
  // answered "how do I get my data in?", so a user had to know which one to
  // open — and the SPA tab silently duplicated work the pipeline does better.
  //
  // Structure is a dependency-ordered checklist: health first (nothing else can
  // work until the probes pass), then a CHOICE of population paths, then the
  // pipeline stages that only make sense once data exists. Steps that cannot yet
  // succeed render locked rather than failing when clicked.
  // ---------------------------------------------------------------------
  async function viewStart() {
    main.innerHTML = `<p class="muted"><span class="spin"></span> Checking your workspace…</p>`;

    const [status, inventory] = await Promise.all([
      api("/setup/status").catch((e) => ({ error: e.message })),
      api("/ingestion/summary").catch(() => ({ configured: false })),
    ]);

    if (status.error) {
      main.innerHTML = `<h2>Get started</h2>` + banner("err",
        `Could not read setup status: ${status.error}`);
      return;
    }

    $("#env").textContent = status.environment || "";

    const check = (name) => (status.checks || []).find((c) => c.name === name) || {};
    const healthy = !!status.ready;
    const discoveryReady = !!inventory.configured && !!inventory.available;
    const hasInventory = (inventory.tables || 0) > 0;
    const hasEnrichment = (inventory.enriched_tables || 0) > 0;

    const pills = (status.checks || []).map((c) => {
      const kind = c.ok ? "ok" : (c.required ? "bad" : "warn");
      return `<span class="pill ${kind}" title="${text(c.detail)}">
        <span class="dot"></span>${text(c.label)}</span>`;
    }).join("");

    const failing = (status.checks || []).filter((c) => !c.ok);
    const failureCards = failing.map((c) => `
      <div class="card" style="margin-top:10px">
        <div class="row">
          <strong>${text(c.label)}</strong>
          <span class="pill ${c.required ? "bad" : "warn"}">
            ${c.required ? "required" : "optional"}</span>
        </div>
        <p class="small muted" style="margin:6px 0 0">${text(c.detail)}</p>
        ${c.fix ? `<p class="small" style="margin:6px 0 0">${text(c.fix)}</p>` : ""}
        ${(c.grants || []).length
          ? `<pre class="sql">${text(c.grants.join("\n"))}</pre>` : ""}
      </div>`).join("");

    const stepNum = (n, state) =>
      `<div class="step-num ${state}">${state === "done" ? "✓" : n}</div>`;

    main.innerHTML = `
      <h2>Get started</h2>
      <p class="lede">Everything needed to go from an empty install to a populated
        portfolio, in order. Each step says why it matters and what it will change;
        nothing here runs on its own.</p>

      <div class="pills">${pills}</div>
      <div id="result"></div>

      <!-- 1. health -->
      <div class="step">
        ${stepNum(1, healthy ? "done" : "active")}
        <div class="card">
          <h3>1 · Check the workspace</h3>
          <p class="step-why">Every later step depends on Lakebase, a warehouse, and
            Unity Catalog. Probing first means a missing grant shows up here with the
            SQL to fix it, instead of as a 403 halfway through a pipeline.</p>
          ${healthy
            ? banner("ok", `Ready — ${status.summary.passing} of ${status.summary.total} `
                + `checks passing.`
                + (status.summary.optional_failing
                  ? ` ${status.summary.optional_failing} optional feature(s) not `
                    + `configured; the portfolio works without them.` : ""))
            : banner("err", `${status.summary.required_failing} required check(s) `
                + `failing. ${status.next_action || ""}`)}
          <div class="row">
            <button class="action secondary" data-act="recheck" data-busy="Re-checking…">
              Re-check</button>
            <button class="action secondary" data-act="grants">Show all GRANTs</button>
            <span class="small muted">Running as
              <code>${text(status.service_principal)}</code></span>
          </div>
          ${failureCards}
        </div>
      </div>

      <!-- 2. populate: two paths -->
      <div class="step">
        ${stepNum(2, hasInventory ? "done" : (healthy ? "active" : ""))}
        <div class="card">
          <h3>2 · Tell the app what data you have</h3>
          <p class="step-why">Two ways in, and they compose — most utilities do both.
            Sweep the estate to discover what exists, then use the workbook to record
            the judgement calls only a person can make.</p>

          <div class="card" style="background:var(--navy-900)">
            <h3>A · Sweep your Databricks estate <span class="pill ${
              hasInventory ? "ok" : "warn"}">${hasInventory
                ? num(inventory.tables) + " tables found" : "nothing yet"}</span></h3>
            <p class="small muted">This app can only authenticate to the one
              workspace it runs in. Download the extractor and run it on your own
              machine — under <em>your</em> credentials, so it reaches every workspace
              you can. Metadata only; no table contents are read, and nothing leaves
              your machine until you upload the CSVs back here.</p>
            <div class="row" style="margin-top:10px">
              <a class="action" href="/api/ingestion/extractor/download">
                ⬇ Download extractor (.zip)</a>
              <span class="small muted" id="extractor-info"></span>
            </div>
            ${discoveryReady ? `
              <div class="stat" style="margin:12px 0">
                <div><span class="k">Workspaces</span><span class="v">${num(inventory.workspaces)}</span></div>
                <div><span class="k">Schemas</span><span class="v">${num(inventory.schemas)}</span></div>
                <div><span class="k">Tables</span><span class="v">${num(inventory.tables)}</span></div>
                <div><span class="k">Enriched</span><span class="v">${num(inventory.enriched_tables)}</span></div>
              </div>
              <div class="row" style="margin-top:8px">
                <button class="action secondary" data-act="bootstrap" data-busy="Creating…">
                  Create discovery tables</button>
              </div>
              <div class="row" style="margin-top:10px">
                <div><label for="f-schemas">all_schemas.csv</label>
                  <input type="file" id="f-schemas" accept=".csv"></div>
                <button class="action" data-act="up-schemas" data-busy="Uploading…">Upload</button>
              </div>
              <div class="row" style="margin-top:8px">
                <div><label for="f-tables">all_tables.csv</label>
                  <input type="file" id="f-tables" accept=".csv"></div>
                <button class="action" data-act="up-tables" data-busy="Uploading…">Upload</button>
              </div>
              <div class="row" style="margin-top:8px">
                <div><label for="f-columns">all_columns.csv
                  <span class="muted">(optional — markedly better AI accuracy)</span></label>
                  <input type="file" id="f-columns" accept=".csv"></div>
                <button class="action" data-act="up-columns" data-busy="Uploading…">Upload</button>
              </div>`
            : banner("info", inventory.configured
                ? `Discovery catalog unreachable: ${text(inventory.error || "unknown")}`
                : "Set ATLAS_CATALOG in app.yaml to enable the estate sweep. The "
                  + "curated 146-module catalog and the workbook below both work "
                  + "without it.")}
          </div>

          <div class="card" style="background:var(--navy-900)">
            <h3>B · Fill in the workbook</h3>
            <p class="small muted">An Excel round-trip over the reference library.
              Circulate it, have owners mark what is actually landed, upload it back.
              This captures the things discovery can't infer — whether a source is
              <em>governed</em>, who owns it, what it is worth.</p>
            <div class="row" style="margin-top:10px">
              <a class="action secondary" href="/api/onboarding/export.xlsx">
                Download workbook</a>
              <div><label for="f-workbook">Filled workbook</label>
                <input type="file" id="f-workbook" accept=".xlsx,.csv"></div>
              <button class="action secondary" data-act="wb-preview" data-busy="Checking…">
                Preview changes</button>
              <button class="action" data-act="wb-apply" data-busy="Applying…">Apply</button>
            </div>
            <p class="small muted" style="margin:8px 0 0">Preview first — it shows
              every field that would change before anything is written.</p>
          </div>

          <div class="card" style="background:var(--navy-900)">
            <h3>C · Detect from system tables</h3>
            <p class="small muted">Reads lineage and job history to auto-advance
              sources that show real activity. Read-only against Databricks, and
              preview-first.</p>
            <div class="row" style="margin-top:10px">
              <button class="action secondary" data-act="sync-dry" data-busy="Checking…">
                Preview</button>
              <button class="action secondary" data-act="sync-apply" data-busy="Syncing…">
                Apply</button>
            </div>
          </div>
        </div>
      </div>

      <!-- 3. enrich -->
      <div class="step ${hasInventory ? "" : "locked"}">
        ${stepNum(3, hasEnrichment ? "done" : (hasInventory ? "active" : ""))}
        <div class="card">
          <h3>3 · Enrich and normalize</h3>
          <p class="step-why">Raw table names don't say what a system is. Enrichment
            names each table in business terms; normalization then collapses the
            free-text labels — otherwise ~1,000 distinct strings describe ~50 real
            systems and every rollup is noise.</p>
          ${hasInventory ? `
            <div class="row">
              <div><label for="company">Company name (prompt context)</label>
                <input id="company" placeholder="e.g. Eversource Energy"></div>
              <div><label for="maxrows">Cap tables (blank = all)</label>
                <input id="maxrows" type="number" min="1" placeholder="500"
                  style="width:120px"></div>
            </div>
            <div class="row" style="margin-top:10px">
              <button class="action secondary" data-act="enrich-schemas" data-busy="Enriching…">
                Enrich schemas</button>
              <button class="action" data-act="enrich-tables" data-busy="Enriching…">
                Enrich tables</button>
              <button class="action secondary" data-act="canonicalize" data-busy="Normalizing…">
                Normalize sources</button>
            </div>
            <p class="small muted" style="margin:10px 0 0">Enrichment spend scales
              with table count — cap the first run to check quality and cost. Only the
              long tail of normalization costs an LLM call.</p>`
            : `<p class="small muted">Upload an inventory in step 2 first.</p>`}
        </div>
      </div>

      <!-- 4. attribute -->
      <div class="step ${hasEnrichment ? "" : "locked"}">
        ${stepNum(4, hasEnrichment ? "active" : "")}
        <div class="card">
          <h3>4 · Connect it to the portfolio</h3>
          <p class="step-why">This is the step that makes the rest of the app move:
            attributing discovered tables to catalog modules is what turns "we have
            40,000 tables" into "these use cases are now shovel-ready".</p>
          ${hasEnrichment ? `
            <div class="row">
              <label class="row small" style="margin:0">
                <input type="checkbox" id="advance" style="width:auto">
                Advance ingestion status to “landed”</label>
            </div>
            <div class="row" style="margin-top:10px">
              <button class="action" data-act="attribute" data-busy="Attributing…">
                Attribute to the catalog</button>
            </div>
            <p class="small muted" style="margin:10px 0 0">Advancing status moves
              readiness and therefore the roadmap, so it is off by default — and caps
              at <em>landed</em>, because finding tables proves data exists, not that
              it is curated.</p>`
            : `<p class="small muted">Run enrichment in step 3 first.</p>`}
        </div>
      </div>

      <!-- 5. what next -->
      <div class="step">
        ${stepNum(5, "")}
        <div class="card">
          <h3>5 · Then what</h3>
          <p class="step-why">Once data is registered, the rest of the app has
            something to reason about.</p>
          <ul class="tight small">
            <li><a href="#coverage">Coverage &amp; gaps</a> — which data needs are
              met per line of business, and what you already have that nothing uses.</li>
            <li><a href="#generate">Generate use cases</a> — propose new ones
              grounded in what is actually landed.</li>
            <li><a href="#taxonomy">Taxonomy</a> — classify how sources arrive and how
              critical they are.</li>
            <li><a href="/">Portfolio</a> — readiness, value, and the roadmap.</li>
          </ul>
        </div>
      </div>`;

    const upload = async (inputId, path, label) => {
      const input = $(`#${inputId}`);
      if (!input || !input.files || !input.files[0]) {
        throw new Error(`Choose a ${label} file first.`);
      }
      const form = new FormData();
      form.append("file", input.files[0]);
      const result = await api(path, { method: "POST", body: form });
      $("#result").innerHTML = banner("ok",
        `${label}: ${num(result.rows_written ?? result.tables_updated)} row(s) written `
        + `from ${num(result.rows_in_file)} in the file.`);
    };

    const enrichBody = () => {
      const maxRows = $("#maxrows") ? $("#maxrows").value : "";
      return {
        company_name: ($("#company") && $("#company").value) || null,
        max_rows: maxRows ? Number(maxRows) : null,
      };
    };

    const workbook = async (apply) => {
      const input = $("#f-workbook");
      if (!input.files || !input.files[0]) throw new Error("Choose a workbook first.");
      const form = new FormData();
      form.append("file", input.files[0]);
      const r = await api(`/onboarding/import?apply=${apply}`,
        { method: "POST", body: form });
      const changes = r.changes || {};
      const lines = Object.entries(changes)
        .filter(([, v]) => (v || []).length)
        .map(([k, v]) => `<li>${text(k.replace(/_/g, " "))}: ${v.length} change(s)</li>`)
        .join("");
      $("#result").innerHTML = banner(apply ? "ok" : "info",
        apply ? `Applied ${num(r.applied)} change(s).`
              : `${num((r.summary || {}).data_sources)} data source(s), `
                + `${num((r.summary || {}).use_cases)} use case(s), `
                + `${num((r.summary || {}).assumptions)} assumption(s) would change.`)
        + (lines ? `<div class="card"><ul class="tight small">${lines}</ul></div>` : "")
        + ((r.errors || []).length
          ? `<div class="card"><h3>Problems</h3><ul class="tight small muted">${
              r.errors.slice(0, 8).map((e) => `<li>${text(e)}</li>`).join("")}</ul></div>`
          : "");
    };

    // Tell the user what they're about to download before they click it.
    api("/ingestion/extractor/info").then((info) => {
      const slot = $("#extractor-info");
      if (!slot) return;
      slot.textContent = info.available
        ? `${info.files.length} files, ${Math.round(info.total_bytes / 1024)} KB · `
          + `needs ${info.requires[0]}`
        : "unavailable in this deployment";
    }).catch(() => { /* the button still works; the hint is a nicety */ });

    onActions(main, {
      recheck: () => viewStart(),
      grants: async () => {
        const payload = await api("/setup/grants");
        $("#result").innerHTML = `<div class="card"><h3>Unity Catalog privileges</h3>
          <p class="small muted">Run as a metastore admin or catalog owner. Steps that
            cannot be expressed as SQL are included as comments.</p>
          <pre class="sql">${text(payload.sql)}</pre></div>`;
      },
      bootstrap: async () => {
        const r = await api("/ingestion/bootstrap", { method: "POST" });
        $("#result").innerHTML = banner("ok",
          `Discovery tables ready in ${text(r.catalog)}.${text(r.schema)}.`);
      },
      "up-schemas": () => upload("f-schemas", "/ingestion/upload/schemas", "Schemas"),
      "up-tables": () => upload("f-tables", "/ingestion/upload/tables", "Tables"),
      "up-columns": () => upload("f-columns", "/ingestion/upload/columns", "Columns"),
      "wb-preview": () => workbook(false),
      "wb-apply": () => workbook(true),
      "sync-dry": () => runSync(false),
      "sync-apply": () => runSync(true),
      "enrich-schemas": async () => {
        const r = await api("/ingestion/enrich/schemas",
          { method: "POST", body: JSON.stringify(enrichBody()) });
        $("#result").innerHTML = banner("ok",
          `${num(r.schemas_enriched_total)} schema(s) now enriched.`);
      },
      "enrich-tables": async () => {
        const r = await api("/ingestion/enrich/tables",
          { method: "POST", body: JSON.stringify(enrichBody()) });
        $("#result").innerHTML = banner(r.staged_errors ? "warn" : "ok",
          `${num(r.tables_enriched_total)} table(s) enriched`
          + (r.staged_rows ? ` (${num(r.staged_ok)} of ${num(r.staged_rows)} model `
            + `calls succeeded this run` : "")
          + (r.staged_errors ? `, ${num(r.staged_errors)} failed).` : ")."));
      },
      canonicalize: async () => {
        const r = await api("/ingestion/canonicalize",
          { method: "POST", body: JSON.stringify({}) });
        $("#result").innerHTML = banner("ok",
          `${num(r.distinct_labels)} distinct label(s); ${num(r.newly_resolved)} newly `
          + `resolved (exact ${num(r.exact)}, normalized ${num(r.normalized)}, `
          + `AI ${num(r.llm)}, unmapped ${num(r.other)}).`
          + (r.other ? " Review the unmapped ones under Source mapping." : ""));
      },
      attribute: async () => {
        const r = await api("/ingestion/attribute", {
          method: "POST",
          body: JSON.stringify({ advance_status: $("#advance").checked }),
        });
        const advanced = (r.advanced || []).map((a) =>
          `<li>${text(a.label)}: ${text(a.from)} → ${text(a.to)}</li>`).join("");
        $("#result").innerHTML = banner("ok",
          `${num(r.assets_matched)} catalog module(s) matched from `
          + `${num(r.canonicals_discovered)} discovered source system(s).`)
          + (advanced ? `<div class="card"><h3>Status advanced</h3>
              <ul class="tight small">${advanced}</ul></div>` : "")
          + ((r.unmatched_canonicals || []).length
            ? `<div class="card"><h3>Discovered but not in the catalog</h3>
                <p class="small muted">Add these as data assets, or map them under
                  Source mapping.</p>
                <p class="small mono">${text(r.unmatched_canonicals.join(", "))}</p>
              </div>` : "");
      },
    });

    async function runSync(apply) {
      const r = await api(`/live/sync?apply=${apply ? "true" : "false"}`,
        { method: "POST" });
      $("#result").innerHTML = banner(apply ? "ok" : "info",
        `${apply ? "Applied" : "Would change"}: `
        + `${(r.asset_changes || []).length} data source(s), `
        + `${(r.uc_changes || []).length} use case(s).`)
        + ((r.notes || []).length
          ? `<div class="card"><ul class="tight small muted">${
              r.notes.map((n) => `<li>${text(n)}</li>`).join("")}</ul></div>` : "");
    }
  }

  // ---------------------------------------------------------------------
  // Coverage & gaps — domains x lines of business
  // ---------------------------------------------------------------------
  async function viewCoverage() {
    main.innerHTML = `<p class="muted"><span class="spin"></span> Building the matrix…</p>`;
    const data = await api("/domains/coverage-matrix");
    const lobs = data.lobs || [];
    const s = data.summary || {};

    // "available" is the interesting state: data you already have that no use case
    // in that LOB asks for. Nothing else in the app surfaces that.
    const rows = (data.rows || []).map((row) => `
      <tr>
        <td>
          <strong>${text(row.domain.label)}</strong>
          ${row.universal_gap ? `<span class="pill bad">universal gap</span>` : ""}
          <div class="small muted mono">${text(row.domain.category || "")}
            · ${row.ready_asset_count}/${row.serving_asset_count} landed</div>
        </td>
        ${row.cells.map((cell) => `
          <td class="cell">
            <span class="swatch sw-${cell.state}" title="${text(row.domain.label)} × ${text(cell.lob_name)} — ${text(cell.state)}${
              cell.use_case_count ? `; ${cell.use_case_count} use case(s), $${cell.value_mm}M` : ""}">
              ${cell.use_case_count || ""}</span>
          </td>`).join("")}
      </tr>`).join("");

    main.innerHTML = `
      <h2>Coverage &amp; gaps</h2>
      <p class="lede">Every data need against every line of business. Numbers are the
        use cases in that cell; colour is whether the need is met.</p>

      <div class="stat" style="margin-bottom:16px">
        <div><span class="k">Covered</span><span class="v">${num(s.covered)}</span></div>
        <div><span class="k">Gaps</span><span class="v">${num(s.gaps)}</span></div>
        <div><span class="k">Have but unused</span><span class="v">${num(s.available_unused)}</span></div>
        <div><span class="k">Universal gaps</span><span class="v">${num(s.universal_gaps)}</span></div>
        <div><span class="k">Value at risk</span><span class="v">$${num(s.value_at_risk_mm)}M</span></div>
      </div>

      <div class="legend">
        <span><span class="key sw-covered"></span>Covered — needed and landed</span>
        <span><span class="key sw-gap"></span>Gap — needed, not landed</span>
        <span><span class="key sw-available"></span>Have it, nothing uses it</span>
        <span><span class="key sw-unused"></span>Not needed here</span>
      </div>

      ${s.available_unused
        ? banner("info", `${s.available_unused} cell(s) are data you have already `
            + `landed that no use case in that line of business asks for — the `
            + `cheapest place to look for a new use case, since the data is there.`)
        : ""}
      ${s.universal_gaps
        ? banner("err", `${s.universal_gaps} data need(s) are unmet in EVERY line of `
            + `business that requires them. Nothing in the estate provides these, so `
            + `they are acquisition decisions rather than ingestion backlog.`)
        : ""}

      ${lobs.length ? `
        <table class="matrix">
          <thead><tr><th style="min-width:230px">Data need</th>
            ${lobs.map((l) => `<th class="rot">${text(l.name)}</th>`).join("")}</tr></thead>
          <tbody>${rows}</tbody>
        </table>`
        : banner("info", "No lines of business defined yet.")}`;
  }
  // ---------------------------------------------------------------------
  // Source mapping review
  // ---------------------------------------------------------------------
  async function viewAliases() {
    main.innerHTML = `<p class="muted"><span class="spin"></span> Loading mappings…</p>`;
    const [needsReview, categories] = await Promise.all([
      api("/ingestion/aliases?needs_review=true&limit=200").catch(() => []),
      api("/data-assets").then((assets) => [...new Set(
        assets.map((a) => a.source_category).filter(Boolean))].sort()).catch(() => []),
    ]);

    const options = ["Other", ...categories].map((c) =>
      `<option value="${text(c)}">${text(c)}</option>`).join("");

    main.innerHTML = `
      <h2>Source mapping</h2>
      <p class="lede">Raw source-system labels the normalizer could not confidently
        resolve. Correcting one here pins it permanently — later runs will never
        overwrite a human decision.</p>
      <div id="result"></div>
      ${needsReview.length ? `
        <table>
          <thead><tr><th>Raw label</th><th>Mapped to</th><th>How</th>
            <th>Confidence</th><th>Correct it</th></tr></thead>
          <tbody>${needsReview.map((alias) => `
            <tr data-id="${alias.id}">
              <td class="mono">${text(alias.raw)}</td>
              <td>${text(alias.canonical || "—")}</td>
              <td class="small muted">${text(alias.mapped_by)}</td>
              <td><span class="pill ${alias.confidence === "high" ? "ok"
                : alias.confidence === "low" ? "bad" : "warn"}">
                ${text(alias.confidence || "none")}</span></td>
              <td class="row">
                <select data-role="canonical">${options}</select>
                <button class="action secondary small" data-act="fix"
                  data-id="${alias.id}" data-busy="Saving…">Save</button>
              </td>
            </tr>`).join("")}</tbody>
        </table>`
        : banner("ok", "Nothing needs review — every source label is confidently mapped.")}`;

    onActions(main, {
      fix: async (button) => {
        const row = button.closest("tr");
        const canonical = $('[data-role="canonical"]', row).value;
        await api(`/ingestion/aliases/${button.dataset.id}`, {
          method: "PATCH",
          body: JSON.stringify({ canonical }),
        });
        row.style.opacity = "0.45";
        $("#result").innerHTML = banner("ok",
          `Mapped to ${canonical}. This mapping is now pinned.`);
      },
    });
  }

  // ---------------------------------------------------------------------
  // Data domains
  // ---------------------------------------------------------------------
  async function viewDomains() {
    main.innerHTML = `<p class="muted"><span class="spin"></span> Loading domains…</p>`;
    const [domains, gaps] = await Promise.all([
      api("/domains"),
      api("/domains/gaps?limit=15").catch(() => ({ gaps: [], summary: {} })),
    ]);

    const satisfied = domains.filter((d) => d.satisfied).length;

    main.innerHTML = `
      <h2>Data domains</h2>
      <p class="lede">Semantic data needs, decoupled from the products that provide
        them. A use case needing <em>work order history</em> is satisfied by any
        system that supplies it — so running Maximo instead of SAP PM no longer
        shows as a gap.</p>

      <div class="stat" style="margin-bottom:20px">
        <div><span class="k">Domains</span><span class="v">${num(domains.length)}</span></div>
        <div><span class="k">Satisfied</span><span class="v">${num(satisfied)}</span></div>
        <div><span class="k">Gaps</span><span class="v">${num(domains.length - satisfied)}</span></div>
        <div><span class="k">Value blocked</span><span class="v">$${num(
          (gaps.summary || {}).total_value_blocked_mm)}M</span></div>
      </div>

      ${(gaps.gaps || []).length ? `
        <section>
          <h3 class="small muted">Gaps ranked by value blocked</h3>
          <table>
            <thead><tr><th>Data need</th><th>Category</th>
              <th class="num">Use cases</th><th class="num">Value blocked</th>
              <th>Why</th></tr></thead>
            <tbody>${gaps.gaps.map((gap) => `<tr>
              <td><strong>${text(gap.domain.label)}</strong></td>
              <td class="small muted">${text(gap.domain.category)}</td>
              <td class="num">${num(gap.blocked_use_case_count)}</td>
              <td class="num">$${num(gap.value_blocked_mm)}M</td>
              <td class="small muted">${text(gap.rationale)}</td>
            </tr>`).join("")}</tbody>
          </table>
        </section>` : ""}

      <section>
        <h3 class="small muted">All domains</h3>
        <table>
          <thead><tr><th>Data need</th><th>Category</th>
            <th class="num">Sources</th><th class="num">Landed</th>
            <th class="num">Used by</th><th>State</th></tr></thead>
          <tbody>${domains.map((domain) => `<tr>
            <td><strong>${text(domain.label)}</strong>
              <div class="small muted mono">${text(domain.name)}</div></td>
            <td class="small muted">${text(domain.category)}</td>
            <td class="num">${num(domain.serving_asset_count)}</td>
            <td class="num">${num(domain.ready_asset_count)}</td>
            <td class="num">${num(domain.required_by_count)}</td>
            <td><span class="pill ${domain.satisfied ? "ok" : "bad"}">
              ${domain.satisfied ? "satisfied" : "gap"}</span></td>
          </tr>`).join("")}</tbody>
        </table>
      </section>`;
  }

  // ---------------------------------------------------------------------
  // Taxonomy
  // ---------------------------------------------------------------------
  async function viewTaxonomy() {
    main.innerHTML = `<p class="muted"><span class="spin"></span> Loading taxonomy…</p>`;
    const [coverage, current] = await Promise.all([
      api("/taxonomy/coverage"),
      api("/taxonomy"),
    ]);

    const distribution = Object.entries(current.distribution || {}).map(([dimension, values]) => {
      const rows = Object.entries(values).sort((a, b) => b[1] - a[1]);
      if (!rows.length) return "";
      return `<div class="card">
        <h3>${text(dimension.replace(/_/g, " "))}</h3>
        <table><tbody>${rows.map(([value, count]) => `<tr>
          <td>${text(value)}</td><td class="num">${num(count)}</td></tr>`).join("")}
        </tbody></table></div>`;
    }).join("");

    main.innerHTML = `
      <h2>Taxonomy</h2>
      <p class="lede">Three dimensions that describe a source in ways nothing else
        here does: how the data arrives, how operationally critical it is, and what
        kind of thing produces it. Classifications are effective-dated, so changing
        one keeps its history.</p>

      <div class="stat" style="margin-bottom:16px">
        <div><span class="k">Assets</span><span class="v">${num(coverage.total_assets)}</span></div>
        <div><span class="k">Fully classified</span>
          <span class="v">${num(coverage.fully_classified)}</span></div>
        ${Object.entries(coverage.by_dimension || {}).map(([dimension, stats]) => `
          <div><span class="k">${text(dimension.replace(/_/g, " "))}</span>
            <span class="v">${stats.pct}%</span></div>`).join("")}
      </div>

      <div class="row" style="margin-bottom:16px">
        <button class="action" data-act="classify" data-busy="Classifying…">
          Classify unlabelled assets</button>
        <span class="small muted">Manual classifications are never overwritten.</span>
      </div>
      <div id="result"></div>
      ${distribution || `<p class="muted small">Nothing classified yet.</p>`}`;

    onActions(main, {
      classify: async () => {
        const result = await api("/taxonomy/classify", {
          method: "POST", body: JSON.stringify({ max_assets: 200 }),
        });
        $("#result").innerHTML = banner("ok",
          `${num(result.values_written)} classification(s) written across `
          + `${num(result.assets_considered)} asset(s) in ${num(result.batches)} batch(es).`)
          + ((result.warnings || []).length
            ? `<div class="card"><h3>Notes</h3><ul class="tight small muted">${
                result.warnings.map((w) => `<li>${text(w)}</li>`).join("")}</ul></div>`
            : "");
        setTimeout(viewTaxonomy, 1200);
      },
    });
  }

  // ---------------------------------------------------------------------
  // Generate use cases
  // ---------------------------------------------------------------------
  async function viewGenerate() {
    main.innerHTML = `<p class="muted"><span class="spin"></span> Loading…</p>`;
    const lobs = await api("/lobs").catch(() => []);

    main.innerHTML = `
      <h2>Generate use cases</h2>
      <p class="lede">Propose new use cases grounded in the data you actually have.
        <strong>Ready</strong> means buildable today; <strong>gap</strong> means it
        makes the case for landing new data. Nothing is saved until you approve it.</p>

      <section class="card">
        <div class="row">
          <div class="field"><label for="lob">Line of business</label>
            <select id="lob"><option value="">All</option>${lobs.map((lob) =>
              `<option value="${lob.id}">${text(lob.name)}</option>`).join("")}</select></div>
          <div class="field"><label for="lens">Lens</label>
            <select id="lens">
              <option value="both">Both</option>
              <option value="ready">Ready — data already landed</option>
              <option value="gap">Gap — justifies new ingestion</option>
            </select></div>
          <div class="field"><label for="count">How many</label>
            <input id="count" type="number" min="1" max="15" value="6" style="width:80px"></div>
          <div class="field"><label for="horizon">Bias</label>
            <select id="horizon">
              <option value="">None</option>
              <option value="quick_win">Quick wins</option>
              <option value="strategic">Strategic</option>
            </select></div>
        </div>
        <div class="row">
          <label class="row small" style="margin:0">
            <input type="checkbox" id="regulatory" style="width:auto">
            Prioritize regulatory / compliance</label>
          <div class="grow"></div>
          <button class="action" data-act="generate" data-busy="Generating…">Generate</button>
        </div>
      </section>
      <div id="result"></div>`;

    onActions(main, {
      generate: async () => {
        const body = {
          lob_id: $("#lob").value ? Number($("#lob").value) : null,
          lens: $("#lens").value,
          count: Number($("#count").value) || 6,
          time_horizon_bias: $("#horizon").value || null,
          prioritize_regulatory: $("#regulatory").checked,
        };
        const preview = await api("/generate/use-cases",
          { method: "POST", body: JSON.stringify(body) });
        renderPreview(preview);
      },
    });

    function renderPreview(preview) {
      const cards = preview.candidates.map((candidate) => `
        <div class="card">
          <div class="row">
            <label class="row" style="margin:0">
              <input type="checkbox" class="pick" value="${text(candidate.candidate_id)}"
                checked style="width:auto"></label>
            <strong class="grow">${text(candidate.title)}</strong>
            <span class="pill ${candidate.lens === "ready" ? "ok" : "warn"}">
              ${text(candidate.lens)}</span>
            <span class="pill ${candidate.effort_tshirt === "S" ? "ok" : "warn"}">
              ${text(candidate.effort_tshirt)}</span>
            ${candidate.is_regulatory ? `<span class="pill bad">regulatory</span>` : ""}
          </div>
          <p class="small" style="margin:8px 0 4px">${text(candidate.description)}</p>
          <p class="small muted" style="margin:0 0 6px">
            ${text(candidate.business_value)}</p>
          <p class="small muted" style="margin:0">
            <strong>Needs:</strong>
            ${candidate.required_domains.map((domain) =>
              `<span class="pill ${domain.satisfied ? "ok" : "bad"}">${text(domain.label)}</span>`
            ).join(" ")}
          </p>
        </div>`).join("");

      $("#result").innerHTML = `
        ${banner("info", `${preview.summary.total} candidate(s): `
          + `${preview.summary.ready} ready, ${preview.summary.gap} gap. `
          + `Generated by ${preview.model}. Nothing is saved yet.`)}
        ${(preview.warnings || []).length ? `<div class="card">
          <h3>Adjustments made</h3>
          <ul class="tight small muted">${preview.warnings.map((w) =>
            `<li>${text(w)}</li>`).join("")}</ul></div>` : ""}
        ${cards}
        <div class="row" style="margin-top:14px">
          <label class="row small" style="margin:0">
            <input type="checkbox" id="in-portfolio" checked style="width:auto">
            Add to the active portfolio</label>
          <div class="grow"></div>
          <button class="action" data-act="commit" data-preview="${text(preview.preview_id)}"
            data-busy="Preparing…">Review &amp; approve selected</button>
        </div>
        <div id="confirm"></div>`;

      onActions($("#result"), {
        commit: async (button) => {
          const picked = [...document.querySelectorAll(".pick:checked")]
            .map((input) => input.value);
          if (!picked.length) throw new Error("Select at least one use case.");
          const card = await api("/generate/use-cases/commit", {
            method: "POST",
            body: JSON.stringify({
              preview_id: button.dataset.preview,
              candidate_ids: picked,
              in_portfolio: $("#in-portfolio").checked,
            }),
          });
          renderConfirm(card);
        },
      });
    }

    function renderConfirm(card) {
      $("#confirm").innerHTML = `
        <div class="card" style="border-color:var(--lava)">
          <h3>Confirm</h3>
          <p class="small">${text(card.summary)}</p>
          <p class="small muted">Portfolio use cases:
            ${num(card.before.portfolio_use_cases)} → ${num(card.after.portfolio_use_cases)}</p>
          <div class="row" style="margin-top:10px">
            <button class="action" data-act="apply" data-token="${text(card.token)}"
              data-busy="Creating…">Confirm</button>
            <button class="action secondary" data-act="cancel">Cancel</button>
            <span class="small muted">This approval expires shortly and can only
              be used once.</span>
          </div>
        </div>`;

      onActions($("#confirm"), {
        apply: async (button) => {
          const result = await api(`/confirm/${button.dataset.token}`, { method: "POST" });
          $("#confirm").innerHTML = banner("ok",
            `Created ${num(result.created_count)} use case(s).`
            + (result.skipped_duplicates
              ? ` ${num(result.skipped_duplicates)} skipped as duplicates.` : ""))
            + `<div class="card"><h3>Next</h3>
                <p class="small muted">Run dependency detection and value estimation
                  on the new use cases so they join the flywheel and get a value model
                  built from your own assumptions — both are in the Portfolio view.</p>
                <ul class="tight small">${(result.created || []).map((uc) =>
                  `<li>${text(uc.title)}</li>`).join("")}</ul></div>`;
        },
        cancel: () => { $("#confirm").innerHTML = ""; },
      });
    }
  }


  // ---------------------------------------------------------------------
  // Ask — chat over the portfolio
  // ---------------------------------------------------------------------
  let chatConversation = null;

  async function viewAsk() {
    main.innerHTML = `
      <h2>Ask</h2>
      <p class="lede">Ask about the portfolio in plain language. Answers come from
        your actual data, not from general utility knowledge. Changes are always
        proposed for your approval, never applied directly.</p>
      <div class="suggestions">
        <button data-q="What's blocking the most value right now?">What's blocking the most value?</button>
        <button data-q="Which use cases could we build today with data we already have?">What could we build today?</button>
        <button data-q="What is our total portfolio value and how much is buildable now?">Portfolio value?</button>
        <button data-q="Are the value assumptions calibrated to this company, or still generic defaults?">Are the numbers calibrated?</button>
      </div>
      <div class="chat-log" id="chat-log"></div>
      <div id="result"></div>
      <div class="chat-input">
        <input id="chat-q" placeholder="Ask about use cases, gaps, or value…"
          autocomplete="off">
        <button class="action" data-act="send" data-busy="Thinking…">Send</button>
      </div>
      <p class="small muted" style="margin-top:10px">
        <a href="#" data-act="new-conv">Start a new conversation</a>
        · <span id="tool-count"></span></p>`;

    api("/chat/tools/list").then((info) => {
      const slot = $("#tool-count");
      if (slot) {
        slot.textContent = `${info.read_only_count} read tools, `
          + `${info.write_count} that propose changes`;
      }
    }).catch(() => {});

    const log = $("#chat-log");

    const append = (role, html) => {
      const div = document.createElement("div");
      div.className = `msg ${role}`;
      div.innerHTML = html;
      log.appendChild(div);
      div.scrollIntoView({ block: "nearest" });
    };

    const ask = async (question) => {
      append("user", text(question));
      const payload = { message: question };
      if (chatConversation) payload.conversation_id = chatConversation;
      const reply = await api("/chat", {
        method: "POST", body: JSON.stringify(payload),
      });
      chatConversation = reply.conversation_id;
      (reply.tools_used || []).forEach((t) =>
        append("tool", `→ ${text(t.tool)}`));
      if (reply.answer) append("assistant", text(reply.answer).replace(/\n/g, "<br>"));
      if (reply.note) append("tool", text(reply.note));
      if (reply.confirm) renderChatConfirm(reply.confirm);
    };

    const send = async () => {
      const input = $("#chat-q");
      const question = input.value.trim();
      if (!question) return;
      input.value = "";
      await ask(question);
    };

    onActions(main, {
      send: () => send(),
      "new-conv": async () => {
        chatConversation = null;
        log.innerHTML = "";
        $("#result").innerHTML = "";
      },
    });

    main.querySelectorAll(".suggestions button").forEach((button) => {
      button.addEventListener("click", async () => {
        const restore = busy(button, "…");
        try {
          await ask(button.dataset.q);
        } catch (error) {
          $("#result").innerHTML = banner("err", error.message);
        } finally {
          restore();
        }
      });
    });

    $("#chat-q").addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        $("[data-act=send]").click();
      }
    });

    function renderChatConfirm(card) {
      $("#result").innerHTML = `
        <div class="card" style="border-color:var(--lava)">
          <h3>Confirm this change</h3>
          <p class="small">${text(card.summary)}</p>
          <div class="row" style="margin-top:10px">
            <button class="action" data-act="apply" data-token="${text(card.token)}"
              data-busy="Applying…">Confirm</button>
            <button class="action secondary" data-act="cancel">Cancel</button>
            <span class="small muted">Single-use, and expires shortly.</span>
          </div>
        </div>`;
      onActions($("#result"), {
        apply: async (button) => {
          const result = await api(`/confirm/${button.dataset.token}`,
            { method: "POST" });
          $("#result").innerHTML = banner("ok", "Applied.");
          append("tool", `✓ applied: ${text(result.intent)}`);
        },
        cancel: () => { $("#result").innerHTML = ""; },
      });
    }
  }

  // ---------------------------------------------------------------------
  // Value flow — Sankey
  // ---------------------------------------------------------------------
  async function viewFlow() {
    main.innerHTML = `<p class="muted"><span class="spin"></span> Tracing value…</p>`;
    const [data, lobs] = await Promise.all([
      api("/flow/sankey?top_use_cases=20"),
      api("/lobs").catch(() => []),
    ]);
    const s = data.summary || {};

    main.innerHTML = `
      <h2>Value flow</h2>
      <p class="lede">Where value comes from and where it stops. Ribbon width is
        annual $M carried; a red data need has no landed source, so everything
        downstream of it is blocked.</p>

      <div class="row" style="margin-bottom:14px">
        <div><label for="flow-lob">Line of business</label>
          <select id="flow-lob"><option value="">All</option>${
            (lobs || []).map((l) => `<option value="${l.id}">${text(l.name)}</option>`)
              .join("")}</select></div>
        <div><label for="flow-top">Use cases shown</label>
          <select id="flow-top">
            <option value="10">10</option>
            <option value="20" selected>20</option>
            <option value="40">40</option>
          </select></div>
        <button class="action secondary" data-act="reload" data-busy="Loading…">Apply</button>
      </div>

      <div class="stat" style="margin-bottom:16px">
        <div><span class="k">Value flowing</span><span class="v">$${num(s.total_value_mm)}M</span></div>
        <div><span class="k">Blocked</span><span class="v">$${num(s.blocked_value_mm)}M</span></div>
        <div><span class="k">Blocked share</span><span class="v">${s.blocked_pct || 0}%</span></div>
        <div><span class="k">Sources</span><span class="v">${num(s.sources)}</span></div>
        <div><span class="k">Data needs</span><span class="v">${num(s.domains)}</span></div>
      </div>

      ${data.note ? banner("info", data.note) : ""}
      ${s.blocked_value_mm > 0
        ? banner("warn", `$${num(s.blocked_value_mm)}M of value (${s.blocked_pct}%) `
            + `cannot flow because a required data need has no landed source. `
            + `The red nodes are where it stops.`)
        : ""}
      <div id="sankey"></div>
      <div class="legend" style="margin-top:12px">
        <span><span class="key sw-covered"></span>satisfied need</span>
        <span><span class="key sw-gap"></span>gap — no landed source</span>
        <span><span class="key sw-available"></span>use case</span>
        <span><span class="key sw-unused"></span>source / line of business</span>
      </div>`;

    renderSankey(data);

    onActions(main, {
      reload: async () => {
        const lob = $("#flow-lob").value;
        const top = $("#flow-top").value;
        const fresh = await api(`/flow/sankey?top_use_cases=${top}`
          + (lob ? `&lob_id=${lob}` : ""));
        renderSankey(fresh);
      },
    });
  }

  /**
   * Draw a 4-column Sankey as inline SVG.
   *
   * Hand-rolled rather than pulling in a charting library: the console has no build
   * step, and a fixed 4-column layout needs simple math — nodes stack vertically per
   * column, ribbons are cubic BÃ©ziers between them. A dependency would cost more
   * than it saves here.
   */
  function renderSankey(data) {
    const host = $("#sankey");
    if (!host) return;
    const nodes = data.nodes || [];
    const links = data.links || [];
    if (!nodes.length) {
      host.innerHTML = `<p class="muted small">Nothing to show yet.</p>`;
      return;
    }

    const COLS = 4;
    const W = 1100, GAP = 8, NODE_W = 14, PAD = 26;
    const byCol = [[], [], [], []];
    nodes.forEach((n) => { if (byCol[n.column]) byCol[n.column].push(n); });

    // Height is driven by the busiest column so nothing overlaps.
    const tallest = Math.max(...byCol.map((c) => c.length), 1);
    const H = Math.max(320, tallest * 26 + PAD * 2);
    const colX = (c) => PAD + c * ((W - PAD * 2 - NODE_W) / (COLS - 1));

    // Flow through each node, so height encodes weight.
    const flow = {};
    links.forEach((l) => {
      flow[l.source] = (flow[l.source] || 0) + l.value;
      flow[l.target] = (flow[l.target] || 0) + l.value;
    });

    const pos = {};
    byCol.forEach((column, index) => {
      const total = column.reduce((sum, n) => sum + (flow[n.id] || 0), 0) || 1;
      const available = H - PAD * 2 - GAP * Math.max(column.length - 1, 0);
      let y = PAD;
      column.forEach((node) => {
        // Floor at 3px: a thin-but-real flow must stay visible and hoverable.
        const height = Math.max(3, ((flow[node.id] || 0) / total) * available);
        pos[node.id] = { x: colX(index), y, h: height, node };
        y += height + GAP;
      });
    });

    const COLOR = {
      landed: "#618794", not_landed: "#2A4A56",
      satisfied: "#00A972", gap: "#FF3621",
      shovel_ready: "#00A972", awaiting_prerequisites: "#FFAB00",
      nearly_ready: "#2272B4", blocked: "#98102A", unknown: "#618794",
      lob: "#618794",
    };
    const colorOf = (node) => COLOR[node.state] || "#618794";

    // Track consumed offsets so parallel ribbons stack instead of overlapping.
    const outAt = {}, inAt = {};
    const ribbons = links.map((link) => {
      const a = pos[link.source], b = pos[link.target];
      if (!a || !b) return "";
      const total = flow[link.source] || 1;
      const thickness = Math.max(1, (link.value / total) * a.h);
      const y0 = a.y + (outAt[link.source] = (outAt[link.source] || 0) + thickness) - thickness / 2;
      const totalIn = flow[link.target] || 1;
      const thicknessIn = Math.max(1, (link.value / totalIn) * b.h);
      const y1 = b.y + (inAt[link.target] = (inAt[link.target] || 0) + thicknessIn) - thicknessIn / 2;
      const x0 = a.x + NODE_W, x1 = b.x;
      const mid = (x0 + x1) / 2;
      return `<path class="link" d="M${x0},${y0} C${mid},${y0} ${mid},${y1} ${x1},${y1}"
        stroke="${colorOf(a.node)}" stroke-width="${Math.max(1, thickness)}"
        ><title>${text(a.node.label)} → ${text(b.node.label)}: $${link.value}M</title></path>`;
    }).join("");

    const boxes = Object.values(pos).map(({ x, y, h, node }) => {
      const anchor = node.column === COLS - 1 ? "end" : "start";
      const tx = node.column === COLS - 1 ? x - 6 : x + NODE_W + 6;
      const label = node.label.length > 34 ? node.label.slice(0, 33) + "…" : node.label;
      return `<g>
        <rect x="${x}" y="${y}" width="${NODE_W}" height="${h}" rx="2"
          fill="${colorOf(node)}"><title>${text(node.label)}${
            node.value_mm ? ` — $${node.value_mm}M` : ""} (${text(node.state)})</title></rect>
        <text x="${tx}" y="${y + h / 2 + 3}" text-anchor="${anchor}">${text(label)}</text>
      </g>`;
    }).join("");

    const headers = (data.legend?.columns || []).map((label, index) =>
      `<text class="col-label" x="${colX(index)}" y="14">${text(label)}</text>`).join("");

    host.innerHTML = `<svg class="sankey" viewBox="0 0 ${W} ${H}"
      preserveAspectRatio="xMidYMid meet">${headers}${ribbons}${boxes}</svg>`;
  }

  // ---------------------------------------------------------------------
  // Catalog — data needs, source mapping, taxonomy, glossary, artifacts, rules
  //
  // One view with sub-tabs rather than six top-level tabs. These are all "look at
  // and curate the catalog" tasks; as separate tabs they crowded out the four
  // things a user actually navigates between (get started, ask, coverage, flow).
  // ---------------------------------------------------------------------
  let catalogTab = "needs";

  async function viewCatalog() {
    const tabs = [
      ["needs", "Data needs"],
      ["mapping", "Source mapping"],
      ["taxonomy", "Taxonomy"],
      ["glossary", "Glossary"],
      ["artifacts", "What's built"],
      ["rules", "Naming rules"],
      ["branding", "Branding"],
    ];
    main.innerHTML = `
      <h2>Catalog</h2>
      <p class="lede">What data you have, what it means, and what has been built on
        it.</p>
      <div class="row" style="gap:6px;margin-bottom:18px">
        ${tabs.map(([id, label]) => `<button class="action ${
          id === catalogTab ? "" : "secondary"}" data-sub="${id}">${label}</button>`).join("")}
      </div>
      <div id="sub"></div>
      <div id="result"></div>`;

    main.querySelectorAll("[data-sub]").forEach((button) => {
      button.addEventListener("click", () => {
        catalogTab = button.dataset.sub;
        viewCatalog();
      });
    });

    const sub = $("#sub");
    sub.innerHTML = `<p class="muted"><span class="spin"></span> Loading…</p>`;
    try {
      if (catalogTab === "needs") await subDomains(sub);
      else if (catalogTab === "mapping") await subMapping(sub);
      else if (catalogTab === "taxonomy") await subTaxonomy(sub);
      else if (catalogTab === "glossary") await subGlossary(sub);
      else if (catalogTab === "artifacts") await subArtifacts(sub);
      else if (catalogTab === "rules") await subRules(sub);
      else if (catalogTab === "branding") await subBranding(sub);
    } catch (error) {
      sub.innerHTML = banner("err", error.message);
    }
  }

  async function subDomains(host) {
    const domains = await api("/domains");
    const satisfied = domains.filter((d) => d.satisfied).length;
    host.innerHTML = `
      <p class="small muted">Semantic data needs, decoupled from the products that
        provide them — so running Maximo instead of SAP PM is not a gap.</p>
      <div class="stat" style="margin:14px 0">
        <div><span class="k">Needs</span><span class="v">${num(domains.length)}</span></div>
        <div><span class="k">Satisfied</span><span class="v">${num(satisfied)}</span></div>
        <div><span class="k">Gaps</span><span class="v">${num(domains.length - satisfied)}</span></div>
      </div>
      <table><thead><tr><th>Data need</th><th>Category</th>
        <th class="num">Sources</th><th class="num">Landed</th>
        <th class="num">Used by</th><th>State</th></tr></thead>
      <tbody>${domains.map((d) => `<tr>
        <td><strong>${text(d.label)}</strong>
          <div class="small muted mono">${text(d.name)}</div></td>
        <td class="small muted">${text(d.category)}</td>
        <td class="num">${num(d.serving_asset_count)}</td>
        <td class="num">${num(d.ready_asset_count)}</td>
        <td class="num">${num(d.required_by_count)}</td>
        <td><span class="pill ${d.satisfied ? "ok" : "bad"}">${
          d.satisfied ? "satisfied" : "gap"}</span></td></tr>`).join("")}</tbody></table>`;
  }

  async function subMapping(host) {
    const [needsReview, categories] = await Promise.all([
      api("/ingestion/aliases?needs_review=true&limit=200").catch(() => []),
      api("/data-assets").then((assets) => [...new Set(
        assets.map((a) => a.source_category).filter(Boolean))].sort()).catch(() => []),
    ]);
    const options = ["Other", ...categories].map((c) =>
      `<option value="${text(c)}">${text(c)}</option>`).join("");
    host.innerHTML = `
      <p class="small muted">Raw source labels the normalizer could not confidently
        resolve. A correction here is pinned permanently.</p>
      ${needsReview.length ? `<table style="margin-top:12px">
        <thead><tr><th>Raw label</th><th>Mapped to</th><th>How</th>
          <th>Confidence</th><th>Correct it</th></tr></thead>
        <tbody>${needsReview.map((a) => `<tr data-id="${a.id}">
          <td class="mono">${text(a.raw)}</td><td>${text(a.canonical || "—")}</td>
          <td class="small muted">${text(a.mapped_by)}</td>
          <td><span class="pill ${a.confidence === "high" ? "ok"
            : a.confidence === "low" ? "bad" : "warn"}">${
            text(a.confidence || "none")}</span></td>
          <td class="row"><select data-role="canonical">${options}</select>
            <button class="action secondary" data-act="fix" data-id="${a.id}"
              data-busy="Saving…">Save</button></td></tr>`).join("")}</tbody></table>`
        : banner("ok", "Every source label is confidently mapped.")}`;
    onActions(host, {
      fix: async (button) => {
        const row = button.closest("tr");
        const canonical = $('[data-role="canonical"]', row).value;
        await api(`/ingestion/aliases/${button.dataset.id}`, {
          method: "PATCH", body: JSON.stringify({ canonical }),
        });
        row.style.opacity = "0.45";
        $("#result").innerHTML = banner("ok", `Pinned to ${canonical}.`);
      },
    });
  }

  async function subTaxonomy(host) {
    const [coverage, current] = await Promise.all([
      api("/taxonomy/coverage"), api("/taxonomy"),
    ]);
    const dist = Object.entries(current.distribution || {})
      .filter(([, v]) => Object.keys(v).length)
      .map(([dim, values]) => `<div class="card"><h3>${
        text(dim.replace(/_/g, " "))}</h3><table><tbody>${
        Object.entries(values).sort((a, b) => b[1] - a[1]).map(([v, n]) =>
          `<tr><td>${text(v)}</td><td class="num">${num(n)}</td></tr>`).join("")
      }</tbody></table></div>`).join("");
    host.innerHTML = `
      <p class="small muted">How data arrives, how critical it is, and what kind of
        thing produces it. Effective-dated, so a reclassification keeps its history.</p>
      <div class="stat" style="margin:14px 0">
        <div><span class="k">Assets</span><span class="v">${num(coverage.total_assets)}</span></div>
        <div><span class="k">Fully classified</span><span class="v">${num(coverage.fully_classified)}</span></div>
        ${Object.entries(coverage.by_dimension || {}).map(([d, st]) =>
          `<div><span class="k">${text(d.replace(/_/g, " "))}</span>
            <span class="v">${st.pct}%</span></div>`).join("")}
      </div>
      <div class="row" style="margin-bottom:14px">
        <button class="action" data-act="classify" data-busy="Classifying…">
          Classify unlabelled assets</button>
        <span class="small muted">Manual classifications are never overwritten.</span>
      </div>
      ${dist || `<p class="muted small">Nothing classified yet.</p>`}`;
    onActions(host, {
      classify: async () => {
        const r = await api("/taxonomy/classify", {
          method: "POST", body: JSON.stringify({ max_assets: 200 }),
        });
        $("#result").innerHTML = banner("ok",
          `${num(r.values_written)} classification(s) across ${
            num(r.assets_considered)} asset(s).`);
        setTimeout(() => viewCatalog(), 1200);
      },
    });
  }

  async function subGlossary(host) {
    const data = await api("/flow/glossary");
    const s = data.summary || {};
    host.innerHTML = `
      <p class="small muted">Business terms with the systems behind them. Data needs
        appear automatically — a need already is a term with a definition — and are
        marked <em>derived</em> until someone curates one.</p>
      <div class="stat" style="margin:14px 0">
        <div><span class="k">Terms</span><span class="v">${num(s.total)}</span></div>
        <div><span class="k">Curated</span><span class="v">${num(s.curated)}</span></div>
        <div><span class="k">Derived</span><span class="v">${num(s.derived)}</span></div>
      </div>
      <table><thead><tr><th>Term</th><th>Definition</th>
        <th>Systems of record</th><th>Source</th></tr></thead>
      <tbody>${(data.terms || []).slice(0, 80).map((t) => `<tr>
        <td><strong>${text(t.term)}</strong></td>
        <td class="small muted">${text((t.definition || "").slice(0, 110))}</td>
        <td class="small mono">${text((t.source_systems || []).slice(0, 3).join(", "))}</td>
        <td><span class="pill ${t.origin_kind === "curated" ? "ok" : "warn"}">${
          text(t.origin_kind)}</span></td></tr>`).join("")}</tbody></table>`;
  }

  async function subArtifacts(host) {
    const [all, unattributed] = await Promise.all([
      api("/artifacts?limit=200"),
      api("/artifacts/unattributed?limit=30").catch(() => ({ artifacts: [], summary: {} })),
    ]);
    const s = all.summary || {};
    const u = unattributed.summary || {};
    host.innerHTML = `
      <p class="small muted">What has already been built on the platform. The
        unattributed part is the point: work the portfolio doesn't know about, or
        something abandoned that still costs money.</p>
      <div class="stat" style="margin:14px 0">
        <div><span class="k">Artifacts</span><span class="v">${num(s.total)}</span></div>
        <div><span class="k">Unclaimed</span><span class="v">${num(s.unattributed)}</span></div>
        <div><span class="k">Active + unclaimed</span><span class="v">${num(u.active_unclaimed)}</span></div>
      </div>
      <div class="row" style="margin-bottom:14px">
        <button class="action" data-act="sync" data-busy="Scanning…">
          Scan the workspace</button>
        <span class="small muted">Read-only against system tables.</span>
      </div>
      ${u.active_unclaimed
        ? banner("warn", `${u.active_unclaimed} artifact(s) ran recently but no use `
            + `case claims them — likely shadow work worth adding to the portfolio.`)
        : ""}
      ${(all.by_type || []).length ? `<table><thead><tr><th>Type</th>
        <th class="num">Total</th><th class="num">Unclaimed</th></tr></thead>
        <tbody>${all.by_type.map((t) => `<tr><td>${text(t.artifact_type)}</td>
          <td class="num">${num(t.n)}</td>
          <td class="num">${num(t.unattributed)}</td></tr>`).join("")}
        </tbody></table>` : `<p class="muted small">Nothing scanned yet.</p>`}`;
    onActions(host, {
      sync: async () => {
        const r = await api("/artifacts/sync", { method: "POST" });
        $("#result").innerHTML = banner(r.total ? "ok" : "info",
          `Found ${num(r.total)} artifact(s). ${text(r.hint || "")}`)
          + ((r.notes || []).length ? `<div class="card"><ul class="tight small muted">${
              r.notes.map((n) => `<li>${text(n)}</li>`).join("")}</ul></div>` : "");
        if (r.total) setTimeout(() => viewCatalog(), 1200);
      },
    });
  }

  async function subRules(host) {
    const data = await api("/rules");
    const vocab = data.vocabulary || {};
    host.innerHTML = `
      <p class="small muted">Teach the app your catalog naming conventions once,
        instead of hand-correcting thousands of discovered rows. First match wins per
        dimension, so a specific rule can be ordered ahead of a general one.</p>
      <div class="row" style="margin:14px 0">
        <button class="action secondary" data-act="seed" data-busy="Loading…">
          Load common conventions</button>
        <button class="action secondary" data-act="test" data-busy="Testing…">
          Test against real tables</button>
      </div>
      <div class="card">
        <h3>Add a rule</h3>
        <div class="row">
          <div><label for="r-dim">Decides</label><select id="r-dim">${
            (vocab.dimensions || []).map((d) => `<option>${text(d)}</option>`).join("")
          }</select></div>
          <div><label for="r-field">Looks at</label><select id="r-field">${
            (vocab.fields || []).map((f) => `<option>${text(f)}</option>`).join("")
          }</select></div>
          <div><label for="r-match">Match</label><select id="r-match">${
            (vocab.match_types || []).map((m) => `<option>${text(m)}</option>`).join("")
          }</select></div>
          <div><label for="r-pattern">Pattern</label>
            <input id="r-pattern" placeholder="prod_"></div>
          <div><label for="r-value">Assign</label>
            <input id="r-value" placeholder="production"></div>
          <button class="action" data-act="add" data-busy="Adding…">Add</button>
        </div>
        <p class="small muted" style="margin:8px 0 0">Leave <em>Assign</em> blank for
          an <code>ignore</code> rule.</p>
      </div>
      ${(data.rules || []).length ? `<table><thead><tr><th>Decides</th><th>Field</th>
        <th>Match</th><th>Pattern</th><th>Assigns</th><th class="num">Priority</th>
        <th></th></tr></thead>
        <tbody>${data.rules.map((r) => `<tr>
          <td>${text(r.dimension)}</td><td class="small">${text(r.field)}</td>
          <td class="small muted">${text(r.match_type)}</td>
          <td class="mono small">${text(r.pattern)}</td>
          <td>${text(r.value || "—")}</td>
          <td class="num">${num(r.priority)}</td>
          <td><button class="action secondary" data-act="del" data-id="${r.id}"
            data-busy="…">Remove</button></td></tr>`).join("")}</tbody></table>`
        : `<p class="muted small">No rules yet.</p>`}`;
    onActions(host, {
      seed: async () => {
        const r = await api("/rules/seed", { method: "POST" });
        $("#result").innerHTML = banner("ok", `Added ${num(r.created)} rule(s).`);
        setTimeout(() => viewCatalog(), 900);
      },
      add: async () => {
        const value = $("#r-value").value.trim();
        await api("/rules", {
          method: "POST",
          body: JSON.stringify({
            dimension: $("#r-dim").value, field: $("#r-field").value,
            match_type: $("#r-match").value, pattern: $("#r-pattern").value.trim(),
            value: value || null,
          }),
        });
        $("#result").innerHTML = banner("ok", "Rule added.");
        setTimeout(() => viewCatalog(), 700);
      },
      del: async (button) => {
        await api(`/rules/${button.dataset.id}`, { method: "DELETE" });
        button.closest("tr").style.opacity = "0.4";
      },
      test: async () => {
        const r = await api("/rules/test", {
          method: "POST", body: JSON.stringify({ limit: 100 }),
        });
        const s = r.summary || {};
        $("#result").innerHTML = banner(s.unmatched ? "warn" : "ok",
          `${num(r.rules_applied)} rule(s) over ${num(s.total)} row(s) from `
          + `${text(r.sample_source)}: ${num(s.ignored)} ignored, `
          + `${num(s.unmatched)} matched nothing.`)
          + Object.entries(s.by_dimension || {}).map(([dim, values]) =>
            `<div class="card"><h3>${text(dim)}</h3><table><tbody>${
              Object.entries(values).map(([v, n]) =>
                `<tr><td>${text(v)}</td><td class="num">${num(n)}</td></tr>`).join("")
            }</tbody></table></div>`).join("");
      },
    });
  }


  // ---------------------------------------------------------------------
  // Research — cold-start a new account and calibrate the value model
  //
  // The backend shipped before this view did, which meant the feature existed
  // only to anyone willing to curl it. This is the surface for it: research a
  // company, review the calibrated assumptions WITH their provenance, and apply
  // the ones you trust.
  //
  // The review table is the point. Every dollar figure in the app derives from
  // these 34 numbers, so the screen is built around judging them — confidence
  // badge, what it was derived from, and the reasoning — rather than just
  // displaying them.
  // ---------------------------------------------------------------------
  async function viewResearch() {
    main.innerHTML = `<p class="muted"><span class="spin"></span> Loading…</p>`;
    const [profile, proposals] = await Promise.all([
      api("/research/company").catch(() => ({ researched: false })),
      api("/research/assumptions").catch(() => ({ assumptions: [], summary: {} })),
    ]);

    const s = proposals.summary || {};
    const rows = proposals.assumptions || [];
    const pending = rows.filter((r) => !r.applied);

    const confidencePill = (c) => `<span class="pill ${
      c === "high" ? "ok" : c === "medium" ? "warn" : "bad"}">${text(c || "low")}</span>`;

    main.innerHTML = `
      <h2>Research</h2>
      <p class="lede">Name a utility and the app researches it, then calibrates the
        34 value assumptions that drive every dollar figure. Nothing is applied until
        you approve it.</p>
      <div id="result"></div>

      <section class="card">
        <h3>${profile.researched ? "Re-run research" : "Research a company"}</h3>
        <p class="step-why">Shipped as generic defaults, the value model describes a
          hypothetical 2-million-customer utility — so every number is directionally
          meaningless until it is scaled to a real company.</p>
        <div class="row">
          <div style="flex:1"><label for="rc-name">Company name</label>
            <input id="rc-name" placeholder="e.g. Eversource Energy"
              value="${text(profile.company_name || "")}" style="width:100%"></div>
          <button class="action" data-act="research" data-busy="Researching…">
            Research</button>
        </div>
        <div class="row" style="margin-top:10px">
          <label class="row small" style="margin:0">
            <input type="checkbox" id="rc-assume" checked style="width:auto">
            Calibrate the value assumptions</label>
          <label class="row small" style="margin:0">
            <input type="checkbox" id="rc-lobs" checked style="width:auto">
            Propose lines of business</label>
        </div>
        <p class="small muted" style="margin:10px 0 0">Use the full legal or operating
          name. Takes up to a minute — it is two model calls.</p>
      </section>

      ${profile.researched ? `
        <section class="card">
          <h3>${text(profile.company_name)}</h3>
          <div class="stat" style="margin-bottom:12px">
            <div><span class="k">Type</span><span class="v" style="font-size:14px">${
              text(profile.utility_type || "—")}</span></div>
            <div><span class="k">Segments</span><span class="v" style="font-size:14px">${
              text((profile.segments || []).join(", ") || "—")}</span></div>
            <div><span class="k">Market</span><span class="v" style="font-size:14px">${
              text(profile.iso_rto || "—")}</span></div>
          </div>
          <p class="small muted">${text(profile.description || "")}</p>
          ${profile.regulator ? `<p class="small muted"><strong>Regulator:</strong> ${
            text(profile.regulator)}</p>` : ""}
          ${profile.research_notes ? banner("warn",
            `Caveats from the research: ${profile.research_notes}`) : ""}
        </section>` : ""}

      ${rows.length ? `
        <section>
          <h3 class="small muted">Calibrated value assumptions</h3>
          <div class="stat" style="margin:12px 0">
            <div><span class="k">Calibrated</span><span class="v">${num(s.total)}</span></div>
            <div><span class="k">Changed</span><span class="v">${num(s.changed)}</span></div>
            <div><span class="k">High confidence</span><span class="v">${
              num((s.by_confidence || {}).high)}</span></div>
            <div><span class="k">Needs review</span><span class="v">${
              num(s.needs_review)}</span></div>
          </div>

          ${s.needs_review
            ? banner("warn", `${s.needs_review} value(s) are industry-typical rather `
                + `than company-specific. They are still scaled to the right size of `
                + `utility, but check them before quoting a number that depends on one.`)
            : ""}

          <div class="row" style="margin-bottom:12px">
            <button class="action" data-act="apply-trusted" data-busy="Preparing…">
              Apply high + medium confidence</button>
            <button class="action secondary" data-act="apply-all" data-busy="Preparing…">
              Apply all ${num(pending.length)}</button>
            <button class="action secondary" data-act="apply-selected" data-busy="Preparing…">
              Apply selected</button>
          </div>

          <table>
            <thead><tr><th></th><th>Assumption</th><th class="num">Default</th>
              <th class="num">Calibrated</th><th class="num">Change</th>
              <th>Confidence</th><th>Basis &amp; reasoning</th></tr></thead>
            <tbody>${rows.map((r) => `
              <tr${r.applied ? ' style="opacity:.5"' : ""}>
                <td><input type="checkbox" class="pick-key" value="${text(r.key)}"
                  ${r.applied ? "disabled" : ""} style="width:auto"></td>
                <td><strong>${text(r.label || r.key)}</strong>
                  <div class="small muted mono">${text(r.key)}${
                    r.unit ? ` · ${text(r.unit)}` : ""}</div></td>
                <td class="num">${num(r.value_before)}</td>
                <td class="num"><strong>${num(r.value_proposed)}</strong></td>
                <td class="num small ${
                  (r.pct_change || 0) > 0 ? "" : "muted"}">${
                  r.pct_change === null || r.pct_change === undefined
                    ? "—" : (r.pct_change > 0 ? "+" : "") + r.pct_change + "%"}</td>
                <td>${confidencePill(r.confidence)}${
                  r.applied ? ' <span class="pill ok">applied</span>' : ""}</td>
                <td class="small muted">${text(r.basis || "")}${
                  r.rationale ? `<div>${text(r.rationale)}</div>` : ""}</td>
              </tr>`).join("")}</tbody>
          </table>
        </section>`
        : (profile.researched
            ? banner("info", "No calibrated assumptions on record. Re-run research "
                + "with 'Calibrate the value assumptions' checked.")
            : "")}`;

    const propose = async (keys) => {
      if (!keys.length) throw new Error("Nothing selected.");
      const card = await api("/research/apply", {
        method: "POST",
        body: JSON.stringify({ run_id: proposals.run_id, keys }),
      });
      renderApplyCard(card);
    };

    onActions(main, {
      research: async () => {
        const name = $("#rc-name").value.trim();
        if (!name) throw new Error("Enter a company name.");
        const result = await api("/research/company", {
          method: "POST",
          body: JSON.stringify({
            company_name: name,
            calibrate_assumptions: $("#rc-assume").checked,
            propose_lobs: $("#rc-lobs").checked,
          }),
        });
        $("#result").innerHTML = banner("ok",
          `Researched ${text(result.company.company_name)} — `
          + `${num((result.assumption_summary || {}).total)} assumption(s) calibrated `
          + `by ${text(result.model)}. Nothing applied yet.`)
          + ((result.warnings || []).length
            ? `<div class="card"><h3>Notes from the research</h3>
                <ul class="tight small muted">${result.warnings.slice(0, 6)
                  .map((w) => `<li>${text(w)}</li>`).join("")}</ul></div>` : "");
        setTimeout(viewResearch, 1400);
      },
      "apply-trusted": () => propose(
        pending.filter((r) => r.confidence !== "low").map((r) => r.key)),
      "apply-all": () => propose(pending.map((r) => r.key)),
      "apply-selected": () => propose(
        [...document.querySelectorAll(".pick-key:checked")].map((i) => i.value)),
    });

    function renderApplyCard(card) {
      $("#result").innerHTML = `
        <div class="card" style="border-color:var(--lava)">
          <h3>Confirm recalibration</h3>
          <p class="small">${text(card.summary)}</p>
          <p class="small muted">This changes every dollar figure in the portfolio at
            once. Calibrated values are badged in Value &amp; Assumptions so anyone
            can see where a number came from.</p>
          <div class="row" style="margin-top:10px">
            <button class="action" data-act="confirm" data-token="${text(card.token)}"
              data-busy="Applying…">Confirm</button>
            <button class="action secondary" data-act="cancel">Cancel</button>
          </div>
        </div>`;
      onActions($("#result"), {
        confirm: async (button) => {
          const result = await api(`/confirm/${button.dataset.token}`,
            { method: "POST" });
          $("#result").innerHTML = banner("ok",
            `Applied ${num(result.applied_count)} value(s). The portfolio has been `
            + `re-quantified.`);
          setTimeout(viewResearch, 1500);
        },
        cancel: () => { $("#result").innerHTML = ""; },
      });
    }
  }

  // ---------------------------------------------------------------------
  // Branding — make the instance look like the customer's
  // ---------------------------------------------------------------------
  async function subBranding(host) {
    const b = await api("/branding");
    host.innerHTML = `
      <p class="small muted">Show the customer's name and logo in the header, so the
        app reads as theirs in a workshop. The name defaults to the researched company
        when one exists.</p>
      <div class="card" style="margin-top:14px">
        <h3>Header</h3>
        <div class="row">
          <div style="flex:1"><label for="b-name">Display name
            <span class="muted">(from ${text(b.source)})</span></label>
            <input id="b-name" style="width:100%" placeholder="${text(b.display_name)}"
              value="${text(b.source === "custom" ? b.display_name : "")}"></div>
        </div>
        <div class="row" style="margin-top:10px">
          <div style="flex:1"><label for="b-sub">Subtitle</label>
            <input id="b-sub" style="width:100%" value="${text(b.subtitle)}"></div>
          <div><label for="b-accent">Accent</label>
            <input id="b-accent" type="color" value="${text(b.accent_color)}"
              style="width:56px;padding:2px"></div>
          <button class="action" data-act="save" data-busy="Saving…">Save</button>
        </div>
      </div>
      <div class="card">
        <h3>Logo</h3>
        ${b.has_logo
          ? `<div class="row" style="margin-bottom:10px">
              <img src="${text(b.logo_url)}?t=${Date.now()}" alt="Current logo"
                style="max-height:44px;background:var(--navy-900);padding:6px;border-radius:4px">
              <button class="action secondary" data-act="del-logo" data-busy="Removing…">
                Remove</button></div>`
          : `<p class="small muted">No logo uploaded.</p>`}
        <div class="row" style="margin-top:8px">
          <div><label for="b-logo">PNG, JPEG, GIF, WebP or SVG · max 2MB</label>
            <input type="file" id="b-logo" accept="image/*"></div>
          <button class="action" data-act="up-logo" data-busy="Uploading…">Upload</button>
        </div>
      </div>`;

    onActions(host, {
      save: async () => {
        await api("/branding", {
          method: "PUT",
          body: JSON.stringify({
            display_name: $("#b-name").value.trim() || null,
            subtitle: $("#b-sub").value.trim() || null,
            accent_color: $("#b-accent").value,
          }),
        });
        $("#result").innerHTML = banner("ok",
          "Saved. Reload to see it in the header.");
      },
      "up-logo": async () => {
        const input = $("#b-logo");
        if (!input.files || !input.files[0]) throw new Error("Choose an image first.");
        const form = new FormData();
        form.append("file", input.files[0]);
        const r = await api("/branding/logo", { method: "POST", body: form });
        $("#result").innerHTML = banner("ok",
          `Uploaded ${Math.round(r.bytes / 1024)}KB. Reload to see it.`);
        setTimeout(() => viewCatalog(), 900);
      },
      "del-logo": async () => {
        await api("/branding/logo", { method: "DELETE" });
        $("#result").innerHTML = banner("ok", "Logo removed.");
        setTimeout(() => viewCatalog(), 700);
      },
    });
  }

  // ---------------------------------------------------------------------
  // Admin — Demo Mode + instance state
  // ---------------------------------------------------------------------
  async function viewAdmin() {
    main.innerHTML = `<p class="muted"><span class="spin"></span> Loading…</p>`;
    const [health, demo] = await Promise.all([
      api("/health").catch((e) => ({ error: e.message })),
      // 404 here is the designed answer when DEMO_MODE is off, not a failure.
      api("/demo/status").then((d) => ({ ...d, available: true }))
        .catch(() => ({ available: false })),
    ]);

    const demoCard = demo.available
      ? `<div class="card">
          <h3>Demo Mode</h3>
          <p class="small muted">Flips this instance's Lakebase between the two
            seeded states. Runs DML inside one transaction — a failure rolls back
            rather than leaving the portfolio half-populated.</p>
          <p class="small" style="margin:10px 0 0">Current state:
            <span class="pill ${demo.mode === "demo" ? "warn" : "ok"}">
              ${text(demo.mode || "unknown")}</span></p>
          <div class="row" style="margin-top:12px">
            <button class="action" data-act="demo-load" data-busy="Loading…">
              Load showcase data</button>
            <button class="action secondary" data-act="demo-reset" data-busy="Resetting…">
              Reset to clean day-1</button>
          </div>
          <p class="small muted" style="margin:10px 0 0">
            <strong>Both are destructive</strong> — they replace the portfolio.
            Don't run them on an instance holding a customer's real data.</p>
        </div>`
      : `<div class="card">
          <h3>Demo Mode</h3>
          ${banner("info", "Disabled on this instance. It is gated behind the "
            + "DEMO_MODE env var, which ships 'off' so a customer install cannot "
            + "reset its own portfolio. To enable it here, redeploy with "
            + "scripts/deploy.py --demo-mode on.")}
        </div>`;

    main.innerHTML = `
      <h2>Admin</h2>
      <p class="lede">Instance state and the controls that change it. Everything
        here affects live data, so each action says what it will do first.</p>
      <div id="result"></div>

      <section class="card">
        <h3>This instance</h3>
        <div class="stat">
          <div><span class="k">Lakebase</span><span class="v">
            ${health.db_connected ? "connected" : "demo mode"}</span></div>
          <div><span class="k">Use cases</span><span class="v">
            ${num((health.counts || {}).use_cases)}</span></div>
          <div><span class="k">Data assets</span><span class="v">
            ${num((health.counts || {}).data_assets)}</span></div>
        </div>
        <p class="small muted" style="margin:12px 0 0">
          Environment <code>${text(health.environment)}</code> ·
          Model <code>${text(health.serving_endpoint)}</code> ·
          Genie ${health.genie_space_configured ? "configured" : "not configured"}</p>
      </section>

      ${demoCard}

      <section class="card">
        <h3>Genie mirror</h3>
        <p class="small muted">Copies the portfolio into Unity Catalog so a Genie
          space can answer questions over it. Read-only projection — it never
          changes portfolio data.</p>
        <div class="row" style="margin-top:10px">
          <button class="action secondary" data-act="sync-genie" data-busy="Syncing…">
            Sync Genie mirror</button>
        </div>
      </section>

      <section class="card">
        <h3>Databricks sync</h3>
        <p class="small muted">Reads system tables to auto-advance data sources that
          show real lineage, and use cases whose linked jobs are running. Read-only
          against Databricks; writes only to this app's own state.</p>
        <div class="row" style="margin-top:10px">
          <button class="action secondary" data-act="sync-dry" data-busy="Checking…">
            Preview changes</button>
          <button class="action" data-act="sync-apply" data-busy="Syncing…">
            Apply</button>
        </div>
      </section>

      <section class="card">
        <h3>Housekeeping</h3>
        <p class="small muted">Deletes expired generation previews and consumed
          confirm tokens. Safe — expiry is already enforced at read time, so this
          only reclaims space.</p>
        <div class="row" style="margin-top:10px">
          <button class="action secondary" data-act="cleanup" data-busy="Cleaning…">
            Clean up expired records</button>
        </div>
      </section>`;

    onActions(main, {
      "demo-load": async () => {
        if (!confirm("Replace the portfolio with the showcase dataset?")) return;
        const r = await api("/demo/load", { method: "POST" });
        $("#result").innerHTML = banner("ok",
          `Showcase data loaded (${num((r.counts || {}).use_cases)} use cases).`);
        setTimeout(viewAdmin, 1200);
      },
      "demo-reset": async () => {
        if (!confirm("Reset the portfolio to pristine day-1?")) return;
        const r = await api("/demo/reset", { method: "POST" });
        $("#result").innerHTML = banner("ok",
          `Reset to clean day-1 (${num((r.counts || {}).use_cases)} use cases).`);
        setTimeout(viewAdmin, 1200);
      },
      "sync-genie": async () => {
        const r = await api("/live/sync-genie", { method: "POST" });
        $("#result").innerHTML = r.ok
          ? banner("ok", `Mirrored to ${text(r.schema)}: ${(r.created || []).join(", ")}`)
          : banner("err", r.error || "Sync failed.");
      },
      "sync-dry": () => runSync(false),
      "sync-apply": () => runSync(true),
      cleanup: async () => {
        const r = await api("/generate/cleanup", { method: "POST" });
        $("#result").innerHTML = banner("ok",
          `Removed ${text(r.previews_deleted)} preview(s) and `
          + `${text(r.tokens_deleted)} token(s).`);
      },
    });

    async function runSync(apply) {
      const r = await api(`/live/sync?apply=${apply ? "true" : "false"}`,
        { method: "POST" });
      const assets = r.asset_changes || [];
      const ucs = r.uc_changes || [];
      $("#result").innerHTML = banner(apply ? "ok" : "info",
        `${apply ? "Applied" : "Would change"}: ${assets.length} data source(s), `
        + `${ucs.length} use case(s).`)
        + ((r.notes || []).length
          ? `<div class="card"><h3>Notes</h3><ul class="tight small muted">${
              r.notes.map((n) => `<li>${text(n)}</li>`).join("")}</ul></div>`
          : "");
    }
  }

  // ---------------------------------------------------------------------
  // Router
  // ---------------------------------------------------------------------
  const VIEWS = {
    start: viewStart,
    ask: viewAsk,
    research: viewResearch,
    coverage: viewCoverage,
    flow: viewFlow,
    catalog: viewCatalog,
    generate: viewGenerate,
    admin: viewAdmin,
    // Deep links straight to a catalog sub-tab. These double as the back-compat
    // targets for the era when each was its own top-level tab, so an old bookmark
    // still lands on the right sub-tab rather than the catalog's default.
    aliases: () => { catalogTab = "mapping"; return viewCatalog(); },
    mapping: () => { catalogTab = "mapping"; return viewCatalog(); },
    domains: () => { catalogTab = "needs"; return viewCatalog(); },
    needs: () => { catalogTab = "needs"; return viewCatalog(); },
    taxonomy: () => { catalogTab = "taxonomy"; return viewCatalog(); },
    glossary: () => { catalogTab = "glossary"; return viewCatalog(); },
    artifacts: () => { catalogTab = "artifacts"; return viewCatalog(); },
    rules: () => { catalogTab = "rules"; return viewCatalog(); },
    branding: () => { catalogTab = "branding"; return viewCatalog(); },
    // Back-compat: the merged onboarding flow replaced these two separate views,
    // so old bookmarks and the /console#setup links in the docs still land
    // somewhere sensible instead of silently falling through to the default.
    setup: viewStart,
    discovery: viewStart,
  };

  // ---------------------------------------------------------------------
  // Navigation model
  // ---------------------------------------------------------------------
  /*
   * Grouped by WORKFLOW STAGE, not by feature taxonomy: a user arrives knowing
   * what they are trying to do ("get my data in", "work out what it's worth"),
   * not which module owns a screen.
   *
   * Every item carries a one-line hint. Names like "Coverage" or "Flow" do not
   * tell a first-time user what they do, and a tooltip is invisible until you
   * already suspect you want it.
   *
   * This is the single source of truth for the menu — index.html renders an empty
   * <nav> and this fills it, so adding a view means adding one entry here rather
   * than editing markup in two files and hoping they stay in sync.
   */
  const NAV_GROUPS = [
    {
      id: "discover",
      label: "Discover",
      hint: "Connect sources and find what you have",
      items: [
        ["start", "Get started",
         "Connect Lakebase, check permissions, bulk-import via Excel"],
        ["needs", "Data needs & gaps",
         "63 semantic domains, with gaps ranked by the value they block"],
        ["mapping", "Source mapping",
         "Correct the source labels the normalizer wasn't sure about"],
        ["rules", "Naming rules",
         "Classify assets by naming convention, first match wins"],
      ],
    },
    {
      id: "analyze",
      label: "Analyze",
      hint: "Understand the portfolio and what it's worth",
      items: [
        ["ask", "Ask",
         "Chat over the portfolio — reads answer, writes need confirmation"],
        ["coverage", "Coverage & readiness",
         "Which use cases are shovel-ready, and what's blocking the rest"],
        ["flow", "Value flow",
         "Sankey from source → domain → use case → line of business"],
        ["research", "Company research",
         "Research a utility and recalibrate all 34 value assumptions"],
      ],
    },
    {
      id: "build",
      label: "Build",
      hint: "Create and document new work",
      items: [
        ["generate", "Generate use cases",
         "Author use cases grounded in the data you actually have"],
        ["artifacts", "What's built",
         "Jobs, pipelines, models and dashboards found in your workspace"],
        ["taxonomy", "Taxonomy",
         "Integration pattern, criticality and vendor type — effective-dated"],
        ["glossary", "Glossary",
         "Business terms, with data domains projected as derived terms"],
      ],
    },
  ];

  // Sits apart from the groups: settings, not a workflow stage.
  const NAV_ADMIN = [
    ["admin", "Admin & audit",
     "Audit log, schema state, rate limits and health"],
    ["branding", "Branding",
     "Customer name, subtitle, accent colour and logo"],
  ];

  /** Which group (if any) contains a view id. */
  function groupOf(view) {
    for (const group of NAV_GROUPS) {
      if (group.items.some(([id]) => id === view)) return group.id;
    }
    return NAV_ADMIN.some(([id]) => id === view) ? "admin" : null;
  }

  function menuItem([id, label, hint]) {
    return `<button data-view="${text(id)}" role="menuitem">
      <span class="item-label">${text(label)}</span>
      <span class="item-hint">${text(hint)}</span>
    </button>`;
  }

  function renderNav() {
    const nav = $("#nav");
    if (!nav) return;
    nav.innerHTML = `
      <!-- A real link out to the SPA, and the only one: the SPA has no URL
           routing, so separate per-tab links could not work. -->
      <a href="/" title="Back to the portfolio app">Portfolio <span
        aria-hidden="true" style="opacity:.55;font-size:11px">&#8599;</span></a>
      <div class="divider" aria-hidden="true"></div>
      ${NAV_GROUPS.map((group) => `
        <div class="navgroup" data-group="${text(group.id)}">
          <button type="button" aria-expanded="false" aria-haspopup="menu"
                  title="${text(group.hint)}">
            ${text(group.label)} <span class="caret" aria-hidden="true">&#9662;</span>
          </button>
          <div class="navmenu" role="menu" data-open="false">
            <div class="menu-heading">${text(group.hint)}</div>
            ${group.items.map(menuItem).join("")}
          </div>
        </div>`).join("")}
      <div class="spacer"></div>
      <div class="navgroup align-end" data-group="admin">
        <button type="button" aria-expanded="false" aria-haspopup="menu"
                title="Settings, audit and health">
          Settings <span class="caret" aria-hidden="true">&#9662;</span>
        </button>
        <div class="navmenu" role="menu" data-open="false">
          ${NAV_ADMIN.map(menuItem).join("")}
        </div>
      </div>`;

    nav.querySelectorAll(".navgroup").forEach((group) => {
      const trigger = group.querySelector(":scope > button");
      const menu = group.querySelector(".navmenu");
      trigger.addEventListener("click", () => {
        // Read the state BEFORE closing, so a second click on the same trigger
        // toggles shut rather than reopening.
        const wasOpen = menu.dataset.open === "true";
        closeMenus();
        if (!wasOpen) {
          menu.dataset.open = "true";
          trigger.setAttribute("aria-expanded", "true");
        }
      });
      menu.querySelectorAll("[data-view]").forEach((item) => {
        item.addEventListener("click", () => {
          closeMenus();
          show(item.dataset.view);
        });
      });
    });
  }

  function closeMenus() {
    document.querySelectorAll(".navmenu[data-open='true']").forEach((menu) => {
      menu.dataset.open = "false";
    });
    document.querySelectorAll(".navgroup > button[aria-expanded='true']")
      .forEach((trigger) => trigger.setAttribute("aria-expanded", "false"));
  }

  // Clicking anywhere else, or pressing Escape, dismisses an open menu — the two
  // things people try when a panel is in the way.
  //
  // Scoped by TARGET rather than by stopPropagation(). Relying on the trigger's
  // handler to stop the event from reaching this one is order-dependent and it
  // broke in the browser: the menu opened and closed within the same click, so
  // nothing appeared to happen. Asking "was the click inside a nav group?" is
  // independent of which listener runs first.
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".navgroup")) closeMenus();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeMenus();
  });

  async function show(name) {
    const render = VIEWS[name] || viewStart;

    // Mark the current item, and underline the group containing it so the top row
    // still answers "where am I" with every menu closed.
    const activeGroup = groupOf(name);
    document.querySelectorAll("#nav [data-view]").forEach((button) => {
      if (button.dataset.view === name) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    document.querySelectorAll("#nav .navgroup").forEach((group) => {
      const trigger = group.querySelector(":scope > button");
      if (group.dataset.group === activeGroup) {
        trigger.setAttribute("aria-current", "page");
      } else {
        trigger.removeAttribute("aria-current");
      }
    });

    // The hash is the source of truth so a view survives a reload and can be
    // linked to — useful when handing a colleague "the GRANTs page".
    if (location.hash.slice(1) !== name) location.hash = name;
    try {
      await render();
    } catch (error) {
      main.innerHTML = banner("err", error.message);
    }
  }

  renderNav();
  window.addEventListener("hashchange", () => show(location.hash.slice(1) || "start"));

  show(location.hash.slice(1) || "start");
})();
