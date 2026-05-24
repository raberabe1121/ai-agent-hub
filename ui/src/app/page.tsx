"use client";

import { useEffect, useMemo, useState } from "react";
import styles from "./page.module.css";

type Tab = "Architecture" | "Intents" | "Logs" | "HITL";
type Intent = { name: string; description?: string };
type LogItem = { timestamp: string; intent: string; sender: string; recipient: string; status: string };
type Approval = { id: string; title?: string; reason?: string; requester?: string };
type Health = { lmtp?: boolean; api?: boolean; queue_dir?: boolean; dlq_count?: number; hitl_pending?: number };

const API_BASE = process.env.NEXT_PUBLIC_HUB_API_URL ?? "http://localhost:8080";

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return (await res.json()) as T;
}

export default function DashboardPage() {
  const [tab, setTab] = useState<Tab>("Architecture");
  const [intents, setIntents] = useState<Intent[]>([]);
  const [logs, setLogs] = useState<LogItem[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [health, setHealth] = useState<Health>({});
  const [liveLogs, setLiveLogs] = useState<LogItem[]>([]);
  const [rejectReasons, setRejectReasons] = useState<Record<string, string>>({});

  const fetchIntents = async () => setIntents(await getJson<Intent[]>("/intents"));
  const fetchLogs = async () => setLogs(await getJson<LogItem[]>("/logs?limit=20"));
  const fetchApprovals = async () => setApprovals(await getJson<Approval[]>("/approvals/pending"));
  const fetchHealth = async () => setHealth(await getJson<Health>("/health"));
  const fetchLiveLogs = async () => setLiveLogs(await getJson<LogItem[]>("/logs?limit=5"));

  useEffect(() => {
    void fetchIntents();
    void fetchLogs();
    void fetchApprovals();
    void fetchHealth();
    void fetchLiveLogs();
    const interval = setInterval(() => {
      void fetchLogs();
      void fetchApprovals();
      void fetchHealth();
      void fetchLiveLogs();
    }, 5000);
    return () => clearInterval(interval);
  }, []);

  const metrics = useMemo(() => {
    const counts = logs.reduce<Record<string, number>>((acc, item) => {
      acc[item.intent] = (acc[item.intent] ?? 0) + 1;
      return acc;
    }, {});
    const top3 = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 3);
    return { total: logs.length, top3 };
  }, [logs]);

  const decideApproval = async (id: string, action: "approve" | "reject") => {
    const body = action === "reject" ? JSON.stringify({ reason: rejectReasons[id] ?? "" }) : undefined;
    await fetch(`${API_BASE}/approvals/${id}/${action}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body
    });
    await fetchApprovals();
    await fetchHealth();
  };

  return (
    <div className={styles.container}>
      <header className={styles.topbar}>
        <strong>AI Agent Hub v0.6</strong>
        <span className={styles.live}>● LMTP LIVE</span>
      </header>

      <nav className={styles.tabs}>
        {(["Architecture", "Intents", "Logs", "HITL"] as Tab[]).map((t) => (
          <button key={t} className={tab === t ? styles.tabActive : styles.tab} onClick={() => setTab(t)}>{t}</button>
        ))}
      </nav>

      <main className={styles.grid}>
        <aside className={styles.card}>
          <h3 className={styles.sectionTitle}>Hub Core</h3>
          <ul className={styles.list}>
            <li>⚙️ LMTP Server :8024</li><li>📬 MTA / Postfix :25</li><li>📋 Envelope Model</li><li>👤 Human-in-Loop</li><li>💀 Dead Letter Queue</li>
          </ul>
          <h3 className={styles.sectionTitle} style={{ marginTop: 16 }}>Intents</h3>
          {intents.map((intent) => <div key={intent.name} className={styles.intentItem}>{intent.name}</div>)}
        </aside>

        <section className={styles.card}>
          {tab === "Architecture" && (<><h3 className={styles.sectionTitle}>AI Agent Hubが担うこと</h3><ul className={styles.list}><li>Intent分類と配信</li><li>LMTP経由のメールワークフロー統合</li><li>承認フロー(HITL)管理</li><li>監査ログ蓄積</li></ul><h3 className={styles.sectionTitle} style={{ marginTop: 16 }}>Architecture Flow</h3><div className={styles.flow}>CLI/SDK → REST API :8080 → LMTP :8024 → Postfix → Agent Worker → LLM</div></>)}
          {tab === "Intents" && intents.map((intent) => (<div key={intent.name} className={styles.intentItem}><strong>{intent.name}</strong><div className={styles.small}>{intent.description ?? "No description"}</div></div>))}
          {tab === "Logs" && logs.map((log, idx) => (<div key={`${log.timestamp}-${idx}`} className={styles.logLine}>[{new Date(log.timestamp).toLocaleTimeString()}] | [{log.intent}] | [{log.sender} → {log.recipient}] | [{log.status}]</div>))}
          {tab === "HITL" && approvals.map((item) => (<div key={item.id} className={styles.intentItem}><strong>{item.title ?? item.id}</strong><div className={styles.small}>{item.requester ?? "unknown"}</div><div className={styles.small}>{item.reason ?? ""}</div><input className={styles.reason} placeholder="却下理由" value={rejectReasons[item.id] ?? ""} onChange={(e) => setRejectReasons((p) => ({ ...p, [item.id]: e.target.value }))} /><div className={styles.buttonRow}><button className={styles.btnApprove} onClick={() => void decideApproval(item.id, "approve")}>承認</button><button className={styles.btnReject} onClick={() => void decideApproval(item.id, "reject")}>却下</button></div></div>))}
        </section>

        <aside className={styles.card}>
          <h3 className={styles.sectionTitle}>Hub Status</h3>
          <div className={styles.statusRow}><span><span className={`${styles.dot} ${health.lmtp ? styles.green : styles.red}`} />LMTP Server</span><span>:8024</span></div>
          <div className={styles.statusRow}><span><span className={`${styles.dot} ${health.api ? styles.green : styles.red}`} />REST API</span><span>:8080</span></div>
          <div className={styles.statusRow}><span><span className={`${styles.dot} ${health.queue_dir ? styles.green : styles.yellow}`} />Queue Dir</span><span>{health.queue_dir ? "OK" : "WARN"}</span></div>
          <div className={styles.statusRow}><span>DLQ件数</span><span>{health.dlq_count ?? 0}</span></div>
          <div className={styles.statusRow}><span>HITL Pending</span><span>{health.hitl_pending ?? approvals.length}</span></div>
          <h3 className={styles.sectionTitle} style={{ marginTop: 16 }}>Today Metrics</h3>
          <div className={styles.statusRow}><span>処理Envelope総数</span><span>{metrics.total}</span></div>
          {metrics.top3.map(([name, count]) => <div key={name} className={styles.statusRow}><span>{name}</span><span>{count}</span></div>)}
          <h3 className={styles.sectionTitle} style={{ marginTop: 16 }}>Live Logs</h3>
          {liveLogs.map((log, idx) => <div key={`${log.timestamp}-${idx}`} className={styles.logLine}>[{log.intent}] {log.status}</div>)}
        </aside>
      </main>
    </div>
  );
}
