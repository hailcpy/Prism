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
  cost_usd_sum: number;
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
  const [knownModels, setKnownModels] = useState<string[]>([]);
  const [knownProviders, setKnownProviders] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loadedAt, setLoadedAt] = useState<Date | null>(null);

  const loadDimensions = useCallback(async () => {
    try {
      const response = await fetch(`${apiUrl}/v1/metrics/dimensions`);
      if (!response.ok) return;
      const body = (await response.json()) as {
        models: string[];
        providers: string[];
      };
      setKnownModels(body.models ?? []);
      setKnownProviders(body.providers ?? []);
    } catch {
      // ignore
    }
  }, [apiUrl]);

  useEffect(() => {
    void loadDimensions();
  }, [loadDimensions]);

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

  const models = useMemo(() => {
    const fromBuckets = buckets.map((b) => b.model);
    return Array.from(new Set([...knownModels, ...fromBuckets])).sort();
  }, [knownModels, buckets]);
  const providers = useMemo(() => {
    const fromBuckets = buckets.map((b) => b.provider);
    return Array.from(new Set([...knownProviders, ...fromBuckets])).sort();
  }, [knownProviders, buckets]);

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
    let cost = 0;
    for (const bucket of buckets) {
      count += bucket.count;
      errors += bucket.error_count;
      prompt += bucket.prompt_tokens_sum;
      completion += bucket.completion_tokens_sum;
      cost += bucket.cost_usd_sum ?? 0;
    }
    return {
      count,
      errors,
      errorRate: count > 0 ? (errors / count) * 100 : 0,
      prompt,
      completion,
      cost,
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
    <main className="max-w-6xl mx-auto p-4 md:p-8 space-y-8 min-h-[calc(100vh-56px)] bg-mesh-light dark:bg-mesh-dark text-zinc-900 dark:text-zinc-100">
      <header className="flex flex-col md:flex-row justify-between items-start md:items-end gap-6 mb-8 border-b border-black/10 dark:border-white/10 pb-6">
        <div>
          <h1 className="text-3xl font-bold mb-1">Prism — Metrics</h1>
          <div className="text-sm text-zinc-500 dark:text-zinc-400 font-medium">
            {error ? (
              <span className="text-red-500">Error: {error}</span>
            ) : loadedAt ? (
              <span>
                Updated {loadedAt.toLocaleTimeString()} — auto refresh 15s
              </span>
            ) : (
              <span>Loading…</span>
            )}
          </div>
        </div>

        <div className="flex flex-wrap items-end gap-4 bg-white/60 dark:bg-zinc-900/60 backdrop-blur-md p-4 rounded-xl border border-black/5 dark:border-white/5 shadow-sm">
          <label className="flex flex-col gap-1.5 text-xs font-semibold text-zinc-600 dark:text-zinc-400">
            RANGE
            <select
              className="px-3 py-1.5 rounded-lg border border-black/10 dark:border-white/10 bg-white dark:bg-zinc-800 text-sm font-medium focus:outline-none focus:ring-2 focus:ring-[#009f8f]/30 min-w-[140px] text-zinc-900 dark:text-zinc-100"
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
          <label className="flex flex-col gap-1.5 text-xs font-semibold text-zinc-600 dark:text-zinc-400">
            MODEL
            <select
              className="px-3 py-1.5 rounded-lg border border-black/10 dark:border-white/10 bg-white dark:bg-zinc-800 text-sm font-medium focus:outline-none focus:ring-2 focus:ring-[#009f8f]/30 min-w-[140px] text-zinc-900 dark:text-zinc-100"
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
          <label className="flex flex-col gap-1.5 text-xs font-semibold text-zinc-600 dark:text-zinc-400">
            PROVIDER
            <select
              className="px-3 py-1.5 rounded-lg border border-black/10 dark:border-white/10 bg-white dark:bg-zinc-800 text-sm font-medium focus:outline-none focus:ring-2 focus:ring-[#009f8f]/30 min-w-[140px] text-zinc-900 dark:text-zinc-100"
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
          <button
            type="button"
            onClick={() => void load()}
            className="px-4 py-1.5 h-[34px] rounded-lg bg-zinc-200 dark:bg-zinc-700 text-sm font-semibold hover:bg-zinc-300 dark:hover:bg-zinc-600 transition-colors"
          >
            Refresh
          </button>
        </div>
      </header>

      <section className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
        <SummaryCard
          label="Cost (USD)"
          value={formatCost(totals.cost)}
          accent
        />
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

      <section className="grid md:grid-cols-2 gap-6">
        <Chart
          title="Cost over time"
          series={buildSeries((b) => b.cost_usd_sum ?? 0)}
          yLabel="USD"
          formatY={(v) => formatCost(v)}
        />
        <Chart
          title="Latency p50"
          series={buildSeries((b) => b.latency_p50_ms)}
          yLabel="ms"
        />
        <Chart
          title="Latency p95"
          series={buildSeries((b) => b.latency_p95_ms)}
          yLabel="ms"
        />
        <Chart
          title="Throughput"
          series={buildSeries((b) => b.count)}
          yLabel="calls / min"
        />
        <Chart
          title="Error rate"
          series={buildSeries((b) =>
            b.count > 0 ? (b.error_count / b.count) * 100 : 0,
          )}
          yLabel="%"
        />
        <Chart
          title="Tokens"
          series={buildSeries(
            (b) => b.prompt_tokens_sum + b.completion_tokens_sum,
          )}
          yLabel="tokens / min"
        />
      </section>
    </main>
  );
}

function SummaryCard({
  label,
  value,
  accent = false,
}: {
  label: string;
  value: string;
  accent?: boolean;
}) {
  return (
    <div
      className={`p-5 rounded-2xl border shadow-sm text-center backdrop-blur-md ${
        accent
          ? "bg-gradient-to-br from-[#ff6d4d]/10 to-[#2453ff]/10 border-[#009f8f]/30"
          : "bg-white/60 dark:bg-zinc-900/60 border-black/5 dark:border-white/5"
      }`}
    >
      <div className="text-[11px] font-bold text-zinc-500 dark:text-zinc-400 uppercase tracking-widest mb-2">
        {label}
      </div>
      <div
        className={`text-2xl font-bold ${
          accent
            ? "bg-gradient-to-br from-[#ff6d4d] to-[#2453ff] bg-clip-text text-transparent"
            : "text-zinc-900 dark:text-zinc-100"
        }`}
      >
        {value}
      </div>
    </div>
  );
}

function formatCost(value: number): string {
  if (!Number.isFinite(value) || value === 0) return "$0.00";
  if (value >= 100) return `$${value.toFixed(0)}`;
  if (value >= 1) return `$${value.toFixed(2)}`;
  return `$${value.toFixed(4)}`;
}

function Chart({
  title,
  series,
  yLabel,
  formatY,
}: {
  title: string;
  series: Series[];
  yLabel: string;
  formatY?: (v: number) => string;
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
  const padding = { top: 16, right: 16, bottom: 28, left: 56 };
  const innerW = width - padding.left - padding.right;
  const innerH = height - padding.top - padding.bottom;

  const xScale = (x: number) =>
    xMax === xMin
      ? padding.left + innerW / 2
      : padding.left + ((x - xMin) / (xMax - xMin)) * innerW;
  const yScale = (y: number) =>
    padding.top + innerH - ((y - yMin) / (yMax - yMin)) * innerH;

  return (
    <div className="bg-white/60 dark:bg-zinc-900/60 backdrop-blur-md p-6 rounded-2xl border border-black/5 dark:border-white/5 shadow-sm flex flex-col">
      <div className="flex items-baseline justify-between mb-4">
        <h2 className="text-sm font-bold">{title}</h2>
        <span className="text-[10px] font-bold text-zinc-400 dark:text-zinc-500 uppercase tracking-widest">
          {yLabel}
        </span>
      </div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={title}
        className="w-full h-auto drop-shadow-sm"
      >
        <rect
          x={padding.left}
          y={padding.top}
          width={innerW}
          height={innerH}
          fill="transparent"
        />
        <text
          x={12}
          y={padding.top + innerH / 2}
          fontSize="10"
          textAnchor="middle"
          transform={`rotate(-90 12 ${padding.top + innerH / 2})`}
          className="fill-zinc-500 dark:fill-zinc-400 font-semibold uppercase tracking-wider"
        >
          {yLabel}
        </text>
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
                className="stroke-zinc-200 dark:stroke-zinc-800"
              />
              <text
                x={padding.left - 6}
                y={yPx + 4}
                textAnchor="end"
                fontSize="10"
                className="fill-zinc-500"
              >
                {formatY ? formatY(yVal) : formatNumber(yVal)}
              </text>
            </g>
          );
        })}
        {!empty && (
          <>
            <text
              x={padding.left}
              y={height - 8}
              fontSize="10"
              className="fill-zinc-400"
            >
              {new Date(xMin).toLocaleTimeString()}
            </text>
            <text
              x={padding.left + innerW}
              y={height - 8}
              fontSize="10"
              className="fill-zinc-400"
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
              <path
                d={d}
                stroke={s.color}
                fill="none"
                strokeWidth={2.5}
                className="drop-shadow-sm"
              />
              {s.points.map((p, i) => (
                <circle
                  key={i}
                  cx={xScale(p.x)}
                  cy={yScale(p.y)}
                  r={3}
                  fill={s.color}
                  className="stroke-white dark:stroke-zinc-900"
                  strokeWidth="1.5"
                >
                  <title>{`${s.key} @ ${new Date(p.x).toLocaleTimeString()}: ${formatY ? formatY(p.y) : formatNumber(p.y)} ${yLabel}`}</title>
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
            className="fill-zinc-400"
          >
            No data
          </text>
        )}
      </svg>
      <div className="mt-6 flex flex-wrap gap-4 text-xs font-semibold">
        {series.map((s) => (
          <span
            key={s.key}
            className="flex items-center gap-2 text-zinc-700 dark:text-zinc-300"
          >
            <span
              className="w-3 h-3 rounded-full shadow-inner"
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
