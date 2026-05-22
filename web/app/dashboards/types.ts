export type WidgetKind = "timeseries" | "bignum" | "table" | "pie";

export type MetricKind =
  | "cost_usd_sum"
  | "count"
  | "error_rate"
  | "latency_p50_ms"
  | "latency_p95_ms"
  | "prompt_tokens_sum"
  | "completion_tokens_sum"
  | "top_conversations_by_cost";

export type GroupBy = "model" | "provider";

export type WidgetFilters = {
  model: string[];
  provider: string[];
};

export type WidgetOptions = {
  group_by: GroupBy | null;
  limit: number;
};

export type Widget = {
  kind: WidgetKind;
  metric_kind: MetricKind;
  title: string | null;
  filters: WidgetFilters;
  options: WidgetOptions;
};

export type WidgetCell = {
  i: string;
  x: number;
  y: number;
  w: number;
  h: number;
  widget: Widget;
};

export type DashboardSummary = {
  id: string;
  name: string;
  updated_at: string;
};

export type DashboardBody = {
  id: string;
  name: string;
  owner_id: string | null;
  layout: { cells: WidgetCell[] };
  created_at: string;
  updated_at: string;
};

export type DashboardData = {
  from: string;
  to: string;
  widgets: Record<string, WidgetResult>;
};

export type TimeseriesResult = {
  kind: "timeseries";
  group_by: GroupBy | null;
  series: Record<string, { bucket: string; value: number }[]>;
};

export type BignumResult = { kind: "bignum"; value: number };

export type PieResult = {
  kind: "pie";
  group_by: GroupBy;
  slices: { label: string; value: number }[];
};

export type TableResult = {
  kind: "table";
  columns: string[];
  rows: Record<string, string | number>[];
};

export type WidgetResult =
  | TimeseriesResult
  | BignumResult
  | PieResult
  | TableResult;

export const METRIC_LABELS: Record<MetricKind, string> = {
  cost_usd_sum: "Cost (USD)",
  count: "Calls",
  error_rate: "Error rate",
  latency_p50_ms: "Latency p50 (ms)",
  latency_p95_ms: "Latency p95 (ms)",
  prompt_tokens_sum: "Prompt tokens",
  completion_tokens_sum: "Completion tokens",
  top_conversations_by_cost: "Top conversations by cost",
};

export const WIDGET_PRESETS: {
  label: string;
  cell: Omit<WidgetCell, "i" | "x" | "y">;
}[] = [
  {
    label: "Cost — total (bignum)",
    cell: {
      w: 3,
      h: 1,
      widget: {
        kind: "bignum",
        metric_kind: "cost_usd_sum",
        title: "Cost (USD)",
        filters: { model: [], provider: [] },
        options: { group_by: null, limit: 10 },
      },
    },
  },
  {
    label: "Calls — total (bignum)",
    cell: {
      w: 3,
      h: 1,
      widget: {
        kind: "bignum",
        metric_kind: "count",
        title: "Calls",
        filters: { model: [], provider: [] },
        options: { group_by: null, limit: 10 },
      },
    },
  },
  {
    label: "Error rate (bignum)",
    cell: {
      w: 3,
      h: 1,
      widget: {
        kind: "bignum",
        metric_kind: "error_rate",
        title: "Error rate",
        filters: { model: [], provider: [] },
        options: { group_by: null, limit: 10 },
      },
    },
  },
  {
    label: "Cost over time (timeseries by model)",
    cell: {
      w: 6,
      h: 2,
      widget: {
        kind: "timeseries",
        metric_kind: "cost_usd_sum",
        title: "Cost over time",
        filters: { model: [], provider: [] },
        options: { group_by: "model", limit: 10 },
      },
    },
  },
  {
    label: "Latency p95 over time",
    cell: {
      w: 6,
      h: 2,
      widget: {
        kind: "timeseries",
        metric_kind: "latency_p95_ms",
        title: "Latency p95 (ms)",
        filters: { model: [], provider: [] },
        options: { group_by: "model", limit: 10 },
      },
    },
  },
  {
    label: "Cost share (pie by model)",
    cell: {
      w: 4,
      h: 2,
      widget: {
        kind: "pie",
        metric_kind: "cost_usd_sum",
        title: "Cost by model",
        filters: { model: [], provider: [] },
        options: { group_by: "model", limit: 10 },
      },
    },
  },
  {
    label: "Top conversations by cost (table)",
    cell: {
      w: 8,
      h: 2,
      widget: {
        kind: "table",
        metric_kind: "top_conversations_by_cost",
        title: "Top conversations",
        filters: { model: [], provider: [] },
        options: { group_by: null, limit: 10 },
      },
    },
  },
];
