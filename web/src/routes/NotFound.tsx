import { Link } from "react-router-dom";

/**
 * 404 page. Replaces the previous one-line catch-all (FE MED-4).
 * Renders inside the AppShell layout so the sidebar / nav stay
 * visible — the user can recover by clicking somewhere familiar
 * rather than having to retype the URL.
 */
export default function NotFound() {
  return (
    <div className="card p-6 max-w-xl space-y-3">
      <h1 className="text-xl font-semibold">Not found</h1>
      <p className="text-sm text-muted">
        The page you're looking for doesn't exist, or you don't have
        access to it. Common destinations:
      </p>
      <ul className="text-sm space-y-1">
        <li>
          <Link to="/parts" className="text-accent hover:underline">
            Parts
          </Link>
        </li>
        <li>
          <Link to="/orders" className="text-accent hover:underline">
            Orders
          </Link>
        </li>
        <li>
          <Link to="/builds" className="text-accent hover:underline">
            Builds
          </Link>
        </li>
        <li>
          <Link to="/projects" className="text-accent hover:underline">
            Projects
          </Link>
        </li>
        <li>
          <Link to="/reports" className="text-accent hover:underline">
            Reports
          </Link>
        </li>
        <li>
          <Link to="/storage" className="text-accent hover:underline">
            Storage
          </Link>
        </li>
        <li>
          <Link to="/settings/workspace" className="text-accent hover:underline">
            Workspace settings
          </Link>
        </li>
      </ul>
    </div>
  );
}
