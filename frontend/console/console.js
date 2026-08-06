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
  // Setup & health
  // ---------------------------------------------------------------------
  async function viewSetup() {
    main.innerHTML = `<p class="muted"><span class="spin"></span> Probing dependencies…</p>`;
    let status;
    try {
      status = await api("/setup/status");
    } catch (error) {
      main.innerHTML = `<h2>Setup &amp; health</h2>` + banner("err",
        `Could not read setup status: ${error.message}`);
      return;
    }

    $("#env").textContent = status.environment || "";

    const pills = status.checks.map((check) => {
      const kind = check.ok ? "ok" : (check.required ? "bad" : "warn");
      return `<span class="pill ${kind}" title="${text(check.detail)}">
        <span class="dot"></span>${text(check.label)}</span>`;
    }).join("");

    const headline = status.ready
      ? banner("ok", `Ready. ${status.summary.passing} of ${status.summary.total} checks passing.`
          + (status.summary.optional_failing
            ? ` ${status.summary.optional_failing} optional feature(s) not configured — the portfolio works without them.`
            : ""))
      : banner("err", `${status.summary.required_failing} required check(s) failing. `
          + (status.next_action || ""));

    const failing = status.checks.filter((c) => !c.ok);
    const detail = failing.length
      ? `<section><h3 class="small muted">Not passing</h3>${failing.map((check) => `
          <div class="card">
            <div class="row">
              <strong>${text(check.label)}</strong>
              <span class="pill ${check.required ? "bad" : "warn"}">
                ${check.required ? "required" : "optional"}</span>
            </div>
            <p class="small muted" style="margin:6px 0 0">${text(check.detail)}</p>
            ${check.fix ? `<p class="small" style="margin:6px 0 0">${text(check.fix)}</p>` : ""}
            ${check.grants && check.grants.length
              ? `<pre class="sql">${text(check.grants.join("\n"))}</pre>` : ""}
          </div>`).join("")}</section>`
      : "";

    main.innerHTML = `
      <h2>Setup &amp; health</h2>
      <p class="lede">Every dependency is probed here. Anything failing shows the
        exact fix — and, where it is a permissions problem, the GRANT statements to
        hand a metastore admin.</p>
      <div class="pills">${pills}</div>
      ${headline}
      <div class="row" style="margin-bottom:18px">
        <button class="action" data-act="recheck" data-busy="Re-checking…">Re-check</button>
        <button class="action secondary" data-act="grants">Show all GRANTs</button>
        <span class="small muted">Service principal:
          <code>${text(status.service_principal)}</code></span>
      </div>
      <div id="result"></div>
      ${detail}`;

    onActions(main, {
      recheck: () => viewSetup(),
      grants: async () => {
        const payload = await api("/setup/grants");
        $("#result").innerHTML = `
          <div class="card">
            <h3>Unity Catalog privileges</h3>
            <p class="small muted">Run as a metastore admin or catalog owner.
              Steps that cannot be expressed as SQL are included as comments.</p>
            <pre class="sql">${text(payload.sql)}</pre>
          </div>`;
      },
    });
  }

  // ---------------------------------------------------------------------
  // Discovery pipeline
  // ---------------------------------------------------------------------
  async function viewDiscovery() {
    main.innerHTML = `<p class="muted"><span class="spin"></span> Reading inventory…</p>`;
    const summary = await api("/ingestion/summary").catch((e) => ({ error: e.message }));

    if (summary.error) {
      main.innerHTML = `<h2>Discovery</h2>` + banner("err", summary.error);
      return;
    }
    if (!summary.configured) {
      main.innerHTML = `
        <h2>Discovery</h2>
        <p class="lede">Sweep your Databricks estate for the data you actually
          have, enrich it with AI, and attribute it to the catalog.</p>
        ${banner("info", "Discovery is not configured. Set ATLAS_CATALOG in app.yaml "
          + "and redeploy. The curated 146-module catalog works without it.")}`;
      return;
    }

    const runs = await api("/ingestion/runs?limit=8").catch(() => []);

    main.innerHTML = `
      <h2>Discovery</h2>
      <p class="lede">Run these in order. Each step is idempotent, so re-running is
        safe — and each shows what it changed rather than only that it finished.</p>

      <section class="card">
        <h3>Inventory</h3>
        <div class="stat" style="margin-bottom:14px">
          <div><span class="k">Workspaces</span><span class="v">${num(summary.workspaces)}</span></div>
          <div><span class="k">Schemas</span><span class="v">${num(summary.schemas)}</span></div>
          <div><span class="k">Tables</span><span class="v">${num(summary.tables)}</span></div>
          <div><span class="k">Enriched</span><span class="v">${num(summary.enriched_tables)}</span></div>
          <div><span class="k">Source systems</span><span class="v">${num(summary.canonicals)}</span></div>
        </div>
        <p class="small muted">Writing to
          <code>${text(summary.catalog)}.${text(summary.schema)}</code></p>
      </section>

      <section class="card">
        <h3>1 · Upload workspace metadata</h3>
        <p class="small muted">Produced by <code>schema-extractor/extract_schemas.py</code>,
          which runs on your machine under your own credentials so it reaches
          workspaces this app cannot. Metadata only — no table contents.</p>
        <div class="row" style="margin-top:10px">
          <button class="action secondary" data-act="bootstrap" data-busy="Creating…">
            Create discovery tables</button>
        </div>
        <div style="margin-top:12px" class="row">
          <div><label for="f-schemas">all_schemas.csv</label>
            <input type="file" id="f-schemas" accept=".csv"></div>
          <button class="action" data-act="up-schemas" data-busy="Uploading…">Upload</button>
        </div>
        <div style="margin-top:8px" class="row">
          <div><label for="f-tables">all_tables.csv</label>
            <input type="file" id="f-tables" accept=".csv"></div>
          <button class="action" data-act="up-tables" data-busy="Uploading…">Upload</button>
        </div>
        <div style="margin-top:8px" class="row">
          <div><label for="f-columns">all_columns.csv <span class="muted">(optional, improves AI accuracy)</span></label>
            <input type="file" id="f-columns" accept=".csv"></div>
          <button class="action" data-act="up-columns" data-busy="Uploading…">Upload</button>
        </div>
      </section>

      <section class="card">
        <h3>2 · AI enrichment</h3>
        <p class="small muted">Generates a business name, definition, and source
          system per table. Costs model-serving spend proportional to table count,
          so start with a capped run on a large estate.</p>
        <div class="row" style="margin-top:10px">
          <div><label for="company">Company name (prompt context)</label>
            <input id="company" placeholder="e.g. Eversource Energy"></div>
          <div><label for="maxrows">Max tables (blank = all)</label>
            <input id="maxrows" type="number" min="1" placeholder="500" style="width:120px"></div>
        </div>
        <div class="row" style="margin-top:10px">
          <button class="action secondary" data-act="enrich-schemas" data-busy="Enriching…">
            Enrich schemas</button>
          <button class="action" data-act="enrich-tables" data-busy="Enriching…">
            Enrich tables</button>
        </div>
      </section>

      <section class="card">
        <h3>3 · Normalize source systems</h3>
        <p class="small muted">Collapses free-text labels to your canonical
          vocabulary. Without this, ~1,000 distinct strings describe ~50 real
          systems and every rollup is noise. Deterministic matching runs first;
          only the remainder costs an LLM call.</p>
        <div class="row" style="margin-top:10px">
          <button class="action" data-act="canonicalize" data-busy="Normalizing…">
            Normalize</button>
          <button class="action secondary" data-act="canonicalize-nollm"
            data-busy="Normalizing…">Deterministic only (free)</button>
        </div>
      </section>

      <section class="card">
        <h3>4 · Attribute to the catalog</h3>
        <p class="small muted">Links discovered tables to catalog modules. Advancing
          ingestion status moves readiness and therefore the roadmap, so it is off
          by default — and capped at <em>landed</em>, because finding tables proves
          data exists, not that it is curated or governed.</p>
        <div class="row" style="margin-top:10px">
          <label class="row small" style="margin:0">
            <input type="checkbox" id="advance" style="width:auto">
            Advance ingestion status to “landed”</label>
        </div>
        <div class="row" style="margin-top:10px">
          <button class="action" data-act="attribute" data-busy="Attributing…">
            Attribute</button>
        </div>
      </section>

      <div id="result"></div>

      <section>
        <h3 class="small muted">Recent runs</h3>
        ${runs.length ? `<table>
          <thead><tr><th>Started</th><th>Step</th><th>Status</th><th>Detail</th></tr></thead>
          <tbody>${runs.map((run) => `<tr>
            <td class="mono small">${text((run.started_at || "").slice(0, 19).replace("T", " "))}</td>
            <td>${text(run.kind)}</td>
            <td><span class="pill ${run.status === "succeeded" ? "ok"
              : run.status === "failed" ? "bad" : "warn"}">${text(run.status)}</span></td>
            <td class="small muted">${text(run.error || JSON.stringify(run.stats_json || {}))}</td>
          </tr>`).join("")}</tbody></table>`
          : `<p class="muted small">No runs yet.</p>`}
      </section>`;

    const upload = async (inputId, path, label) => {
      const input = $(`#${inputId}`);
      if (!input.files || !input.files[0]) {
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
      const maxRows = $("#maxrows").value;
      return {
        company_name: $("#company").value || null,
        max_rows: maxRows ? Number(maxRows) : null,
      };
    };

    onActions(main, {
      bootstrap: async () => {
        const result = await api("/ingestion/bootstrap", { method: "POST" });
        $("#result").innerHTML = banner("ok",
          `Discovery tables ready in ${result.catalog}.${result.schema}.`);
      },
      "up-schemas": () => upload("f-schemas", "/ingestion/upload/schemas", "Schemas"),
      "up-tables": () => upload("f-tables", "/ingestion/upload/tables", "Tables"),
      "up-columns": () => upload("f-columns", "/ingestion/upload/columns", "Columns"),
      "enrich-schemas": async () => {
        const result = await api("/ingestion/enrich/schemas",
          { method: "POST", body: JSON.stringify(enrichBody()) });
        $("#result").innerHTML = banner("ok",
          `${num(result.schemas_enriched_total)} schema(s) now enriched.`);
      },
      "enrich-tables": async () => {
        const result = await api("/ingestion/enrich/tables",
          { method: "POST", body: JSON.stringify(enrichBody()) });
        $("#result").innerHTML = banner(result.staged_errors ? "warn" : "ok",
          `${num(result.tables_enriched_total)} table(s) enriched`
          + (result.staged_rows ? ` (${num(result.staged_ok)} of ${num(result.staged_rows)} `
            + `model calls succeeded this run` : "")
          + (result.staged_errors ? `, ${num(result.staged_errors)} failed).` : ")."));
      },
      canonicalize: () => runCanonicalize(false),
      "canonicalize-nollm": () => runCanonicalize(true),
      attribute: async () => {
        const result = await api("/ingestion/attribute", {
          method: "POST",
          body: JSON.stringify({ advance_status: $("#advance").checked }),
        });
        const advanced = (result.advanced || []).map((a) =>
          `<li>${text(a.label)}: ${text(a.from)} → ${text(a.to)}</li>`).join("");
        $("#result").innerHTML = banner("ok",
          `${num(result.assets_matched)} catalog module(s) matched from `
          + `${num(result.canonicals_discovered)} discovered source system(s).`)
          + (advanced ? `<div class="card"><h3>Status advanced</h3>
              <ul class="tight small">${advanced}</ul></div>` : "")
          + ((result.unmatched_canonicals || []).length
            ? `<div class="card"><h3>Discovered but not in the catalog</h3>
                <p class="small muted">Add these as data assets, or map them under
                  Source mapping.</p>
                <p class="small mono">${text(result.unmatched_canonicals.join(", "))}</p>
              </div>` : "");
      },
    });

    async function runCanonicalize(skipLlm) {
      const result = await api("/ingestion/canonicalize", {
        method: "POST",
        body: JSON.stringify({ skip_llm: skipLlm }),
      });
      $("#result").innerHTML = banner("ok",
        `${num(result.distinct_labels)} distinct label(s); `
        + `${num(result.newly_resolved)} newly resolved `
        + `(exact ${num(result.exact)}, normalized ${num(result.normalized)}, `
        + `AI ${num(result.llm)}, unmapped ${num(result.other)}). `
        + (result.skipped_manual ? `${num(result.skipped_manual)} manual mapping(s) left untouched.` : ""));
    }
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
  // Router
  // ---------------------------------------------------------------------
  const VIEWS = {
    setup: viewSetup,
    discovery: viewDiscovery,
    aliases: viewAliases,
    domains: viewDomains,
    taxonomy: viewTaxonomy,
    generate: viewGenerate,
  };

  async function show(name) {
    const render = VIEWS[name] || viewSetup;
    document.querySelectorAll("#nav button").forEach((button) => {
      if (button.dataset.view === name) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
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

  document.querySelectorAll("#nav button").forEach((button) => {
    button.addEventListener("click", () => show(button.dataset.view));
  });
  window.addEventListener("hashchange", () => show(location.hash.slice(1) || "setup"));

  show(location.hash.slice(1) || "setup");
})();
