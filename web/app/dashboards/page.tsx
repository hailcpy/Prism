"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import type { DashboardSummary } from "./types";

export default function DashboardsIndexPage() {
  const apiUrl = useMemo(() => "/api/backend", []);
  const router = useRouter();
  const [dashboards, setDashboards] = useState<DashboardSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    try {
      const response = await fetch(`${apiUrl}/v1/dashboards`);
      if (!response.ok) throw new Error(`status ${response.status}`);
      const body = (await response.json()) as {
        dashboards: DashboardSummary[];
      };
      setDashboards(body.dashboards);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "load failed");
    }
  }, [apiUrl]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleCreate = useCallback(
    async (event: React.FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      if (!name.trim()) return;
      setCreating(true);
      try {
        const response = await fetch(`${apiUrl}/v1/dashboards`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ name: name.trim(), layout: { cells: [] } }),
        });
        if (!response.ok) throw new Error(`status ${response.status}`);
        const body = (await response.json()) as { id: string };
        router.push(`/dashboards/${body.id}`);
      } catch (err) {
        setError(err instanceof Error ? err.message : "create failed");
        setCreating(false);
      }
    },
    [apiUrl, name, router],
  );

  const handleDelete = useCallback(
    async (id: string) => {
      if (!confirm("Delete this dashboard?")) return;
      try {
        const response = await fetch(`${apiUrl}/v1/dashboards/${id}`, {
          method: "DELETE",
        });
        if (!response.ok && response.status !== 204) {
          throw new Error(`status ${response.status}`);
        }
        await load();
      } catch (err) {
        setError(err instanceof Error ? err.message : "delete failed");
      }
    },
    [apiUrl, load],
  );

  return (
    <main className="dash-shell">
      <header className="dash-header">
        <h1>Custom dashboards</h1>
        <nav className="dash-nav">
          <Link href="/">Chat</Link>
          <Link href="/metrics">Metrics</Link>
        </nav>
      </header>
      {error && <div className="dash-error">{error}</div>}
      <form onSubmit={handleCreate} className="dash-create">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="New dashboard name…"
          maxLength={200}
        />
        <button type="submit" disabled={creating || !name.trim()}>
          {creating ? "Creating…" : "Create"}
        </button>
      </form>
      <ul className="dash-list">
        {dashboards.length === 0 && (
          <li className="dash-empty">No dashboards yet.</li>
        )}
        {dashboards.map((dashboard) => (
          <li key={dashboard.id} className="dash-list-row">
            <Link
              href={`/dashboards/${dashboard.id}`}
              className="dash-list-link"
            >
              <span className="dash-list-name">{dashboard.name}</span>
              <span className="dash-list-meta">
                updated {new Date(dashboard.updated_at).toLocaleString()}
              </span>
            </Link>
            <button
              type="button"
              className="dash-list-delete"
              onClick={() => void handleDelete(dashboard.id)}
            >
              Delete
            </button>
          </li>
        ))}
      </ul>
    </main>
  );
}
