// Shapes returned by the FastAPI routes under server/routes/.
//
// These mirror the API rather than the database: fields the server merges in
// (readiness, value_range, realized) are part of the same object the SPA reads,
// so they live here too. Anything the server can omit is optional — the SPA
// renders against a partially-populated portfolio all the time.

export type SubVertical = 'fossil' | 'hydro' | 'renewables' | 'nuclear' | 'cross'
export type Status =
  | 'not_started'
  | 'scoping'
  | 'in_progress'
  | 'live'
  | 'value_realized'
export type Readiness =
  | 'shovel_ready'
  | 'awaiting_prerequisites'
  | 'nearly_ready'
  | 'blocked'
export type IngestionStatus = 'not_started' | 'landed' | 'curated' | 'governed'
export type Effort = 'S' | 'M' | 'L' | 'XL'
export type Criticality = 'required' | 'helpful'

export interface Lob {
  id: number
  name: string
  description?: string | null
}

export interface ValueComponent {
  name?: string
  calculationDisplay?: string
  assumptionKeys?: string[]
  [key: string]: unknown
}

export interface HypothesizedValue {
  low?: number | null
  mid?: number | null
  high?: number | null
  driver?: string | null
  components?: ValueComponent[]
  roiMonths?: number | null
  notes?: string | null
  [key: string]: unknown
}

export interface ValueRange {
  low: number
  mid: number
  high: number
}

export interface RealizedValue {
  mode: 'override' | 'calculated' | 'none'
  value: number | null
  note: string | null
}

export interface PendingPrereq {
  id: number
  title: string
}

export interface UseCase {
  id: number
  title: string
  description?: string | null
  lob_id?: number | null
  sub_vertical?: SubVertical | null
  stage?: string | null
  phase?: number | null
  status?: Status | null
  category?: string | null
  effort_tshirt?: Effort | null
  priority_score?: number | null
  risk_tags?: string[] | null
  compliance_tags?: string[] | null
  hypothesized_value_json?: HypothesizedValue | null
  realized_value_amount?: number | null
  realized_value_json?: HypothesizedValue | null
  realized_override_enabled?: boolean | null
  realized_override_amount?: number | null
  realized_override_note?: string | null
  status_source?: string | null
  created_by?: string | null
  created_at?: string | null
  updated_at?: string | null
  requires_locked?: boolean | null
  domains_locked?: boolean | null
  origin?: 'catalog' | 'custom' | 'auto' | null
  in_portfolio?: boolean | null

  // merged by the server
  readiness?: Readiness | null
  ready_pct?: number | null
  required_total?: number | null
  required_ready?: number | null
  prereqs_total?: number | null
  prereqs_built?: number | null
  pending_prereqs?: PendingPrereq[] | null
  value_range?: ValueRange | null
  computed_value?: number | null
  realized?: RealizedValue | null
}

export interface RequiredAsset extends DataAsset {
  criticality?: Criticality | null
}

export interface LinkedUseCase {
  id: number
  title: string
  stage?: string | null
  phase?: number | null
  status?: Status | null
  rationale?: string | null
  detected_by_agent?: boolean | null
}

export interface Comment {
  id: number
  entity_type: string
  entity_id: number
  body: string
  author?: string | null
  mentions?: string[] | null
  created_at?: string | null
}

export interface ValueRecord {
  id: number
  use_case_id: number
  kind: 'hypothesized' | 'realized'
  metric_type?: string | null
  amount?: number | null
  unit?: string | null
  fiscal_period?: string | null
  confidence?: string | null
}

export interface UseCaseDetail extends UseCase {
  required_assets?: RequiredAsset[]
  enables?: LinkedUseCase[]
  enabled_by?: LinkedUseCase[]
  value_records?: ValueRecord[]
  comments?: Comment[]
}

export interface DataAsset {
  id: number
  source_category?: string | null
  vendor?: string | null
  source_system?: string | null
  module?: string | null
  description?: string | null
  sub_vertical?: SubVertical | null
  ingestion_status?: IngestionStatus | null
  ingest_effort?: Effort | null
  ingest_cost_low?: number | null
  ingest_cost_high?: number | null
  uc_catalog?: string | null
  uc_schema?: string | null
  owning_lob_id?: number | null
  origin?: string | null
  auto_captured?: boolean | null
  auto_note?: string | null
  status_user_edited?: boolean | null
  benefiting_lob_ids?: number[] | null
}

export interface RequiresEdge {
  use_case_id: number
  data_asset_id: number
  criticality?: Criticality | null
  manual?: boolean | null
}

export interface EnablesEdge {
  from_use_case_id: number
  to_use_case_id: number
  detected_by_agent?: boolean | null
  rationale?: string | null
}

export interface Assumption {
  id?: number
  key: string
  label?: string | null
  value: number
  unit?: string | null
  category?: string | null
  description?: string | null
  source?: string | null
  source_note?: string | null
  confidence?: string | null
}

export interface PortfolioValue {
  assumptions: Record<string, number>
  per_use_case: Record<
    string,
    { hypothesized: number | null; realized: RealizedValue; readiness: string | null }
  >
  total_hypothesized_value: number
  total_hypothesized_buildable_value: number
  total_realized_value: number
}

export interface DashboardData {
  totals: {
    hypothesized_mm: number
    hypothesized_buildable_mm: number
    realized_mm: number
    capture_pct: number
  }
  waterfall_by_status: { status: Status; hyp_mm: number }[]
  lob_coverage: { lob: string; count: number; hyp: number; real: number; live: number }[]
  heatmap: { lob: string; phase: number; count: number }[]
  readiness_dist: Record<Readiness, number>
  status_dist: Record<Status, number>
  utilization: { label: string; uc_count: number; ingestion_status: string }[]
  realized_by_period: { period: string; amount: number }[]
}

export interface Recommendation {
  id: number
  title: string
  lob_name?: string | null
  readiness?: Readiness | null
  value_mm?: number | null
  effort_tshirt?: Effort | null
  opportunity_score?: number | null
  track?: 'do_now' | 'fast_follow' | 'prep' | null
  pending_prereqs?: string[] | null
  rationale?: string | null
  narrative?: string | null
  sub_vertical?: SubVertical | null
  phase?: number | null
}

export interface RecommendResponse {
  model?: string | null
  used_llm?: boolean
  fallback_note?: string | null
  recommendations: Recommendation[]
}

export interface CatalogRecommendResponse {
  recommendations: Recommendation[]
  total_candidates?: number
}

export interface SourceRecommendation {
  asset: {
    id: number
    source_category?: string | null
    source_system?: string | null
    module?: string | null
    vendor?: string | null
    ingestion_status?: IngestionStatus | null
    ingest_effort?: Effort | null
  }
  flips_to_ready: number
  value_unlocked_mm: number
  unlocks: { id: number; title: string; lob_id?: number | null; lob?: string | null; value_mm?: number | null }[]
  awaiting_prereqs: { id: number; title: string; lob?: string | null; value_mm?: number | null; pending_prereqs?: number }[]
  awaiting_prereqs_count: number
  sets_up: { id: number; title: string; lob?: string | null; value_mm?: number | null; still_needs?: number }[]
  sets_up_count: number
  lob_count: number
  ingest_cost_low?: number | null
  ingest_cost_high?: number | null
  ingest_cost_mid?: number | null
  score?: number
  rationale?: string | null
}

export interface SourceRecommendResponse {
  recommendations: SourceRecommendation[]
  total: number
  summary: { total_flips_available: number; total_value_unlockable_mm: number }
}

export interface UnlocksResponse {
  use_case_id: number
  newly_enabled: number[]
  becomes_ready: number[]
  awaiting_prerequisites: number[]
  unlocked: {
    id: number
    title: string
    lob_id?: number | null
    phase?: number | null
    value_mm?: number | null
    reason: 'enabled' | 'becomes_ready'
  }[]
  summary: {
    count: number
    awaiting_prerequisites_count: number
    lob_count: number
    hypothesized_value: number
  }
}

export interface NetworkResponse {
  focal: number
  prerequisites: number[]
  builds_upon: number[]
  shared_data: number[]
  titles: Record<string, string>
}

export interface DetectDependenciesResponse {
  use_case_id: number
  model?: string | null
  used_llm?: boolean
  requires: { data_asset_id: number; label: string; criticality?: Criticality | null; rationale?: string | null }[]
  enables: { to_use_case_id: number; label: string; rationale?: string | null }[]
}

export interface EstimateValueResponse {
  model?: string | null
  used_llm?: boolean
  fallback_note?: string | null
  value_model: HypothesizedValue
}

export interface RoadmapItem {
  use_case_id: number
  title: string
  lob_name?: string | null
  wave?: number | null
  horizon: 'now' | 'next' | 'later'
  value_mm?: number | null
  readiness?: Readiness | null
  opportunity?: number | null
}

export interface RoadmapResponse {
  horizons: { now: RoadmapItem[]; next: RoadmapItem[]; later: RoadmapItem[] }
  persisted: number
  waves: number
}

export interface FundingRequest {
  id: number
  data_asset_id: number
  requesting_lob_id?: number | null
  co_funding_lobs?: number[] | null
  combined_value?: number | null
  status?: string | null
  sponsor?: string | null
  cost_share_json?: Record<string, number> | null
  brief_md?: string | null
  created_at?: string | null
}

export interface JointCase {
  asset: {
    id: number
    source_category?: string | null
    module?: string | null
    vendor?: string | null
    ingestion_status?: IngestionStatus | null
    ingest_effort?: Effort | null
  }
  benefiting_lobs: { id: number; name: string; value_mm: number }[]
  lob_count: number
  unlocked: {
    id: number
    title: string
    lob_id?: number | null
    lob?: string | null
    value_mm?: number | null
    full_value_mm?: number | null
    criticality?: Criticality | null
    becomes_ready?: boolean
    awaiting_prereqs?: boolean
  }[]
  uc_count: number
  becomes_shovel_ready: number
  awaiting_prerequisites: number
  combined_value_mm: number
  ingest_cost_low?: number | null
  ingest_cost_high?: number | null
  ingest_cost_mid?: number | null
  delivery_cost_mid?: number | null
  build_cost?: number | null
  annual_run?: number | null
  program_cost_mid?: number | null
  cost_low?: number | null
  cost_high?: number | null
  cost_mid?: number | null
  roi_pct?: number | null
  roi_basis?: string | null
  payback_months?: number | null
  cost_share?: Record<string, number> | null
  impact_score?: number | null
  pitch?: string | null
}

export interface BlastNode {
  node_id: string
  type: 'asset' | 'usecase' | 'lob'
  id: number
  label: string
  sublabel?: string | null
  lob_id?: number | null
  ingestion_status?: IngestionStatus | null
  stage?: string | null
  downstream_uc_count?: number
}

export interface BlastRadiusResponse {
  focal: BlastNode | null
  rings: string[][]
  nodes: BlastNode[]
  summary: {
    downstream_uc_count: number
    lob_count: number
    lob_ids: number[]
    hypothesized_value: number
    becomes_shovel_ready: number
  }
}

export interface HealthResponse {
  status: string
  app?: string
  app_env?: string | null
  environment?: string
  db_connected?: boolean
  demo_mode?: boolean
  counts?: Record<string, number> | { error: string }
  error?: string
  detail?: string
}

export interface GenieAskResponse {
  configured: boolean
  answer: string
  sql?: string | null
  conversation_id?: string | null
}

export interface LiveStatusResponse {
  system_tables?: boolean | Record<string, boolean>
}

export interface LiveSyncResponse {
  system_tables?: Record<string, boolean> | boolean
  asset_changes?: { label: string; from: string; to: string }[]
  uc_changes?: { asset: string; from: string; to: string }[]
  notes?: string[]
  [key: string]: unknown
}

export interface DemoStatus {
  enabled: boolean
  db_connected?: boolean
  mode?: string
  error?: string
}

/**
 * A pending write, held server-side behind a single-use expiring token.
 *
 * Issued by `server/confirm.py:79-113` and re-readable via `GET /confirm/{token}`
 * without consuming it. `before`/`after` are display-only — the executor reads a
 * `payload` the client never sees, so a rendering change cannot alter what gets
 * written.
 */
export interface ConfirmCardData {
  token: string
  intent: string
  /** ISO-8601. The token stops being usable at this instant. */
  expires_at?: string | null
  summary?: string | null
  before?: Record<string, unknown>
  after?: Record<string, unknown>
  /** Set on a peeked token that has already been used (`GET /confirm/{token}`). */
  consumed_at?: string | null
  /** Server-computed `expires_at <= now()`, so the client need not trust its clock. */
  expired?: boolean
}

/** The result of consuming a token. Executors merge their own keys in. */
export interface ConfirmApplyResponse {
  ok: boolean
  intent: string
  elapsed_ms?: number
  [key: string]: unknown
}

export interface OnboardingImportResponse {
  apply: boolean
  applied?: number
  changes: {
    data_sources: { label: string; from?: string; to?: string }[]
    use_cases: { label: string; from?: string; to?: string }[]
    assumptions: { label: string; from?: string; to?: string }[]
  }
  errors: string[]
  summary?: { data_sources: number; use_cases: number; assumptions: number }
}

// ---------------------------------------------------------------------------
// Tier 3 Phase 3 — the read-only console surfaces.
//
// Coverage, What-if, Trend, Glossary, Artifacts and the Executive pack. Fields
// are optional wherever the route can omit them, which is more of them than the
// older types above: these endpoints degrade rather than fail when the discovery
// tables are absent (`snapshots.py` returns `sources_*`/`domains_*` as null when
// the pool is unavailable) and several return a bare `note` instead of rows.
// ---------------------------------------------------------------------------

/** A semantic data need. `GET /domains` — see components/DomainsTable.tsx. */
export interface Domain {
  id: number
  name: string
  label: string
  category?: string | null
  description?: string | null
  serving_asset_count?: number
  ready_asset_count?: number
  required_by_count?: number
  use_case_count?: number
  satisfied?: boolean
}

/** One need × one line of business. `state` is the whole point of the matrix. */
export interface CoverageCell {
  lob_id: number
  lob_name: string
  /** `covered` needed+landed · `gap` needed, not landed · `available` landed,
   *  nothing asks for it · `unused` not needed here. */
  state: 'covered' | 'gap' | 'available' | 'unused'
  use_case_count: number
  required_count: number
  value_mm: number
}

export interface CoverageRow {
  domain: Pick<Domain, 'id' | 'name' | 'label' | 'category'>
  satisfied: boolean
  serving_asset_count: number
  ready_asset_count: number
  /** Unmet in EVERY LOB that requires it — an acquisition decision, not backlog. */
  universal_gap: boolean
  cells: CoverageCell[]
}

export interface CoverageMatrixResponse {
  lobs: { id: number; name: string }[]
  rows: CoverageRow[]
  summary: {
    domains?: number
    universal_gaps?: number
    covered?: number
    gaps?: number
    available_unused?: number
    value_at_risk_mm?: number
  }
}

export interface DomainGap {
  domain: Pick<Domain, 'id' | 'name' | 'label' | 'category' | 'description'>
  serving_asset_count: number
  has_no_source: boolean
  blocked_use_case_count: number
  value_blocked_mm: number
  rationale: string
}

export interface DomainGapsResponse {
  gaps: DomainGap[]
  total?: number
  summary: { total_value_blocked_mm?: number; domains_with_no_source?: number }
}

/** An unlanded source, ranked by what landing it would unblock. */
export interface WhatIfCandidate {
  data_asset_id: number
  source: string
  module: string
  vendor?: string | null
  status?: string | null
  effort?: string | null
  cost_low: number
  cost_high: number
  use_cases_unblocked: number
  value_unblocked_mm: number
  /** `null` when the source is free or uncosted — division would be undefined. */
  value_per_cost?: number | null
}

export interface WhatIfCandidatesResponse {
  candidates: WhatIfCandidate[]
  evaluated?: number
  with_impact?: number
  note?: string | null
}

/** A use case the projection moves. `still_pending` names what it waits on. */
export interface WhatIfUseCase {
  id: number
  title: string
  lob?: string | null
  effort?: string | null
  value_mm: number
  still_pending?: string[]
}

export interface WhatIfProjection {
  sources: {
    id: number
    source?: string | null
    module: string
    vendor?: string | null
    current_status?: string | null
    effort?: string | null
  }[]
  unlocked: WhatIfUseCase[]
  unlocked_count: number
  annual_value_mm: number
  value_by_lob: Record<string, number>
  /** Data gap closes, sequencing does not — reported apart so neither is oversold. */
  data_complete_awaiting_prerequisites: WhatIfUseCase[]
  awaiting_count: number
  awaiting_value_mm: number
  cost: {
    sources_low?: number
    sources_high?: number
    delivery_mid?: number
    total_mid?: number
  }
  payback_months?: number | null
  baseline?: { shovel_ready?: number }
  projected?: { shovel_ready?: number }
  note?: string | null
}

export interface WhatIfComparison {
  options: {
    option: number
    sources: string[]
    unlocked_count: number
    annual_value_mm: number
    total_cost: number
    payback_months?: number | null
    value_per_cost?: number | null
    top_unlocked?: string[]
  }[]
  note?: string | null
}

/** The metrics a snapshot records. Shared by a stored point and `/current`. */
export interface SnapshotMetrics {
  total_value_mm?: number | null
  buildable_value_mm?: number | null
  realized_value_mm?: number | null
  shovel_ready?: number | null
  nearly_ready?: number | null
  awaiting_prereqs?: number | null
  blocked?: number | null
  /** Null rather than 0 when the discovery tables are unreadable. */
  sources_total?: number | null
  sources_ready?: number | null
  domains_total?: number | null
  domains_satisfied?: number | null
  use_cases_total?: number | null
  use_cases_live?: number | null
}

export interface Snapshot extends SnapshotMetrics {
  id: number
  /** ISO-8601. Every point is a real event, which is why `reason` exists. */
  captured_at: string
  reason?: string | null
  detail?: string | null
  captured_by?: string | null
}

export interface SnapshotsResponse {
  snapshots: Snapshot[]
  count?: number
  first_captured_at?: string | null
  latest_captured_at?: string | null
  change_since_first?: SnapshotMetrics
  latest?: Snapshot
  /** Sent instead of rows when there is nothing captured yet. */
  note?: string | null
}

export interface CurrentSnapshotResponse {
  metrics: SnapshotMetrics
  stored: boolean
}

/** A business term. `origin_kind` splits curated from need-derived. */
export interface GlossaryTerm {
  id?: number | null
  term: string
  definition?: string | null
  domain_label?: string | null
  lob_name?: string | null
  synonyms?: string[]
  source_systems?: string[]
  owner?: string | null
  origin?: string | null
  origin_kind: 'curated' | 'derived'
  category?: string | null
  landed_sources?: number | null
  use_case_count?: number | null
}

export interface GlossaryResponse {
  terms: GlossaryTerm[]
  summary: { total?: number; curated?: number; derived?: number }
}

/** Something already built on the platform, claimed by a use case or not. */
export interface Artifact {
  id: number
  artifact_type: string
  artifact_id: string
  name: string
  owner?: string | null
  last_run?: string | null
  run_count_30d?: number | null
  status?: string | null
  use_case_id?: number | null
  use_case_title?: string | null
  lob_name?: string | null
  is_present?: boolean
}

export interface ArtifactsResponse {
  artifacts: Artifact[]
  summary: { total?: number; unattributed?: number; types?: number }
  by_type?: { artifact_type: string; n: number; unattributed: number }[]
}

export interface UnattributedArtifactsResponse {
  artifacts: Artifact[]
  summary: { total?: number; active_unclaimed?: number; inactive_unclaimed?: number }
  interpretation?: string | null
}

/** A use-case row in the executive pack. Flatter than `UseCase`: the pack is a
 *  rendering, so the server has already resolved lob and value to scalars. */
export interface ExecutivePackUseCase {
  id: number
  title: string
  lob?: string | null
  readiness?: string | null
  confidence?: string | null
  confidence_score?: number | null
  value_mm?: number | null
  pending_prereqs?: { id?: number; title: string }[]
  pending_domains?: { id?: number; label?: string | null; name?: string | null }[]
}

export interface ExecutivePack {
  /** ISO-8601. Shown because a pack pasted into a deck needs a date on it. */
  generated_at?: string | null
  company?: { company_name?: string | null } | null
  metrics: {
    use_cases_total?: number | null
    shovel_ready?: number | null
    blocked?: number | null
    total_value_mm?: number | null
    buildable_value_mm?: number | null
    realized_value_mm?: number | null
  }
  top_buildable_use_cases?: ExecutivePackUseCase[]
  top_blocked_or_awaiting_use_cases?: ExecutivePackUseCase[]
  assumptions: {
    total?: number
    /** Customer-researched. The gap between this and `total` is the caveat. */
    calibrated?: number
    generic?: number
  }
  whatif?: {
    candidates?: {
      source?: string | null
      module?: string | null
      use_cases_unblocked?: number | null
      value_unblocked_mm?: number | null
      value_per_cost?: number | null
    }[]
    note?: string | null
  }
  next_actions?: string[]
}

// ---------------------------------------------------------------------------
// Tier 3 Phase 4 — knowledge base
//
// Shapes mirror `server/routes/knowledge.py`. Nearly everything is optional
// because the list and the read endpoints return DIFFERENT projections of the
// same row: `build_search_sql` selects a fixed column set plus `rank`/`excerpt`
// only when searching, while `GET /articles/{slug}` returns `a.*` plus four
// resolved collections. One type per row with optional members beats two types
// that drift, since the list feeds straight into the article view's cache.
// ---------------------------------------------------------------------------

/** A folder in the KB tree. `path` is the addressable form; filtering is by subtree. */
export interface KbFolder {
  id: number
  name: string
  parent_id?: number | null
  path: string
  sort_order?: number | null
  article_count?: number | null
}

export interface KbTreeResponse {
  folders: KbFolder[]
  unfiled_count: number
  totals: { articles?: number; published?: number; generated?: number }
}

/** What an article is attached to. `label` is server-resolved and may be null
 *  when the entity is out of scope — render the id rather than an empty cell. */
export interface KbLink {
  id: number
  entity_type: string
  entity_id: number
  relation: string
  label?: string | null
  created_by?: string | null
  created_at?: string | null
}

export interface KbAttachment {
  id: number
  filename: string
  mime_type?: string | null
  size_bytes?: number | null
  storage?: string | null
  checksum?: string | null
  uploaded_by?: string | null
  uploaded_at?: string | null
}

export interface KbVersion {
  version: number
  title?: string | null
  change_note?: string | null
  edited_by?: string | null
  edited_at?: string | null
}

/**
 * A resolved `[[wiki link]]`.
 *
 * `exists` is the whole point: the renderer cannot know which slugs resolve —
 * only the server does — so a reference that points at nothing is marked here
 * rather than rendered as a live link to an error page.
 */
export interface KbReference {
  slug: string
  title?: string | null
  exists: boolean
}

export interface KbArticle {
  id: number
  title: string
  slug: string
  summary?: string | null
  body_md?: string | null
  tags?: string[] | null
  status: string
  version?: number | null
  folder_id?: number | null
  folder_name?: string | null
  folder_path?: string | null
  /** Set when an agent wrote it. Drives the "review before sharing" banner. */
  generated_by?: string | null
  created_by?: string | null
  updated_by?: string | null
  created_at?: string | null
  updated_at?: string | null
  /** Counts, on list rows only. */
  attachment_count?: number | null
  link_count?: number | null
  /** Search-only: relevance rank and the `<<match>>`-marked body excerpt. */
  rank?: number | null
  excerpt?: string | null
  /** Present on `GET /articles/{slug}` only. */
  links?: KbLink[]
  attachments?: KbAttachment[]
  versions?: KbVersion[]
  references?: KbReference[]
}

export interface KbArticleListResponse {
  items: KbArticle[]
  count: number
  limit: number
  offset: number
}

/** `POST /articles` and `PUT /articles/{slug}` both return a thin row, not the
 *  full article — the caller refetches by slug rather than trusting this. */
export interface KbArticleWriteResponse {
  id: number
  title: string
  slug: string
  status: string
  version: number
  content_changed?: boolean
  created_at?: string | null
  updated_at?: string | null
}

/** Upload response. `already_existed` is a same-checksum re-upload, which the
 *  server answers with the existing row instead of storing a second copy. */
export interface KbAttachmentUploadResponse extends Partial<KbAttachment> {
  id: number
  filename: string
  already_existed?: boolean
  note?: string | null
}

// ---------------------------------------------------------------------------
// Tier 3 Phase 5 — the curation-write surfaces.
//
// These three views WRITE, which is what separates them from the Phase-3 ports
// above, and it is why the shapes below carry more than rows. Each write answers
// with a count, a set of notes, or a distribution the user is meant to judge:
// `/taxonomy/classify` reports `warnings` per batch, `/rules/test` reports what
// matched NOTHING, and `/rules/seed` reports how many of the seeds were new. A
// type that modelled only the rows would drop exactly the part the user acts on.
// ---------------------------------------------------------------------------

/**
 * One raw source label and what the normalizer resolved it to.
 *
 * `confidence` is the field the review queue is ordered around: `low` and a null
 * `canonical` are what `?needs_review=true` selects for (`ingestion.py:898-912`).
 * `is_user_edited` is why a correction is permanent — a later sweep skips the row.
 */
export interface SourceAlias {
  id: number
  raw: string
  canonical?: string | null
  mapped_by?: string | null
  confidence?: string | null
  is_user_edited?: boolean | null
  updated_at?: string | null
}

/** The three taxonomy dimensions, as the server names them. */
export type TaxonomyDimension = 'integration_pattern' | 'criticality' | 'vendor_type'

/**
 * How much of the catalog is labelled. `GET /taxonomy/coverage`.
 *
 * `pct` is on the response rather than derived here for the reason the route
 * gives: a distribution over 12 of 146 assets looks authoritative and means
 * nothing, so the view must be able to say what the distribution is over.
 */
export interface TaxonomyCoverage {
  total_assets: number
  fully_classified: number
  by_dimension: Record<string, { classified: number; pct: number }>
}

/** `GET /taxonomy` — current (effective_to IS NULL) rows plus a rollup. */
export interface TaxonomyResponse {
  classifications: {
    data_asset_id: number
    dimension: string
    value: string
    source?: string | null
    confidence?: number | null
    ai_reasoning?: string | null
    source_category?: string | null
    module?: string | null
  }[]
  /** dimension -> value -> count. Empty inner objects are real: an unclassified
   *  dimension is present with no values, and the view must not render a card. */
  distribution: Record<string, Record<string, number>>
  total: number
}

/**
 * `POST /taxonomy/classify`. Rate-limited `generate` (burst 4 / 12 per minute).
 *
 * `warnings` is load-bearing and de-duplicated server-side: a run that wrote
 * nothing because the serving endpoint was unreachable must not read as "every
 * asset was already classified", and `detail` carries that distinction.
 */
export interface ClassifyResponse {
  ok: boolean
  model?: string | null
  used_llm?: boolean
  assets_considered?: number
  values_written?: number
  batches?: number
  warnings?: string[]
  /** Present only on the nothing-to-do path. */
  detail?: string | null
}

/** A naming-convention rule. First match wins per dimension, hence `priority`. */
export interface ClassificationRule {
  id: number
  dimension: string
  field: string
  match_type: string
  pattern: string
  value?: string | null
  case_sensitive?: boolean
  priority: number
  is_active?: boolean
  notes?: string | null
  origin?: string | null
}

/** What a new or edited rule submits. Mirrors `RuleIn` in `inventory.py`. */
export interface RuleInput {
  dimension: string
  field: string
  match_type: string
  pattern: string
  value?: string | null
  case_sensitive?: boolean
  priority?: number
  is_active?: boolean
  notes?: string | null
}

/**
 * `GET /rules`. The vocabulary travels with the rows deliberately.
 *
 * `dimension`, `field` and `match_type` are closed sets defined in
 * `server/rules.py`, and hardcoding them in the SPA would mean a server-side
 * addition silently 422s from a form that cannot offer it. So the selects are
 * built from this.
 */
export interface RulesResponse {
  rules: ClassificationRule[]
  vocabulary: { dimensions: string[]; fields: string[]; match_types: string[] }
}

/** `POST /rules/seed`. Idempotent, so `created` can legitimately be 0. */
export interface RuleSeedResponse {
  created: number
  total_seeds: number
}

/**
 * `POST /rules/test`. Rate-limited `generate` because it queries the warehouse.
 *
 * `sample_source` is the honesty field: `discovered_tables` means the rules were
 * tried against the real estate, `supplied` means they were tried against rows
 * the caller passed. A summary that does not say which is not interpretable.
 */
export interface RuleTestResponse {
  sample_source: string
  rules_applied: number
  results: { sample: Record<string, unknown>; result: Record<string, unknown> }[]
  summary: {
    total: number
    ignored: number
    /** The number that says whether the rules actually work. */
    unmatched: number
    by_dimension: Record<string, Record<string, number>>
  }
}

// ---------------------------------------------------------------------------
// Tier 3 Phase 9 — the onboarding wizard.
//
// Shapes mirror `server/routes/setup.py` and `server/routes/ingestion.py`. Almost
// everything is optional, and that is not defensive habit: both routers DEGRADE
// rather than fail. `GET /setup/status` skips downstream probes when an upstream
// one is red and emits the skipped checks with `ok: false` and no `grants`;
// `GET /ingestion/summary` returns `{configured: false}` alone when ATLAS_CATALOG
// is unset, and `{configured: true, available: false, error}` when the catalog is
// set but the discovery tables are missing. The wizard has to render all three
// states, so the type must be able to express all three.
// ---------------------------------------------------------------------------

/** One dependency probe. `grants` is the SQL a metastore admin runs to fix it. */
export interface SetupCheck {
  name: string
  label: string
  ok: boolean
  detail?: string
  /** Required checks decide `ready`; optional ones are unconfigured features. */
  required?: boolean
  fix?: string | null
  grants?: string[]
}

export interface SetupStatusResponse {
  /** Reflects only the REQUIRED checks — an install is not broken for lacking
   *  a feature the customer never asked for (`setup.py:16-22`). */
  ready: boolean
  checks?: SetupCheck[]
  summary?: {
    total?: number
    passing?: number
    required_failing?: number
    optional_failing?: number
  }
  service_principal?: string
  grants_sql?: string[]
  environment?: string
  /** The first required failure's fix — the root cause, not a symptom. */
  next_action?: string | null
}

export interface SetupGrantsResponse {
  service_principal?: string
  sql: string
}

/**
 * Discovery inventory rollup.
 *
 * The three states this has to carry: not configured (`configured: false`),
 * configured but unreachable (`available: false` plus `error`), and populated.
 * The wizard keys step 2A's controls off exactly that distinction — the bootstrap
 * button has to be reachable from the middle state or a first-run install cannot
 * get to the upload controls at all.
 */
export interface IngestionSummaryResponse {
  configured: boolean
  available?: boolean
  error?: string | null
  catalog?: string | null
  schema?: string | null
  schemas?: number
  tables?: number
  enriched_tables?: number
  workspaces?: number
  canonicals?: number
  serving_endpoint?: string | null
}

/** What the extractor ZIP contains, rendered before anyone clicks download. */
export interface ExtractorInfoResponse {
  available: boolean
  files?: string[]
  total_bytes?: number
  requires?: string[]
  reads?: string
  produces?: string[]
}

export interface BootstrapResponse {
  ok?: boolean
  catalog?: string | null
  schema?: string | null
  tables?: string[]
}

/** A CSV ingest. `rows_in_file` vs `rows_written` is the number that matters —
 *  a file whose rows were all skipped for missing columns reports the gap. */
export interface InventoryUploadResponse {
  ok?: boolean
  run_id?: number | null
  rows_in_file?: number
  rows_written?: number
}

export interface EnrichSchemasResponse {
  ok?: boolean
  run_id?: number | null
  schemas_enriched_total?: number
}

/** `staged_errors` is a PARTIAL run, not a failure: `failOnError=>false` lets
 *  individual `ai_query` rows fail while the rest of the batch succeeds. */
export interface EnrichTablesResponse {
  ok?: boolean
  run_id?: number | null
  status?: string
  tables_enriched_total?: number
  staged_rows?: number
  staged_ok?: number
  staged_errors?: number
}

export interface CanonicalizeResponse {
  ok?: boolean
  run_id?: number | null
  distinct_labels?: number
  newly_resolved?: number
  exact?: number
  normalized?: number
  llm?: number
  other?: number
  skipped_manual?: number
}

/** Attribution — the join that turns raw inventory into portfolio signal.
 *  `unmatched_canonicals` are discovered source systems the catalog has no
 *  module for, which is a prompt to add one rather than an error. */
export interface AttributeResponse {
  ok?: boolean
  run_id?: number | null
  canonicals_discovered?: number
  assets_matched?: number
  assets_advanced?: number
  stale_assets_reset?: number
  advanced?: { id?: number; label: string; from?: string; to?: string }[]
  unmatched_canonicals?: string[]
}
