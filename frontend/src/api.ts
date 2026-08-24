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
  Account,
  AccountsResponse,
  AccountCreateResponse,
  ArtifactsResponse,
  Assumption,
  AssumptionResearchResponse,
  AttributeResponse,
  BlastRadiusResponse,
  BlastNode,
  BootstrapResponse,
  Branding,
  BrandingLogoResponse,
  CanonicalizeResponse,
  CleanupResponse,
  CatalogRecommendResponse,
  ChatResponse,
  ClassificationRule,
  ClassifyResponse,
  Comment,
  ConfirmApplyResponse,
  ConfirmCardData,
  CompanyProfile,
  CoverageMatrixResponse,
  CurrentSnapshotResponse,
  CustomerEnhancementResponse,
  DashboardData,
  DataAsset,
  DemoStatusResponse,
  DetectDependenciesResponse,
  Domain,
  DomainGapsResponse,
  EnablesEdge,
  EnrichSchemasResponse,
  EnrichTablesResponse,
  EstimateValueResponse,
  ExecutivePack,
  ExtractorInfoResponse,
  FundingRequest,
  GenerateCommitInput,
  GenerateUseCasesInput,
  GenerateUseCasesResponse,
  GenieAskResponse,
  GenieProvisionResponse,
  GenieStatusResponse,
  GlossaryResponse,
  HealthResponse,
  HypothesizedValue,
  IngestionSummaryResponse,
  InventoryUploadResponse,
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
  ProposalContextResponse,
  ProposalGenerateResponse,
  RecommendResponse,
  RequiresEdge,
  ResearchApplyInput,
  ResearchApplyResponse,
  ResearchCompanyInput,
  ResearchCompanyResponse,
  RoadmapResponse,
  RoadmapImportApplyResponse,
  RoadmapImportPreviewResponse,
  RoadmapPackage,
  RuleInput,
  RuleSeedResponse,
  RuleTestResponse,
  RulesResponse,
  SetupGrantsResponse,
  SetupStatusResponse,
  SnapshotsResponse,
  SourceAlias,
  SyncGenieResponse,
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

  dataAsset: (id: number) => http.get<DataAsset>(`/data-assets/${id}`).then((r) => r.data),

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
  // Tier 3 Phase 11 — the one assistant.
  //
  // `/api/chat` is the tool-calling loop that supersedes `Ask Genie`
  // (`/api/genie/ask`, kept server-side for a future SQL-mode toggle). It is
  // `limiter("chat")` server-side — 6 burst / 20 per minute (`server/limits.py`)
  // — and spends real tokens, so the caller marks the mutation `NO_RETRY`: an
  // automatic second POST after a 429 spends the budget the `Retry-After` asked us
  // to wait out, and re-runs a whole tool-calling turn for money.
  //
  // A write tool never writes here: the server turns its proposal into a
  // single-use confirm token in `response.confirm`, which the SPA feeds straight
  // into the shared `<ConfirmCard>` so no chat-proposed change auto-applies.
  // -------------------------------------------------------------------------
  chat: (message: string, conversation_id?: string | null) =>
    http
      .post<ChatResponse>('/chat', { message, conversation_id })
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

  // -------------------------------------------------------------------------
  // Tier 3 Phase 9 — the onboarding wizard.
  //
  // These are the endpoints behind the merged "Get started" surface: the setup
  // probes, the discovery pipeline, and the workbook round-trip. Most of the
  // writes here are `limiter("sweep")` or `limiter("generate")` server-side
  // (`ingestion.py`), so every mutation built on them spreads `NO_RETRY` — an
  // automatic second POST after a 429 spends the budget the `Retry-After` asked
  // us to wait out, and an enrichment retry spends real money twice.
  //
  // None is confirm-gated, and that is the server's decision rather than an
  // omission: the destructive-looking one (`/ingestion/attribute` with
  // `advance_status`) is capped at `landed` and only ever moves a status FORWARD
  // (`ingestion.py:836-846`), so it cannot demote a source a human marked
  // governed. The UI's job is to say what will change, which the wizard does in
  // words next to the checkbox — not to invent a gate the server does not have.
  // -------------------------------------------------------------------------

  /** Every dependency probe at once. Drives which wizard steps unlock. */
  setupStatus: () => http.get<SetupStatusResponse>('/setup/status').then((r) => r.data),

  /** Just the GRANT statements, for handing to a metastore admin. */
  setupGrants: () => http.get<SetupGrantsResponse>('/setup/grants').then((r) => r.data),

  /** Inventory rollup. Reports not-configured and configured-but-unreachable
   *  as data rather than as an error — see `IngestionSummaryResponse`. */
  ingestionSummary: () =>
    http.get<IngestionSummaryResponse>('/ingestion/summary').then((r) => r.data),

  extractorInfo: () =>
    http.get<ExtractorInfoResponse>('/ingestion/extractor/info').then((r) => r.data),

  /**
   * The metadata extractor as a ZIP, for running under the user's own credentials.
   *
   * `responseType: 'blob'` and routed through axios rather than an `<a href>` for
   * §4.1's reason: an anchor skips the account interceptor, a missing account
   * header does NOT raise (`server/accounts.py:19-22` falls back to the default
   * account), so the failure is silent. The ZIP is per-account only in that its
   * instructions name this deployment, but the rule does not get to be applied
   * selectively — a bare `/api` anchor anywhere is the pattern that comes back.
   */
  extractorZip: () =>
    http
      .get<Blob>('/ingestion/extractor/download', { responseType: 'blob' })
      .then((r) => r.data),

  /** CREATE the discovery schema + tables in Unity Catalog. Idempotent. */
  ingestionBootstrap: () =>
    http.post<BootstrapResponse>('/ingestion/bootstrap').then((r) => r.data),

  /**
   * Ingest one extractor CSV. ≤64 MB server-side (`ingestion.py:53`).
   *
   * Like every other multipart call, no `Content-Type` is set: axios derives the
   * boundary from the `FormData`, and setting one by hand omits it so the server
   * cannot parse the body.
   */
  uploadInventoryCsv: (kind: 'schemas' | 'tables' | 'columns', file: File) => {
    const body = new FormData()
    body.append('file', file)
    return http
      .post<InventoryUploadResponse>(`/ingestion/upload/${kind}`, body)
      .then((r) => r.data)
  },

  enrichSchemas: (body: { company_name?: string | null; max_rows?: number | null }) =>
    http.post<EnrichSchemasResponse>('/ingestion/enrich/schemas', body).then((r) => r.data),

  enrichTables: (body: { company_name?: string | null; max_rows?: number | null }) =>
    http.post<EnrichTablesResponse>('/ingestion/enrich/tables', body).then((r) => r.data),

  /** Resolve raw source-system labels to the canonical vocabulary. */
  canonicalizeSources: () =>
    http.post<CanonicalizeResponse>('/ingestion/canonicalize', {}).then((r) => r.data),

  /** Attribute discovered tables to catalog modules. `advance_status` is the
   *  only part that moves readiness, which is why the caller makes it explicit. */
  attributeDiscovered: (advance_status: boolean) =>
    http
      .post<AttributeResponse>('/ingestion/attribute', { advance_status })
      .then((r) => r.data),

  /**
   * The onboarding workbook, pre-filled with this account's portfolio.
   *
   * Returns the blob AND the `Content-Disposition`, because the server names the
   * file and the client should not second-guess it. Through axios, not an anchor:
   * this response is SCOPED TO AN ACCOUNT — `onboarding.py:_compute_import` and
   * the export both read `accounts.current()` — so a request without the header
   * silently exports the default account's portfolio. That is the tenant leak
   * §4.1 is about, and it is the one download in the app where the leaked bytes
   * are the customer's whole portfolio.
   */
  onboardingTemplate: () =>
    http
      .get<Blob>('/onboarding/export.xlsx', { responseType: 'blob' })
      .then((response) => ({
        blob: response.data,
        // `AxiosHeaders.get` is case-insensitive; the plain-object fallback is for
        // a mocked adapter, which is how the tests drive this.
        contentDisposition:
          (typeof response.headers?.get === 'function'
            ? (response.headers.get('content-disposition') as string | null)
            : ((response.headers as unknown as Record<string, string>)?.[
                'content-disposition'
              ] ?? null)) ?? null,
      })),

  // Tier 3 Phase 6 — every call stays on the account-scoped axios client. The
  // views mark all POST mutations NO_RETRY because generation and import must
  // never be replayed automatically after an ambiguous failure.
  generateUseCases: (body: GenerateUseCasesInput) =>
    http.post<GenerateUseCasesResponse>('/generate/use-cases', body).then((r) => r.data),

  prepareGeneratedUseCases: (body: GenerateCommitInput) =>
    http.post<ConfirmCardData>('/generate/use-cases/commit', body).then((r) => r.data),

  proposalContext: (useCaseId: number) =>
    http
      .get<ProposalContextResponse>(`/proposals/use-cases/${useCaseId}/context`)
      .then((r) => r.data),

  generateProposal: (useCaseId: number, regenerate = false) =>
    http
      .post<ProposalGenerateResponse>(
        `/proposals/use-cases/${useCaseId}${regenerate ? '?regenerate=true' : ''}`,
      )
      .then((r) => r.data),

  previewRoadmapImport: (roadmapPackage: RoadmapPackage) =>
    http
      .post<RoadmapImportPreviewResponse>('/sync/maturity-roadmap/preview', {
        package: roadmapPackage,
        dry_run: true,
      })
      .then((r) => r.data),

  applyRoadmapImport: (roadmapPackage: RoadmapPackage) =>
    http
      .post<RoadmapImportApplyResponse>('/sync/maturity-roadmap/apply', {
        package: roadmapPackage,
        dry_run: false,
      })
      .then((r) => r.data),

  // -------------------------------------------------------------------------
  // Tier 3 Phase 7 — company research & assumption recalibration.
  //
  // `POST /research/company` and `GET /agents/customer-enhancements` are the two
  // token-spending calls (both `limiter("research")` server-side,
  // `server/routes/research.py` / `agents.py`), so the views spread `NO_RETRY`:
  // an automatic second POST after a 429 spends the budget the `Retry-After`
  // asked us to wait out, and research is minutes of model time, not a cheap GET.
  //
  // Applying goes through the shared confirm gate (`prepareResearchApply` returns
  // a ConfirmCardData; `<ConfirmCard>` consumes the token via `applyConfirm`),
  // because recalibration re-quantifies every dollar figure in the portfolio at
  // once — the exact write `useAssumptionInvalidation` exists to refresh.
  // -------------------------------------------------------------------------

  /** The stored profile, or `{ researched: false }` when none exists yet. */
  researchCompany: () =>
    http.get<CompanyProfile>('/research/company').then((r) => r.data),

  /** The latest run's assumption proposals, with provenance. */
  researchAssumptions: () =>
    http.get<AssumptionResearchResponse>('/research/assumptions').then((r) => r.data),

  /** Research a company — writes the profile + proposals, never the assumptions. */
  runResearch: (body: ResearchCompanyInput) =>
    http.post<ResearchCompanyResponse>('/research/company', body).then((r) => r.data),

  /** Stage a recalibration for confirmation. Writes nothing itself. */
  prepareResearchApply: (body: ResearchApplyInput) =>
    http.post<ResearchApplyResponse>('/research/apply', body).then((r) => r.data),

  /** Advisory agent: refinements + 10 app enhancements. Applies nothing. */
  customerEnhancements: () =>
    http
      .get<CustomerEnhancementResponse>('/agents/customer-enhancements')
      .then((r) => r.data),

  // -------------------------------------------------------------------------
  // Tier 3 Phase 8 — the Settings surfaces (Accounts, Admin & audit, Branding).
  //
  // These are operator surfaces, and three of the writes here are ADMIN-GATED
  // server-side after the Phase 0 hardening: `POST /demo/load`, `POST /demo/reset`
  // and `POST /genie/provision` all call `require_admin` and fail closed. There is
  // deliberately no `is_admin` endpoint the client could trust, so the UI does NOT
  // gate them client-side — it attempts the call and surfaces the server's 403
  // `detail`. `include_inactive=true` on the account list is admin-gated the same
  // way (`accounts.py:list_accounts`), which is why the switcher requests it and
  // treats a 403 as "you cannot see archived accounts", not as a broken page.
  //
  // Every one goes through `http`, so the account interceptor scopes it — a
  // missing header on a WRITE mutates the DEFAULT account's data (`§4.1`). The
  // account SWITCH itself is the highest-risk item: it writes localStorage and the
  // caller clears the whole react-query cache, because a scoped view holding the
  // previous account's rows is the exact tenant-mixing failure this phase guards.
  // -------------------------------------------------------------------------

  /**
   * The accounts this caller may switch between, with counts for the switcher.
   *
   * `include_inactive` enumerates ARCHIVED accounts and is admin-gated — a 403 is
   * the designed answer for a non-admin, not a failure, so the view narrows the
   * request to active accounts on 403 rather than showing an error.
   */
  accounts: (include_inactive = false) =>
    http
      .get<AccountsResponse>(`/accounts?include_inactive=${include_inactive}`)
      .then((r) => r.data),

  /** Add an account. Admin-gated server-side; a 403 carries the reason. */
  createAccount: (body: { name: string; utility_type?: string | null }) =>
    http.post<AccountCreateResponse>('/accounts', body).then((r) => r.data),

  /** Rename / retype / activate / make-default. Admin-gated server-side. */
  updateAccount: (
    id: number,
    body: {
      name?: string
      utility_type?: string | null
      is_active?: boolean
      make_default?: boolean
    },
  ) => http.patch<Account>(`/accounts/${id}`, body).then((r) => r.data),

  // ---- Admin & audit -------------------------------------------------------

  /**
   * Demo-mode state. A 404 is the designed answer when `DEMO_MODE` is off, so the
   * view catches it and renders the disabled card rather than an error.
   */
  demoStatus: () => http.get<DemoStatusResponse>('/demo/status').then((r) => r.data),

  /**
   * Replace the portfolio with the showcase dataset. Destructive, confirm-gated in
   * the view, and ADMIN-GATED server-side — a non-admin gets a 403 whose `detail`
   * is surfaced verbatim. `NO_RETRY` at the call site: an automatic replay of a
   * destructive write after an ambiguous failure is never correct.
   */
  demoLoad: () => http.post<{ counts?: Record<string, number> }>('/demo/load').then((r) => r.data),

  /** Reset the portfolio to pristine day-1. Destructive, admin-gated server-side. */
  demoReset: () => http.post<{ counts?: Record<string, number> }>('/demo/reset').then((r) => r.data),

  /** Genie readiness, and whether this app can provision a space itself. */
  genieStatus: () => http.get<GenieStatusResponse>('/genie/status').then((r) => r.data),

  /**
   * Create the Genie space over the portfolio mirror. ADMIN-GATED server-side and
   * `sweep`-limited; the view spreads `NO_RETRY` because it mirrors the portfolio
   * and builds a space — replaying it after a timeout could half-build a second.
   */
  genieProvision: () =>
    http.post<GenieProvisionResponse>('/genie/provision', {}).then((r) => r.data),

  /** Refresh the mirror the Genie space reads. Not admin-gated. */
  syncGenieMirror: () =>
    http.post<SyncGenieResponse>('/live/sync-genie').then((r) => r.data),

  /** Reclaim expired generation previews and consumed confirm tokens. Safe. */
  generateCleanup: () =>
    http.post<CleanupResponse>('/generate/cleanup').then((r) => r.data),

  // ---- Branding ------------------------------------------------------------

  /** What the header renders. Never raises server-side — branding is chrome. */
  branding: () => http.get<Branding>('/branding').then((r) => r.data),

  /** Set name / subtitle / accent. Returns the recomputed branding. */
  updateBranding: (body: {
    display_name?: string | null
    subtitle?: string | null
    accent_color?: string | null
  }) => http.put<Branding>('/branding', body).then((r) => r.data),

  /**
   * Upload a logo (≤2MB, image MIME only server-side, `branding.py:MAX_LOGO_BYTES`).
   *
   * Like every other multipart call, no `Content-Type` is set: axios derives the
   * boundary from the `FormData`, and setting one by hand omits it so the server
   * cannot parse the body. It still goes through `http`, so the interceptor scopes
   * it — a logo is per-account.
   */
  uploadBrandingLogo: (file: File) => {
    const body = new FormData()
    body.append('file', file)
    return http.post<BrandingLogoResponse>('/branding/logo', body).then((r) => r.data)
  },

  /** Remove the logo. Returns the recomputed branding. */
  deleteBrandingLogo: () => http.delete<Branding>('/branding/logo').then((r) => r.data),
}

/** Value-model helper: the drawer and the wizard both read components this way. */
export function valueComponents(value?: HypothesizedValue | null) {
  return Array.isArray(value?.components) ? value!.components! : []
}
