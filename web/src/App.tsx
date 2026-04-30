import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "@/lib/auth";
import AppShell from "@/components/layout/AppShell";

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
import PartCreate from "@/routes/parts/PartCreate";
import PartScan from "@/routes/parts/PartScan";
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

function Gate({ children }: { children: React.ReactNode }) {
  const { me, loading } = useAuth();
  if (loading) return <div className="p-6 text-muted">Loading…</div>;
  if (!me) return <Navigate to="/login" replace />;
  return <AppShell>{children}</AppShell>;
}

const lazyFallback = <div className="p-6 text-muted">Loading…</div>;

export default function App() {
  return (
    <AuthProvider>
      <Suspense fallback={lazyFallback}>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/signup" element={<Signup />} />

          <Route path="/" element={<Gate><Navigate to="/parts" replace /></Gate>} />

          <Route path="/parts" element={<Gate><PartsList /></Gate>} />
          <Route path="/parts/archived" element={<Gate><PartsList archived /></Gate>} />
          <Route path="/parts/create" element={<Gate><PartCreate /></Gate>} />
          <Route path="/parts/scan" element={<Gate><PartScan /></Gate>} />
          <Route path="/parts/scan-import" element={<Gate><ScanImport /></Gate>} />
          <Route path="/parts/lots" element={<Gate><LotsList /></Gate>} />
          <Route path="/parts/stock/history" element={<Gate><StockHistory /></Gate>} />

          <Route path="/parts/:partId" element={<Gate><PartLayout /></Gate>}>
            <Route index element={<Navigate to="info" replace />} />
            <Route path="info" element={<PartInfo />} />
            <Route path="specs" element={<PartSpecs />} />
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

          <Route path="/storage" element={<Gate><StorageListPage /></Gate>} />
          <Route path="/storage/archived" element={<Gate><StorageListPage archived /></Gate>} />
          <Route path="/storage/create" element={<Gate><StorageCreate /></Gate>} />
          <Route path="/storage/:storageId" element={<Gate><StorageDetailLayout /></Gate>}>
            <Route index element={<Navigate to="info" replace />} />
            <Route path="info" element={<StorageInfo />} />
            <Route path="history" element={<StorageHistory />} />
            <Route path="settings" element={<StorageSettings />} />
            <Route path="other" element={<StorageOther />} />
          </Route>

          <Route path="/lots/:lotId" element={<Gate><LotLayout /></Gate>}>
            <Route index element={<Navigate to="info" replace />} />
            <Route path="info" element={<LotInfo />} />
            <Route path="move" element={<LotMove />} />
            <Route path="adjust" element={<LotAdjust />} />
            <Route path="history" element={<LotHistory />} />
          </Route>

          <Route path="/orders" element={<Gate><OrdersList /></Gate>} />
          <Route path="/orders/archived" element={<Gate><OrdersList archived /></Gate>} />
          <Route path="/orders/create" element={<Gate><OrderCreate /></Gate>} />
          <Route path="/orders/:orderId" element={<Gate><OrderDetail /></Gate>} />

          <Route path="/builds" element={<Gate><BuildsList /></Gate>} />
          <Route path="/builds/archived" element={<Gate><BuildsList archived /></Gate>} />
          <Route path="/builds/create" element={<Gate><BuildCreate /></Gate>} />
          <Route path="/builds/:buildId" element={<Gate><BuildDetail /></Gate>} />

          <Route path="/reports" element={<Gate><ReportsLayout /></Gate>}>
            <Route index element={<LowStockReport />} />
            <Route path="value" element={<StockValueReport />} />
            <Route path="bom" element={<BomShortageReport />} />
            <Route path="expiring" element={<ExpiringLotsReport />} />
          </Route>

          <Route path="/projects" element={<Gate><ProjectsList /></Gate>} />
          <Route path="/projects/archived" element={<Gate><ProjectsList archived /></Gate>} />
          <Route path="/projects/create" element={<Gate><ProjectCreate /></Gate>} />
          <Route path="/projects/:projectId" element={<Gate><ProjectLayout /></Gate>}>
            <Route index element={<Navigate to="data" replace />} />
            <Route path="data" element={<ProjectData />} />
            <Route path="bom" element={<ProjectBOM />} />
            <Route path="import" element={<ProjectImport />} />
            <Route path="builds" element={<ProjectBuilds />} />
            <Route path="other" element={<ProjectOther />} />
          </Route>

          <Route path="/settings/account" element={<Gate><Account /></Gate>} />
          <Route path="/settings/workspace" element={<Gate><WorkspaceSettings /></Gate>} />

          <Route path="*" element={<Gate><div className="text-muted">Not found.</div></Gate>} />
        </Routes>
      </Suspense>
    </AuthProvider>
  );
}
