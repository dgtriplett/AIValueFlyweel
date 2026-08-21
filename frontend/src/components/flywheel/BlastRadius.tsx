// The blast-radius map: every use case as a bubble, grouped by LOB around a dial.
//
// Hand-rolled SVG rather than a chart library because none of them lay out a
// polar grid of hundreds of bubbles packed per sector — and because the whole
// point of this view is the click interaction (light up one use case's network),
// which a chart abstraction would fight. Pan/zoom is a viewBox transform, so it
// stays crisp and the hit targets stay honest at every zoom level.

import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Zap } from 'lucide-react'
import { api } from '../../api'
import type { UseCaseScope } from '../../api'
import { LOBS, LOB_COLORS, fmtMoney } from '../../constants'
import type { UseCase } from '../../types'

const WIDTH = 1000
const HEIGHT = 1000
const CX = WIDTH / 2
const CY = HEIGHT / 2
const OUTER = 430
const INNER = 60

/** Prerequisite / dependent / sibling. Deliberately not the readiness palette —
 *  these encode a *direction* of dependency, not a state. */
const REL_COLORS = { prereq: '#F59E0B', builds: '#10B981', shared: '#8B5CF6' }

/** 0 radians points north so the first LOB reads at the top of the dial. */
function polar(r: number, theta: number) {
  return { x: CX + r * Math.sin(theta), y: CY - r * Math.cos(theta) }
}

interface Bubble {
  uc: UseCase
  x: number
  y: number
  r: number
  domain: string
  number: number
}

export function BlastRadius({
  onOpen,
  focusUcId,
  scope = 'portfolio',
}: {
  onOpen: (id: number) => void
  focusUcId?: number | null
  scope?: 'portfolio' | 'catalog'
}) {
  const apiScope: UseCaseScope = scope === 'catalog' ? 'all' : 'portfolio'
  const useCases = useQuery({ queryKey: ['use-cases', apiScope], queryFn: () => api.useCases(apiScope) })
  const lobs = useQuery({ queryKey: ['lobs'], queryFn: api.lobs })
  const requires = useQuery({ queryKey: ['requires'], queryFn: api.requires })
  const enables = useQuery({ queryKey: ['enables'], queryFn: api.enables })

  const [focus, setFocus] = useState<number | null>(null)
  useEffect(() => {
    if (focusUcId != null) setFocus(focusUcId)
  }, [focusUcId])

  const [hover, setHover] = useState<UseCase | null>(null)
  const [zoom, setZoom] = useState(1)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const [drag, setDrag] = useState<{ x: number; y: number } | null>(null)
  const svgRef = useRef<SVGSVGElement | null>(null)

  // Bare wheel would hijack page scroll, so zoom is gated on the modifier key —
  // and the listener has to be non-passive for preventDefault to take effect.
  useEffect(() => {
    const node = svgRef.current
    if (!node) return
    const onWheel = (event: WheelEvent) => {
      if (!(event.ctrlKey || event.metaKey)) return
      event.preventDefault()
      setZoom((current) => Math.min(3, Math.max(0.5, current * (event.deltaY < 0 ? 1.1 : 0.9))))
    }
    node.addEventListener('wheel', onWheel, { passive: false })
    return () => node.removeEventListener('wheel', onWheel)
  }, [])

  const network = useQuery({
    queryKey: ['network', focus],
    queryFn: () => api.network(focus!),
    enabled: focus != null,
    staleTime: 60000,
  })
  const unlocks = useQuery({
    queryKey: ['unlocks', focus],
    queryFn: () => api.unlocks(focus!),
    enabled: focus != null,
    staleTime: 60000,
  })

  const lobName = (id?: number | null) =>
    id != null ? ((lobs.data ?? []).find((lob) => lob.id === id)?.name ?? '') : ''

  // The network response carries titles for use cases outside the current scope,
  // so the side panel can name a prerequisite that isn't on the dial.
  const titleById = useMemo(() => {
    const map = new Map<number, string>()
    for (const uc of useCases.data ?? []) map.set(uc.id, uc.title)
    const titles = network.data?.titles
    if (titles) for (const key of Object.keys(titles)) map.set(Number(key), titles[key])
    return map
  }, [useCases.data, network.data])

  const { bubbles, byId } = useMemo(() => {
    const items = useCases.data ?? []
    const half = Math.ceil(LOBS.length / 2)
    const left = LOBS.slice(0, half)
    const right = LOBS.slice(half)
    const out: Bubble[] = []
    const index = new Map<number, Bubble>()
    let counter = 1

    // Pack each LOB's use cases into its angular sector as a rings × columns
    // grid, with a deterministic jitter so equal-size bubbles don't read as a
    // machine-made lattice. Rings are chosen to make the cells roughly square.
    const place = (domains: readonly string[], start: number, span: number) => {
      const slot = span / Math.max(1, domains.length)
      domains.forEach((domain, domainIndex) => {
        const slotStart = start + domainIndex * slot
        const gutter = slot * 0.08
        const usable = slot - 2 * gutter
        const inDomain = items
          .filter((uc) => lobName(uc.lob_id) === domain)
          .slice()
          .sort(
            (a, b) =>
              (a.phase ?? 9) - (b.phase ?? 9) ||
              (b.computed_value ?? 0) - (a.computed_value ?? 0),
          )
        const total = inDomain.length
        if (!total) return

        const depth = OUTER - INNER
        const arc = (INNER + depth / 2) * usable
        const aspect = arc / depth
        const rings = Math.max(1, Math.round(Math.sqrt(total / aspect)))
        const columns = Math.ceil(total / rings)
        const cellArc = arc / columns
        const cellDepth = depth / rings
        const radius = Math.max(6, Math.min(12, Math.min(cellArc, cellDepth) * 0.4))

        inDomain.forEach((uc, i) => {
          const ring = Math.floor(i / columns)
          const column = i % columns
          const inRing = ring === rings - 1 ? total - ring * columns : columns
          const centering = (columns - inRing) / 2
          const ringT = (ring + 0.5) / rings
          const columnT = (column + centering + 0.5) / columns
          const jitterR = (((i * 37) % 11) / 11 - 0.5) * (cellDepth * 0.25)
          const jitterA = (((i * 53) % 13) / 13 - 0.5) * ((usable / columns) * 0.25)
          const r = INNER + depth * 0.06 + ringT * depth * 0.88 + jitterR
          const theta = slotStart + gutter + columnT * usable + jitterA
          const { x, y } = polar(r, theta)
          const bubble: Bubble = { uc, x, y, r: radius, domain, number: counter++ }
          out.push(bubble)
          index.set(uc.id, bubble)
        })
      })
    }

    place(left, -Math.PI, Math.PI)
    place(right, 0, Math.PI)
    return { bubbles: out, byId: index }
  }, [useCases.data, lobs.data])

  const relations = useMemo(() => {
    if (focus == null || !network.data) return null
    const onDial = (id: number) => byId.has(id)
    const prereq = new Set((network.data.prerequisites ?? []).filter(onDial))
    const builds = new Set((network.data.builds_upon ?? []).filter(onDial))
    const shared = new Set((network.data.shared_data ?? []).filter(onDial))
    const related = new Set([focus, ...prereq, ...builds, ...shared])
    return {
      prereq,
      builds,
      shared,
      related,
      colorFor: (id: number): string | null =>
        id === focus
          ? '#FFFFFF'
          : prereq.has(id)
            ? REL_COLORS.prereq
            : builds.has(id)
              ? REL_COLORS.builds
              : shared.has(id)
                ? REL_COLORS.shared
                : null,
    }
  }, [focus, network.data, byId])

  const isError = useCases.isError || lobs.isError || requires.isError || enables.isError
  const ready = requires.data && enables.data && useCases.data && lobs.data
  const viewBox = `${-pan.x} ${-pan.y} ${WIDTH / zoom} ${HEIGHT / zoom}`
  const summary = unlocks.data?.summary
  const summaryLoading = focus != null && (unlocks.isLoading || unlocks.isFetching)
  const kpi = (value: string): string => (focus == null ? '—' : summaryLoading ? '…' : value)

  if (isError)
    return <div className="text-lava-300 text-sm">Couldn't load the flywheel. Please retry.</div>
  if (!ready) return <div className="text-navy-400">Loading flywheel…</div>

  const focal = focus != null ? byId.get(focus) : null
  const links =
    relations && focal
      ? [...relations.prereq]
          .map((id) => ({ id, color: REL_COLORS.prereq }))
          .concat([...relations.builds].map((id) => ({ id, color: REL_COLORS.builds })))
          .concat([...relations.shared].map((id) => ({ id, color: REL_COLORS.shared })))
          .flatMap(({ id, color }) => {
            const other = byId.get(id)
            return other
              ? [{ x1: focal.x, y1: focal.y, x2: other.x, y2: other.y, color }]
              : []
          })
      : []

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Kpi
          label="Focal use case"
          value={
            focus != null
              ? (useCases.data?.find((uc) => uc.id === focus)?.title ?? '—')
              : 'Click a bubble'
          }
          accent="#FF3621"
          small
        />
        <Kpi
          label="Unlocks"
          value={kpi(summary ? String(summary.count) : '0')}
          accent="#00A972"
          sub={focus != null && !summaryLoading ? 'use cases' : undefined}
        />
        <Kpi
          label="Across LOBs"
          value={kpi(summary ? String(summary.lob_count) : '0')}
          accent="#8B5CF6"
          sub="cross-LOB spillover"
        />
        <Kpi
          label="Hypothesized value"
          value={kpi(summary ? fmtMoney(summary.hypothesized_value) : '—')}
          accent="#FFAB00"
          sub={focus != null ? 'unlocked / yr' : undefined}
        />
      </div>

      <div className="card p-3 flex flex-wrap items-center gap-3 text-xs">
        <span className="font-semibold text-white flex items-center gap-1.5">
          <Zap className="w-4 h-4 text-lava" /> Value Flywheel — Blast Radius by LOB
        </span>
        {relations ? (
          <>
            <span className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-full" style={{ background: REL_COLORS.prereq }} />
              {relations.prereq.size} prerequisites
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-full" style={{ background: REL_COLORS.builds }} />
              {relations.builds.size} build on this
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-full" style={{ background: REL_COLORS.shared }} />
              {relations.shared.size} share data
            </span>
            <button className="btn-secondary text-xs py-1 ml-2" onClick={() => setFocus(null)}>
              Clear focus
            </button>
          </>
        ) : (
          LOBS.map((domain) => (
            <span key={domain} className="flex items-center gap-1.5">
              <span
                className="w-2.5 h-2.5 rounded-full"
                style={{ background: LOB_COLORS[domain] }}
              />
              {domain}
            </span>
          ))
        )}
      </div>

      <div
        className="card p-0 overflow-hidden relative"
        style={{ height: 'calc(100vh - 340px)', minHeight: 500 }}
      >
        <svg
          ref={svgRef}
          viewBox={viewBox}
          className="w-full h-full cursor-grab"
          style={{ background: '#0B2026' }}
          onMouseDown={(event) => setDrag({ x: event.clientX, y: event.clientY })}
          onMouseMove={(event) => {
            if (!drag) return
            setPan((current) => ({
              x: current.x + (event.clientX - drag.x) / zoom,
              y: current.y + (event.clientY - drag.y) / zoom,
            }))
            setDrag({ x: event.clientX, y: event.clientY })
          }}
          onMouseUp={() => setDrag(null)}
          onMouseLeave={() => {
            setDrag(null)
            setHover(null)
          }}
          onClick={() => setFocus(null)}
        >
          {[0.35, 0.6, 0.85, 1].map((t, i) => (
            <circle
              key={i}
              cx={CX}
              cy={CY}
              r={INNER + (OUTER - INNER) * t}
              fill="none"
              stroke="#143D4A"
              strokeWidth={1}
              strokeDasharray="3 6"
              opacity={0.6}
            />
          ))}
          {(() => {
            const half = Math.ceil(LOBS.length / 2)
            const labels: { domain: string; angle: number }[] = []
            LOBS.slice(0, half).forEach((domain, i, all) =>
              labels.push({ domain, angle: -Math.PI + (i + 0.5) * (Math.PI / all.length) }),
            )
            LOBS.slice(half).forEach((domain, i, all) =>
              labels.push({ domain, angle: (i + 0.5) * (Math.PI / all.length) }),
            )
            return labels.map(({ domain, angle }) => {
              const at = polar(OUTER + 22, angle)
              return (
                <text
                  key={domain}
                  x={at.x}
                  y={at.y}
                  fill={LOB_COLORS[domain]}
                  fontSize={15}
                  textAnchor="middle"
                  fontWeight={700}
                >
                  {domain}
                </text>
              )
            })
          })()}
          {links.map((link, i) => (
            <line
              key={i}
              x1={link.x1}
              y1={link.y1}
              x2={link.x2}
              y2={link.y2}
              stroke={link.color}
              strokeWidth={1.5}
              strokeDasharray="5 4"
              opacity={0.85}
            />
          ))}
          {bubbles.map((bubble) => {
            const isFocal = bubble.uc.id === focus
            const relColor = relations?.colorFor(bubble.uc.id)
            const dimmed = !!relations && !relations.related.has(bubble.uc.id)
            const fill =
              relations && relColor && relColor !== '#FFFFFF'
                ? relColor
                : (LOB_COLORS[bubble.domain] ?? '#618794')
            return (
              <g
                key={bubble.uc.id}
                className="cursor-pointer"
                opacity={dimmed ? 0.18 : 1}
                onClick={(event) => {
                  event.stopPropagation()
                  setFocus(bubble.uc.id)
                }}
                onDoubleClick={(event) => {
                  event.stopPropagation()
                  onOpen(bubble.uc.id)
                }}
                onMouseEnter={() => setHover(bubble.uc)}
                onMouseLeave={() =>
                  setHover((current) => (current?.id === bubble.uc.id ? null : current))
                }
              >
                {isFocal && (
                  <circle
                    cx={bubble.x}
                    cy={bubble.y}
                    r={bubble.r + 6}
                    fill="none"
                    stroke="#fff"
                    strokeWidth={2}
                    opacity={0.55}
                  />
                )}
                <circle
                  cx={bubble.x}
                  cy={bubble.y}
                  r={isFocal ? bubble.r + 1 : bubble.r}
                  fill={fill}
                  fillOpacity={
                    relations ? (relations.related.has(bubble.uc.id) ? 0.95 : 0.55) : 0.85
                  }
                  stroke={isFocal ? '#fff' : 'rgba(11,32,38,0.6)'}
                  strokeWidth={isFocal ? 2 : 1}
                />
                {bubble.r >= 7 && (
                  <text
                    x={bubble.x}
                    y={bubble.y}
                    textAnchor="middle"
                    dominantBaseline="central"
                    fontSize={9}
                    fontWeight={700}
                    fill="#fff"
                    pointerEvents="none"
                  >
                    {bubble.number}
                  </text>
                )}
              </g>
            )
          })}
          <circle cx={CX} cy={CY} r={4} fill="rgba(148,163,184,0.5)" />
        </svg>

        {hover && (
          <div
            className="absolute pointer-events-none card p-2 text-xs"
            style={{ left: '50%', top: 12, transform: 'translateX(-50%)', maxWidth: 380 }}
          >
            <span className="font-semibold text-white">
              #{byId.get(hover.id)?.number} {hover.title}
            </span>
            <span className="text-navy-400">
              {' · '}
              {lobName(hover.lob_id)}
              {' · '}
              {fmtMoney(hover.computed_value)}/yr
            </span>
          </div>
        )}

        {relations && (
          <div className="absolute top-3 right-3 card p-3 text-xs w-64 max-h-[70%] overflow-y-auto">
            <div className="font-semibold text-white mb-2">
              {focus != null ? (titleById.get(focus) ?? '') : ''}
            </div>
            <Section
              title="Prerequisites"
              color={REL_COLORS.prereq}
              ids={[...relations.prereq]}
              titleById={titleById}
              onOpen={onOpen}
              setFocus={setFocus}
            />
            <Section
              title="Builds on this"
              color={REL_COLORS.builds}
              ids={[...relations.builds]}
              titleById={titleById}
              onOpen={onOpen}
              setFocus={setFocus}
            />
            <Section
              title="Shares a prerequisite"
              color={REL_COLORS.shared}
              ids={[...relations.shared]}
              titleById={titleById}
              onOpen={onOpen}
              setFocus={setFocus}
            />
            {relations.prereq.size + relations.builds.size + relations.shared.size === 0 && (
              <div className="text-navy-500">
                No linked use cases — this is a foundational or standalone use case.
              </div>
            )}
          </div>
        )}

        <div className="absolute bottom-3 right-3 flex flex-col gap-1">
          <button
            aria-label="Zoom in"
            title="Zoom in"
            className="btn-secondary text-xs px-2 py-1"
            onClick={() => setZoom((current) => Math.min(3, current * 1.2))}
          >
            +
          </button>
          <button
            aria-label="Zoom out"
            title="Zoom out"
            className="btn-secondary text-xs px-2 py-1"
            onClick={() => setZoom((current) => Math.max(0.5, current / 1.2))}
          >
            −
          </button>
          <button
            aria-label="Reset zoom and pan"
            title="Reset view"
            className="btn-secondary text-xs px-2 py-1"
            onClick={() => {
              setZoom(1)
              setPan({ x: 0, y: 0 })
            }}
          >
            ⤢
          </button>
        </div>

        <div className="absolute bottom-3 left-3 text-xs text-navy-500">
          Click a bubble to light its network · double-click to open · ⌘/Ctrl + scroll (or pinch) to
          zoom · drag to pan
        </div>
      </div>
    </div>
  )
}

function Section({
  title,
  color,
  ids,
  titleById,
  onOpen,
  setFocus,
}: {
  title: string
  color: string
  ids: number[]
  titleById: Map<number, string>
  onOpen: (id: number) => void
  setFocus: (id: number) => void
}) {
  if (ids.length === 0) return null
  const label = (id: number) => titleById.get(id) ?? `Use case ${id}`
  return (
    <div className="mb-2">
      <div className="flex items-center gap-1.5 mb-1">
        <span className="w-2 h-2 rounded-full" style={{ background: color }} />
        <span className="text-navy-300 font-medium">{title}</span>
        <span className="text-navy-500">({ids.length})</span>
      </div>
      {ids.map((id) => (
        <button
          key={id}
          className="block w-full text-left text-navy-300 hover:text-white py-0.5 truncate"
          onClick={() => setFocus(id)}
          onDoubleClick={() => onOpen(id)}
          title="click to re-center, double-click to open"
        >
          {label(id)}
        </button>
      ))}
    </div>
  )
}

/** The flywheel's own KPI card — no icon, and the focal title needs to wrap
 *  small rather than truncate, so it isn't the shared StatCard. */
function Kpi({
  label,
  value,
  accent,
  sub,
  small,
}: {
  label: string
  value: ReactNode
  accent: string
  sub?: string
  small?: boolean
}) {
  return (
    <div className="card border-l-4" style={{ borderLeftColor: accent }}>
      <div className="text-xs text-navy-400 uppercase tracking-wide">{label}</div>
      <div className={`font-bold text-white ${small ? 'text-sm leading-tight mt-1' : 'text-2xl'}`}>
        {value}
      </div>
      {sub && <div className="text-xs text-success mt-0.5">{sub}</div>}
    </div>
  )
}
