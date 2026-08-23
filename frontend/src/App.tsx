// The app shell: KPI strip, the active view, and the drawer that overlays them.
//
// Navigation is a single piece of state rather than a router. Every view reads
// the same portfolio and the same filters, so a URL per view would buy nothing
// and cost a full remount on every tab change — the flywheel's layout and the
// dashboards' queries would be thrown away and recomputed each time.

import { lazy, Suspense, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Database, Layers, TrendingUp, Zap } from 'lucide-react'
import { api } from './api'
import { fmtMoney } from './constants'
import { FilterProvider } from './context/FilterContext'
import { EulaGate } from './components/EulaGate'
import { FilterBar } from './components/FilterBar'
import { AssistantPanel } from './components/AssistantPanel'
import { Header } from './components/Header'
import type { TabId } from './components/Header'
import { NewUseCaseModal } from './components/NewUseCaseModal'
import { slugFromLocation } from './lib/kbroute'
import type { UseCaseView } from './components/ScopeSwitch'
import { StatCard } from './components/StatCard'
import { ToastProvider } from './components/Toasts'
import { UseCaseDrawer } from './components/UseCaseDrawer'
import { PortfolioView } from './views/PortfolioView'
import { RegistryView } from './views/RegistryView'

// Each of these pulls in a heavy dependency the first screen does not need —
// recharts, xyflow+dagre — so they load on demand rather than in the entry chunk.
const DashboardsView = lazy(() => import('./views/DashboardsView'))
const FlywheelTab = lazy(() => import('./components/flywheel/FlywheelTab'))
const RoadmapView = lazy(() => import('./views/RoadmapView'))
const JointFundingView = lazy(() => import('./views/JointFundingView'))
const AssumptionsView = lazy(() => import('./views/AssumptionsView'))
const OnboardingView = lazy(() => import('./views/OnboardingView'))

// Tier 3 Phase 3 — the ported read-only console views. Lazy for the same reason
// as the block above: `TrendView` pulls recharts, and the rest are wide tables
// nobody loads on the first screen. Each becomes a chunk fetched on demand rather
// than weight in the entry bundle every visitor pays for.
const CoverageView = lazy(() => import('./views/CoverageView'))
const WhatIfView = lazy(() => import('./views/WhatIfView'))
const TrendView = lazy(() => import('./views/TrendView'))
const GlossaryView = lazy(() => import('./views/GlossaryView'))
const ArtifactsView = lazy(() => import('./views/ArtifactsView'))
const ExecutiveView = lazy(() => import('./views/ExecutiveView'))

// Tier 3 Phase 4 — the knowledge base. Lazy for the same reason, and it is the
// largest of these: three modes plus the markdown parser and renderer.
const KnowledgeView = lazy(() => import('./views/KnowledgeView'))

// Tier 3 Phase 5 — the curation writes. Lazy for the same reason as the rest:
// these are the screens a data steward opens deliberately, not ones every visitor
// lands on, so they should not be weight in the entry chunk.
const SourceMappingView = lazy(() => import('./views/SourceMappingView'))
const TaxonomyView = lazy(() => import('./views/TaxonomyView'))
const RulesView = lazy(() => import('./views/RulesView'))

// Tier 3 Phase 6 — token-spending generation and roadmap handoff flows.
const GenerateView = lazy(() => import('./views/GenerateView'))
const ProposalsView = lazy(() => import('./views/ProposalsView'))
const RoadmapImportView = lazy(() => import('./views/RoadmapImportView'))

// Tier 3 Phase 7 — company research and assumption recalibration. Lazy for the
// same reason as the rest: a wide review table with its own model calls, opened
// deliberately at a new account rather than on the first screen.
const ResearchView = lazy(() => import('./views/ResearchView'))

// Tier 3 Phase 8 — the Settings surfaces. Lazy for the same reason as the rest:
// the account switcher, the branding editor and the admin console are operator
// screens opened deliberately, not weight every visitor pays for in the entry chunk.
const AccountsView = lazy(() => import('./views/AccountsView'))
const AdminView = lazy(() => import('./views/AdminView'))
const BrandingView = lazy(() => import('./views/BrandingView'))

function AppShell() {
  // 'portfolio' unless the app was cold-loaded on a KB article deep link.
  //
  // This is the ONE place navigation reads the URL, and it is the other half of the
  // narrow exception `lib/kbroute.ts` documents: `app.py`'s SPA catch-all serves
  // index.html for `/kb/<slug>`, so without this a pasted article link would boot
  // the app on the portfolio tab and silently drop what was asked for. Read once in
  // the initializer, never subscribed to here — `KnowledgeView` owns `popstate`.
  const [tab, setTab] = useState<TabId>(() =>
    slugFromLocation() ? 'knowledge' : 'portfolio',
  )
  // Which use-case scope the merged destination shows. Owned here, not in the
  // view, so flipping to the flywheel and back does not silently reset you to
  // the portfolio when you were reading the catalog.
  const [ucScope, setUcScope] = useState<UseCaseView>('portfolio')
  const [drawerUcId, setDrawerUcId] = useState<number | null>(null)
  const [focusUcId, setFocusUcId] = useState<number | null>(null)
  const [creating, setCreating] = useState(false)
  const [proposalUcId, setProposalUcId] = useState<number | null>(null)

  /** Jump to the flywheel with a use case already lit up. */
  const focusOnFlywheel = (id: number) => {
    setFocusUcId(id)
    setTab('flywheel')
  }

  const health = useQuery({ queryKey: ['health'], queryFn: api.health })
  const lobsQuery = useQuery({ queryKey: ['lobs'], queryFn: api.lobs })
  const useCasesQuery = useQuery({ queryKey: ['use-cases'], queryFn: () => api.useCases() })
  const assetsQuery = useQuery({ queryKey: ['data-assets'], queryFn: api.dataAssets })

  const lobs = lobsQuery.data ?? []
  const useCases = useCasesQuery.data ?? []
  const assets = assetsQuery.data ?? []

  const totalValue = useCases.reduce((sum, uc) => sum + (uc.computed_value ?? 0), 0)
  // "Buildable" excludes blocked work: a number that includes value you cannot
  // start on is the one customers quote back and then cannot deliver.
  const buildableValue = useCases.reduce(
    (sum, uc) => sum + (uc.readiness !== 'blocked' ? (uc.computed_value ?? 0) : 0),
    0,
  )
  const realizedValue = useCases.reduce((sum, uc) => sum + (uc.realized?.value ?? 0), 0)
  const shovelReady = useCases.filter((uc) => uc.readiness === 'shovel_ready').length
  const governed = assets.filter((asset) => asset.ingestion_status === 'governed').length

  const renderActiveView = () => {
    switch (tab) {
      case 'onboarding':
        // `setTab` because the wizard's last step links onward into the app. Those
        // were `<a href="#coverage">` hash links into the console's router and one
        // `<a href="/">` back to the SPA; with one app they are ordinary nav, and a
        // full page load there would throw away every cached query.
        return <OnboardingView setTab={setTab} />
      case 'portfolio':
        return (
          <PortfolioView
            useCases={useCases}
            lobs={lobs}
            onOpen={setDrawerUcId}
            onNew={() => setCreating(true)}
            scope={ucScope}
            onScope={setUcScope}
            loading={useCasesQuery.isLoading}
            error={useCasesQuery.isError}
          />
        )
      case 'registry':
        return <RegistryView lobs={lobs} onOpenUseCase={setDrawerUcId} />
      case 'flywheel':
        return <FlywheelTab focusUcId={focusUcId} onOpen={setDrawerUcId} />
      case 'dashboards':
        return <DashboardsView />
      case 'roadmap':
        return <RoadmapView lobs={lobs} onOpen={setDrawerUcId} />
      case 'funding':
        return <JointFundingView lobs={lobs} />
      case 'value':
        return <AssumptionsView />
      case 'coverage':
        return <CoverageView />
      case 'whatif':
        return <WhatIfView />
      case 'trend':
        return <TrendView />
      case 'glossary':
        return <GlossaryView />
      case 'artifacts':
        return <ArtifactsView />
      case 'executive':
        return <ExecutiveView />
      case 'knowledge':
        return <KnowledgeView />
      case 'sourcemapping':
        return <SourceMappingView />
      case 'taxonomy':
        return <TaxonomyView />
      case 'rules':
        return <RulesView />
      case 'generate':
        return <GenerateView />
      case 'proposals':
        return <ProposalsView initialUseCaseId={proposalUcId} />
      case 'roadmap_import':
        return <RoadmapImportView />
      case 'research':
        return <ResearchView />
      case 'accounts':
        return <AccountsView />
      case 'admin':
        return <AdminView />
      case 'branding':
        return <BrandingView />
      default: {
        const unhandledTab: never = tab
        return unhandledTab
      }
    }
  }

  return (
    <div className="min-h-screen">
      <Header env={health.data?.environment} tab={tab} setTab={setTab} />

      <main className="max-w-[1440px] mx-auto px-6 py-5 space-y-5">
        <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
          <StatCard
            icon={<Layers className="w-5 h-5" />}
            label="Use Cases"
            value={useCases.length}
            accent="#FF3621"
          />
          <StatCard
            icon={<Database className="w-5 h-5" />}
            label="Data Sources"
            value={assets.length}
            sub={`${governed} governed`}
            accent="#2272B4"
          />
          <StatCard
            icon={<Zap className="w-5 h-5" />}
            label="Shovel-ready"
            value={shovelReady}
            accent="#00A972"
          />
          <StatCard
            icon={<TrendingUp className="w-5 h-5" />}
            label="Buildable value/yr"
            value={fmtMoney(buildableValue)}
            sub={`of ${fmtMoney(totalValue)} total potential`}
            muted
            accent="#FFAB00"
          />
          <StatCard
            icon={<TrendingUp className="w-5 h-5" />}
            label="Realized/yr"
            value={fmtMoney(realizedValue)}
            accent="#42BA91"
          />
        </div>

        {tab === 'portfolio' || tab === 'registry' ? (
          <FilterBar lobs={lobs} showIngestion={tab === 'registry'} />
        ) : null}

        <Suspense fallback={<div className="text-navy-400 py-8 text-center">Loading…</div>}>
          {renderActiveView()}
        </Suspense>

        <footer className="text-center text-xs text-navy-600 pt-4 pb-8">
          AI Value Flywheel · Powered by Databricks · P&amp;U Data &amp; AI catalog
        </footer>
      </main>

      {drawerUcId != null ? (
        <UseCaseDrawer
          ucId={drawerUcId}
          lobs={lobs}
          onClose={() => setDrawerUcId(null)}
          onOpenUseCase={setDrawerUcId}
          onWriteProposal={(id) => {
            setProposalUcId(id)
            setDrawerUcId(null)
            setTab('proposals')
          }}
        />
      ) : null}

      {creating ? (
        <NewUseCaseModal
          lobs={lobs}
          onClose={() => setCreating(false)}
          onCreated={() => undefined}
          onViewOnFlywheel={(id) => {
            setCreating(false)
            focusOnFlywheel(id)
          }}
        />
      ) : null}

      <AssistantPanel />
      <EulaGate />
    </div>
  )
}

export default function App() {
  return (
    // ToastProvider is OUTSIDE FilterProvider, so a failure can be reported even
    // if it happened while filter state was being torn down, and so the fixed
    // viewport it renders is a sibling of the shell rather than inside <main>'s
    // stacking context — a toast that loses a z-index fight is a toast nobody sees.
    <ToastProvider>
      <FilterProvider>
        <AppShell />
      </FilterProvider>
    </ToastProvider>
  )
}
