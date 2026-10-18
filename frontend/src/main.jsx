import React, { StrictMode, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  ReferenceLine,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import "./styles.css";

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
const today = new Date().toISOString().slice(0, 10);
const startOfYear = `${new Date().getFullYear()}-01-01`;

function formatValue(value, suffix = "") {
  return value == null ? "—" : `${Number(value).toFixed(1)}${suffix}`;
}

function dateLabel(value) {
  return new Intl.DateTimeFormat("en-SG", { day: "2-digit", month: "short" }).format(
    new Date(`${value}T00:00:00`),
  );
}

function isPartial(reading) {
  if (!reading.rain_reading_count) return false;
  const raw = reading.raw_json ? JSON.parse(reading.raw_json) : null;
  const stationCount = raw?.rainfall?.stations?.length || 1;
  return reading.rain_reading_count < 288 * stationCount * 0.9;
}

async function api(path, options = {}) {
  const response = await fetch(`${API_URL}${path}`, {
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${response.status})`);
  }
  return response.json();
}

function Card({ label, value, basis }) {
  return (
    <article className="card">
      <span className="card-label">{label}</span>
      <strong>{value}</strong>
      <small>{basis == null ? "No data yet" : `Based on ${basis} ${basis === 1 ? "day" : "days"}`}</small>
    </article>
  );
}

function EmptyState() {
  return <div className="empty-state"><strong>No data yet, fetch to begin</strong><span>Choose a date range above and fetch readings from data.gov.sg.</span></div>;
}

function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return <div className="chart-tooltip"><strong>{label}</strong>{payload.map((item) => <div key={item.dataKey}>{item.name}: {formatValue(item.value)}</div>)}</div>;
}

function Dashboard() {
  const [from, setFrom] = useState(startOfYear);
  const [to, setTo] = useState(today);
  const [readings, setReadings] = useState([]);
  const [stats, setStats] = useState(null);
  const [fetchResults, setFetchResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(null);

  const query = `from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`;
  const loadDashboard = async () => {
    setError("");
    try {
      const [rows, summary] = await Promise.all([
        api(`/api/readings?${query}`),
        api(`/api/stats?${query}`),
      ]);
      setReadings(rows);
      setStats(summary);
    } catch (err) {
      setError(err.message);
    }
  };

  useEffect(() => { loadDashboard(); }, []);

  const chartData = useMemo(() => readings.map((reading) => ({
    ...reading,
    label: dateLabel(reading.date),
    partial: isPartial(reading),
  })), [readings]);

  const runFetch = async (backfill = false) => {
    setLoading(true);
    setError("");
    setFetchResults([]);
    try {
      const range = { from, to };
      const result = await api(backfill ? "/api/backfill" : `/api/fetch?from=${range.from}&to=${range.to}`, { method: "POST" });
      setFetchResults(result);
      await loadDashboard();
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const saveManual = async (reading, values) => {
    try {
      const updated = await api(`/api/readings/${reading.date}`, {
        method: "PATCH",
        body: JSON.stringify(values),
      });
      setReadings((current) => current.map((item) => item.date === updated.date ? updated : item));
      setEditing(null);
      await loadDashboard();
    } catch (err) {
      setError(err.message);
    }
  };

  return (
    <main className="shell">
      <header className="hero">
        <div>
          <p className="eyebrow">NEA • DATA.GOV.SG</p>
          <h1>Woodlands Rain &amp; Haze</h1>
          <p className="subtitle">A factual daily view of weather and air quality. Missing readings stay missing.</p>
        </div>
        <div className="status-dot"><span /> Live API dashboard</div>
      </header>

      <section className="controls panel" aria-label="Dashboard controls">
        <label>From<input type="date" value={from} onChange={(event) => setFrom(event.target.value)} /></label>
        <label>To<input type="date" value={to} onChange={(event) => setTo(event.target.value)} /></label>
        <button onClick={loadDashboard} disabled={loading}>Apply range</button>
        <button className="primary" onClick={() => runFetch(false)} disabled={loading}>
          {loading ? "Fetching…" : "Fetch from data.gov.sg"}
        </button>
        <button className="secondary" onClick={() => runFetch(true)} disabled={loading}>Backfill missing days</button>
      </section>

      {error && <div className="alert" role="alert">{error}</div>}
      {fetchResults.length > 0 && <section className="fetch-summary panel"><strong>Fetch results</strong><div className="result-list">{fetchResults.map((result) => <span key={result.date} className={`result ${result.status}`}>{result.date}: {result.status}{result.message ? ` — ${result.message}` : ""}</span>)}</div></section>}

      <section className="cards">
        <Card label="Days with rainfall" value={stats?.days_with_data ?? "—"} basis={stats?.basis_days?.rainfall} />
        <Card label="Total rainfall" value={formatValue(stats?.total_rainfall_mm, " mm")} basis={stats?.basis_days?.rainfall} />
        <Card label="Wettest day" value={stats?.wettest_day ? `${dateLabel(stats.wettest_day)} (${formatValue(stats.wettest_day_rainfall_mm, " mm")})` : "—"} basis={stats?.basis_days?.rainfall} />
        <Card label="Average PSI" value={formatValue(stats?.average_psi)} basis={stats?.basis_days?.psi} />
        <Card label="Average PM2.5" value={formatValue(stats?.average_pm25)} basis={stats?.basis_days?.pm25} />
        <Card label="Haze days with rain after" value={stats?.haze_days_with_rain_after ?? "—"} basis={stats?.basis_days?.haze} />
      </section>

      {readings.length === 0 ? <EmptyState /> : <>
        <section className="chart-grid">
          <article className="panel chart-card"><h2>Rainfall</h2><ResponsiveContainer width="100%" height={280}><ComposedChart data={chartData}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="label" /><YAxis /><Tooltip content={<ChartTooltip />} /><Legend /><Bar dataKey="rain_24h_mm" name="24h (mm)" fill="#2e7d9a" /><Bar dataKey="rain_48h_mm" name="48h (mm)" fill="#9bc9d6" /></ComposedChart></ResponsiveContainer></article>
          <article className="panel chart-card"><h2>Air quality</h2><ResponsiveContainer width="100%" height={280}><ComposedChart data={chartData}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="label" /><YAxis yAxisId="psi" /><YAxis yAxisId="pm" orientation="right" /><Tooltip content={<ChartTooltip />} /><Legend /><ReferenceLine yAxisId="psi" y={100} stroke="#d97706" strokeDasharray="5 5" label="PSI 100" /><Line yAxisId="psi" type="monotone" dataKey="psi_24h" name="PSI" stroke="#d97706" connectNulls={false} /><Line yAxisId="pm" type="monotone" dataKey="pm25" name="PM2.5" stroke="#8b5cf6" connectNulls={false} /></ComposedChart></ResponsiveContainer></article>
          <article className="panel chart-card"><h2>Temperature</h2><ResponsiveContainer width="100%" height={280}><ComposedChart data={chartData}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="label" /><YAxis /><Tooltip content={<ChartTooltip />} /><Line type="monotone" dataKey="temp_c" name="Temperature (°C)" stroke="#ef6c55" strokeWidth={2} connectNulls={false} /></ComposedChart></ResponsiveContainer></article>
        </section>

        <section className="panel table-panel"><div className="section-heading"><div><h2>Daily readings</h2><p>Hover the completeness badge for counts and contributing stations.</p></div><span>{readings.length} rows</span></div><div className="table-wrap"><table><thead><tr><th>Date</th><th>Rain 24h</th><th>Rain 48h</th><th>Temp °C</th><th>PSI 24h</th><th>PM2.5</th><th>Haze</th><th>Rain after haze</th><th>Notes</th><th>Completeness</th><th /></tr></thead><tbody>{readings.map((reading) => <ReadingRow key={reading.date} reading={reading} editing={editing === reading.date} onEdit={() => setEditing(reading.date)} onCancel={() => setEditing(null)} onSave={saveManual} />)}</tbody></table></div></section>
      </>}
    </main>
  );
}

function ReadingRow({ reading, editing, onEdit, onCancel, onSave }) {
  const [values, setValues] = useState({ haze: reading.haze ?? "", rain_after_haze: reading.rain_after_haze ?? "", sampling_notes: reading.sampling_notes ?? "" });
  const partial = isPartial(reading);
  const raw = reading.raw_json ? JSON.parse(reading.raw_json) : {};
  const stations = raw.rainfall?.stations?.join(", ") || "none";
  if (editing) return <tr><td>{reading.date}</td><td>{formatValue(reading.rain_24h_mm, " mm")}</td><td>{formatValue(reading.rain_48h_mm, " mm")}</td><td>{formatValue(reading.temp_c, " °C")}</td><td>{formatValue(reading.psi_24h)} {reading.psi_24h > 100 && <span className="hint">PSI &gt; 100</span>}</td><td>{formatValue(reading.pm25)}</td><td><select aria-label={`Haze on ${reading.date}`} value={values.haze} onChange={(event) => setValues({ ...values, haze: event.target.value })}><option value="">—</option><option value="Y">Y</option><option value="N">N</option></select></td><td><select aria-label={`Rain after haze on ${reading.date}`} value={values.rain_after_haze} onChange={(event) => setValues({ ...values, rain_after_haze: event.target.value })}><option value="">—</option><option value="Y">Y</option><option value="N">N</option></select></td><td><input aria-label={`Notes for ${reading.date}`} value={values.sampling_notes} onChange={(event) => setValues({ ...values, sampling_notes: event.target.value })} /></td><td title={`Rain: ${reading.rain_reading_count}; temperature: ${reading.temp_reading_count}; PSI: ${reading.psi_reading_count}; PM2.5: ${reading.pm25_reading_count}; stations: ${stations}`}>{partial ? <span className="badge partial">Partial</span> : <span className="badge complete">Complete</span>}</td><td><button className="link-button" onClick={() => onSave(reading, { haze: values.haze || null, rain_after_haze: values.rain_after_haze || null, sampling_notes: values.sampling_notes || null })}>Save</button><button className="link-button" onClick={onCancel}>Cancel</button></td></tr>;
  return <tr><td>{reading.date}</td><td>{formatValue(reading.rain_24h_mm, " mm")}</td><td>{formatValue(reading.rain_48h_mm, " mm")}</td><td>{formatValue(reading.temp_c, " °C")}</td><td>{formatValue(reading.psi_24h)} {reading.psi_24h > 100 && <span className="hint">PSI &gt; 100</span>}</td><td>{formatValue(reading.pm25)}</td><td>{reading.haze ?? "—"}</td><td>{reading.rain_after_haze ?? "—"}</td><td className="notes">{reading.sampling_notes ?? "—"}</td><td title={`Rain: ${reading.rain_reading_count}; temperature: ${reading.temp_reading_count}; PSI: ${reading.psi_reading_count}; PM2.5: ${reading.pm25_reading_count}; stations: ${stations}`}>{partial ? <span className="badge partial">Partial</span> : <span className="badge complete">Complete</span>}</td><td><button className="link-button" onClick={onEdit}>Edit</button></td></tr>;
}

createRoot(document.getElementById("root")).render(<StrictMode><Dashboard /></StrictMode>);
