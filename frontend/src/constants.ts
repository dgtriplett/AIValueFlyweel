// Domain vocabulary and the colours that encode it.
//
// The labels here are what the customer reads, so they are the strings the tests
// assert. The colours that are NOT Tailwind tokens are deliberate: several are
// used as inline `style` values (SVG fills, alpha-composited badge backgrounds)
// where a utility class cannot reach, so they have to be literal hex.

import type {
  Effort,
  IngestionStatus,
  Readiness,
  Status,
  SubVertical,
} from './types'

export const LOBS = [
  'Generation',
  'Transmission',
  'Distribution',
  'Customer',
  'Regulatory',
  'Corporate Services',
] as const

/** Regulatory and Corporate Services have no Tailwind token — hence literal hex. */
export const LOB_COLORS: Record<string, string> = {
  Generation: '#FF3621',
  Transmission: '#2272B4',
  Distribution: '#00A972',
  Customer: '#FFAB00',
  Regulatory: '#8B5CF6',
  'Corporate Services': '#06B6D4',
}

// Phase is DERIVED from prerequisite depth and is deliberately not shown to
// customers (it reads as a commitment the data does not support). The labels and
// colours stay because the dashboards heatmap still groups by it internally.
export const PHASE_LABELS: Record<number, string> = {
  0: 'Pre-Foundation',
  1: 'Operational Transparency',
  2: 'Proactive Optimization',
  3: 'Mitigate Risk & Unplanned O&M',
}
export const PHASE_COLORS: Record<number, string> = {
  0: '#618794',
  1: '#2272B4',
  2: '#00A972',
  3: '#FFAB00',
  4: '#98102A',
}
export const KANBAN_PHASES = [1, 2, 3]

export const STATUSES: Status[] = [
  'not_started',
  'scoping',
  'in_progress',
  'live',
  'value_realized',
]
export const STATUS_LABELS: Record<Status, string> = {
  not_started: 'Not started',
  scoping: 'Scoping',
  in_progress: 'In progress',
  live: 'Live',
  value_realized: 'Value realized',
}
export const STATUS_COLORS: Record<Status, string> = {
  not_started: '#618794',
  scoping: '#2272B4',
  in_progress: '#FFAB00',
  live: '#00A972',
  value_realized: '#42BA91',
}

export const READINESS: Readiness[] = [
  'shovel_ready',
  'awaiting_prerequisites',
  'nearly_ready',
  'blocked',
]
export const READINESS_LABELS: Record<Readiness, string> = {
  shovel_ready: 'Shovel-ready',
  awaiting_prerequisites: 'Awaiting prerequisites',
  nearly_ready: 'Nearly ready',
  blocked: 'Blocked',
}
export const READINESS_COLORS: Record<Readiness, string> = {
  shovel_ready: '#00A972',
  awaiting_prerequisites: '#7C6BFF',
  nearly_ready: '#FFAB00',
  blocked: '#98102A',
}

export const INGESTION: IngestionStatus[] = [
  'not_started',
  'landed',
  'curated',
  'governed',
]
export const INGESTION_LABELS: Record<IngestionStatus, string> = {
  not_started: 'Not started',
  landed: 'Landed',
  curated: 'Curated',
  governed: 'Governed',
}
export const INGESTION_COLORS: Record<IngestionStatus, string> = {
  not_started: '#618794',
  landed: '#FFAB00',
  curated: '#2272B4',
  governed: '#00A972',
}

export const SUB_VERTICALS: SubVertical[] = [
  'fossil',
  'hydro',
  'renewables',
  'nuclear',
  'cross',
]
export const SUB_VERTICAL_LABELS: Record<SubVertical, string> = {
  fossil: 'Fossil',
  hydro: 'Hydro',
  renewables: 'Renewables',
  nuclear: 'Nuclear',
  cross: 'All (cross-cutting)',
}
export function subVerticalLabel(value?: SubVertical | null): string {
  return value ? (SUB_VERTICAL_LABELS[value] ?? value) : '—'
}

export const EFFORTS: Effort[] = ['S', 'M', 'L', 'XL']

export const SOURCE_CATEGORIES = [
  'ERP',
  'EAM/APM (Asset & Work Mgmt)',
  'Data Historian',
  'SCADA/EMS',
  'ADMS/OMS',
  'DERMS',
  'GIS',
  'PMU/Synchrophasor',
  'Grid Sensors',
  'Meter/AMI (MDM)',
  'Customer/Billing (CIS)',
  'Market/ISO Feed',
  'Weather',
  'Emissions Monitoring (CEMS)',
  'Lab (LIMS)',
  'Radiation/Dosimetry',
  'Fuel Management',
  'Document/Content Mgmt',
  'Safety/EHS',
  'Supply Chain',
]

/** Categories absent here fall through to an empty list — vendor is free text. */
export const VENDORS_BY_CATEGORY: Record<string, string[]> = {
  ERP: ['SAP (ECC)', 'SAP S/4HANA', 'Oracle Fusion', 'Oracle EBS', 'IFS', 'Infor', 'MS Dynamics'],
  'EAM/APM (Asset & Work Mgmt)': ['IBM Maximo', 'SAP EAM', 'GE APM', 'AVEVA APM'],
  'Data Historian': ['OSIsoft PI', 'AVEVA PI', 'GE Proficy', 'Honeywell PHD', 'Aspen IP.21'],
  GIS: ['ESRI', 'GE Smallworld', 'Hexagon'],
  'ADMS/OMS': ['GE', 'Schneider', 'Oracle', 'Survalent'],
  'SCADA/EMS': ['GE', 'Hitachi-OSI', 'Siemens', 'Schneider'],
  'Lab (LIMS)': ['LabWare', 'STARLIMS', 'Thermo'],
  'Radiation/Dosimetry': ['Mirion', 'Landauer'],
  'Meter/AMI (MDM)': ['Itron', 'Landis+Gyr', 'Oracle'],
  Weather: ['NOAA', 'DTN', 'Vaisala'],
  'Market/ISO Feed': ['PJM', 'ERCOT', 'MISO', 'CAISO', 'ISO-NE', 'NYISO', 'SPP'],
  'Customer/Billing (CIS)': ['Oracle', 'SAP IS-U', 'Salesforce'],
}

/**
 * Money in $M — the unit almost every value field in this app carries.
 * Rolls up to $B and down to $K so a portfolio total and a single small use case
 * are both readable without a unit toggle.
 */
export function fmtMoney(value?: number | null): string {
  if (value == null) return '—'
  if (value >= 1e3) return `$${(value / 1e3).toFixed(2)}B`
  if (value >= 1) return `$${value.toFixed(1)}M`
  return `$${(value * 1e3).toFixed(0)}K`
}

/** Raw dollars — ingestion costs come off the API unscaled. */
export function fmtDollars(value: number): string {
  return value >= 1e6 ? `$${(value / 1e6).toFixed(1)}M` : `$${Math.round(value / 1e3)}K`
}

/** Raw dollars, nullable, down to the dollar — the joint-funding cost split. */
export function fmtDollarsExact(value?: number | null): string {
  if (value == null) return '—'
  if (value >= 1e6) return `$${(value / 1e6).toFixed(1)}M`
  if (value >= 1e3) return `$${(value / 1e3).toFixed(0)}K`
  return `$${Math.round(value)}`
}

/** Shared <select> chrome. Repeated verbatim across every filter and form. */
export const SELECT_CLASS =
  'bg-navy-700 border border-navy-600 rounded text-sm px-2.5 py-1.5 ' +
  'text-navy-300 focus:outline-none focus:border-info'

/** Native <option> needs explicit colours or it inherits the OS light palette. */
export const OPTION_STYLE = { color: '#E6EDF3', background: '#0B2026' }

export const EULA_KEY = 'vf_eula_accepted_v1'
