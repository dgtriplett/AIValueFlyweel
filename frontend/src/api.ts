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
  Assumption,
  BlastRadiusResponse,
  BlastNode,
  CatalogRecommendResponse,
  Comment,
  ConfirmApplyResponse,
  ConfirmCardData,
  DashboardData,
  DataAsset,
  DetectDependenciesResponse,
  EnablesEdge,
  EstimateValueResponse,
  FundingRequest,
  GenieAskResponse,
  HealthResponse,
  HypothesizedValue,
  JointCase,
  LiveStatusResponse,
  LiveSyncResponse,
  Lob,
  PortfolioValue,
  RecommendResponse,
  RequiresEdge,
  RoadmapResponse,
  SourceRecommendResponse,
  NetworkResponse,
  OnboardingImportResponse,
  UnlocksResponse,
  UseCase,
  UseCaseDetail,
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
}

/** Value-model helper: the drawer and the wizard both read components this way. */
export function valueComponents(value?: HypothesizedValue | null) {
  return Array.isArray(value?.components) ? value!.components! : []
}
