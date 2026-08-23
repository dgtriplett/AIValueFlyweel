// The single axios instance and the whole API surface the SPA uses.
//
// Everything goes through one object so the set of endpoints the UI depends on is
// readable in one place, and so a route rename is a compile error here rather
// than a 404 discovered in a workshop. baseURL is "/api" because the app is
// served from the same origin as FastAPI; the vite dev server proxies it.

import axios from 'axios'
import { ACCOUNT_HEADER, accountId } from './lib/account'
import { describeError } from './lib/errors'
import type {
  ArtifactsResponse,
  Assumption,
  BlastRadiusResponse,
  BlastNode,
  CatalogRecommendResponse,
  ClassificationRule,
  ClassifyResponse,
  Comment,
  ConfirmApplyResponse,
  ConfirmCardData,
  CoverageMatrixResponse,
  CurrentSnapshotResponse,
  DashboardData,
  DataAsset,
  DetectDependenciesResponse,
  Domain,
  DomainGapsResponse,
  EnablesEdge,
  EstimateValueResponse,
  ExecutivePack,
  FundingRequest,
  GenieAskResponse,
  GlossaryResponse,
  HealthResponse,
  HypothesizedValue,
  JointCase,
  KbArticle,
  KbArticleListResponse,
  KbArticleWriteResponse,
  KbAttachmentUploadResponse,
  KbLink,
  KbTreeResponse,
  LiveStatusResponse,
  LiveSyncResponse,
  Lob,
  PortfolioValue,
  RecommendResponse,
  RequiresEdge,
  RoadmapResponse,
  RuleInput,
  RuleSeedResponse,
  RuleTestResponse,
  RulesResponse,
  SnapshotsResponse,
  SourceAlias,
  SourceRecommendResponse,
  TaxonomyCoverage,
  TaxonomyResponse,
  NetworkResponse,
  OnboardingImportResponse,
  UnattributedArtifactsResponse,
  UnlocksResponse,
  UseCase,
  UseCaseDetail,
  WhatIfCandidatesResponse,
  WhatIfComparison,
  WhatIfProjection,
} from './types'

export const http = axios.create({ baseURL: '/api' })

// The selected account travels on EVERY request, injected once here.
//
// Deliberately an interceptor and not a per-call-site header, for the reason the
// console records at `console.js:61-70`: scoping each fetch remembers to apply
// "fails silently and shows one customer another's numbers". A forgotten header
// does not raise — the server quietly falls back to the default account
// (`server/accounts.py:19-22`) — so the failure surfaces as one tenant reading
// another's numbers, which is the worst way to find out.
//
// Anything reaching the API outside axios must add the header itself; the two
// raw-`fetch` writes use `accountHeaders()` from `lib/account`, and §4.1 of
// TIER3_MIGRATION_PLAN.md tracks the remaining anchor-href downloads.
http.interceptors.request.use((config) => {
  const id = accountId()
  if (id) config.headers.set(ACCOUNT_HEADER, id)
  return config
})

// One place translates a failed response into an `ApiError`.
//
// Without this, `error.response.data.detail` chains grow at each call site and
// disagree about which shapes to check — and the messages that matter most get
// dropped. `server/limits.py:169-195` returns 429 with `Retry-After` and a
// deliberately actionable message ("wait 12s and try again"); `GeniePanel` used
// to discard it and tell the user the assistant was broken.
//
// Rejecting with an `ApiError` rather than the `AxiosError` means callers narrow
// with `isApiError` and never touch axios' error shape. React Query treats any
// rejection as a failure exactly as before, so this changes what an error SAYS,
// not when one happens.
http.interceptors.response.use(undefined, (error: unknown) => {
  if (axios.isCancel(error)) return Promise.reject(error)
  if (axios.isAxiosError(error)) {
    return Promise.reject(
      describeError({
        status: error.response?.status ?? null,
        body: error.response?.data,
        // `AxiosHeaders` is case-insensitive on `get`, and a plain-object
        // fallback covers a mocked adapter in tests.
        header: (name) => {
          const headers = error.response?.headers
          if (!headers) return null
          if (typeof headers.get === 'function') {
            const value = headers.get(name)
            return typeof value === 'string' ? value : null
          }
          const record = headers as unknown as Record<string, unknown>
          const hit = record[name] ?? record[name.toLowerCase()]
          return typeof hit === 'string' ? hit : null
        },
        cause: error,
      }),
    )
  }
  return Promise.reject(error)
})

/** The scopes the catalog/portfolio switcher offers. `all` is the full universe. */
export type UseCaseScope = 'portfolio' | 'catalog' | 'all'

export const api = {
  health: () => http.get<HealthResponse>('/health').then((r) => r.data),

  lobs: () => http.get<Lob[]>('/lobs').then((r) => r.data),

  /** `portfolio` returns a bare array; every other scope returns an envelope. */
  useCases: (scope: UseCaseScope = 'portfolio'): Promise<UseCase[]> =>
    scope === 'portfolio'
      ? http.get<UseCase[]>('/use-cases').then((r) => r.data)
      : http
          .get<{ items: UseCase[] }>(`/use-cases?scope=${scope}`)
          .then((r) => r.data.items),

  useCaseDetail: (id: number) =>
    http.get<UseCaseDetail>(`/use-cases/${id}/detail`).then((r) => r.data),

  createUseCase: (body: Partial<UseCase>) =>
    http.post<UseCase>('/use-cases', body).then((r) => r.data),

  updateUseCase: (id: number, body: Partial<UseCase>) =>
    http.put<UseCase>(`/use-cases/${id}`, body).then((r) => r.data),

  setStatus: (id: number, status: string) =>
    http.patch<UseCase>(`/use-cases/${id}/status`, { status }).then((r) => r.data),

  advanceStatus: (id: number) =>
    http.patch<UseCase>(`/use-cases/${id}/status`, { advance: true }).then((r) => r.data),

  setInPortfolio: (id: number, in_portfolio: boolean) =>
    http
      .patch<UseCase>(`/use-cases/${id}/portfolio`, { in_portfolio })
      .then((r) => r.data),

  bulkPortfolio: (ids: number[], in_portfolio = true) =>
    http
      .post<{ updated: number }>('/use-cases/portfolio/bulk', { ids, in_portfolio })
      .then((r) => r.data),

  recommendCatalog: (top_n = 8) =>
    http
      .get<CatalogRecommendResponse>(`/agents/recommend-catalog?top_n=${top_n}`)
      .then((r) => r.data),

  deleteUseCase: (id: number) =>
    http.delete<{ deleted: boolean }>(`/use-cases/${id}`).then((r) => r.data),

  dataAssets: () => http.get<DataAsset[]>('/data-assets').then((r) => r.data),

  createDataAsset: (body: Partial<DataAsset>) =>
    http.post<DataAsset>('/data-assets', body).then((r) => r.data),

  updateDataAsset: (id: number, body: Partial<DataAsset>) =>
    http.put<DataAsset>(`/data-assets/${id}`, body).then((r) => r.data),

  /** Marks the row user-edited server-side, so a resync will not overwrite it. */
  setIngestionStatus: (id: number, ingestion_status: string) =>
    http
      .patch<DataAsset>(`/data-assets/${id}/status`, { ingestion_status })
      .then((r) => r.data),

  deleteDataAsset: (id: number) =>
    http.delete<{ deleted: boolean }>(`/data-assets/${id}`).then((r) => r.data),

  requires: () => http.get<RequiresEdge[]>('/dependencies/requires').then((r) => r.data),

  enables: () => http.get<EnablesEdge[]>('/dependencies/enables').then((r) => r.data),

  createRequires: (body: RequiresEdge) =>
    http.post<RequiresEdge>('/dependencies/requires', body).then((r) => r.data),

  /** DELETE takes query params, not a body — the route reads them off the query. */
  deleteRequires: (use_case_id: number, data_asset_id: number, manual = false) =>
    http
      .delete<{ deleted: boolean }>(
        `/dependencies/requires?use_case_id=${use_case_id}` +
          `&data_asset_id=${data_asset_id}&manual=${manual}`,
      )
      .then((r) => r.data),

  createEnables: (body: EnablesEdge) =>
    http.post<EnablesEdge>('/dependencies/enables', body).then((r) => r.data),

  deleteEnables: (from_use_case_id: number, to_use_case_id: number) =>
    http
      .delete<{ deleted: boolean }>(
        `/dependencies/enables?from_use_case_id=${from_use_case_id}` +
          `&to_use_case_id=${to_use_case_id}`,
      )
      .then((r) => r.data),

  createComment: (body: {
    entity_type: string
    entity_id: number
    body: string
    mentions?: string[]
  }) => http.post<Comment>('/comments', body).then((r) => r.data),

  assumptions: () => http.get<Assumption[]>('/value-assumptions').then((r) => r.data),

  updateAssumption: (key: string, value: number) =>
    http.put<Assumption>(`/value-assumptions/${key}`, { value }).then((r) => r.data),

  portfolioValue: () =>
    http.get<PortfolioValue>('/value-assumptions/computed/portfolio').then((r) => r.data),

  /** Returns literal `null` when nothing is mapped yet. */
  topAsset: () => http.get<BlastNode | null>('/impact/top-asset').then((r) => r.data),

  blastRadius: (node_id: string) =>
    http.get<BlastRadiusResponse>(`/impact/${node_id}`).then((r) => r.data),

  network: (id: number) =>
    http.get<NetworkResponse>(`/use-cases/${id}/network`).then((r) => r.data),

  detectDependencies: (use_case_id: number) =>
    http
      .post<DetectDependenciesResponse>('/agents/detect-dependencies', { use_case_id })
      .then((r) => r.data),

  unlocks: (id: number) =>
    http.get<UnlocksResponse>(`/use-cases/${id}/unlocks`).then((r) => r.data),

  recommend: (top_n = 6) =>
    http.post<RecommendResponse>('/agents/recommend', { top_n }).then((r) => r.data),

  generateRoadmap: (persist = false) =>
    http.post<RoadmapResponse>('/agents/roadmap', { persist }).then((r) => r.data),

  estimateValue: (body: { use_case_id?: number; title?: string; description?: string }) =>
    http.post<EstimateValueResponse>('/agents/estimate-value', body).then((r) => r.data),

  decomposeSource: (source_category: string, vendor?: string | null) =>
    http
      .post<{ modules: unknown }>('/agents/decompose-source', { source_category, vendor })
      .then((r) => r.data),

  dashboard: () => http.get<DashboardData>('/analytics/dashboard').then((r) => r.data),

  liveStatus: () => http.get<LiveStatusResponse>('/live/status').then((r) => r.data),

  liveSync: (apply = true) =>
    http.post<LiveSyncResponse>(`/live/sync?apply=${apply}`).then((r) => r.data),

  syncGenie: () => http.post<unknown>('/live/sync-genie').then((r) => r.data),

  fundingRequests: () =>
    http.get<FundingRequest[]>('/funding-requests').then((r) => r.data),

  sourceRecommendations: () =>
    http.get<SourceRecommendResponse>('/data-sources/recommendations').then((r) => r.data),

  jointOpportunities: () =>
    http
      .get<{ opportunities: JointCase[] }>('/joint-funding/opportunities')
      .then((r) => r.data),

  jointOpportunity: (id: number) =>
    http.get<JointCase>(`/joint-funding/opportunity/${id}`).then((r) => r.data),

  jointBrief: (asset_id: number) =>
    http
      .post<{ model: string; used_llm: boolean; brief_md: string; case: JointCase }>(
        '/joint-funding/brief',
        { asset_id },
      )
      .then((r) => r.data),

  createJointRequest: (body: Partial<FundingRequest> & { cost_share?: unknown }) =>
    http.post<FundingRequest>('/joint-funding/request', body).then((r) => r.data),

  updateJointRequest: (id: number, status: string, sponsor?: string) =>
    http
      .put<FundingRequest>(`/joint-funding/request/${id}`, { status, sponsor })
      .then((r) => r.data),

  /**
   * Preview or apply an onboarding workbook.
   *
   * The one multipart upload in the app. It goes through `http` like everything
   * else so the account interceptor scopes it; axios derives the multipart
   * boundary from the `FormData` itself, so no `Content-Type` is set here —
   * setting one by hand omits the boundary and the server cannot parse the body.
   *
   * Generic in the response so a caller can narrow the diff rows it renders
   * (`OnboardingView` adds the row ids it keys on) without widening the shared
   * type for everyone.
   */
  importOnboarding: <T = OnboardingImportResponse>(body: FormData, apply: boolean) =>
    http.post<T>(`/onboarding/import?apply=${apply}`, body).then((r) => r.data),

  genieAsk: (question: string, conversation_id?: string | null) =>
    http
      .post<GenieAskResponse>('/genie/ask', { question, conversation_id })
      .then((r) => r.data),

  // -------------------------------------------------------------------------
  // Tier 3 Phase 3 — the read-only console surfaces.
  //
  // Every one of these is a GET, and none is rate-limited server-side (the
  // `limiter` dependencies in `snapshots.py` and `inventory.py` are on the POSTs
  // and DELETEs this phase deliberately does not port). The two POSTs below are
  // the exception that proves the rule: `/whatif/simulate` is a projection —
  // `server/routes/whatif.py:26-31` states it writes nothing, has no confirm
  // gate and no audit row — so it is a read modelled as a POST because its input
  // is a list of ids too long for a query string.
  // -------------------------------------------------------------------------

  coverageMatrix: () =>
    http.get<CoverageMatrixResponse>('/domains/coverage-matrix').then((r) => r.data),

  domains: () => http.get<Domain[]>('/domains').then((r) => r.data),

  domainGaps: (limit = 15) =>
    http.get<DomainGapsResponse>(`/domains/gaps?limit=${limit}`).then((r) => r.data),

  whatIfCandidates: (limit = 40) =>
    http.get<WhatIfCandidatesResponse>(`/whatif/candidates?limit=${limit}`).then((r) => r.data),

  /** Projection only. Writes nothing — see the note above. */
  whatIfSimulate: (data_asset_ids: number[]) =>
    http
      .post<WhatIfProjection>('/whatif/simulate', { data_asset_ids })
      .then((r) => r.data),

  /** Each option is a set landed together, so "OMS alone" can lose to "OMS + AMI". */
  whatIfCompare: (options: number[][]) =>
    http.post<WhatIfComparison>('/whatif/simulate/compare', { options }).then((r) => r.data),

  snapshots: (limit = 500) =>
    http.get<SnapshotsResponse>(`/snapshots?limit=${limit}`).then((r) => r.data),

  /** What a snapshot would say if taken now. Explicitly not stored. */
  currentSnapshot: () =>
    http.get<CurrentSnapshotResponse>('/snapshots/current').then((r) => r.data),

  glossary: () => http.get<GlossaryResponse>('/flow/glossary').then((r) => r.data),

  /** `inventory.py` declares no router prefix, so these live at `/api/artifacts`
   *  rather than `/api/inventory/artifacts` — see TIER3_MIGRATION_PLAN.md §1. */
  artifacts: (limit = 200) =>
    http.get<ArtifactsResponse>(`/artifacts?limit=${limit}`).then((r) => r.data),

  unattributedArtifacts: (limit = 30) =>
    http
      .get<UnattributedArtifactsResponse>(`/artifacts/unattributed?limit=${limit}`)
      .then((r) => r.data),

  executivePack: () => http.get<ExecutivePack>('/exports/executive-pack').then((r) => r.data),

  /**
   * The same pack as Markdown, for download.
   *
   * `responseType: 'blob'` because the response is `text/markdown` with
   * `Content-Disposition: attachment`, not JSON. It goes through `http` rather
   * than an anchor href so the account interceptor scopes it — an `<a download>`
   * skips axios and therefore skips the header, which is §4.1's whole point.
   */
  executivePackMarkdown: () =>
    http
      .get<Blob>('/exports/executive-pack.md', { responseType: 'blob' })
      .then((r) => r.data),

  /**
   * Re-read a pending confirm card without consuming it.
   * 404 when the token was never issued; a real token that is expired or already
   * applied comes back with `expired` / `consumed_at` set, which is what lets
   * `<ConfirmCard>` explain the state instead of just failing to apply.
   */
  readConfirm: (token: string) =>
    http.get<ConfirmCardData>(`/confirm/${token}`).then((r) => r.data),

  /**
   * Consume a token and perform its write. Single-use.
   * 409 covers already-applied and expired alike (`routes/generate.py:437-441`);
   * the server's `detail` distinguishes them in words meant to be shown.
   */
  applyConfirm: (token: string) =>
    http.post<ConfirmApplyResponse>(`/confirm/${token}`).then((r) => r.data),

  // -------------------------------------------------------------------------
  // Tier 3 Phase 4 — the knowledge base.
  //
  // Every write here is `limiter("write")` server-side and NONE is confirm-gated,
  // which `server/routes/knowledge.py:24-36` argues for at length: editing an
  // article you are looking at is a person typing into a document, not an agent
  // exercising judgement, and gating it would teach people to click through
  // confirmations where the gate actually matters. Versioning is what protects the
  // content instead — every edit snapshots the previous body.
  //
  // Slugs are interpolated through `encodeURIComponent`. `server/knowledge.py`
  // restricts a generated slug to `[a-z0-9-]`, but a slug also arrives from a
  // pasted deep link and from a `[[wiki link]]`, so it is untrusted input on the
  // way in even though it is well-formed on the way out.
  // -------------------------------------------------------------------------

  kbTree: () => http.get<KbTreeResponse>('/kb/tree').then((r) => r.data),

  /**
   * List / search / filter articles.
   *
   * `q` runs `websearch_to_tsquery` server-side and adds `rank` + a
   * `<<match>>`-marked `excerpt` to each row. A malformed expression comes back
   * 422 with words meant to be read ("Try plain words, or quote a phrase") rather
   * than a 500 — so the caller shows the server's message, not a generic failure.
   *
   * `folder_path` filters by SUBTREE, not by exact folder: asking for
   * `/standards` returns everything filed beneath it (`build_search_sql`).
   */
  kbArticles: (params: {
    q?: string | null
    folder_path?: string | null
    tag?: string | null
    status?: string | null
    limit?: number
  } = {}) => {
    const query = new URLSearchParams({ limit: String(params.limit ?? 50) })
    if (params.q) query.set('q', params.q)
    if (params.folder_path) query.set('folder_path', params.folder_path)
    if (params.tag) query.set('tag', params.tag)
    if (params.status) query.set('status', params.status)
    return http.get<KbArticleListResponse>(`/kb/articles?${query}`).then((r) => r.data)
  },

  /** One article with its links, attachments, versions and resolved wiki links. */
  kbArticle: (slug: string) =>
    http.get<KbArticle>(`/kb/articles/${encodeURIComponent(slug)}`).then((r) => r.data),

  /** Returns a thin row — the server assigns the slug, so the caller reads it back. */
  createKbArticle: (body: {
    title: string
    body_md?: string
    summary?: string | null
    folder_id?: number | null
    tags?: string[]
    status?: string
  }) => http.post<KbArticleWriteResponse>('/kb/articles', body).then((r) => r.data),

  /**
   * Edit an article. Only a title/body/summary change makes a new version —
   * re-filing or re-tagging deliberately does not, so the history stays skimmable.
   */
  updateKbArticle: (
    slug: string,
    body: {
      title?: string
      body_md?: string
      summary?: string | null
      folder_id?: number | null
      tags?: string[]
      status?: string
      change_note?: string | null
    },
  ) =>
    http
      .put<KbArticleWriteResponse>(`/kb/articles/${encodeURIComponent(slug)}`, body)
      .then((r) => r.data),

  /**
   * Archive an article, or `hard` to delete it permanently.
   *
   * Archiving is the default for the reason the route gives: an article is
   * somebody's written work, and the usual intent is "get this out of my way", not
   * "destroy it". Archived articles drop out of search but keep links and history.
   */
  deleteKbArticle: (slug: string, hard = false) =>
    http
      .delete<{ archived?: boolean; deleted?: boolean; slug: string }>(
        `/kb/articles/${encodeURIComponent(slug)}?hard=${hard}`,
      )
      .then((r) => r.data),

  /**
   * Roll back to an earlier version.
   *
   * The rollback is itself a new version — the current text is snapshotted before
   * being replaced — so restoring is undoable. That is what lets the UI offer this
   * without a confirm token behind it.
   */
  restoreKbVersion: (slug: string, version: number) =>
    http
      .post<KbArticleWriteResponse>(
        `/kb/articles/${encodeURIComponent(slug)}/restore/${version}`,
      )
      .then((r) => r.data),

  /** Attach an article to a portfolio entity, which is what makes it show up
   *  when someone opens that use case rather than only inside the KB. */
  createKbLink: (slug: string, body: { entity_type: string; entity_id: number; relation: string }) =>
    http
      .post<KbLink>(`/kb/articles/${encodeURIComponent(slug)}/links`, body)
      .then((r) => r.data),

  deleteKbLink: (linkId: number) =>
    http.delete<{ deleted: boolean }>(`/kb/links/${linkId}`).then((r) => r.data),

  /**
   * Upload an attachment (<=25MB, `server/knowledge.py:115`).
   *
   * Like the onboarding import, no `Content-Type` is set: axios derives the
   * multipart boundary from the `FormData`, and setting one by hand omits the
   * boundary so the server cannot parse the body. It still goes through `http`,
   * so the account interceptor scopes it.
   */
  uploadKbAttachment: (slug: string, file: File) => {
    const body = new FormData()
    body.append('file', file)
    return http
      .post<KbAttachmentUploadResponse>(
        `/kb/articles/${encodeURIComponent(slug)}/attachments`,
        body,
      )
      .then((r) => r.data)
  },

  /**
   * Fetch an attachment's bytes for saving to disk.
   *
   * `responseType: 'blob'` and routed through axios rather than an `<a href>` for
   * the reason §4.1 exists: an anchor skips the interceptor, and a missing account
   * header does not raise — the server falls back to the default account — so the
   * user silently downloads another tenant's document. `saveBlob` then saves it
   * WITHOUT previewing: the route serves these `Content-Disposition: attachment`
   * with `nosniff` and `default-src 'none'` because the bytes are user-supplied,
   * and rendering one inline would discard that decision.
   */
  kbAttachmentBlob: (attachmentId: number) =>
    http
      .get<Blob>(`/kb/attachments/${attachmentId}`, { responseType: 'blob' })
      .then((r) => r.data),

  deleteKbAttachment: (attachmentId: number) =>
    http.delete<{ deleted: boolean }>(`/kb/attachments/${attachmentId}`).then((r) => r.data),

  // -------------------------------------------------------------------------
  // Tier 3 Phase 5 — the curation writes.
  //
  // These are the first ported surfaces where the user CHANGES the estate rather
  // than reading it, and the rate-limit classes differ per call in a way the call
  // sites have to respect (`server/limits.py:129-145`):
  //
  //   PATCH /ingestion/aliases/{id}   unlimited — a per-row human correction
  //   POST  /taxonomy/classify        `generate`  burst 4, 12/min — calls an LLM
  //   POST  /rules, PUT, DELETE       unlimited — small metadata writes
  //   POST  /rules/seed               unlimited, and idempotent
  //   POST  /rules/test               `generate`  burst 4, 12/min — hits the warehouse
  //   POST  /artifacts/sync           `sweep`     burst 2, 4/min — walks system tables
  //
  // Every mutation built on the two `generate` calls and the one `sweep` call
  // spreads `NO_RETRY` from `lib/retry.ts`. That is not a change to react-query's
  // default (mutations already resolve `retry ?? 0`) but a statement at the call
  // site, because retrying a 429 spends the budget the `Retry-After` asked us to
  // wait out and can push a soft limit into a longer one.
  //
  // `inventory.py` declares NO router prefix, so the rules endpoints live at
  // `/api/rules`, not `/api/inventory/rules` — the same trap `artifacts` above
  // documents.
  // -------------------------------------------------------------------------

  /** Alias mappings. `needs_review` selects unresolved and low-confidence rows. */
  sourceAliases: (needsReview = true, limit = 200) =>
    http
      .get<SourceAlias[]>(`/ingestion/aliases?needs_review=${needsReview}&limit=${limit}`)
      .then((r) => r.data),

  /**
   * Correct one mapping by hand. Pins it permanently against future sweeps.
   *
   * The server validates `canonical` against the live vocabulary and 422s an
   * unknown value, so the caller builds its select from the real categories on
   * `/data-assets` rather than from a hardcoded list.
   */
  patchSourceAlias: (aliasId: number, canonical: string) =>
    http
      .patch<SourceAlias>(`/ingestion/aliases/${aliasId}`, { canonical })
      .then((r) => r.data),

  taxonomyCoverage: () =>
    http.get<TaxonomyCoverage>('/taxonomy/coverage').then((r) => r.data),

  taxonomy: () => http.get<TaxonomyResponse>('/taxonomy').then((r) => r.data),

  /** AI-classify unlabelled assets. `generate`-limited — see the note above. */
  classifyTaxonomy: (max_assets = 200) =>
    http
      .post<ClassifyResponse>('/taxonomy/classify', { max_assets })
      .then((r) => r.data),

  rules: () => http.get<RulesResponse>('/rules').then((r) => r.data),

  createRule: (body: RuleInput) =>
    http.post<ClassificationRule>('/rules', body).then((r) => r.data),

  updateRule: (ruleId: number, body: RuleInput) =>
    http.put<ClassificationRule>(`/rules/${ruleId}`, body).then((r) => r.data),

  deleteRule: (ruleId: number) =>
    http.delete<{ deleted: boolean }>(`/rules/${ruleId}`).then((r) => r.data),

  /** Load the common conventions. Idempotent, so `created` can be 0. */
  seedRules: () => http.post<RuleSeedResponse>('/rules/seed').then((r) => r.data),

  /** Dry-run the active rules against real discovered rows. `generate`-limited. */
  testRules: (limit = 100) =>
    http.post<RuleTestResponse>('/rules/test', { limit }).then((r) => r.data),
}

/** Value-model helper: the drawer and the wizard both read components this way. */
export function valueComponents(value?: HypothesizedValue | null) {
  return Array.isArray(value?.components) ? value!.components! : []
}
