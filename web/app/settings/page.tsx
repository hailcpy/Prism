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

  return (
    <main className="settings-shell">
      <header className="settings-header">
        <h1>Settings</h1>
        <p>
          Provider credentials are encrypted at rest. The chat page lists models
          discovered from the default credential of each provider.
        </p>
      </header>

      <section className="settings-card">
        <h2>Add credential</h2>
        <p className="card-subtitle">
          Pick a provider — the required fields are loaded from the backend.
        </p>
        <form onSubmit={onSave} className="cred-form">
          <div className="cred-field">
            <label>Provider</label>
            <select
              value={providerId}
              onChange={(e) => onProviderChange(e.target.value)}
            >
              {providers.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </select>
          </div>
          <div className="cred-field">
            <label>Name</label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. personal-key"
              required
            />
          </div>
          {selected?.secret_fields.map((f) => (
            <div className="cred-field" key={`s-${f.name}`}>
              <label>
                {f.label}
                {f.required ? "" : " (optional)"}
              </label>
              <input
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
            <div className="cred-field" key={`m-${f.name}`}>
              <label>
                {f.label}
                {f.required ? "" : " (optional)"}
              </label>
              <input
                value={fields[f.name] ?? ""}
                onChange={(e) => setField(f.name, e.target.value)}
                placeholder={f.default ?? ""}
                required={f.required}
              />
              {f.default ? (
                <span className="field-hint">default: {f.default}</span>
              ) : null}
            </div>
          ))}

          <div className="cred-actions" style={{ gridColumn: "1 / -1" }}>
            <button type="submit" className="primary" disabled={busy}>
              {busy ? "Saving…" : "Save credential"}
            </button>
            <button
              type="button"
              className="secondary"
              onClick={onTest}
              disabled={test.status === "testing"}
            >
              {test.status === "testing" ? "Testing…" : "Test connection"}
            </button>
            <label className="cred-default">
              <input
                type="checkbox"
                checked={isDefault}
                onChange={(e) => setIsDefault(e.target.checked)}
              />
              Set as default for this provider
            </label>
          </div>

          {test.status === "ok" ? (
            <div className="cred-status ok" style={{ gridColumn: "1 / -1" }}>
              ✓ Connected. Models available:{" "}
              {test.models.length === 0
                ? "(none returned)"
                : test.models.join(", ")}
              {test.models.length === 8 ? "…" : ""}
            </div>
          ) : null}
          {test.status === "err" ? (
            <div className="cred-status err" style={{ gridColumn: "1 / -1" }}>
              ✗ {test.error}
            </div>
          ) : null}
          {error ? (
            <div className="cred-status err" style={{ gridColumn: "1 / -1" }}>
              {error}
            </div>
          ) : null}
        </form>
      </section>

      <section className="settings-card">
        <h2>Saved credentials</h2>
        {credentials.length === 0 ? (
          <p className="card-subtitle">No credentials yet.</p>
        ) : null}
        {grouped.map(([provider, items]) => (
          <div key={provider}>
            <h3
              style={{
                margin: "8px 0 10px 0",
                fontSize: 13,
                color: "#6b7c80",
                textTransform: "uppercase",
                letterSpacing: "0.05em",
              }}
            >
              {providers.find((p) => p.id === provider)?.label ?? provider}
            </h3>
            <ul className="cred-list">
              {items.map((c) => (
                <li
                  key={c.id}
                  className={`cred-row ${c.is_default ? "default" : ""}`}
                >
                  <span className="provider-chip">{c.provider}</span>
                  <div>
                    <div className="cred-name">
                      {c.name}
                      {c.is_default ? (
                        <span className="default-pill">default</span>
                      ) : null}
                    </div>
                    <div className="cred-meta">
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
                  <div className="cred-actions-row">
                    <button
                      className="danger"
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
