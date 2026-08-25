// The app shell: KPI strip, the active view, and the drawer that overlays them.
//
// Navigation is a single piece of state rather than a router. Every view reads
// the same portfolio and the same filters, so a URL per view would buy nothing
// and cost a full remount on every tab change — the flywheel's layout and the
// dashboards' queries would be thrown away and recomputed each time.

import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from './api'
import { FilterProvider } from './context/FilterContext'
import { RoleProvider, useRole } from './context/RoleContext'
import { EulaGate } from './components/EulaGate'
import { FilterBar } from './components/FilterBar'
import { AssistantPanel } from './components/AssistantPanel'
import { Header } from './components/Header'
import type { TabId } from './components/Header'
import { resolveLandingTab, visibleTabsForPersona } from './components/Header'
import { readLastTab, writeLastTab } from './lib/lastTab'
import { NewUseCaseModal } from './components/NewUseCaseModal'
import { slugFromLocation } from './lib/kbroute'
import type { UseCaseView } from './components/ScopeSwitch'
import { TopKpis } from './components/TopKpis'
import { ToastProvider } from './components/Toasts'
import { UseCaseDrawer, UseCaseDetailPage } from './components/UseCaseDrawer'
import { DataAssetDrawer } from './components/DataAssetDrawer'
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
const AtRiskView = lazy(() => import('./views/AtRiskView'))
const ValueTimelineView = lazy(() => import('./views/ValueTimelineView'))
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

/**
 * PHASE 4 — ADMIN LOCKDOWN: the render-time gate for admin-only tabs.
 *
 * Shown instead of the real view when a non-admin reaches an admin-only tab
 * (deep link, forced tab, stale/coerced persona). Defense in depth: the nav
 * already hides these for non-admins and the server 403s the API, but a
 * non-admin must never SEE admin content even if they force the tab.
 */
function NotAuthorized() {
  return (
    <div
      data-ga-not-authorized="1"
      className="rounded-lg border border-navy-600 bg-navy-800/60 p-8 text-center"
    >
      <div className="text-lg font-semibold text-white">Not authorized</div>
      <p className="mt-2 text-sm text-navy-400">
        Admin access required. This view is restricted to workspace administrators.
      </p>
    </div>
  )
}

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
  // Feedback item A: the full-page use-case workspace. When set, it renders in
  // place of the active view; `pageReturnTab` is where 'Back' returns the user.
  const [pageUcId, setPageUcId] = useState<number | null>(null)
  const [pageReturnTab, setPageReturnTab] = useState<TabId | null>(null)
  const [drawerAssetId, setDrawerAssetId] = useState<number | null>(null)
  const [focusUcId, setFocusUcId] = useState<number | null>(null)
  const [creating, setCreating] = useState(false)
  const [proposalUcId, setProposalUcId] = useState<number | null>(null)

  const { activePersona, isAdmin, loading } = useRole()
  // Executive persona is read-only across the app; the full-page workspace honours
  // that by hiding edit controls (admin + pm get the full editing surface).
  const detailReadOnly = activePersona === 'executive'

  // SAVED VIEWS — per-persona default landing + last-viewed memory.
  //
  // Persona is not known at mount (RoleContext resolves /api/me asynchronously),
  // so the initializer above can only seed the KB deep-link exception. The two
  // effects below own the persona-aware navigation once identity is known:
  //
  //  1. COLD LOAD: land the persona on its remembered tab, or its default home
  //     when there is nothing remembered / the stored tab is no longer visible to
  //     it (resolveLandingTab reuses the persona→visible-tabs logic — no second
  //     list). A KB deep link wins over this: it is an explicit destination, so we
  //     do not overwrite 'knowledge' with a landing tab.
  //  2. PERSONA SWITCH: an admin 'view as' change restores THAT persona's last
  //     tab, or its home when the stored tab is not visible to it — the same
  //     resolveLandingTab call, so the switch and the cold load agree.
  //
  // `landedRef` gates the cold-load landing to run exactly once (after loading
  // clears), and `personaRef` distinguishes the first resolution from a later
  // switch so a cold load does not double as a "switch" and vice versa.
  const landedRef = useRef(false)
  const personaRef = useRef<typeof activePersona | null>(null)
  const deepLinkedRef = useRef<boolean>(slugFromLocation() !== null)

  // Persist the active tab as this persona's last-viewed tab on every change,
  // including programmatic navigation (flywheel focus, proposal handoff, drawer
  // Back). Skipped while identity is still loading — we do not yet know whose
  // memory to write, and a KB deep link is a transient destination, not a home.
  useEffect(() => {
    if (loading) return
    if (deepLinkedRef.current && !landedRef.current) return
    writeLastTab(activePersona, tab)
  }, [activePersona, tab, loading])

  // Cold-load landing + persona-switch restore + visibility fallback, unified so
  // they read the SAME resolveLandingTab and cannot disagree.
  useEffect(() => {
    if (loading) return

    if (!landedRef.current) {
      // First time identity is known this session.
      landedRef.current = true
      personaRef.current = activePersona
      // A KB deep link is an explicit destination — honour it, do not override.
      if (deepLinkedRef.current) return
      const landing = resolveLandingTab(activePersona, readLastTab(activePersona))
      if (landing !== tab) setTab(landing)
      return
    }

    if (personaRef.current !== activePersona) {
      // Persona switch (admin 'view as'): restore that persona's last tab, or its
      // home when the stored tab is not visible to it.
      personaRef.current = activePersona
      setTab(resolveLandingTab(activePersona, readLastTab(activePersona)))
      return
    }

    // Same persona, no switch: only correct an active tab that is not visible to
    // this persona (a nav edit could strand it), falling back to its home.
    if (!visibleTabsForPersona(activePersona).has(tab)) {
      setTab(resolveLandingTab(activePersona, readLastTab(activePersona)))
    }
  }, [activePersona, tab, loading])

  /** Jump to the flywheel with a use case already lit up. */
  const focusOnFlywheel = (id: number) => {
    setFocusUcId(id)
    setTab('flywheel')
  }

  const health = useQuery({ queryKey: ['health'], queryFn: api.health })
  const brandingQuery = useQuery({ queryKey: ['branding'], queryFn: api.branding })
  const lobsQuery = useQuery({ queryKey: ['lobs'], queryFn: api.lobs })
  const useCasesQuery = useQuery({ queryKey: ['use-cases'], queryFn: () => api.useCases() })

  const lobs = lobsQuery.data ?? []
  const useCases = useCasesQuery.data ?? []
  const branding = brandingQuery.data ?? null

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
      case 'atrisk':
        return <AtRiskView />
      case 'timeline':
        return <ValueTimelineView />
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
        if (!isAdmin) return <NotAuthorized />
        return <SourceMappingView />
      case 'taxonomy':
        if (!isAdmin) return <NotAuthorized />
        return <TaxonomyView />
      case 'rules':
        if (!isAdmin) return <NotAuthorized />
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
        if (!isAdmin) return <NotAuthorized />
        return <AccountsView />
      case 'admin':
        if (!isAdmin) return <NotAuthorized />
        return <AdminView />
      case 'branding':
        if (!isAdmin) return <NotAuthorized />
        return <BrandingView />
      default: {
        const unhandledTab: never = tab
        return unhandledTab
      }
    }
  }

  return (
    <div className="min-h-screen">
      <Header env={health.data?.app_env} tab={tab} setTab={setTab} branding={branding} />

      <main className="max-w-[1440px] mx-auto px-6 py-5 space-y-5">
        <TopKpis persona={activePersona} useCases={useCases} />

        {tab === 'portfolio' || tab === 'registry' ? (
          <FilterBar lobs={lobs} showIngestion={tab === 'registry'} />
        ) : null}

        <Suspense fallback={<div className="text-navy-400 py-8 text-center">Loading…</div>}>
          {renderActiveView()}
        </Suspense>

        <footer className="text-center text-xs text-navy-600 pt-4 pb-8">
          <div className="space-y-1">
            <div>
              {branding?.display_name ?? 'AI Value Flywheel'}
            </div>
            <div className="text-navy-700">
              Powered by Databricks
            </div>
          </div>
        </footer>
      </main>

      {drawerUcId != null ? (
        <UseCaseDrawer
          ucId={drawerUcId}
          lobs={lobs}
          readOnly={detailReadOnly}
          onClose={() => setDrawerUcId(null)}
          onOpenUseCase={setDrawerUcId}
          onOpenDataAsset={(id) => {
            setDrawerAssetId(id)
            setDrawerUcId(null)
          }}
          onWriteProposal={(id) => {
            setProposalUcId(id)
            setDrawerUcId(null)
            setTab('proposals')
          }}
          // Feedback item A: expand the drawer into the full-page workspace for the
          // SAME use case. The drawer closes and the page opens; Back returns here.
          onExpand={() => {
            setPageReturnTab(tab)
            setPageUcId(drawerUcId)
            setDrawerUcId(null)
          }}
        />
      ) : null}

      {pageUcId != null ? (
        <UseCaseDetailPage
          ucId={pageUcId}
          lobs={lobs}
          readOnly={detailReadOnly}
          onBack={() => {
            const returnTab = pageReturnTab
            setPageUcId(null)
            setPageReturnTab(null)
            if (returnTab) setTab(returnTab)
          }}
          onOpenUseCase={setPageUcId}
          onOpenDataAsset={(id) => {
            setDrawerAssetId(id)
            setPageUcId(null)
          }}
          onWriteProposal={(id) => {
            setProposalUcId(id)
            setPageUcId(null)
            setTab('proposals')
          }}
        />
      ) : null}

      {drawerAssetId != null ? (
        <DataAssetDrawer
          assetId={drawerAssetId}
          lobs={lobs}
          onClose={() => setDrawerAssetId(null)}
          onOpenUseCase={(id) => {
            setDrawerUcId(id)
            setDrawerAssetId(null)
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

      <AssistantPanel hidden={drawerUcId != null || pageUcId != null} />
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
    //
    // RoleProvider wraps everything so identity + persona are available everywhere,
    // and loads ONCE on app mount (no remount on navigation).
    <ToastProvider>
      <RoleProvider>
        <FilterProvider>
          <AppShell />
        </FilterProvider>
      </RoleProvider>
    </ToastProvider>
  )
}
