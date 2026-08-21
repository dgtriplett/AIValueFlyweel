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
