import { lazy, Suspense } from "react";
import { Navigate, Outlet, Route, Routes, useLocation } from "react-router-dom";
import { AuthProvider, useAuth } from "@/lib/auth";
import AppShell from "@/components/layout/AppShell";
import { ConfirmDialogProvider } from "@/components/ConfirmDialog";
import { ChunkLoadErrorBoundary } from "@/components/ChunkLoadErrorBoundary";
import { RouteSkeleton } from "@/components/RouteSkeleton";

// Auth pages — small, eager-loaded so the login form renders without
// a fallback flash on first paint.
import Login from "@/routes/auth/Login";
import Signup from "@/routes/auth/Signup";

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
const ExpiringLotsReport = lazy(() =>
  import("@/routes/reports/Reports").then(m => ({ default: m.ExpiringLotsReport }))
);

const ProjectsList = lazy(() => import("@/routes/projects/ProjectsList"));
const ProjectCreate = lazy(() => import("@/routes/projects/ProjectCreate"));
const ProjectLayout = lazy(() => import("@/routes/projects/detail/ProjectLayout"));
const ProjectData = lazy(() => import("@/routes/projects/detail/ProjectData"));
const ProjectBOM = lazy(() => import("@/routes/projects/detail/ProjectBOM"));
const ProjectImport = lazy(() => import("@/routes/projects/detail/ProjectImport"));
const ProjectBuilds = lazy(() => import("@/routes/projects/detail/ProjectBuilds"));
const ProjectOther = lazy(() => import("@/routes/projects/detail/ProjectOther"));

const Account = lazy(() => import("@/routes/settings/Account"));
const WorkspaceSettings = lazy(() => import("@/routes/settings/Workspace"));

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

const lazyFallback = <RouteSkeleton variant="table" />;

export default function App() {
  return (
    <AuthProvider>
      <ConfirmDialogProvider>
        <ChunkLoadErrorBoundary>
        <Suspense fallback={lazyFallback}>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/signup" element={<Signup />} />

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

            <Suspense fallback={<RouteSkeleton variant="table" />}>
              <Route path="/orders" element={<OrdersList />} />
              <Route path="/orders/archived" element={<OrdersList archived />} />
              <Route path="/orders/create" element={<OrderCreate />} />
              <Route path="/orders/:orderId" element={<OrderDetail />} />
            </Suspense>

            <Suspense fallback={<RouteSkeleton variant="table" />}>
              <Route path="/builds" element={<BuildsList />} />
              <Route path="/builds/archived" element={<BuildsList archived />} />
              <Route path="/builds/create" element={<BuildCreate />} />
              <Route path="/builds/:buildId" element={<BuildDetail />} />
            </Suspense>

            <Suspense fallback={<RouteSkeleton variant="table" />}>
              <Route path="/reports" element={<ReportsLayout />}>
                <Route index element={<LowStockReport />} />
                <Route path="value" element={<StockValueReport />} />
                <Route path="bom" element={<BomShortageReport />} />
                <Route path="expiring" element={<ExpiringLotsReport />} />
              </Route>
            </Suspense>

            <Suspense fallback={<RouteSkeleton variant="table" />}>
              <Route path="/projects" element={<ProjectsList />} />
              <Route path="/projects/archived" element={<ProjectsList archived />} />
              <Route path="/projects/create" element={<ProjectCreate />} />
              <Route path="/projects/:projectId" element={<ProjectLayout />}>
                <Route index element={<Navigate to="data" replace />} />
                <Route path="data" element={<ProjectData />} />
                <Route path="bom" element={<ProjectBOM />} />
                <Route path="import" element={<ProjectImport />} />
                <Route path="builds" element={<ProjectBuilds />} />
                <Route path="other" element={<ProjectOther />} />
              </Route>
            </Suspense>

            <Route path="/settings/account" element={<Account />} />
            <Route path="/settings/workspace" element={<WorkspaceSettings />} />

            <Route path="*" element={<NotFound />} />
          </Route>
        </Routes>
        </Suspense>
        </ChunkLoadErrorBoundary>
      </ConfirmDialogProvider>
    </AuthProvider>
  );
}
