"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type DragEvent,
} from "react";

import {
  type DashboardBody,
  type DashboardData,
  type Widget,
  type WidgetCell,
  type WidgetResult,
  METRIC_LABELS,
  WIDGET_PRESETS,
} from "../types";

const RANGE_OPTIONS = [
  { label: "Last 15 min", minutes: 15 },
  { label: "Last 1 hour", minutes: 60 },
  { label: "Last 6 hours", minutes: 360 },
  { label: "Last 24 hours", minutes: 1440 },
];

const SERIES_COLORS = [
  "#0b6b75",
  "#cc5803",
  "#5b3c88",
  "#1f6feb",
  "#a83279",
  "#3a7d44",
];

function randomId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return Math.random().toString(36).slice(2, 12);
}

export default function DashboardEditorPage() {
  const params = useParams<{ id: string }>();
  const dashboardId = params?.id ?? "";
  const apiUrl = useMemo(() => "/api/backend", []);
  const [dashboard, setDashboard] = useState<DashboardBody | null>(null);
  const [name, setName] = useState("");
  const [cells, setCells] = useState<WidgetCell[]>([]);
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rangeMinutes, setRangeMinutes] = useState(60);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);

  const dragCellId = useRef<string | null>(null);

  const loadDashboard = useCallback(async () => {
    try {
      const response = await fetch(`${apiUrl}/v1/dashboards/${dashboardId}`);
      if (!response.ok) throw new Error(`status ${response.status}`);
      const body = (await response.json()) as DashboardBody;
      setDashboard(body);
      setName(body.name);
      setCells(body.layout.cells);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "load failed");
    }
  }, [apiUrl, dashboardId]);

  const loadData = useCallback(async () => {
    try {
      const now = new Date();
      const from = new Date(now.getTime() - rangeMinutes * 60_000);
      const params = new URLSearchParams({
        from: from.toISOString(),
        to: now.toISOString(),
      });
      const response = await fetch(
        `${apiUrl}/v1/dashboards/${dashboardId}/data?${params.toString()}`,
      );
      if (!response.ok) throw new Error(`status ${response.status}`);
      setData((await response.json()) as DashboardData);
    } catch (err) {
      setError(err instanceof Error ? err.message : "data load failed");
    }
  }, [apiUrl, dashboardId, rangeMinutes]);

  useEffect(() => {
    void loadDashboard();
  }, [loadDashboard]);

  useEffect(() => {
    if (!dashboard) return;
    void loadData();
  }, [dashboard, loadData]);

  const handleSave = useCallback(async () => {
    setSaving(true);
    try {
      const response = await fetch(`${apiUrl}/v1/dashboards/${dashboardId}`, {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ name, layout: { cells } }),
      });
      if (!response.ok) throw new Error(`status ${response.status}`);
      const body = (await response.json()) as DashboardBody;
      setDashboard(body);
      setDirty(false);
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "save failed");
    } finally {
      setSaving(false);
    }
  }, [apiUrl, cells, dashboardId, loadData, name]);

  const addCell = useCallback((preset: (typeof WIDGET_PRESETS)[number]) => {
    setCells((current) => {
      const nextY = current.reduce((max, c) => Math.max(max, c.y + c.h), 0);
      return [
        ...current,
        {
          ...preset.cell,
          i: randomId(),
          x: 0,
          y: nextY,
        },
      ];
    });
    setDirty(true);
  }, []);

  const removeCell = useCallback((cellId: string) => {
    setCells((current) => current.filter((c) => c.i !== cellId));
    setDirty(true);
  }, []);

  const updateCell = useCallback(
    (cellId: string, patch: Partial<WidgetCell>) => {
      setCells((current) =>
        current.map((c) => (c.i === cellId ? { ...c, ...patch } : c)),
      );
      setDirty(true);
    },
    [],
  );

  const updateWidget = useCallback((cellId: string, patch: Partial<Widget>) => {
    setCells((current) =>
      current.map((c) =>
        c.i === cellId ? { ...c, widget: { ...c.widget, ...patch } } : c,
      ),
    );
    setDirty(true);
  }, []);

  const handleDragStart = useCallback((cellId: string) => {
    dragCellId.current = cellId;
  }, []);

  const handleDrop = useCallback((targetCellId: string) => {
    const sourceId = dragCellId.current;
    dragCellId.current = null;
    if (!sourceId || sourceId === targetCellId) return;
    setCells((current) => {
      const sourceIdx = current.findIndex((c) => c.i === sourceId);
      const targetIdx = current.findIndex((c) => c.i === targetCellId);
      if (sourceIdx === -1 || targetIdx === -1) return current;
      const next = [...current];
      const [moved] = next.splice(sourceIdx, 1);
      next.splice(targetIdx, 0, moved);
      return next;
    });
    setDirty(true);
  }, []);

  return (
    <main className="min-h-[calc(100vh-56px)] bg-mesh-light dark:bg-mesh-dark flex flex-col text-zinc-900 dark:text-zinc-100">
      <header className="p-4 md:px-8 border-b border-black/10 dark:border-white/10 bg-white/40 dark:bg-zinc-900/40 backdrop-blur-md flex flex-col md:flex-row justify-between items-start md:items-center gap-4 sticky top-0 z-10">
        <div className="flex items-center gap-4 flex-1 w-full max-w-2xl">
          <Link href="/dashboards" className="text-[#009f8f] dark:text-[#ff6d4d] font-bold shrink-0 hover:opacity-80 transition-opacity">
            ← Dashboards
          </Link>
          <input
            className="flex-1 bg-transparent px-3 py-1.5 focus:bg-white dark:focus:bg-zinc-800 border-b border-dashed border-zinc-400 focus:border-solid focus:border-[#009f8f] outline-none font-bold text-lg transition-all"
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              setDirty(true);
            }}
          />
        </div>
        <div className="flex items-center gap-3 w-full md:w-auto overflow-x-auto pb-1 md:pb-0">
          <label className="flex items-center gap-2 text-xs font-semibold text-zinc-600 dark:text-zinc-400 shrink-0">
            RANGE
            <select
              className="px-3 py-1.5 rounded-lg border border-black/10 dark:border-white/10 bg-white dark:bg-zinc-800 text-sm font-medium focus:outline-none focus:ring-2 focus:ring-[#009f8f]/30 text-zinc-900 dark:text-zinc-100"
              value={rangeMinutes}
              onChange={(e) => setRangeMinutes(Number(e.target.value))}
            >
              {RANGE_OPTIONS.map((opt) => (
                <option key={opt.minutes} value={opt.minutes}>
                  {opt.label}
                </option>
              ))}
            </select>
          </label>
          <button 
            type="button" 
            onClick={() => void loadData()}
            className="px-4 py-1.5 rounded-lg bg-zinc-200 dark:bg-zinc-700 text-sm font-semibold hover:bg-zinc-300 dark:hover:bg-zinc-600 transition-colors shrink-0"
          >
            Refresh
          </button>
          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={saving || !dirty}
            className={`px-4 py-1.5 rounded-lg text-sm font-semibold transition-all shrink-0 ${
              dirty 
                ? "bg-gradient-to-br from-[#ff6d4d] to-[#2453ff] text-white hover:opacity-90" 
                : "bg-zinc-200 dark:bg-zinc-800 text-zinc-400 dark:text-zinc-500 cursor-not-allowed"
            }`}
          >
            {saving ? "Saving…" : dirty ? "Save" : "Saved"}
          </button>
        </div>
      </header>
      {error && <div className="p-3 m-4 rounded bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400 text-sm border border-red-200 dark:border-red-800/30">{error}</div>}
      <div className="flex-1 flex flex-col md:flex-row overflow-hidden relative">
        <aside className="w-full md:w-64 p-4 md:p-6 bg-white/40 dark:bg-zinc-900/40 backdrop-blur-3xl border-r border-black/10 dark:border-white/10 flex flex-col overflow-y-auto z-10 shrink-0">
          <h2 className="text-lg font-bold mb-2">Widgets</h2>
          <p className="text-xs text-zinc-500 dark:text-zinc-400 leading-relaxed mb-6">
            Click a widget to add it. Drag a cell header to reorder. Resize with the buttons on each cell.
          </p>
          <div className="flex flex-col gap-2">
            {WIDGET_PRESETS.map((preset) => (
              <button
                key={preset.label}
                type="button"
                className="text-left px-3 py-2 rounded-lg text-sm font-medium bg-white dark:bg-zinc-800 border border-black/5 dark:border-white/5 hover:border-[#009f8f]/50 transition-colors shadow-sm"
                onClick={() => addCell(preset)}
              >
                + {preset.label}
              </button>
            ))}
          </div>
        </aside>
        <section className="flex-1 p-4 md:p-8 overflow-y-auto custom-scrollbar">
          {cells.length === 0 && (
            <div className="text-center bg-white/60 dark:bg-zinc-900/60 backdrop-blur-md rounded-2xl border border-dashed border-black/10 dark:border-white/10 max-w-lg mx-auto py-16 text-zinc-500 dark:text-zinc-400 font-medium">
              Add widgets from the palette to start building your dashboard.
            </div>
          )}
          <div className="grid grid-cols-1 md:grid-cols-12 gap-6 auto-rows-[minmax(120px,auto)] place-items-stretch">
            {cells.map((cell) => (
              <CellView
                key={cell.i}
                cell={cell}
                result={data?.widgets[cell.i]}
                onRemove={() => removeCell(cell.i)}
                onWidthChange={(w) => updateCell(cell.i, { w })}
                onHeightChange={(h) => updateCell(cell.i, { h })}
                onTitleChange={(title) => updateWidget(cell.i, { title })}
                onDragStart={() => handleDragStart(cell.i)}
                onDrop={() => handleDrop(cell.i)}
              />
            ))}
          </div>
        </section>
      </div>
    </main>
  );
}

type CellViewProps = {
  cell: WidgetCell;
  result: WidgetResult | undefined;
  onRemove: () => void;
  onWidthChange: (w: number) => void;
  onHeightChange: (h: number) => void;
  onTitleChange: (title: string) => void;
  onDragStart: () => void;
  onDrop: () => void;
};

// Map cell.w to Tailwind grid columns specifically.
// Note: dynamically injecting `col-span-${w}` can fail in Tailwind if the class isn't detected at compile time,
// but since cell.w goes from 1 to 12, we can just use an inline style or a class mapper.
// I will use an inline style for the grid layout span so it accurately follows the dynamic value.

function CellView({
  cell,
  result,
  onRemove,
  onWidthChange,
  onHeightChange,
  onTitleChange,
  onDragStart,
  onDrop,
}: CellViewProps) {
  const handleDragOver = (event: DragEvent<HTMLDivElement>) =>
    event.preventDefault();
  return (
    <div
      className="bg-white/90 dark:bg-zinc-800/90 backdrop-blur-xl border border-black/10 dark:border-white/10 rounded-2xl shadow-sm flex flex-col overflow-hidden transition-all"
      style={{ gridColumn: `span ${cell.w}`, gridRow: `span ${cell.h}` }}
      draggable
      onDragStart={onDragStart}
      onDragOver={handleDragOver}
      onDrop={onDrop}
    >
      <header className="flex flex-wrap items-center justify-between gap-2 px-4 py-2 border-b border-black/5 dark:border-white/5 bg-black/5 dark:bg-white/5 cursor-move">
        <input
          className="flex-1 min-w-0 bg-transparent outline-none font-bold text-sm text-zinc-900 dark:text-zinc-100"
          value={cell.widget.title ?? METRIC_LABELS[cell.widget.metric_kind]}
          onChange={(e) => onTitleChange(e.target.value)}
          onClick={(e) => e.stopPropagation()}
        />
        <div className="flex items-center gap-1">
          <button
            type="button"
            className="w-6 h-6 flex items-center justify-center rounded text-xs font-mono font-bold text-zinc-500 hover:bg-zinc-200 dark:hover:bg-zinc-700"
            onClick={(e) => { e.stopPropagation(); onWidthChange(Math.max(2, cell.w - 1)); }}
          >
            -w
          </button>
          <button
            type="button"
            className="w-6 h-6 flex items-center justify-center rounded text-xs font-mono font-bold text-zinc-500 hover:bg-zinc-200 dark:hover:bg-zinc-700"
            onClick={(e) => { e.stopPropagation(); onWidthChange(Math.min(12, cell.w + 1)); }}
          >
            +w
          </button>
          <button
            type="button"
            className="w-6 h-6 flex items-center justify-center rounded text-xs font-mono font-bold text-zinc-500 hover:bg-zinc-200 dark:hover:bg-zinc-700 ml-1"
            onClick={(e) => { e.stopPropagation(); onHeightChange(Math.max(1, cell.h - 1)); }}
          >
            -h
          </button>
          <button
            type="button"
            className="w-6 h-6 flex items-center justify-center rounded text-xs font-mono font-bold text-zinc-500 hover:bg-zinc-200 dark:hover:bg-zinc-700"
            onClick={(e) => { e.stopPropagation(); onHeightChange(Math.min(4, cell.h + 1)); }}
          >
            +h
          </button>
          <button 
            type="button" 
            onClick={(e) => { e.stopPropagation(); onRemove(); }} 
            className="w-6 h-6 flex items-center justify-center rounded text-[16px] leading-none text-red-500 hover:bg-red-100 dark:hover:bg-red-900/30 ml-2"
            title="Remove Widget"
          >
            ×
          </button>
        </div>
      </header>
      <div className="flex-1 p-4 flex flex-col justify-center min-h-[100px] overflow-x-auto min-w-0">
        <WidgetView cell={cell} result={result} />
      </div>
    </div>
  );
}

function WidgetView({
  cell,
  result,
}: {
  cell: WidgetCell;
  result: WidgetResult | undefined;
}) {
  if (!result) return <div className="text-zinc-400 text-sm text-center">Loading…</div>;
  if (result.kind === "bignum") {
    return (
      <div className="text-4xl md:text-5xl lg:text-6xl font-black text-center text-zinc-900 dark:text-zinc-100 tracking-tight">
        {formatValue(result.value, cell.widget.metric_kind)}
      </div>
    );
  }
  if (result.kind === "timeseries") return <TimeseriesChart result={result} />;
  if (result.kind === "pie") return <PieChart result={result} />;
  return <TableView result={result} />;
}

function TimeseriesChart({
  result,
}: {
  result: Extract<WidgetResult, { kind: "timeseries" }>;
}) {
  const seriesEntries = Object.entries(result.series);
  const points = seriesEntries.flatMap(([, list]) => list);
  if (points.length === 0)
    return <div className="text-zinc-400 text-sm text-center">No data in range.</div>;
  const xs = points.map((p) => new Date(p.bucket).getTime());
  const ys = points.map((p) => p.value);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const maxY = Math.max(...ys, 1);
  const width = 600;
  const height = 200;
  const padding = 24;
  const projectX = (x: number) =>
    padding + ((x - minX) / Math.max(1, maxX - minX)) * (width - padding * 2);
  const projectY = (y: number) =>
    height - padding - (y / maxY) * (height - padding * 2);
  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-full min-h-[140px] drop-shadow-sm">
      {seriesEntries.map(([key, list], idx) => {
        const path = list
          .map((point, i) => {
            const x = projectX(new Date(point.bucket).getTime());
            const y = projectY(point.value);
            return `${i === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`;
          })
          .join(" ");
        return (
          <path
            key={key}
            d={path}
            fill="none"
            stroke={SERIES_COLORS[idx % SERIES_COLORS.length]}
            strokeWidth={3}
          />
        );
      })}
      {seriesEntries.length > 1 && (
        <g>
          {seriesEntries.map(([key], idx) => (
            <g
              key={key}
              transform={`translate(${padding + idx * 120}, ${padding - 12})`}
            >
              <rect
                width={10}
                height={10}
                rx={2}
                fill={SERIES_COLORS[idx % SERIES_COLORS.length]}
              />
              <text x={16} y={9} fontSize={11} className="fill-zinc-600 dark:fill-zinc-400 font-semibold" textLength="100" fontStyle="11px">
                {key}
              </text>
            </g>
          ))}
        </g>
      )}
    </svg>
  );
}

function PieChart({
  result,
}: {
  result: Extract<WidgetResult, { kind: "pie" }>;
}) {
  const total = result.slices.reduce((sum, slice) => sum + slice.value, 0);
  if (total <= 0)
    return <div className="text-zinc-400 text-sm text-center">No data in range.</div>;
  let cumulative = 0;
  const cx = 100;
  const cy = 100;
  const r = 80;
  return (
    <div className="flex flex-row items-center gap-6 h-full">
      <svg viewBox="0 0 200 200" className="w-[120px] h-[120px] shrink-0 drop-shadow-md">
        {result.slices.map((slice, idx) => {
          const startAngle = (cumulative / total) * Math.PI * 2;
          cumulative += slice.value;
          const endAngle = (cumulative / total) * Math.PI * 2;
          const x1 = cx + r * Math.sin(startAngle);
          const y1 = cy - r * Math.cos(startAngle);
          const x2 = cx + r * Math.sin(endAngle);
          const y2 = cy - r * Math.cos(endAngle);
          const largeArc = endAngle - startAngle > Math.PI ? 1 : 0;
          const path = `M ${cx} ${cy} L ${x1} ${y1} A ${r} ${r} 0 ${largeArc} 1 ${x2} ${y2} Z`;
          return (
            <path
              key={slice.label}
              d={path}
              fill={SERIES_COLORS[idx % SERIES_COLORS.length]}
              stroke="rgba(0,0,0,0.1)"
              strokeWidth="1"
            />
          );
        })}
      </svg>
      <ul className="flex flex-col gap-2 flex-1">
        {result.slices.map((slice, idx) => (
          <li key={slice.label} className="flex items-center gap-2 text-sm font-semibold text-zinc-800 dark:text-zinc-200">
            <span
              className="w-3 h-3 rounded-full shadow-inner shrink-0"
              style={{ background: SERIES_COLORS[idx % SERIES_COLORS.length] }}
            />
            <span className="truncate">{slice.label}</span>
            <span className="ml-auto text-zinc-500 font-mono tracking-tighter">{(slice.value / total * 100).toFixed(0)}%</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function TableView({
  result,
}: {
  result: Extract<WidgetResult, { kind: "table" }>;
}) {
  if (result.rows.length === 0)
    return <div className="text-zinc-400 text-sm text-center">No data in range.</div>;
  return (
    <div className="w-full h-full overflow-auto custom-scrollbar">
      <table className="w-full text-left text-sm border-collapse whitespace-nowrap">
        <thead className="bg-black/5 dark:bg-white/5 sticky top-0 backdrop-blur-sm z-10">
          <tr>
            {result.columns.map((col) => (
              <th key={col} className="p-3 font-semibold text-zinc-600 dark:text-zinc-400 capitalize tracking-wide text-xs">
                {col.replace(/_/g, " ")}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-black/5 dark:divide-white/5">
          {result.rows.map((row, idx) => (
            <tr key={idx} className="hover:bg-black/5 dark:hover:bg-white/5 transition-colors">
              {result.columns.map((col) => (
                <td key={col} className="p-3 text-zinc-800 dark:text-zinc-200 font-mono text-[13px]">
                  {formatCell(row[col])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatCell(value: string | number | undefined): string {
  if (value === undefined || value === null) return "";
  if (typeof value === "number") {
    if (Number.isInteger(value)) return value.toLocaleString();
    return value.toFixed(4);
  }
  return value;
}

function formatValue(value: number, metricKind: string): string {
  if (metricKind === "cost_usd_sum") return `$${value.toFixed(4)}`;
  if (metricKind === "error_rate") return `${(value * 100).toFixed(2)}%`;
  if (metricKind.endsWith("_ms")) return `${Math.round(value)} ms`;
  if (Number.isInteger(value)) return value.toLocaleString();
  return value.toFixed(2);
}
