"use client";

import { useEffect, useMemo, useState } from "react";
import styles from "./page.module.css";

type Tab = "Architecture" | "Intents" | "Logs" | "HITL";
type Intent = { name: string; description?: string };
type LogItem = {
  id: string;
  time: string;
  intent: string | null;
  from: string;
  to: string;
  type: string;
  payload: Record<string, unknown>;
};
type Approval = {
  envelope_id: string;
  thread_id: string;
  description?: string;
  requester?: string;
  approver?: string;
  status?: string;
  created_at?: string;
  callback_payload?: Record<string, unknown>;
};
type Health = { api?: boolean; queue_dir?: boolean; dlq_count?: number; hitl_pending?: number };

const API_BASE = process.env.NEXT_PUBLIC_HUB_API_URL ?? "http://localhost:8080";

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return (await res.json()) as T;
}

function formatJapanTime(value?: string): string {
  if (!value) return "-";
  return new Date(value).toLocaleString("ja-JP", { timeZone: "Asia/Tokyo" });
}

function getCallbackIntent(approval: Approval): string {
  const intent = approval.callback_payload?.intent;
  return typeof intent === "string" ? intent : "-";
}

export default function DashboardPage() {
  const [tab, setTab] = useState<Tab>("Architecture");
  const [intents, setIntents] = useState<Intent[]>([]);
  const [logs, setLogs] = useState<LogItem[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [health, setHealth] = useState<Health>({});
  const [liveLogs, setLiveLogs] = useState<LogItem[]>([]);
  const [rejectReasons, setRejectReasons] = useState<Record<string, string>>({});
  const [logFilter, setLogFilter] = useState<string | null>(null);
  const [selectedIntent, setSelectedIntent] = useState<string | null>(null);
  const [intentText, setIntentText] = useState("");
  const [intentResult, setIntentResult] = useState<string | null>(null);
  const [intentLoading, setIntentLoading] = useState(false);

  const fetchIntents = async () => {
    try {
      setIntents(await getJson<Intent[]>("/intents"));
    } catch {
      // Ignore transient dashboard API errors so the page remains usable.
    }
  };
  const fetchLogs = async () => {
    try {
      const data = await getJson<{ logs: LogItem[] }>("/logs?limit=20");
      setLogs((data.logs ?? []).filter((log) => log.intent !== null));
    } catch {
      // Ignore transient dashboard API errors so the page remains usable.
    }
  };
  const fetchApprovals = async () => {
    try {
      setApprovals(await getJson<Approval[]>("/approvals/pending"));
    } catch {
      // Ignore transient dashboard API errors so the page remains usable.
    }
  };
  const fetchHealth = async () => {
    try {
      setHealth(await getJson<Health>("/health"));
    } catch {
      // Ignore transient dashboard API errors so the page remains usable.
    }
  };
  const fetchLiveLogs = async () => {
    try {
      const data = await getJson<{ logs: LogItem[] }>("/logs?limit=5");
      setLiveLogs((data.logs ?? []).filter((log) => log.intent !== null));
    } catch {
      // Ignore transient dashboard API errors so the page remains usable.
    }
  };

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
      const intentName = item.intent ?? "-";
      acc[intentName] = (acc[intentName] ?? 0) + 1;
      return acc;
    }, {});
    const top3 = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 3);
    return { total: logs.length, top3 };
  }, [logs]);

  const intentTypes = useMemo(() => [...new Set(logs.map((log) => log.intent).filter(Boolean))], [logs]);
  const filteredLogs = useMemo(() => (logFilter ? logs.filter((log) => log.intent === logFilter) : logs), [logFilter, logs]);

  const sendIntent = async () => {
    if (!selectedIntent) return;

    setIntentLoading(true);
    setIntentResult(null);

    try {
      const res = await fetch(`${API_BASE}/envelopes`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ intent: selectedIntent, text: intentText })
      });
      if (!res.ok) throw new Error(`Envelope API error: ${res.status}`);

      const data = (await res.json()) as { envelope_id?: string };
      if (!data.envelope_id) throw new Error("Envelope API response did not include envelope_id");

      const reply = await fetch(`${API_BASE}/envelopes/${data.envelope_id}/reply?timeout_sec=30`);
      if (!reply.ok) throw new Error(`Reply API error: ${reply.status}`);

      const replyData = (await reply.json()) as { payload?: unknown };
      setIntentResult(JSON.stringify(replyData.payload ?? replyData, null, 2));
    } catch (error) {
      setIntentResult(error instanceof Error ? error.message : "送信に失敗しました");
    } finally {
      setIntentLoading(false);
    }
  };

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
        <span className={styles.live}>● QUEUE LIVE</span>
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
            <li>📥 Direct Queue</li><li>📋 Envelope Model</li><li>👤 Human-in-Loop</li><li>💀 Dead Letter Queue</li>
          </ul>
          <h3 className={styles.sectionTitle} style={{ marginTop: 16 }}>Intents</h3>
          {intents.map((intent) => <div key={intent.name} className={styles.intentItem}>{intent.name}</div>)}
        </aside>

        <section className={styles.card}>
          {tab === "Architecture" && (<><h3 className={styles.sectionTitle}>AI Agent Hubが担うこと</h3><ul className={styles.list}><li>Intent分類と配信</li><li>Queueベースのワークフロー統合</li><li>承認フロー(HITL)管理</li><li>監査ログ蓄積</li></ul><h3 className={styles.sectionTitle} style={{ marginTop: 16 }}>Architecture Flow</h3><div className={styles.flow}>CLI/SDK → REST API :8080 → Queue → Agent Worker → LLM</div></>)}
          {tab === "Intents" && (
            <div className={styles.intentTestLayout}>
              <div>
                {intents.map((intent) => (
                  <button
                    key={intent.name}
                    className={selectedIntent === intent.name ? styles.intentButtonActive : styles.intentButton}
                    onClick={() => {
                      setSelectedIntent(intent.name);
                      setIntentResult(null);
                    }}
                  >
                    <strong>{intent.name}</strong>
                    <span className={styles.small}>{intent.description ?? "No description"}</span>
                  </button>
                ))}
              </div>
              <div className={styles.intentPanel}>
                {selectedIntent ? (
                  <>
                    <h3 className={styles.intentPanelTitle}>{selectedIntent}</h3>
                    <label className={styles.fieldLabel} htmlFor="intent-text">text</label>
                    <textarea
                      id="intent-text"
                      className={styles.intentTextarea}
                      value={intentText}
                      onChange={(e) => setIntentText(e.target.value)}
                      placeholder="送信するtextを入力"
                    />
                    <button className={styles.sendButton} onClick={() => void sendIntent()} disabled={intentLoading}>
                      {intentLoading ? "送信中..." : "送信"}
                    </button>
                    {intentLoading && <div className={styles.small}>返信を待っています...</div>}
                    {intentResult && <pre className={styles.intentResult}>{intentResult}</pre>}
                  </>
                ) : (
                  <div className={styles.small}>左のintentを選択してください。</div>
                )}
              </div>
            </div>
          )}
          {tab === "Logs" && (
            <>
              <div className={styles.filterRow}>
                <button className={logFilter === null ? styles.filterButtonActive : styles.filterButton} onClick={() => setLogFilter(null)}>全て</button>
                {intentTypes.map((intent) => (
                  <button key={intent} className={logFilter === intent ? styles.filterButtonActive : styles.filterButton} onClick={() => setLogFilter(intent)}>{intent}</button>
                ))}
              </div>
              {filteredLogs.map((log, idx) => (
                <div key={`${log.id}-${idx}`} className={styles.logLine}>[{new Date(log.time).toLocaleTimeString()}] | [{log.intent ?? "-"}] | [{log.from} → {log.to}] | [{log.type}]</div>
              ))}
            </>
          )}
          {tab === "HITL" && approvals.map((item) => (<div key={item.envelope_id} className={styles.approvalCard}><strong className={styles.approvalDescription}>{item.description ?? "承認内容の説明がありません"}</strong><div className={styles.small}>ID: {item.envelope_id.slice(0, 8)}</div><div className={styles.small}>requester: {item.requester ?? "-"}</div><div className={styles.small}>approver: {item.approver ?? "-"}</div><div className={styles.small}>created_at: {formatJapanTime(item.created_at)}</div><div className={styles.small}>callback intent: {getCallbackIntent(item)}</div><input className={styles.reason} placeholder="却下理由" value={rejectReasons[item.envelope_id] ?? ""} onChange={(e) => setRejectReasons((p) => ({ ...p, [item.envelope_id]: e.target.value }))} /><div className={styles.buttonRow}><button className={styles.btnApprove} onClick={() => void decideApproval(item.envelope_id, "approve")}>承認</button><button className={styles.btnReject} onClick={() => void decideApproval(item.envelope_id, "reject")}>却下</button></div></div>))}
        </section>

        <aside className={styles.card}>
          <h3 className={styles.sectionTitle}>Hub Status</h3>
          <div className={styles.statusRow}><span><span className={`${styles.dot} ${health.queue_dir ? styles.green : styles.red}`} />Queue</span><span>direct</span></div>
          <div className={styles.statusRow}><span><span className={`${styles.dot} ${health.api ? styles.green : styles.red}`} />REST API</span><span>:8080</span></div>
          <div className={styles.statusRow}><span><span className={`${styles.dot} ${health.queue_dir ? styles.green : styles.yellow}`} />Queue Dir</span><span>{health.queue_dir ? "OK" : "WARN"}</span></div>
          <div className={styles.statusRow}><span>DLQ件数</span><span>{health.dlq_count ?? 0}</span></div>
          <div className={styles.statusRow}><span>HITL Pending</span><span>{health.hitl_pending ?? approvals.length}</span></div>
          <h3 className={styles.sectionTitle} style={{ marginTop: 16 }}>Today Metrics</h3>
          <div className={styles.statusRow}><span>処理Envelope総数</span><span>{metrics.total}</span></div>
          {metrics.top3.map(([name, count]) => <div key={name} className={styles.statusRow}><span>{name}</span><span>{count}</span></div>)}
          <h3 className={styles.sectionTitle} style={{ marginTop: 16 }}>Live Logs</h3>
          {liveLogs.map((log, idx) => <div key={`${log.id}-${idx}`} className={styles.logLine}>[{log.intent ?? "-"}] {log.type}</div>)}
        </aside>
      </main>
    </div>
  );
}
