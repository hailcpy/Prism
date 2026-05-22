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
    <main className="dash-shell">
      <header className="dash-header">
        <div className="dash-header-left">
          <Link href="/dashboards" className="dash-back">
            ← Dashboards
          </Link>
          <input
            className="dash-name-input"
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              setDirty(true);
            }}
          />
        </div>
        <div className="dash-header-actions">
          <label>
            Range
            <select
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
          <button type="button" onClick={() => void loadData()}>
            Refresh
          </button>
          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={saving || !dirty}
            className="dash-save"
          >
            {saving ? "Saving…" : dirty ? "Save" : "Saved"}
          </button>
        </div>
      </header>
      {error && <div className="dash-error">{error}</div>}
      <div className="dash-editor">
        <aside className="dash-palette">
          <h2>Widgets</h2>
          <p className="dash-palette-hint">
            Click a widget to add it. Drag a cell to reorder. Resize with the
            buttons on each cell.
          </p>
          {WIDGET_PRESETS.map((preset) => (
            <button
              key={preset.label}
              type="button"
              className="dash-palette-item"
              onClick={() => addCell(preset)}
            >
              {preset.label}
            </button>
          ))}
        </aside>
        <section className="dash-grid">
          {cells.length === 0 && (
            <div className="dash-grid-empty">
              Add widgets from the palette to start building your dashboard.
            </div>
          )}
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
      className="dash-cell"
      style={{ gridColumn: `span ${cell.w}`, gridRow: `span ${cell.h}` }}
      draggable
      onDragStart={onDragStart}
      onDragOver={handleDragOver}
      onDrop={onDrop}
    >
      <header className="dash-cell-header">
        <input
          className="dash-cell-title"
          value={cell.widget.title ?? METRIC_LABELS[cell.widget.metric_kind]}
          onChange={(e) => onTitleChange(e.target.value)}
        />
        <div className="dash-cell-controls">
          <button
            type="button"
            onClick={() => onWidthChange(Math.max(2, cell.w - 1))}
          >
            −W
          </button>
          <button
            type="button"
            onClick={() => onWidthChange(Math.min(12, cell.w + 1))}
          >
            +W
          </button>
          <button
            type="button"
            onClick={() => onHeightChange(Math.max(1, cell.h - 1))}
          >
            −H
          </button>
          <button
            type="button"
            onClick={() => onHeightChange(Math.min(4, cell.h + 1))}
          >
            +H
          </button>
          <button type="button" onClick={onRemove} className="dash-cell-remove">
            ×
          </button>
        </div>
      </header>
      <div className="dash-cell-body">
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
  if (!result) return <div className="dash-widget-empty">Loading…</div>;
  if (result.kind === "bignum") {
    return (
      <div className="dash-bignum">
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
    return <div className="dash-widget-empty">No data in range.</div>;
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
    <svg viewBox={`0 0 ${width} ${height}`} className="dash-chart">
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
            strokeWidth={2}
          />
        );
      })}
      {seriesEntries.length > 1 && (
        <g>
          {seriesEntries.map(([key], idx) => (
            <g
              key={key}
              transform={`translate(${padding + idx * 120}, ${padding - 8})`}
            >
              <rect
                width={10}
                height={10}
                fill={SERIES_COLORS[idx % SERIES_COLORS.length]}
              />
              <text x={14} y={9} fontSize={11}>
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
    return <div className="dash-widget-empty">No data in range.</div>;
  let cumulative = 0;
  const cx = 100;
  const cy = 100;
  const r = 80;
  return (
    <div className="dash-pie-wrap">
      <svg viewBox="0 0 200 200" className="dash-pie">
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
            />
          );
        })}
      </svg>
      <ul className="dash-pie-legend">
        {result.slices.map((slice, idx) => (
          <li key={slice.label}>
            <span
              className="dash-pie-swatch"
              style={{ background: SERIES_COLORS[idx % SERIES_COLORS.length] }}
            />
            <span>{slice.label}</span>
            <span className="dash-pie-value">{slice.value.toFixed(2)}</span>
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
    return <div className="dash-widget-empty">No data in range.</div>;
  return (
    <div className="dash-table-wrap">
      <table className="dash-table">
        <thead>
          <tr>
            {result.columns.map((col) => (
              <th key={col}>{col}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {result.rows.map((row, idx) => (
            <tr key={idx}>
              {result.columns.map((col) => (
                <td key={col}>{formatCell(row[col])}</td>
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
