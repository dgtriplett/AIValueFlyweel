// Joint funding: the argument for paying for one ingestion once, across LOBs.
//
// Two screens in one module — the ranked list and the business case for a single
// asset — because the transition between them is a local `selected` state, not a
// route. The whole point of the view is the cost-share table, which is seeded
// proportionally to the value each LOB receives and then edited live in the room.

import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Coins, FileText, HandCoins, Sparkles, Users, X } from 'lucide-react'

import { api } from '../api'
import { LOB_COLORS, fmtDollarsExact, fmtMoney } from '../constants'
import type { JointCase, Lob } from '../types'

export default function JointFundingView({ lobs = [] }: { lobs?: Lob[] }) {
  const [selected, setSelected] = useState<number | null>(null)

  const opportunities = useQuery({
    queryKey: ['joint-opportunities'],
    queryFn: api.jointOpportunities,
  })
  const requests = useQuery({ queryKey: ['funding-requests'], queryFn: api.fundingRequests })

  const lobName = (lobId?: number | null) =>
    lobId != null ? (lobs.find((lob) => lob.id === lobId)?.name ?? '—') : '—'

  if (selected != null) {
    return <JointCaseDetail assetId={selected} lobs={lobs} onBack={() => setSelected(null)} />
  }

  const cases = opportunities.data?.opportunities ?? []

  return (
    <div className="space-y-4">
      <div className="card border-l-4 border-l-lava">
        <div className="flex items-center gap-2">
          <HandCoins className="w-5 h-5 text-lava" />
          <h2 className="font-bold text-white">Joint-Funding Business-Case Builder</h2>
        </div>
        <p className="text-sm text-navy-400 mt-1">
          Data sources that serve multiple lines of business are the strongest co-funding plays — one
          ingestion investment, value across LOBs. Opportunities are ranked by real impact (combined
          value × LOBs served × use cases unlocked). Open one to build the funding case.
        </p>
      </div>

      {opportunities.isLoading ? (
        <div className="text-navy-400">Ranking opportunities…</div>
      ) : null}
      {opportunities.isError ? (
        <div className="card text-warning">Couldn't load opportunities. Please retry.</div>
      ) : null}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2 space-y-2">
          <h3 className="text-sm font-semibold text-white">{`Ranked opportunities (${cases.length})`}</h3>
          {cases.map((jointCase, index) => (
            <button
              key={jointCase.asset.id}
              onClick={() => setSelected(jointCase.asset.id)}
              className="w-full text-left card hover:border-navy-500 transition-colors"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex items-start gap-2">
                  <span className="text-lava-300 font-bold">{`#${index + 1}`}</span>
                  <div>
                    <div className="font-medium text-white">
                      {`${jointCase.asset.source_category} · ${jointCase.asset.module}`}
                    </div>
                    <div className="text-xs text-navy-400 mt-0.5">{jointCase.pitch}</div>
                  </div>
                </div>
                <div className="text-right shrink-0">
                  <div className="text-lava-300 font-bold">
                    {fmtMoney(jointCase.combined_value_mm)}
                  </div>
                  <div className="text-[10px] text-navy-500">combined/yr</div>
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-1.5 mt-2">
                {jointCase.benefiting_lobs.map((lob) => {
                  const color = LOB_COLORS[lob.name] ?? '#2A4A56'
                  return (
                    <span
                      key={lob.id}
                      className="text-xs px-2 py-0.5 rounded-full border"
                      style={{
                        color: LOB_COLORS[lob.name] ?? '#90A5B1',
                        borderColor: `${color}66`,
                        background: `${color}22`,
                      }}
                    >
                      {lob.name} {fmtMoney(lob.value_mm)}
                    </span>
                  )
                })}
                <span className="text-xs text-info flex items-center gap-1">
                  <Users className="w-3 h-3" /> {`${jointCase.lob_count} LOBs`}
                </span>
                {jointCase.becomes_shovel_ready > 0 ? (
                  <span className="text-xs text-success">
                    {`${jointCase.becomes_shovel_ready} become shovel-ready`}
                  </span>
                ) : null}
              </div>
            </button>
          ))}
          {!opportunities.isLoading && cases.length === 0 ? (
            <div className="text-sm text-navy-500">
              No assets benefiting ≥2 LOBs yet. Assign benefiting LOBs to data sources (Data Assets
              tab) to surface joint-funding plays.
            </div>
          ) : null}
        </div>

        <div className="space-y-2">
          <h3 className="text-sm font-semibold text-white">
            {`Funding requests (${(requests.data ?? []).length})`}
          </h3>
          {(requests.data ?? []).map((request) => (
            <div key={request.id} className="card">
              <div className="flex items-center justify-between">
                <div className="text-sm text-white">{`Request #${request.id}`}</div>
                <RequestStatusBadge status={request.status} />
              </div>
              <div className="text-xs text-navy-400 mt-1">{`Sponsor: ${request.sponsor ?? '—'}`}</div>
              <div className="text-xs text-navy-400">
                {`Requesting ${lobName(request.requesting_lob_id)} · ` +
                  `+${(request.co_funding_lobs ?? []).length} co-funders`}
              </div>
              <div className="text-sm text-lava-300 font-semibold mt-1">
                {`${fmtDollarsExact(request.combined_value)} combined`}
              </div>
            </div>
          ))}
          {(requests.data ?? []).length === 0 ? (
            <div className="text-sm text-navy-500">
              No funding requests yet — open an opportunity and start one.
            </div>
          ) : null}
        </div>
      </div>
    </div>
  )
}

/** The badge text is the raw status string — the server owns this vocabulary. */
function RequestStatusBadge({ status }: { status?: string | null }) {
  const className =
    status === 'funded'
      ? 'badge-low'
      : status === 'committed'
        ? 'badge-medium'
        : status === 'declined'
          ? 'badge-critical'
          : 'badge-muted'
  return <span className={className}>{status}</span>
}

function JointCaseDetail({
  assetId,
  lobs,
  onBack,
}: {
  assetId: number
  lobs: Lob[]
  onBack: () => void
}) {
  const queryClient = useQueryClient()
  const opportunity = useQuery({
    queryKey: ['joint-opp', assetId],
    queryFn: () => api.jointOpportunity(assetId),
  })
  const [costShare, setCostShare] = useState<Record<string, number>>({})
  const [sponsor, setSponsor] = useState('')
  const [briefOpen, setBriefOpen] = useState(false)

  const lobName = (lobId?: number | null) =>
    lobId != null ? (lobs.find((lob) => lob.id === lobId)?.name ?? '—') : '—'

  // Seeded from the server's proportional suggestion; edits then live locally so
  // a refetch does not silently undo what someone typed in front of the room.
  useEffect(() => {
    if (opportunity.data?.cost_share) setCostShare(opportunity.data.cost_share)
  }, [opportunity.data])

  const brief = useMutation({ mutationFn: () => api.jointBrief(assetId) })

  const createRequest = useMutation({
    mutationFn: () => {
      const jointCase = opportunity.data as JointCase
      const lobIds = jointCase.benefiting_lobs.map((lob) => lob.id)
      return api.createJointRequest({
        data_asset_id: assetId,
        requesting_lob_id: lobIds[0] ?? null,
        co_funding_lobs: lobIds.slice(1),
        combined_value: Math.round(jointCase.combined_value_mm * 1e6),
        sponsor: sponsor || undefined,
        cost_share: costShare,
        brief_md: brief.data?.brief_md,
        status: 'proposed',
      })
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['funding-requests'] }),
  })

  if (opportunity.isLoading || !opportunity.data) {
    return <div className="text-navy-400">Building the business case…</div>
  }

  const jointCase = opportunity.data

  const unlockedByLob: Record<string, JointCase['unlocked']> = {}
  for (const unlocked of jointCase.unlocked) {
    const key = unlocked.lob ?? '—'
    ;(unlockedByLob[key] = unlockedByLob[key] || []).push(unlocked)
  }

  const totalPledged = Object.values(costShare).reduce((sum, value) => sum + (value || 0), 0)

  return (
    <div className="space-y-4">
      <button className="btn-secondary text-sm" onClick={onBack}>
        <ArrowLeft className="w-4 h-4" /> Back to opportunities
      </button>

      <div className="card border-l-4 border-l-lava">
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div>
            <h2 className="font-bold text-lg text-white">
              {`${jointCase.asset.source_category} · ${jointCase.asset.module}`}
            </h2>
            <div className="text-sm text-navy-400">
              Currently <span className="text-white">{jointCase.asset.ingestion_status}</span>
              {` · ingestion effort ${jointCase.asset.ingest_effort ?? 'M'} · ` +
                `serves ${jointCase.lob_count} LOBs`}
            </div>
          </div>
          <div className="flex gap-2">
            <button
              className="btn-secondary text-sm"
              onClick={() => {
                setBriefOpen(true)
                if (!brief.data) brief.mutate()
              }}
            >
              <FileText className="w-4 h-4" /> AI funding brief
            </button>
            <button
              className="btn-primary text-sm"
              disabled={createRequest.isPending}
              onClick={() => createRequest.mutate()}
            >
              <HandCoins className="w-4 h-4" />{' '}
              {createRequest.isSuccess ? 'Request created ✓' : 'Create funding request'}
            </button>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <CaseStat
          label="Annual value unlocked"
          value={fmtMoney(jointCase.combined_value_mm)}
          accent="#FFAB00"
          sub="attributed / yr"
        />
        <CaseStat
          label="Cost"
          value={fmtDollarsExact(jointCase.build_cost)}
          accent="#2272B4"
          sub={`build + ${fmtDollarsExact(jointCase.annual_run)}/yr run`}
        />
        <CaseStat
          label="ROI (3-yr TCO)"
          value={jointCase.roi_pct != null ? `${jointCase.roi_pct}%` : '—'}
          accent="#00A972"
          sub={
            jointCase.payback_months != null ? `payback ${jointCase.payback_months} mo` : undefined
          }
        />
        <CaseStat
          label="Become shovel-ready"
          value={String(jointCase.becomes_shovel_ready)}
          accent="#8B5CF6"
          sub={`of ${jointCase.uc_count} unlocked`}
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="card">
          <h3 className="text-sm font-semibold text-white mb-2 flex items-center gap-1.5">
            <Users className="w-4 h-4 text-lava-300" /> What it unlocks, by LOB
          </h3>
          {jointCase.benefiting_lobs.map((lob) => (
            <div key={lob.id} className="mb-2">
              <div className="flex items-center justify-between text-sm">
                <span className="font-medium" style={{ color: LOB_COLORS[lob.name] ?? '#C4CCD6' }}>
                  {lob.name}
                </span>
                <span className="text-lava-300">{`${fmtMoney(lob.value_mm)}/yr`}</span>
              </div>
              {(unlockedByLob[lob.name] ?? []).map((unlocked) => (
                <div
                  key={unlocked.id}
                  className="text-xs text-navy-400 flex justify-between pl-3 py-0.5"
                >
                  <span>
                    {unlocked.title}{' '}
                    {unlocked.becomes_ready ? (
                      <span className="text-success">· becomes shovel-ready</span>
                    ) : null}
                  </span>
                  <span className="text-navy-300">{fmtMoney(unlocked.value_mm)}</span>
                </div>
              ))}
            </div>
          ))}
        </div>

        <div className="card">
          <h3 className="text-sm font-semibold text-white mb-1 flex items-center gap-1.5">
            <Coins className="w-4 h-4 text-warning" /> Proposed cost-share split
          </h3>
          <p className="text-xs text-navy-500 mb-3">
            {'Auto-suggested proportional to the value each LOB receives (editable). ' +
              `Total should cover the ~${fmtDollarsExact(jointCase.cost_mid)} mid ingestion cost.`}
          </p>
          {jointCase.benefiting_lobs.map((lob) => (
            <div key={lob.id} className="flex items-center justify-between gap-2 mb-1.5">
              <span className="text-sm flex-1" style={{ color: LOB_COLORS[lob.name] ?? '#C4CCD6' }}>
                {lob.name}
              </span>
              <span className="text-xs text-navy-500">
                {`${
                  jointCase.combined_value_mm
                    ? Math.round((lob.value_mm / jointCase.combined_value_mm) * 100)
                    : 0
                }% of value`}
              </span>
              <div className="flex items-center gap-1">
                <span className="text-xs text-navy-500">$</span>
                <input
                  type="number"
                  id={`split-${lob.id}`}
                  name={`split-${lob.id}`}
                  aria-label={`Cost share for ${lob.name}`}
                  className="input-field w-28 text-right"
                  value={Math.round(costShare[String(lob.id)] ?? 0)}
                  onChange={(event) =>
                    setCostShare((current) => ({
                      ...current,
                      [String(lob.id)]: Number(event.target.value),
                    }))
                  }
                />
              </div>
            </div>
          ))}
          <div className="flex items-center justify-between text-sm border-t border-navy-600 pt-2 mt-2">
            <span className="text-navy-400">Total pledged</span>
            <span
              className={
                totalPledged >= (jointCase.cost_low ?? 0)
                  ? 'text-success font-semibold'
                  : 'text-warning font-semibold'
              }
            >
              {fmtDollarsExact(totalPledged)}
            </span>
          </div>
          <div className="mt-3">
            <label className="text-xs text-navy-500" htmlFor="jf-sponsor">
              Champion / sponsor
            </label>
            <input
              id="jf-sponsor"
              name="jf-sponsor"
              aria-label="Champion or sponsor"
              className="input-field"
              placeholder="who's driving this"
              value={sponsor}
              onChange={(event) => setSponsor(event.target.value)}
            />
          </div>
        </div>
      </div>

      {briefOpen ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div
            className="absolute inset-0 bg-black/70 print:hidden"
            onClick={() => setBriefOpen(false)}
          />
          <div
            className="relative bg-white text-slate-900 w-full max-w-2xl max-h-[90vh] overflow-y-auto rounded-lg"
            style={{ padding: 28 }}
          >
            <div className="flex items-center justify-between mb-3 print:hidden">
              <span className="text-xs text-slate-500">
                {`AI funding brief — ${lobName(jointCase.benefiting_lobs[0]?.id)}` +
                  ` + ${jointCase.lob_count - 1} more`}
              </span>
              <div className="flex gap-2">
                <button
                  className="text-sm px-3 py-1.5 rounded bg-slate-900 text-white flex items-center gap-1.5"
                  onClick={() => window.print()}
                >
                  Print / PDF
                </button>
                <button
                  className="text-slate-500 hover:text-slate-900"
                  aria-label="Close brief"
                  onClick={() => setBriefOpen(false)}
                >
                  <X className="w-5 h-5" />
                </button>
              </div>
            </div>
            {brief.isPending ? (
              <div className="text-slate-500 flex items-center gap-2">
                <Sparkles className="w-4 h-4" /> Generating funding brief…
              </div>
            ) : null}
            {brief.data ? <Markdown md={brief.data.brief_md} /> : null}
            <div className="text-[10px] text-slate-400 mt-4 pt-2 border-t">
              Generated by AI Value Flywheel · Powered by Databricks
            </div>
          </div>
        </div>
      ) : null}
    </div>
  )
}

/**
 * A deliberately tiny markdown renderer.
 *
 * The brief is LLM-generated and only ever uses headings, bullets and bold, so a
 * markdown dependency (and a sanitizer for it) buys nothing. Anything it does
 * not recognise falls through as a paragraph rather than raw HTML, which is also
 * what keeps model output from injecting markup.
 */
function Markdown({ md }: { md: string }) {
  const lines = (md || '').split('\n')
  return (
    <div className="prose-sm">
      {lines.map((line, index) => {
        if (line.startsWith('### ')) {
          return (
            <h3 key={index} className="font-semibold text-base mt-3">
              {line.slice(4)}
            </h3>
          )
        }
        if (line.startsWith('## ')) {
          return (
            <h2 key={index} className="font-bold text-lg mt-3" style={{ color: '#FF3621' }}>
              {line.slice(3)}
            </h2>
          )
        }
        if (line.startsWith('# ')) {
          return (
            <h1 key={index} className="font-bold text-xl mb-1">
              {line.slice(2)}
            </h1>
          )
        }
        if (line.startsWith('- ') || line.startsWith('* ')) {
          return (
            <li key={index} className="ml-5 list-disc text-sm">
              {inline(line.slice(2))}
            </li>
          )
        }
        if (line.trim()) {
          return (
            <p key={index} className="text-sm my-1">
              {inline(line)}
            </p>
          )
        }
        return <div key={index} className="h-2" />
      })}
    </div>
  )
}

function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, index) =>
    part.startsWith('**') && part.endsWith('**') ? (
      <strong key={index}>{part.slice(2, -2)}</strong>
    ) : (
      <span key={index}>{part}</span>
    ),
  )
}

/** Local to this view — narrower than the shared KPI card, and no icon slot. */
function CaseStat({
  label,
  value,
  accent,
  sub,
}: {
  label: string
  value: string
  accent: string
  sub?: string
}) {
  return (
    <div className="card border-l-4" style={{ borderLeftColor: accent }}>
      <div className="text-xs text-navy-400 uppercase tracking-wide">{label}</div>
      <div className="text-xl font-bold text-white">{value}</div>
      {sub ? <div className="text-[10px] text-navy-500">{sub}</div> : null}
    </div>
  )
}
