"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";

import {
  Credential,
  ProviderSpec,
  deleteCredential,
  getCredentials,
  getProviders,
  upsertCredential,
  validateCredential,
} from "@/lib/api";
import { TailwindSelect } from "@/lib/tailwind-select";

type FieldState = Record<string, string>;

type TestState =
  | { status: "idle" }
  | { status: "testing" }
  | { status: "ok"; models: string[] }
  | { status: "err"; error: string };

function blankFields(spec: ProviderSpec | undefined): FieldState {
  if (!spec) return {};
  const out: FieldState = {};
  for (const f of spec.secret_fields) out[f.name] = "";
  for (const f of spec.metadata_fields) out[f.name] = f.default ?? "";
  return out;
}

export default function SettingsPage() {
  const [providers, setProviders] = useState<ProviderSpec[]>([]);
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [providerId, setProviderId] = useState<string>("");
  const [name, setName] = useState("");
  const [fields, setFields] = useState<FieldState>({});
  const [isDefault, setIsDefault] = useState(true);
  const [busy, setBusy] = useState(false);
  const [test, setTest] = useState<TestState>({ status: "idle" });
  const [error, setError] = useState<string | null>(null);

  const selected = useMemo(
    () => providers.find((p) => p.id === providerId),
    [providers, providerId],
  );

  async function refresh() {
    try {
      const [p, c] = await Promise.all([getProviders(), getCredentials()]);
      setProviders(p);
      setCredentials(c);
      if (!providerId && p[0]) {
        setProviderId(p[0].id);
        setFields(blankFields(p[0]));
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "load failed");
    }
  }

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function onProviderChange(id: string) {
    setProviderId(id);
    setFields(blankFields(providers.find((p) => p.id === id)));
    setTest({ status: "idle" });
  }

  function setField(name: string, value: string) {
    setFields((cur) => ({ ...cur, [name]: value }));
  }

  function partition(): { secrets: FieldState; metadata: FieldState } {
    const secrets: FieldState = {};
    const metadata: FieldState = {};
    if (!selected) return { secrets, metadata };
    for (const f of selected.secret_fields) {
      const v = fields[f.name] ?? "";
      if (v) secrets[f.name] = v;
    }
    for (const f of selected.metadata_fields) {
      const v = fields[f.name] ?? "";
      if (v) metadata[f.name] = v;
    }
    return { secrets, metadata };
  }

  async function onTest() {
    if (!selected) return;
    setTest({ status: "testing" });
    try {
      const { secrets, metadata } = partition();
      const result = await validateCredential({
        provider: selected.id,
        secrets,
        metadata,
      });
      if (result.ok)
        setTest({ status: "ok", models: result.models.slice(0, 8) });
      else setTest({ status: "err", error: result.error ?? "failed" });
    } catch (e) {
      setTest({
        status: "err",
        error: e instanceof Error ? e.message : "test failed",
      });
    }
  }

  async function onSave(event: FormEvent) {
    event.preventDefault();
    if (!selected || !name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const { secrets, metadata } = partition();
      await upsertCredential({
        provider: selected.id,
        name: name.trim(),
        secrets,
        metadata,
        is_default: isDefault,
      });
      setName("");
      setFields(blankFields(selected));
      setTest({ status: "idle" });
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "save failed");
    } finally {
      setBusy(false);
    }
  }

  async function onDelete(id: string) {
    if (!confirm("Delete this credential?")) return;
    try {
      await deleteCredential(id);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "delete failed");
    }
  }

  const grouped = useMemo(() => {
    const map = new Map<string, Credential[]>();
    for (const c of credentials) {
      const arr = map.get(c.provider) ?? [];
      arr.push(c);
      map.set(c.provider, arr);
    }
    return Array.from(map.entries());
  }, [credentials]);
  const providerOptions = providers.map((provider) => ({
    value: provider.id,
    label: provider.label,
  }));

  return (
    <main className="max-w-4xl mx-auto p-8 space-y-8 min-h-[calc(100vh-56px)] bg-mesh-light dark:bg-mesh-dark text-zinc-900 dark:text-zinc-100">
      <header className="mb-8">
        <h1 className="text-3xl font-bold mb-2">Settings</h1>
        <p className="text-zinc-500 dark:text-zinc-400">
          Provider credentials are encrypted at rest. The chat page lists models
          discovered from the default credential of each provider.
        </p>
      </header>

      <section className="bg-white/60 dark:bg-zinc-900/60 backdrop-blur-md rounded-2xl border border-black/10 dark:border-white/10 p-6 shadow-sm">
        <h2 className="text-xl font-bold mb-1">Add credential</h2>
        <p className="text-sm text-zinc-500 dark:text-zinc-400 mb-6">
          Pick a provider — the required fields are loaded from the backend.
        </p>
        <form
          onSubmit={onSave}
          className="grid grid-cols-1 sm:grid-cols-2 gap-4"
        >
          <div className="flex flex-col gap-1">
            <label className="text-sm font-semibold">Provider</label>
            <TailwindSelect
              value={providerId}
              options={providerOptions}
              onChange={onProviderChange}
              ariaLabel="Provider"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-sm font-semibold">Name</label>
            <input
              className="px-3 py-2 rounded-lg border border-black/10 dark:border-white/10 bg-white dark:bg-zinc-800 text-sm outline-none focus:ring-2 focus:ring-[#009f8f]/30"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. personal-key"
              required
            />
          </div>
          {selected?.secret_fields.map((f) => (
            <div className="flex flex-col gap-1" key={`s-${f.name}`}>
              <label className="text-sm font-semibold">
                {f.label}
                {f.required ? "" : " (optional)"}
              </label>
              <input
                className="px-3 py-2 rounded-lg border border-black/10 dark:border-white/10 bg-white dark:bg-zinc-800 text-sm outline-none focus:ring-2 focus:ring-[#009f8f]/30"
                type="password"
                value={fields[f.name] ?? ""}
                onChange={(e) => setField(f.name, e.target.value)}
                placeholder={f.required ? "required" : "optional"}
                required={f.required}
                autoComplete="off"
              />
            </div>
          ))}
          {selected?.metadata_fields.map((f) => (
            <div className="flex flex-col gap-1" key={`m-${f.name}`}>
              <label className="text-sm font-semibold">
                {f.label}
                {f.required ? "" : " (optional)"}
              </label>
              <input
                className="px-3 py-2 rounded-lg border border-black/10 dark:border-white/10 bg-white dark:bg-zinc-800 text-sm outline-none focus:ring-2 focus:ring-[#009f8f]/30"
                value={fields[f.name] ?? ""}
                onChange={(e) => setField(f.name, e.target.value)}
                placeholder={f.default ?? ""}
                required={f.required}
              />
              {f.default ? (
                <span className="text-xs text-zinc-500">
                  default: {f.default}
                </span>
              ) : null}
            </div>
          ))}

          <div className="sm:col-span-2 flex flex-wrap items-center gap-4 mt-2">
            <button
              type="submit"
              className="px-4 py-2 rounded-lg bg-gradient-to-br from-[#ff6d4d] to-[#2453ff] text-white font-semibold transition-all hover:opacity-90 disabled:opacity-50"
              disabled={busy}
            >
              {busy ? "Saving…" : "Save credential"}
            </button>
            <button
              type="button"
              className="px-4 py-2 rounded-lg bg-zinc-200 dark:bg-zinc-800 text-zinc-800 dark:text-zinc-200 font-semibold transition-all hover:bg-zinc-300 dark:hover:bg-zinc-700 disabled:opacity-50"
              onClick={onTest}
              disabled={test.status === "testing"}
            >
              {test.status === "testing" ? "Testing…" : "Test connection"}
            </button>
            <label className="flex items-center gap-2 text-sm text-zinc-700 dark:text-zinc-300 ml-auto cursor-pointer">
              <input
                type="checkbox"
                className="rounded border-gray-300 focus:ring-[#009f8f] h-4 w-4"
                checked={isDefault}
                onChange={(e) => setIsDefault(e.target.checked)}
              />
              Set as default for this provider
            </label>
          </div>

          {test.status === "ok" ? (
            <div className="sm:col-span-2 p-3 mt-2 rounded bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-400 text-sm border border-green-200 dark:border-green-800/30">
              ✓ Connected. Models available:{" "}
              {test.models.length === 0
                ? "(none returned)"
                : test.models.join(", ")}
              {test.models.length === 8 ? "…" : ""}
            </div>
          ) : null}
          {test.status === "err" ? (
            <div className="sm:col-span-2 p-3 mt-2 rounded bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400 text-sm border border-red-200 dark:border-red-800/30">
              ✗ {test.error}
            </div>
          ) : null}
          {error ? (
            <div className="sm:col-span-2 p-3 mt-2 rounded bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400 text-sm border border-red-200 dark:border-red-800/30">
              {error}
            </div>
          ) : null}
        </form>
      </section>

      <section className="bg-white/60 dark:bg-zinc-900/60 backdrop-blur-md rounded-2xl border border-black/10 dark:border-white/10 p-6 shadow-sm">
        <h2 className="text-xl font-bold mb-4">Saved credentials</h2>
        {credentials.length === 0 ? (
          <p className="text-zinc-500 dark:text-zinc-400">
            No credentials yet.
          </p>
        ) : null}
        {grouped.map(([provider, items]) => (
          <div key={provider} className="mb-6 last:mb-0">
            <h3 className="text-xs font-bold text-zinc-500 dark:text-zinc-400 uppercase tracking-widest mb-3">
              {providers.find((p) => p.id === provider)?.label ?? provider}
            </h3>
            <ul className="space-y-3">
              {items.map((c) => (
                <li
                  key={c.id}
                  className={`flex items-center justify-between p-4 rounded-xl border ${
                    c.is_default
                      ? "border-[#009f8f]/30 bg-gradient-to-br from-[#009f8f]/5 to-transparent dark:border-[#009f8f]/50 dark:from-[#009f8f]/10"
                      : "border-black/5 dark:border-white/5 bg-white dark:bg-zinc-800/50"
                  }`}
                >
                  <div className="flex items-center gap-4">
                    <span className="px-2.5 py-1 rounded bg-zinc-100 dark:bg-zinc-800 text-xs font-mono text-zinc-600 dark:text-zinc-300 border border-zinc-200 dark:border-zinc-700">
                      {c.provider}
                    </span>
                    <div>
                      <div className="font-semibold text-zinc-900 dark:text-zinc-100 flex items-center gap-2">
                        {c.name}
                        {c.is_default ? (
                          <span className="px-2 py-0.5 rounded-full bg-[#009f8f]/10 text-[#009f8f] text-[10px] uppercase font-bold tracking-wider">
                            default
                          </span>
                        ) : null}
                      </div>
                      <div className="text-xs text-zinc-500 dark:text-zinc-400 mt-1">
                        {Object.entries(c.metadata)
                          .map(([k, v]) => `${k}: ${v}`)
                          .join(" · ") || "—"}
                        {c.last_test_ok === true
                          ? ` · last test ✓ ${c.last_tested_at ?? ""}`
                          : c.last_test_ok === false
                            ? ` · last test ✗ ${c.last_test_error ?? ""}`
                            : ""}
                      </div>
                    </div>
                  </div>
                  <div>
                    <button
                      className="px-3 py-1.5 rounded-lg text-sm font-medium text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors"
                      onClick={() => void onDelete(c.id)}
                    >
                      Delete
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </section>
    </main>
  );
}
