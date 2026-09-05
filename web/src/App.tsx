import { lazy, Suspense, type ReactNode } from "react";
import { Navigate, Outlet, Route, Routes, useLocation, useParams, type Location } from "react-router-dom";
import { AuthProvider, useAuth } from "@/lib/auth";
import AppShell from "@/components/layout/AppShell";
import { ConfirmDialogProvider } from "@/components/ConfirmDialog";
import { ChunkLoadErrorBoundary } from "@/components/ChunkLoadErrorBoundary";
import { RouteSkeleton } from "@/components/RouteSkeleton";

// Auth pages — small, eager-loaded so the login form renders without
// a fallback flash on first paint.
import Login from "@/routes/auth/Login";
import RequestReset from "@/routes/auth/RequestReset";
import ResetPassword from "@/routes/auth/ResetPassword";
import Signup from "@/routes/auth/Signup";
// SEC2-014: email-verification landing page.
import Verify from "@/routes/auth/Verify";

// Parts area — the home of the app. Eagerly loaded because every authed
// session lands on /parts and the alternative is a Suspense flash on
// every navigation. Detail-tabs come along for the ride; they're already
// small, and code-splitting them would just trade one set of network
// round-trips for another.
import PartsList from "@/routes/parts/PartsList";
import NotFound from "@/routes/NotFound";
import PartCreate from "@/routes/parts/PartCreate";
import ScanImport from "@/routes/parts/ScanImport";
import StockHistory from "@/routes/parts/StockHistory";
import LotsList from "@/routes/parts/LotsList";

import PartLayout from "@/routes/parts/detail/PartLayout";
import PartInfo from "@/routes/parts/detail/PartInfo";
import PartStock from "@/routes/parts/detail/PartStock";
import PartAddStock from "@/routes/parts/detail/PartAddStock";
import PartRemoveStock from "@/routes/parts/detail/PartRemoveStock";
import PartMoveStock from "@/routes/parts/detail/PartMoveStock";
import PartHistory from "@/routes/parts/detail/PartHistory";
import PartLots from "@/routes/parts/detail/PartLots";
import PartSubstitutes from "@/routes/parts/detail/PartSubstitutes";
import PartMembers from "@/routes/parts/detail/PartMembers";
import PartSettings from "@/routes/parts/detail/PartSettings";
import PartOther from "@/routes/parts/detail/PartOther";
import PartSpecs from "@/routes/parts/detail/PartSpecs";
import PartSourcing from "@/routes/parts/detail/PartSourcing";
import PartCad from "@/routes/parts/detail/PartCad";
import AuthorizedSupplyTab from "@/routes/parts/detail/AuthorizedSupplyTab";
import PartAttachments from "@/routes/parts/detail/PartAttachments";
import PartActivity from "@/routes/parts/detail/PartActivity";

// Storage / lots — small, also eagerly imported.
import StorageListPage from "@/routes/storage/StorageList";
import StorageCreate from "@/routes/storage/StorageCreate";
import {
  StorageDetailLayout,
  StorageInfo,
  StorageHistory,
  StorageSettings,
  StorageOther,
} from "@/routes/storage/StorageDetail";

import {
  LotLayout,
  LotInfo,
  LotMove,
  LotAdjust,
  LotHistory,
} from "@/routes/lots/LotDetail";

// ---------------------------------------------------------------------
// Lazy-loaded sections. Each becomes its own chunk in the build. Picked
// because they pull in their own data-grid + filter machinery and most
// users land on /parts first.
// ---------------------------------------------------------------------
const OrdersList = lazy(() => import("@/routes/orders/OrdersList"));
const OrderCreate = lazy(() => import("@/routes/orders/OrderCreate"));
const OrderDetail = lazy(() => import("@/routes/orders/OrderDetail"));

const BuildsList = lazy(() => import("@/routes/builds/BuildsList"));
const BuildCreate = lazy(() => import("@/routes/builds/BuildCreate"));
const BuildDetail = lazy(() => import("@/routes/builds/BuildDetail"));
const PickListView = lazy(() => import("@/routes/builds/picklist/PickListView"));

// Reports module exports the layout + four sub-reports from one file —
// lazy() requires a default export, so wrap each named export in a
// shim returning an object with `default`. The whole module still ships
// as a single Reports chunk; we just hand it out per-route.
const ReportsLayout = lazy(() =>
  import("@/routes/reports/Reports").then(m => ({ default: m.default }))
);
const LowStockReport = lazy(() =>
  import("@/routes/reports/Reports").then(m => ({ default: m.LowStockReport }))
);
const StockValueReport = lazy(() =>
  import("@/routes/reports/Reports").then(m => ({ default: m.StockValueReport }))
);
const BomShortageReport = lazy(() =>
  import("@/routes/reports/Reports").then(m => ({ default: m.BomShortageReport }))
);
const BomBuyabilityReport = lazy(() => import("@/routes/reports/BomBuyabilityReport"));
const ExpiringLotsReport = lazy(() =>
  import("@/routes/reports/Reports").then(m => ({ default: m.ExpiringLotsReport }))
);
const SourcingRiskReport = lazy(() => import("@/routes/reports/SourcingRiskReport"));
const ReplenishmentCostReport = lazy(() => import("@/routes/reports/ReplenishmentCostReport"));
const AlertsPage = lazy(() => import("@/routes/sourcing/alerts/AlertsPage"));

const ProjectsList = lazy(() => import("@/routes/projects/ProjectsList"));
const ProjectCreate = lazy(() => import("@/routes/projects/ProjectCreate"));
const ProjectLayout = lazy(() => import("@/routes/projects/detail/ProjectLayout"));
const ProjectData = lazy(() => import("@/routes/projects/detail/ProjectData"));
const ProjectBOM = lazy(() => import("@/routes/projects/detail/ProjectBOM"));
const ProjectImport = lazy(() => import("@/routes/projects/detail/ProjectImport"));
const ProjectBuilds = lazy(() => import("@/routes/projects/detail/ProjectBuilds"));
const ProjectOther = lazy(() => import("@/routes/projects/detail/ProjectOther"));
const ProjectSourcingPage = lazy(() => import("@/routes/projects/sourcing/ProjectSourcingPage"));
const PurchasePlanReviewPage = lazy(() => import("@/routes/projects/sourcing/PurchasePlanReviewPage"));

const Account = lazy(() => import("@/routes/settings/Account"));
const WorkspaceSettings = lazy(() => import("@/routes/settings/Workspace"));
const CategoriesSettings = lazy(() => import("@/routes/settings/Categories"));
const ApiTokensSettings = lazy(() => import("@/routes/settings/ApiTokens"));
const KicadSetupSettings = lazy(() => import("@/routes/settings/KicadSetup"));
const LabelTemplatesSettings = lazy(() => import("@/routes/labels/LabelTemplates"));

// Scan landing page (/c/:code). Lazy because it is only ever reached
// from a QR scan, never from in-app navigation.
const CodeResolve = lazy(() => import("@/routes/codes/CodeResolve"));

// Help / About. Lazy for a reason beyond route weight: the manual pages
// and the changelog are inlined as raw markdown strings at build time
// (see lib/userDocs.ts), and react-markdown + remark-gfm come with them.
// Keeping the whole lot out of the entry chunk means a user who never
// opens Help never downloads it.
const HelpLayout = lazy(() => import("@/routes/help/HelpLayout"));
const HelpIndex = lazy(() => import("@/routes/help/HelpIndex"));
const HelpPage = lazy(() => import("@/routes/help/HelpPage"));
const About = lazy(() => import("@/routes/about/About"));

/**
 * Auth gate + AppShell as a layout route. Mounting this once at the top
 * of the authed subtree means `<AppShell>` survives every navigation;
 * its mobile-drawer state, command-palette state, user-menu state, and
 * react-query in-flight requests don't get torn down on every URL change.
 * Pre-fix this lived inside an `element={<Gate><X/></Gate>}` per route,
 * which made React Router treat each one as a separate element and
 * remount AppShell on every navigation (FE CRIT-1).
 */
function Gate() {
  const { me, loading } = useAuth();
  const location = useLocation();
  if (loading) return <div className="p-6 text-muted">Loading…</div>;
  // Preserve the deep-link target across the login round-trip so a user
  // who hits /parts/abc123 with no session lands back there after
  // signing in (FE2-010).
  if (!me) return <Navigate to="/login" replace state={{ from: location }} />;
  return (
    <AppShell>
      <Outlet />
    </AppShell>
  );
}

/**
 * Bounce already-authenticated users away from /login and /signup (FE2-019).
 * Renders null while the session check is in flight (avoids a flash of the
 * login form before the redirect fires). Honours state.from so a deep-link
 * round-trip still lands on the originally-requested page; falls back to
 * /parts, and avoids a /login → /login loop.
 */
function RedirectIfAuthed({ children }: { children: React.ReactNode }) {
  const { me, loading } = useAuth();
  const location = useLocation();
  if (loading) return null;
  if (me) {
    const from = (location.state as { from?: Location } | null)?.from;
    // Preserve search + hash so deep-links like /parts/scan-import?storage_id=abc
    // or /parts/abc?tab=specs#anchor survive the auth round-trip (#304).
    if (from && from.pathname !== "/login" && from.pathname !== "/signup") {
      return (
        <Navigate
          to={{ pathname: from.pathname, search: from.search, hash: from.hash }}
          replace
        />
      );
    }
    return <Navigate to="/parts" replace />;
  }
  return <>{children}</>;
}

const lazyFallback = <RouteSkeleton variant="table" />;

/**
 * Per-route Suspense boundary. Used as the `element` of a <Route> so the
 * skeleton shows in-place (instead of unmounting the whole authed shell)
 * while a lazy chunk is fetched. Cannot wrap <Route> directly inside
 * <Routes> — react-router-dom v6 requires every direct child of <Routes>
 * to be a <Route> or <React.Fragment>, so any Suspense boundary needs to
 * live *inside* a route element.
 */
function LazyRoute({ children }: { children: ReactNode }) {
  return <Suspense fallback={lazyFallback}>{children}</Suspense>;
}

function AuthorizedSupplyTabRoute() {
  const { partId } = useParams<{ partId: string }>();
  if (!partId) return null;
  return <AuthorizedSupplyTab partId={partId} />;
}

export default function App() {
  return (
    <AuthProvider>
      <ConfirmDialogProvider>
        <ChunkLoadErrorBoundary>
        <Suspense fallback={lazyFallback}>
        <Routes>
          <Route path="/login" element={<RedirectIfAuthed><Login /></RedirectIfAuthed>} />
          <Route path="/auth/request-reset" element={<RequestReset />} />
          <Route path="/auth/reset-password" element={<ResetPassword />} />
          <Route path="/signup" element={<RedirectIfAuthed><Signup /></RedirectIfAuthed>} />
          {/* SEC2-014: email verification landing — pre-auth, no Gate wrapper */}
          <Route path="/verify" element={<Verify />} />

          {/* Single layout route — Gate stays mounted across every
              authed navigation, so AppShell + its state survive. */}
          <Route element={<Gate />}>
            <Route path="/" element={<Navigate to="/parts" replace />} />

            <Route path="/parts" element={<PartsList />} />
            <Route path="/parts/archived" element={<PartsList archived />} />
            <Route path="/parts/create" element={<PartCreate />} />
            {/* /parts/scan was a single-MPN-lookup page; superseded by
                the bulk-import flow which already handles the duplicate
                case the way scan did (shows "Already in library" + an
                Open Existing button). Redirect for any external links. */}
            <Route path="/parts/scan" element={<Navigate to="/parts/scan-import" replace />} />
            <Route path="/parts/scan-import" element={<ScanImport />} />
            <Route path="/parts/lots" element={<LotsList />} />
            <Route path="/parts/stock/history" element={<StockHistory />} />

            <Route path="/parts/:partId" element={<PartLayout />}>
              <Route index element={<Navigate to="info" replace />} />
              <Route path="info" element={<PartInfo />} />
              <Route path="specs" element={<PartSpecs />} />
              <Route path="sourcing" element={<PartSourcing />} />
              <Route path="cad" element={<PartCad />} />
              <Route path="authorized-supply" element={<AuthorizedSupplyTabRoute />} />
              <Route path="stock" element={<PartStock />} />
              <Route path="add" element={<PartAddStock />} />
              <Route path="remove" element={<PartRemoveStock />} />
              <Route path="move" element={<PartMoveStock />} />
              <Route path="history" element={<PartHistory />} />
              <Route path="lots" element={<PartLots />} />
              <Route path="substitutes" element={<PartSubstitutes />} />
              <Route path="members" element={<PartMembers />} />
              <Route path="settings" element={<PartSettings />} />
              <Route path="other" element={<PartOther />} />
              <Route path="attachments" element={<PartAttachments />} />
              <Route path="activity" element={<PartActivity />} />
            </Route>

            <Route path="/storage" element={<StorageListPage />} />
            <Route path="/storage/archived" element={<StorageListPage archived />} />
            <Route path="/storage/create" element={<StorageCreate />} />
            <Route path="/storage/:storageId" element={<StorageDetailLayout />}>
              <Route index element={<Navigate to="info" replace />} />
              <Route path="info" element={<StorageInfo />} />
              <Route path="history" element={<StorageHistory />} />
              <Route path="settings" element={<StorageSettings />} />
              <Route path="other" element={<StorageOther />} />
            </Route>

            <Route path="/lots/:lotId" element={<LotLayout />}>
              <Route index element={<Navigate to="info" replace />} />
              <Route path="info" element={<LotInfo />} />
              <Route path="move" element={<LotMove />} />
              <Route path="adjust" element={<LotAdjust />} />
              <Route path="history" element={<LotHistory />} />
            </Route>

            {/* Lazy-loaded sections. Each <Route element> is its own
                Suspense boundary so the fallback shows in-place while
                only that route's chunk is in flight (and so the
                fallback is a valid child of <Routes> — wrapping a
                <Route> in <Suspense> directly inside <Routes> is a
                react-router-dom v6 invariant violation: every direct
                child of <Routes> must be a <Route> or <Fragment>). */}
            <Route path="/orders" element={<LazyRoute><OrdersList /></LazyRoute>} />
            <Route path="/orders/archived" element={<LazyRoute><OrdersList archived /></LazyRoute>} />
            <Route path="/orders/create" element={<LazyRoute><OrderCreate /></LazyRoute>} />
            <Route path="/orders/:orderId" element={<LazyRoute><OrderDetail /></LazyRoute>} />

            <Route path="/builds" element={<LazyRoute><BuildsList /></LazyRoute>} />
            <Route path="/builds/archived" element={<LazyRoute><BuildsList archived /></LazyRoute>} />
            <Route path="/builds/create" element={<LazyRoute><BuildCreate /></LazyRoute>} />
            <Route path="/builds/:buildId" element={<LazyRoute><BuildDetail /></LazyRoute>} />
            {/* Printable pick lists (Track B4). Declared before the bare
                `:buildId` route would ever match them anyway — react-router
                v6 ranks by specificity, not order — but kept adjacent so the
                whole-build and per-stage sheets read as one pair. */}
            <Route path="/builds/:buildId/pick-list" element={<LazyRoute><PickListView /></LazyRoute>} />
            <Route path="/builds/:buildId/stages/:stageId/pick-list" element={<LazyRoute><PickListView /></LazyRoute>} />

            <Route path="/reports" element={<LazyRoute><ReportsLayout /></LazyRoute>}>
              <Route index element={<LazyRoute><LowStockReport /></LazyRoute>} />
              <Route path="value" element={<LazyRoute><StockValueReport /></LazyRoute>} />
              <Route path="replenishment-cost" element={<LazyRoute><ReplenishmentCostReport /></LazyRoute>} />
              <Route path="bom" element={<LazyRoute><BomShortageReport /></LazyRoute>} />
              <Route path="buyability" element={<LazyRoute><BomBuyabilityReport /></LazyRoute>} />
              <Route path="expiring" element={<LazyRoute><ExpiringLotsReport /></LazyRoute>} />
              <Route path="sourcing-risk" element={<LazyRoute><SourcingRiskReport /></LazyRoute>} />
            </Route>
            <Route path="/sourcing/alerts" element={<LazyRoute><AlertsPage /></LazyRoute>} />

            <Route path="/projects" element={<LazyRoute><ProjectsList /></LazyRoute>} />
            <Route path="/projects/archived" element={<LazyRoute><ProjectsList archived /></LazyRoute>} />
            <Route path="/projects/create" element={<LazyRoute><ProjectCreate /></LazyRoute>} />
            <Route path="/projects/:projectId" element={<LazyRoute><ProjectLayout /></LazyRoute>}>
              <Route index element={<Navigate to="data" replace />} />
              <Route path="data" element={<LazyRoute><ProjectData /></LazyRoute>} />
              <Route path="bom" element={<LazyRoute><ProjectBOM /></LazyRoute>} />
              <Route path="import" element={<LazyRoute><ProjectImport /></LazyRoute>} />
              <Route path="builds" element={<LazyRoute><ProjectBuilds /></LazyRoute>} />
              <Route path="sourcing" element={<LazyRoute><ProjectSourcingPage /></LazyRoute>} />
              <Route path="purchase-plans/:planId" element={<LazyRoute><PurchasePlanReviewPage /></LazyRoute>} />
              <Route path="other" element={<LazyRoute><ProjectOther /></LazyRoute>} />
            </Route>

            <Route path="/settings/account" element={<LazyRoute><Account /></LazyRoute>} />
            <Route path="/settings/workspace" element={<LazyRoute><WorkspaceSettings /></LazyRoute>} />
            <Route path="/settings/categories" element={<LazyRoute><CategoriesSettings /></LazyRoute>} />
            <Route path="/settings/api-tokens" element={<LazyRoute><ApiTokensSettings /></LazyRoute>} />
            <Route path="/settings/kicad" element={<LazyRoute><KicadSetupSettings /></LazyRoute>} />
            <Route path="/settings/label-templates" element={<LazyRoute><LabelTemplatesSettings /></LazyRoute>} />

            {/* Scan landing. A printed label's QR encodes /c/<code>; the
                page resolves the code and redirects to the object's own
                detail route. Deliberately short — it is a URL people
                photograph, and every character costs QR density. */}
            <Route path="/c/:code" element={<LazyRoute><CodeResolve /></LazyRoute>} />

            {/* In-app manual. One route per `docs/user/` page so every
                help page is deep-linkable and quotable in a support
                reply. `/about` carries the build identifiers. */}
            <Route path="/help" element={<LazyRoute><HelpLayout /></LazyRoute>}>
              <Route index element={<LazyRoute><HelpIndex /></LazyRoute>} />
              <Route path=":slug" element={<LazyRoute><HelpPage /></LazyRoute>} />
            </Route>
            <Route path="/about" element={<LazyRoute><About /></LazyRoute>} />

            <Route path="*" element={<NotFound />} />
          </Route>
        </Routes>
        </Suspense>
        </ChunkLoadErrorBoundary>
      </ConfirmDialogProvider>
    </AuthProvider>
  );
}
