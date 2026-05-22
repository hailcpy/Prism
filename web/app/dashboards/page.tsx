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
    <main className="max-w-5xl mx-auto p-6 md:p-12 space-y-8 min-h-[calc(100vh-56px)] bg-mesh-light dark:bg-mesh-dark text-zinc-900 dark:text-zinc-100">
      <header className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <h1 className="text-3xl font-bold">Custom dashboards</h1>
        <nav className="flex items-center gap-4 text-sm font-semibold">
          <Link href="/" className="text-[#009f8f] hover:text-[#0b6b75] dark:text-[#ff6d4d] dark:hover:text-[#ff8f75] transition-colors">Chat</Link>
          <Link href="/metrics" className="text-[#009f8f] hover:text-[#0b6b75] dark:text-[#ff6d4d] dark:hover:text-[#ff8f75] transition-colors">Metrics</Link>
        </nav>
      </header>
      {error && <div className="p-3 mt-2 rounded bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400 text-sm border border-red-200 dark:border-red-800/30">{error}</div>}
      <form onSubmit={handleCreate} className="flex flex-col sm:flex-row gap-3">
        <input
          className="flex-1 px-4 py-2.5 rounded-lg border border-black/10 dark:border-white/10 bg-white/60 dark:bg-zinc-900/60 backdrop-blur-md text-sm outline-none focus:ring-2 focus:ring-[#009f8f]/30"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="New dashboard name…"
          maxLength={200}
        />
        <button 
          type="submit" 
          disabled={creating || !name.trim()}
          className="px-6 py-2.5 rounded-lg bg-gradient-to-br from-[#ff6d4d] to-[#2453ff] text-white font-semibold transition-all hover:opacity-90 disabled:opacity-50"
        >
          {creating ? "Creating…" : "Create"}
        </button>
      </form>
      <ul className="flex flex-col gap-3">
        {dashboards.length === 0 && (
          <li className="text-center text-zinc-500 py-12 bg-white/60 dark:bg-zinc-900/60 backdrop-blur-md rounded-xl border border-dashed border-black/10 dark:border-white/10">No dashboards yet.</li>
        )}
        {dashboards.map((dashboard) => (
          <li key={dashboard.id} className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 p-4 rounded-xl border border-black/10 dark:border-white/10 bg-white/60 dark:bg-zinc-900/60 backdrop-blur-md shadow-sm transition-all hover:shadow-md">
            <Link
              href={`/dashboards/${dashboard.id}`}
              className="flex flex-col flex-1"
            >
              <span className="font-semibold text-lg">{dashboard.name}</span>
              <span className="text-xs text-zinc-500 dark:text-zinc-400 mt-1">
                updated {new Date(dashboard.updated_at).toLocaleString()}
              </span>
            </Link>
            <button
              type="button"
              className="px-4 py-2 rounded-lg text-sm font-medium border border-red-200 dark:border-red-800/50 text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors self-start sm:self-center"
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
