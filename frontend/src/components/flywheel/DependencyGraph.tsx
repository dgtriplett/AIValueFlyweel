// The dependency editor: one node's neighborhood, laid out by dagre.
//
// It renders a neighborhood rather than the whole graph on purpose. The full
// requires/enables graph is thousands of edges — laid out at once it is both slow
// and unreadable, and nobody edits a hairball. You pick a focus, expand hops as
// far as you care, and drag between handles to create the link.

import { useCallback, useEffect, useMemo, useState } from 'react'
import type { MouseEvent as ReactMouseEvent } from 'react'
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  Position,
  useNodesState,
  useEdgesState,
  addEdge,
  useReactFlow,
} from '@xyflow/react'
import type { Connection, Edge, Node, NodeProps, NodeTypes } from '@xyflow/react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Database, Layers, Plus, Search, X } from 'lucide-react'
import dagre from 'dagre'
import { api } from '../../api'
import type { UseCaseScope } from '../../api'
import { INGESTION_COLORS, READINESS_COLORS } from '../../constants'
import type { EnablesEdge, RequiresEdge } from '../../types'
import '@xyflow/react/dist/style.css'

interface GraphNodeData extends Record<string, unknown> {
  label: string
  sublabel?: string | null
  color: string
  focused: boolean
}

type GraphNode = Node<GraphNodeData, 'usecase' | 'asset'>

function UseCaseNode({ data }: NodeProps<GraphNode>) {
  return (
    <div
      className="rounded-lg border-2 px-3 py-2 text-xs font-semibold shadow-lg"
      style={{
        background: 'linear-gradient(145deg,#143D4A,#0B2026)',
        borderColor: data.color,
        color: '#E5EAF0',
        maxWidth: 190,
        boxShadow: data.focused ? `0 0 0 3px ${data.color}66` : undefined,
      }}
    >
      <Handle type="target" position={Position.Left} style={{ background: data.color }} />
      <div className="flex items-center gap-1">
        <Layers className="w-3 h-3 shrink-0" style={{ color: data.color }} />
        <span className="truncate">{data.label}</span>
      </div>
      <Handle type="source" position={Position.Right} style={{ background: data.color }} />
    </div>
  )
}

function AssetNode({ data }: NodeProps<GraphNode>) {
  return (
    <div
      className="rounded-md border px-2.5 py-1.5 text-xs shadow"
      style={{
        background: '#102545',
        borderColor: data.color,
        color: '#C4CCD6',
        maxWidth: 180,
        boxShadow: data.focused ? `0 0 0 3px ${data.color}66` : undefined,
      }}
    >
      <div className="flex items-center gap-1">
        <Database className="w-3 h-3 shrink-0" style={{ color: data.color }} />
        <span className="truncate font-medium">{data.label}</span>
      </div>
      {data.sublabel && (
        <div className="truncate text-[10px] text-navy-500">{data.sublabel}</div>
      )}
      <Handle type="source" position={Position.Right} style={{ background: data.color }} />
      <Handle type="target" position={Position.Left} style={{ background: data.color }} />
    </div>
  )
}

const NODE_TYPES: NodeTypes = { usecase: UseCaseNode, asset: AssetNode }

const NODE_WIDTH = 190
const NODE_HEIGHT = 46

/** dagre gives node centres; ReactFlow wants top-left, hence the half offsets. */
function layout(nodes: GraphNode[], edges: Edge[]): GraphNode[] {
  const graph = new dagre.graphlib.Graph()
  graph.setDefaultEdgeLabel(() => ({}))
  graph.setGraph({ rankdir: 'LR', nodesep: 28, ranksep: 120, marginx: 24, marginy: 24 })
  nodes.forEach((node) => graph.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT }))
  edges.forEach((edge) => graph.setEdge(edge.source, edge.target))
  dagre.layout(graph)
  return nodes.map((node) => {
    const placed = graph.node(node.id)
    return { ...node, position: { x: placed.x - 95, y: placed.y - 23 } }
  })
}

const ucId = (id: number) => `uc-${id}`
const assetId = (id: number) => `asset-${id}`
const numberOf = (nodeId: string) => Number(nodeId.split('-')[1])

type Focus = { kind: 'uc' | 'asset'; id: number }

function DependencyGraphInner({ scope = 'portfolio' }: { scope?: 'portfolio' | 'catalog' }) {
  const queryClient = useQueryClient()
  const apiScope: UseCaseScope = scope === 'catalog' ? 'all' : 'portfolio'
  const useCases = useQuery({ queryKey: ['use-cases', apiScope], queryFn: () => api.useCases(apiScope) })
  const assets = useQuery({ queryKey: ['data-assets'], queryFn: api.dataAssets })
  const requires = useQuery({ queryKey: ['requires'], queryFn: api.requires })
  const enables = useQuery({ queryKey: ['enables'], queryFn: api.enables })
  const flow = useReactFlow()

  const [nodes, setNodes, onNodesChange] = useNodesState<GraphNode>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([])
  const [focus, setFocus] = useState<Focus | null>(null)
  const [hops, setHops] = useState(1)
  const [showAssets, setShowAssets] = useState(true)
  const [query, setQuery] = useState('')
  const [toast, setToast] = useState<string | null>(null)
  const [pendingDelete, setPendingDelete] = useState<{ id: string; kind: string } | null>(null)

  useEffect(() => {
    if (!toast) return
    const timer = setTimeout(() => setToast(null), 4000)
    return () => clearTimeout(timer)
  }, [toast])

  const ucById = useMemo(
    () => new Map((useCases.data ?? []).map((uc) => [uc.id, uc])),
    [useCases.data],
  )
  const assetById = useMemo(
    () => new Map((assets.data ?? []).map((asset) => [asset.id, asset])),
    [assets.data],
  )

  const graph = useMemo(() => {
    if (!focus || !useCases.data || !assets.data || !requires.data || !enables.data) return null
    const reqs = requires.data
    const enas = enables.data

    const neighbours = (nodeId: string): string[] => {
      const out: string[] = []
      const [kind, raw] = nodeId.split('-')
      const id = Number(raw)
      if (kind === 'uc') {
        reqs.forEach((edge) => {
          if (edge.use_case_id === id) out.push(assetId(edge.data_asset_id))
        })
        enas.forEach((edge) => {
          if (edge.from_use_case_id === id) out.push(ucId(edge.to_use_case_id))
          if (edge.to_use_case_id === id) out.push(ucId(edge.from_use_case_id))
        })
      } else {
        reqs.forEach((edge) => {
          if (edge.data_asset_id === id) out.push(ucId(edge.use_case_id))
        })
      }
      return out
    }

    const root = focus.kind === 'uc' ? ucId(focus.id) : assetId(focus.id)
    const visited = new Set([root])
    let frontier = [root]
    for (let hop = 0; hop < hops; hop++) {
      const next: string[] = []
      for (const nodeId of frontier)
        for (const neighbour of neighbours(nodeId)) {
          if (!showAssets && neighbour.startsWith('asset-')) continue
          if (visited.has(neighbour)) continue
          visited.add(neighbour)
          next.push(neighbour)
        }
      frontier = next
      if (!next.length) break
    }

    const included = (nodeId: string) => visited.has(nodeId)
    const laidOut: GraphNode[] = []
    visited.forEach((nodeId) => {
      const id = numberOf(nodeId)
      if (nodeId.startsWith('uc-')) {
        const uc = ucById.get(id)
        if (!uc) return
        laidOut.push({
          id: nodeId,
          type: 'usecase',
          position: { x: 0, y: 0 },
          data: {
            label: uc.title,
            color: uc.readiness ? READINESS_COLORS[uc.readiness] : '#1E90FF',
            focused: nodeId === root,
          },
        })
      } else {
        const asset = assetById.get(id)
        if (!asset) return
        laidOut.push({
          id: nodeId,
          type: 'asset',
          position: { x: 0, y: 0 },
          data: {
            label: asset.module ?? '',
            sublabel: asset.source_category ?? asset.source_system,
            color: asset.ingestion_status ? INGESTION_COLORS[asset.ingestion_status] : '#618794',
            focused: nodeId === root,
          },
        })
      }
    })

    const graphEdges: Edge[] = [
      ...reqs
        .filter((edge) => included(ucId(edge.use_case_id)) && included(assetId(edge.data_asset_id)))
        .map((edge) => ({
          id: `req-${edge.use_case_id}-${edge.data_asset_id}`,
          source: assetId(edge.data_asset_id),
          target: ucId(edge.use_case_id),
          style: {
            stroke: edge.criticality === 'required' ? '#4dabff' : '#334155',
            strokeWidth: 1.5,
            strokeDasharray: edge.criticality === 'helpful' ? '4 3' : undefined,
          },
        })),
      ...enas
        .filter(
          (edge) =>
            included(ucId(edge.from_use_case_id)) && included(ucId(edge.to_use_case_id)),
        )
        .map((edge) => ({
          id: `ena-${edge.from_use_case_id}-${edge.to_use_case_id}`,
          source: ucId(edge.from_use_case_id),
          target: ucId(edge.to_use_case_id),
          animated: true,
          style: { stroke: '#00A972', strokeWidth: 2 },
        })),
    ]

    return { nodes: layout(laidOut, graphEdges), edges: graphEdges, count: laidOut.length }
  }, [focus, hops, showAssets, useCases.data, assets.data, requires.data, enables.data, ucById, assetById])

  // Keyed on the node-id list so a re-layout with the same membership (e.g. a
  // node the user dragged) doesn't stomp their positions or re-fit the viewport.
  const signature = graph ? graph.nodes.map((node) => node.id).join('|') : ''

  useEffect(() => {
    if (!graph) {
      setNodes([])
      setEdges([])
      return
    }
    setNodes(graph.nodes)
    setEdges(graph.edges)
  }, [signature])

  useEffect(() => {
    if (!graph || graph.count === 0) return
    let frame = 0
    const timer = setTimeout(() => {
      frame = requestAnimationFrame(() =>
        flow.fitView({ padding: 0.25, duration: 300, maxZoom: 1.4 }),
      )
    }, 60)
    return () => {
      clearTimeout(timer)
      cancelAnimationFrame(frame)
    }
  }, [signature])

  const onNodeClick = useCallback((_event: ReactMouseEvent, node: GraphNode) => {
    const [kind, raw] = node.id.split('-')
    setFocus({ kind: kind as Focus['kind'], id: Number(raw) })
    setHops(1)
  }, [])

  const createRequires = useMutation({
    mutationFn: (body: RequiresEdge) => api.createRequires(body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['requires'] }),
  })
  const createEnables = useMutation({
    mutationFn: (body: EnablesEdge) => api.createEnables(body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['enables'] }),
  })

  const onConnect = useCallback(
    (connection: Connection) => {
      if (!connection.source || !connection.target) return
      if (connection.source.startsWith('asset-') && connection.target.startsWith('uc-')) {
        createRequires.mutate({
          use_case_id: numberOf(connection.target),
          data_asset_id: numberOf(connection.source),
          criticality: 'required',
        })
        setEdges((current) =>
          addEdge({ ...connection, style: { stroke: '#4dabff', strokeWidth: 1.5 } }, current),
        )
      } else if (connection.source.startsWith('uc-') && connection.target.startsWith('uc-')) {
        createEnables.mutate({
          from_use_case_id: numberOf(connection.source),
          to_use_case_id: numberOf(connection.target),
        })
        setEdges((current) =>
          addEdge(
            { ...connection, animated: true, style: { stroke: '#00A972', strokeWidth: 2 } },
            current,
          ),
        )
      } else {
        setToast('Connect an asset → use case (requires) or a use case → use case (enables).')
      }
    },
    [createRequires, createEnables, setEdges],
  )

  const deleteRequires = useMutation({
    mutationFn: ({ uc, asset }: { uc: number; asset: number }) => api.deleteRequires(uc, asset),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['requires'] }),
  })
  const deleteEnables = useMutation({
    mutationFn: ({ from, to }: { from: number; to: number }) => api.deleteEnables(from, to),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['enables'] }),
  })

  const onEdgeClick = useCallback((_event: ReactMouseEvent, edge: Edge) => {
    if (edge.id.startsWith('req-') || edge.id.startsWith('ena-'))
      setPendingDelete({ id: edge.id, kind: edge.id.startsWith('req-') ? 'requires' : 'enables' })
  }, [])

  const confirmDelete = () => {
    const target = pendingDelete
    if (!target) return
    const parts = target.id.split('-')
    if (target.kind === 'requires')
      deleteRequires.mutate({ uc: Number(parts[1]), asset: Number(parts[2]) })
    else deleteEnables.mutate({ from: Number(parts[1]), to: Number(parts[2]) })
    setEdges((current) => current.filter((edge) => edge.id !== target.id))
    setPendingDelete(null)
  }

  if (useCases.isError || assets.isError || requires.isError || enables.isError)
    return (
      <div className="text-lava-300 text-sm">Couldn't load the dependency graph. Please retry.</div>
    )
  if (!useCases.data || !assets.data || !requires.data || !enables.data)
    return <div className="text-navy-400">Loading…</div>

  const term = query.trim().toLowerCase()
  const ucMatches = term
    ? useCases.data.filter((uc) => uc.title.toLowerCase().includes(term)).slice(0, 6)
    : []
  const assetMatches = term
    ? assets.data
        .filter((asset) =>
          `${asset.source_category} ${asset.module}`.toLowerCase().includes(term),
        )
        .slice(0, 5)
    : []
  const focusLabel = focus
    ? focus.kind === 'uc'
      ? ucById.get(focus.id)?.title
      : `${assetById.get(focus.id)?.source_category} · ${assetById.get(focus.id)?.module}`
    : null

  return (
    <div className="space-y-3">
      <div className="card p-3 flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[260px]">
          <Search className="w-4 h-4 text-navy-500 absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            id="graph-search"
            name="graph-search"
            aria-label="Search a use case or data asset to explore its dependencies"
            className="input-field pl-9"
            placeholder="Select a use case or data asset to explore its dependencies…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          {term && (ucMatches.length > 0 || assetMatches.length > 0) && (
            <div className="absolute z-20 mt-1 w-full card p-1 max-h-72 overflow-y-auto">
              {ucMatches.map((uc) => (
                <button
                  key={`u${uc.id}`}
                  className="block w-full text-left px-2 py-1.5 text-sm rounded hover:bg-lava/10 text-navy-300"
                  onClick={() => {
                    setFocus({ kind: 'uc', id: uc.id })
                    setHops(1)
                    setQuery('')
                  }}
                >
                  <Layers className="w-3 h-3 inline mr-1 text-lava-300" /> {uc.title}
                </button>
              ))}
              {assetMatches.map((asset) => (
                <button
                  key={`a${asset.id}`}
                  className="block w-full text-left px-2 py-1.5 text-sm rounded hover:bg-lava/10 text-navy-300"
                  onClick={() => {
                    setFocus({ kind: 'asset', id: asset.id })
                    setHops(1)
                    setQuery('')
                  }}
                >
                  <Database className="w-3 h-3 inline mr-1 text-info" /> {asset.source_category} ·{' '}
                  {asset.module}
                </button>
              ))}
            </div>
          )}
        </div>
        {focus && (
          <>
            <span className="text-xs text-navy-400">
              Focus: <span className="text-white font-medium">{focusLabel}</span> · {hops} hop
              {hops > 1 ? 's' : ''}
            </span>
            <button className="btn-secondary text-xs" onClick={() => setHops((n) => n + 1)}>
              <Plus className="w-3.5 h-3.5" /> Expand
            </button>
            {hops > 1 && (
              <button className="btn-secondary text-xs" onClick={() => setHops(1)}>
                Collapse
              </button>
            )}
            <label
              htmlFor="graph-show-assets"
              className="flex items-center gap-1.5 text-xs text-navy-300 cursor-pointer"
            >
              <input
                id="graph-show-assets"
                name="graph-show-assets"
                aria-label="Show data assets in the graph"
                type="checkbox"
                checked={showAssets}
                onChange={(event) => setShowAssets(event.target.checked)}
              />{' '}
              Show data assets
            </label>
            <button
              className="btn-secondary text-xs"
              onClick={() => {
                setFocus(null)
                setQuery('')
              }}
            >
              <X className="w-3.5 h-3.5" /> Clear
            </button>
          </>
        )}
        <span className="text-xs text-navy-500 ml-auto">
          requires <span style={{ color: '#4dabff' }}>—</span> · enables{' '}
          <span style={{ color: '#00A972' }}>—</span> · drag handle to connect · click edge to delete
        </span>
      </div>

      <div
        className="card p-0 overflow-hidden relative"
        style={{ height: 'calc(100vh - 320px)', minHeight: 480 }}
      >
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          onNodeClick={onNodeClick}
          onEdgeClick={onEdgeClick}
          nodeTypes={NODE_TYPES}
          minZoom={0.2}
          proOptions={{ hideAttribution: true }}
          zoomOnScroll={false}
          panOnScroll={false}
          preventScrolling={false}
          zoomOnPinch
          zoomActivationKeyCode={['Meta', 'Control']}
        >
          <Background variant={BackgroundVariant.Dots} gap={20} color="#143D4A" />
          <Controls className="!bg-navy-700 !border-navy-600" />
        </ReactFlow>

        {!focus && (
          <div
            className="absolute inset-0 flex flex-col items-center justify-center text-center px-6 pointer-events-none"
            style={{ background: 'rgba(11,32,38,0.85)' }}
          >
            <Search className="w-10 h-10 text-navy-600 mb-3" />
            <div className="text-navy-300 font-medium">
              Select a use case or data asset to explore its dependencies
            </div>
            <div className="text-navy-500 text-sm mt-1 max-w-md">
              The dependency editor renders one node's neighborhood at a time — fast and readable.
              Use the search above, or open the Blast Radius for the portfolio-wide overview.
            </div>
          </div>
        )}

        {toast && (
          <div
            className="absolute bottom-4 left-1/2 -translate-x-1/2 z-20 card px-4 py-2 text-sm text-navy-300 shadow-card-hover animate-fade-in"
            role="status"
          >
            {toast}
          </div>
        )}
      </div>

      {pendingDelete && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div className="absolute inset-0 bg-black/60" onClick={() => setPendingDelete(null)} />
          <div className="relative card w-full max-w-sm animate-scale-in">
            <h3 className="font-bold text-white mb-1">Delete {pendingDelete.kind} edge?</h3>
            <p className="text-sm text-navy-400 mb-4">
              This removes the dependency link. You can re-connect it by dragging between nodes.
            </p>
            <div className="flex justify-end gap-2">
              <button className="btn-secondary text-sm" onClick={() => setPendingDelete(null)}>
                Cancel
              </button>
              <button className="btn-primary text-sm" onClick={confirmDelete}>
                Delete edge
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

/** useReactFlow needs a provider above it, so the export is the wrapper. */
export function DependencyGraph({ scope = 'portfolio' }: { scope?: 'portfolio' | 'catalog' }) {
  return (
    <ReactFlowProvider>
      <DependencyGraphInner scope={scope} />
    </ReactFlowProvider>
  )
}
