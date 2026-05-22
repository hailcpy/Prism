"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

type Bucket = {
  minute_bucket: string;
  model: string;
  provider: string;
  count: number;
  error_count: number;
  latency_p50_ms: number;
  latency_p95_ms: number;
  prompt_tokens_sum: number;
  completion_tokens_sum: number;
};

type Series = {
  key: string;
  color: string;
  points: { x: number; y: number }[];
};

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

export default function MetricsPage() {
  const apiUrl = useMemo(() => "/api/backend", []);
  const [rangeMinutes, setRangeMinutes] = useState(60);
  const [modelFilter, setModelFilter] = useState<string>("");
  const [providerFilter, setProviderFilter] = useState<string>("");
  const [buckets, setBuckets] = useState<Bucket[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loadedAt, setLoadedAt] = useState<Date | null>(null);

  const load = useCallback(async () => {
    const now = new Date();
    const from = new Date(now.getTime() - rangeMinutes * 60_000);
    const params = new URLSearchParams();
    params.set("from", from.toISOString());
    params.set("to", now.toISOString());
    if (modelFilter) params.append("model", modelFilter);
    if (providerFilter) params.append("provider", providerFilter);
    try {
      const response = await fetch(`${apiUrl}/v1/metrics?${params.toString()}`);
      if (!response.ok) {
        throw new Error(`status ${response.status}`);
      }
      const body = (await response.json()) as { buckets: Bucket[] };
      setBuckets(body.buckets);
      setError(null);
      setLoadedAt(new Date());
    } catch (err) {
      setError(err instanceof Error ? err.message : "load failed");
    }
  }, [apiUrl, rangeMinutes, modelFilter, providerFilter]);

  useEffect(() => {
    void load();
    const handle = window.setInterval(() => void load(), 15_000);
    return () => window.clearInterval(handle);
  }, [load]);

  const models = useMemo(
    () => Array.from(new Set(buckets.map((b) => b.model))).sort(),
    [buckets],
  );
  const providers = useMemo(
    () => Array.from(new Set(buckets.map((b) => b.provider))).sort(),
    [buckets],
  );

  const seriesByModel = useMemo(() => {
    const grouped: Record<string, Bucket[]> = {};
    for (const bucket of buckets) {
      (grouped[bucket.model] ??= []).push(bucket);
    }
    for (const list of Object.values(grouped)) {
      list.sort((a, b) => a.minute_bucket.localeCompare(b.minute_bucket));
    }
    return grouped;
  }, [buckets]);

  const totals = useMemo(() => {
    let count = 0;
    let errors = 0;
    let prompt = 0;
    let completion = 0;
    for (const bucket of buckets) {
      count += bucket.count;
      errors += bucket.error_count;
      prompt += bucket.prompt_tokens_sum;
      completion += bucket.completion_tokens_sum;
    }
    return {
      count,
      errors,
      errorRate: count > 0 ? (errors / count) * 100 : 0,
      prompt,
      completion,
    };
  }, [buckets]);

  const buildSeries = useCallback(
    (extract: (b: Bucket) => number): Series[] => {
      const entries = Object.entries(seriesByModel);
      return entries.map(([model, list], idx) => ({
        key: model,
        color: SERIES_COLORS[idx % SERIES_COLORS.length],
        points: list.map((b) => ({
          x: new Date(b.minute_bucket).getTime(),
          y: extract(b),
        })),
      }));
    },
    [seriesByModel],
  );

  return (
    <main className="metrics-shell">
      <header className="metrics-header">
        <h1>Prism — Dashboard</h1>
        <div className="metrics-controls">
          <label>
            Range
            <select
              value={rangeMinutes}
              onChange={(e) => setRangeMinutes(Number(e.target.value))}
            >
              {RANGE_OPTIONS.map((option) => (
                <option key={option.minutes} value={option.minutes}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Model
            <select
              value={modelFilter}
              onChange={(e) => setModelFilter(e.target.value)}
            >
              <option value="">All</option>
              {models.map((model) => (
                <option key={model} value={model}>
                  {model}
                </option>
              ))}
            </select>
          </label>
          <label>
            Provider
            <select
              value={providerFilter}
              onChange={(e) => setProviderFilter(e.target.value)}
            >
              <option value="">All</option>
              {providers.map((provider) => (
                <option key={provider} value={provider}>
                  {provider}
                </option>
              ))}
            </select>
          </label>
          <button type="button" onClick={() => void load()}>
            Refresh
          </button>
        </div>
        <div className="metrics-status">
          {error ? (
            <span className="metrics-error">Error: {error}</span>
          ) : loadedAt ? (
            <span>
              Updated {loadedAt.toLocaleTimeString()} — auto refresh 15s
            </span>
          ) : (
            <span>Loading…</span>
          )}
        </div>
      </header>

      <section className="metrics-summary">
        <SummaryCard label="Calls" value={totals.count.toLocaleString()} />
        <SummaryCard label="Errors" value={totals.errors.toLocaleString()} />
        <SummaryCard
          label="Error rate"
          value={`${totals.errorRate.toFixed(2)}%`}
        />
        <SummaryCard
          label="Prompt tokens"
          value={totals.prompt.toLocaleString()}
        />
        <SummaryCard
          label="Completion tokens"
          value={totals.completion.toLocaleString()}
        />
      </section>

      <section className="metrics-grid">
        <Chart
          title="Latency p50 (ms)"
          series={buildSeries((b) => b.latency_p50_ms)}
          yLabel="ms"
        />
        <Chart
          title="Latency p95 (ms)"
          series={buildSeries((b) => b.latency_p95_ms)}
          yLabel="ms"
        />
        <Chart
          title="Throughput (calls / min)"
          series={buildSeries((b) => b.count)}
          yLabel="calls"
        />
        <Chart
          title="Error rate (%)"
          series={buildSeries((b) =>
            b.count > 0 ? (b.error_count / b.count) * 100 : 0,
          )}
          yLabel="%"
        />
        <Chart
          title="Tokens / min"
          series={buildSeries(
            (b) => b.prompt_tokens_sum + b.completion_tokens_sum,
          )}
          yLabel="tokens"
        />
      </section>
    </main>
  );
}

function SummaryCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="metrics-card">
      <div className="metrics-card-label">{label}</div>
      <div className="metrics-card-value">{value}</div>
    </div>
  );
}

function Chart({
  title,
  series,
  yLabel,
}: {
  title: string;
  series: Series[];
  yLabel: string;
}) {
  const allPoints = series.flatMap((s) => s.points);
  const empty = allPoints.length === 0;

  const xs = allPoints.map((p) => p.x);
  const ys = allPoints.map((p) => p.y);
  const xMin = empty ? 0 : Math.min(...xs);
  const xMax = empty ? 1 : Math.max(...xs);
  const yMin = 0;
  const yMaxRaw = empty ? 1 : Math.max(...ys, 1);
  const yMax = yMaxRaw * 1.1;

  const width = 480;
  const height = 200;
  const padding = { top: 16, right: 16, bottom: 28, left: 44 };
  const innerW = width - padding.left - padding.right;
  const innerH = height - padding.top - padding.bottom;

  const xScale = (x: number) =>
    xMax === xMin
      ? padding.left + innerW / 2
      : padding.left + ((x - xMin) / (xMax - xMin)) * innerW;
  const yScale = (y: number) =>
    padding.top + innerH - ((y - yMin) / (yMax - yMin)) * innerH;

  return (
    <div className="metrics-chart">
      <h2>{title}</h2>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={title}>
        <rect
          x={padding.left}
          y={padding.top}
          width={innerW}
          height={innerH}
          fill="#ffffff"
          stroke="#d9e2e5"
        />
        {[0, 0.25, 0.5, 0.75, 1].map((t) => {
          const yVal = yMin + (yMax - yMin) * (1 - t);
          const yPx = padding.top + innerH * t;
          return (
            <g key={t}>
              <line
                x1={padding.left}
                x2={padding.left + innerW}
                y1={yPx}
                y2={yPx}
                stroke="#eef2f3"
              />
              <text
                x={padding.left - 6}
                y={yPx + 4}
                textAnchor="end"
                fontSize="10"
                fill="#647176"
              >
                {formatNumber(yVal)}
              </text>
            </g>
          );
        })}
        {!empty && (
          <>
            <text x={padding.left} y={height - 8} fontSize="10" fill="#647176">
              {new Date(xMin).toLocaleTimeString()}
            </text>
            <text
              x={padding.left + innerW}
              y={height - 8}
              fontSize="10"
              fill="#647176"
              textAnchor="end"
            >
              {new Date(xMax).toLocaleTimeString()}
            </text>
          </>
        )}
        {series.map((s) => {
          if (s.points.length === 0) return null;
          const d = s.points
            .map(
              (p, i) =>
                `${i === 0 ? "M" : "L"} ${xScale(p.x).toFixed(1)} ${yScale(p.y).toFixed(1)}`,
            )
            .join(" ");
          return (
            <g key={s.key}>
              <path d={d} stroke={s.color} fill="none" strokeWidth={2} />
              {s.points.map((p, i) => (
                <circle
                  key={i}
                  cx={xScale(p.x)}
                  cy={yScale(p.y)}
                  r={2.5}
                  fill={s.color}
                >
                  <title>{`${s.key} @ ${new Date(p.x).toLocaleTimeString()}: ${formatNumber(p.y)} ${yLabel}`}</title>
                </circle>
              ))}
            </g>
          );
        })}
        {empty && (
          <text
            x={width / 2}
            y={height / 2}
            textAnchor="middle"
            fontSize="12"
            fill="#94a3a8"
          >
            No data
          </text>
        )}
      </svg>
      <div className="metrics-legend">
        {series.map((s) => (
          <span key={s.key} className="metrics-legend-item">
            <span
              className="metrics-legend-swatch"
              style={{ background: s.color }}
            />
            {s.key}
          </span>
        ))}
      </div>
    </div>
  );
}

function formatNumber(value: number): string {
  if (!Number.isFinite(value)) return "—";
  if (Math.abs(value) >= 1000)
    return value.toLocaleString(undefined, { maximumFractionDigits: 0 });
  if (Math.abs(value) >= 10) return value.toFixed(0);
  return value.toFixed(2);
}
