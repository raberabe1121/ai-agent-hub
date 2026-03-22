"""AI Agent Hub統合デモ: AIによる経費申請承認フロー."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ["AI_AGENT_HUB_QUEUE_DIR"] = "/opt/ai-agent-hub/queue"
os.environ["AI_AGENT_HUB_PROCESSED_DIR"] = "/opt/ai-agent-hub/processed"
os.environ["AI_AGENT_HUB_STORAGE"] = "sqlite"
os.environ["AI_AGENT_HUB_SQLITE_PATH"] = "/opt/ai-agent-hub/demo.db"
os.environ["AI_AGENT_HUB_APPROVAL_DB"] = "/opt/ai-agent-hub/approvals.db"

from ai_agent_hub import Envelope
import ai_agent_hub.agent_worker as agent_worker
from ai_agent_hub.governance_milter import evaluate_policy
from ai_agent_hub.human_in_the_loop import ApprovalStore
from ai_agent_hub.repository import PROCESSED, SQLiteRepository, get_repository

QUEUE_DIR = Path(os.environ["AI_AGENT_HUB_QUEUE_DIR"])
PROCESSED_DIR = Path(os.environ["AI_AGENT_HUB_PROCESSED_DIR"])
DLQ_DIR = Path("/opt/ai-agent-hub/dlq")
SQLITE_PATH = Path(os.environ["AI_AGENT_HUB_SQLITE_PATH"])
APPROVAL_DB_PATH = Path(os.environ["AI_AGENT_HUB_APPROVAL_DB"])


@dataclass
class DemoContext:
    thread_id: str
    started_at: float
    demo_flaky_attempts: dict[str, int]


def print_step(step_no: int, title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"ステップ{step_no}: {title}")
    print(f"{'=' * 60}")


def ensure_directories() -> None:
    for directory in (QUEUE_DIR, PROCESSED_DIR, DLQ_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    APPROVAL_DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def reset_demo_storage() -> None:
    for path in (SQLITE_PATH, APPROVAL_DB_PATH):
        if path.exists():
            path.unlink()
    for directory in (QUEUE_DIR, PROCESSED_DIR, DLQ_DIR):
        if not directory.exists():
            continue
        for json_file in directory.glob("*.json"):
            json_file.unlink()


def repository() -> SQLiteRepository:
    repo = get_repository()
    if not isinstance(repo, SQLiteRepository):
        raise RuntimeError("このデモはSQLiteストレージ前提です")
    return repo


def queue_file_path(env: Envelope) -> Path:
    timestamp = env.created_at.strftime("%Y%m%dT%H%M%S")
    return QUEUE_DIR / f"{timestamp}_{env.id}.json"


def processed_file_path(env: Envelope) -> Path:
    timestamp = env.created_at.strftime("%Y%m%dT%H%M%S")
    return PROCESSED_DIR / f"{timestamp}_{env.id}.json"


def dlq_file_path(env: Envelope) -> Path:
    timestamp = env.created_at.strftime("%Y%m%dT%H%M%S")
    return DLQ_DIR / f"{timestamp}_{env.id}.json"


def persist_queue_snapshot(env: Envelope) -> None:
    queue_file_path(env).write_text(env.to_json(indent=2), encoding="utf-8")


def archive_processed_snapshot(env: Envelope) -> None:
    queued = queue_file_path(env)
    if queued.exists():
        queued.replace(processed_file_path(env))
        return
    processed_file_path(env).write_text(env.to_json(indent=2), encoding="utf-8")


def move_to_dlq(env: Envelope, error_message: str) -> None:
    payload = env.to_dict()
    payload["dlq_reason"] = error_message
    dlq_file_path(env).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    queued = queue_file_path(env)
    if queued.exists():
        queued.unlink()


def record_envelope(env: Envelope, *, status: str = PROCESSED) -> None:
    repo = repository()
    repo.save(env)
    repo.mark_status(env.id, status)
    archive_processed_snapshot(env)


def enqueue_envelope(env: Envelope) -> None:
    repository().save(env)
    persist_queue_snapshot(env)
    print(f"  → Enqueue: {env.id} | {env.sender} → {env.recipient}")


def wait_for_reply(original_id: str, timeout: int = 30) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for file_path in sorted(PROCESSED_DIR.glob("*.json"), key=lambda path: path.stat().st_mtime):
            try:
                data = json.loads(file_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if data.get("inReplyTo") == original_id:
                return data
        time.sleep(0.5)
    return None


def query_audit_rows(thread_id: str) -> list[sqlite3.Row]:
    with sqlite3.connect(SQLITE_PATH) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute(
            """
            SELECT id, sender, recipient, envelope_type, payload, context, created_at, status
            FROM envelopes
            WHERE context = ?
            ORDER BY created_at ASC
            """,
            (json.dumps(thread_id, ensure_ascii=False),),
        ).fetchall()


def query_processed_count() -> int:
    with sqlite3.connect(SQLITE_PATH) as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM envelopes WHERE status = ?",
            (PROCESSED,),
        ).fetchone()
    return int(row[0]) if row else 0


def display_audit_trail(thread_id: str) -> None:
    rows = query_audit_rows(thread_id)
    if not rows:
        print("  → 監査ログが見つかりません")
        return

    for index, row in enumerate(rows, start=1):
        payload = json.loads(row["payload"])
        intent = payload.get("intent") if isinstance(payload, dict) else None
        description = payload.get("description") if isinstance(payload, dict) else None
        print(
            f"  {index:02d}. {row['created_at']} | {row['sender']} → {row['recipient']} "
            f"| type={row['envelope_type']} | status={row['status']} "
            f"| intent={intent or '-'}"
        )
        if description:
            print(f"      説明: {description}")


def build_policy_message(policy_header: str, amount_jpy: int) -> EmailMessage:
    message = EmailMessage()
    message["From"] = "policy-agent@company.local"
    message["To"] = "worker@company.local"
    message["X-Agent-Policy"] = policy_header
    message.set_content(
        json.dumps(
            {
                "payload": {
                    "intent": "request-approval",
                    "amount_jpy": amount_jpy,
                    "policy_checked": True,
                }
            },
            ensure_ascii=False,
        ),
        subtype="plain",
        charset="utf-8",
    )
    return message


def register_demo_flaky_handler(context: DemoContext) -> None:
    def _handle_demo_flaky_task(env: Envelope) -> dict[str, Any]:
        attempt = context.demo_flaky_attempts.get(env.id, 0) + 1
        context.demo_flaky_attempts[env.id] = attempt
        if attempt == 1:
            raise RuntimeError("Demo DLQ: 一時的な実行エラー")
        return {"status": "retried", "attempt": attempt, "message": "リトライ成功"}

    agent_worker.INTENT_HANDLERS["demo-flaky-task"] = _handle_demo_flaky_task


def patched_send_factory(sent_messages: list[Envelope]) -> Any:
    def _patched_send(env: Envelope) -> None:
        sent_messages.append(env)
        enqueue_envelope(env)

    return _patched_send


def process_single_envelope() -> bool:
    repo = repository()
    pending = repo.list_pending()
    if not pending:
        return False

    env = pending[0]
    print(f"  → Worker処理中: {env.id}")
    try:
        reply = agent_worker._handle_envelope(env)
        if (
            isinstance(env.payload, dict)
            and env.payload.get("intent") == "demo-flaky-task"
            and reply is not None
            and isinstance(reply.payload, dict)
            and "Demo DLQ" in str(reply.payload.get("error", ""))
        ):
            repo.mark_status(env.id, "failed")
            move_to_dlq(env, str(reply.payload["error"]))
            print(f"  → DLQ移動: {env.id} ({reply.payload['error']})")
            return False
        repo.mark_status(env.id, PROCESSED)
        archive_processed_snapshot(env)
        if reply is not None:
            enqueue_envelope(reply)
        return True
    except Exception as exc:
        repo.mark_status(env.id, "failed")
        move_to_dlq(env, str(exc))
        print(f"  → DLQ移動: {env.id} ({exc})")
        return False


def worker_loop(stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        processed = process_single_envelope()
        if not processed:
            time.sleep(0.2)


def send_and_wait(env: Envelope, timeout: int = 30) -> dict[str, Any] | None:
    enqueue_envelope(env)
    reply = wait_for_reply(env.id, timeout=timeout)
    if reply:
        print(f"  → Reply受信: {reply.get('id')}")
    else:
        print("  → Reply待機タイムアウト")
    return reply


def choose_approval_action() -> str:
    print("承認しますか？ [approve/reject]: ")
    choice = input().strip().lower()
    if choice not in {"approve", "reject"}:
        print("  → 不正な入力のため approve として継続します")
        return "approve"
    return choice


def show_pending_approvals(reply_payload: dict[str, Any]) -> str | None:
    pending_items = reply_payload.get("payload", {}).get("pending", [])
    if not pending_items:
        print("  → 承認待ちはありません")
        return None

    print(f"  → 承認待ち件数: {len(pending_items)}")
    for item in pending_items:
        print(
            f"     - approval_id={item['envelope_id']} | status={item['status']} "
            f"| approver={item['approver']} | description={item['description']}"
        )
    return pending_items[0]["envelope_id"]


def show_dlq_state() -> Envelope | None:
    dlq_files = sorted(DLQ_DIR.glob("*.json"))
    if not dlq_files:
        print("  → DLQは空です")
        return None

    raw = json.loads(dlq_files[0].read_text(encoding="utf-8"))
    print(f"  → DLQ格納Envelope: {raw['id']} | reason={raw.get('dlq_reason')}")
    return Envelope.from_dict(raw)


def retry_from_dlq(env: Envelope) -> dict[str, Any] | None:
    dlq_file = dlq_file_path(env)
    if dlq_file.exists():
        dlq_file.unlink()
    retry_env = Envelope.new(
        envelope_type=env.envelope_type,
        sender=env.sender,
        recipient=env.recipient,
        payload=env.payload,
        context=env.context,
        in_reply_to=env.in_reply_to,
        created_at=env.created_at,
        envelope_id=env.id,
    )
    return send_and_wait(retry_env, timeout=30)


def main() -> None:
    ensure_directories()
    reset_demo_storage()
    ensure_directories()

    context = DemoContext(
        thread_id=f"expense-demo-{int(time.time())}",
        started_at=time.perf_counter(),
        demo_flaky_attempts={},
    )
    register_demo_flaky_handler(context)

    sent_messages: list[Envelope] = []
    original_send = agent_worker.send_envelope_via_smtp
    agent_worker.send_envelope_via_smtp = patched_send_factory(sent_messages)

    stop_event = threading.Event()
    worker_thread = threading.Thread(target=worker_loop, args=(stop_event,), daemon=True)
    worker_thread.start()

    try:
        print("=" * 60)
        print("AI Agent Hub 統合デモ: AIによる経費申請承認フロー")
        print("=" * 60)
        print(f"thread_id: {context.thread_id}")
        print("登場エージェント:")
        print("  - RequestAgent")
        print("  - PolicyAgent")
        print("  - HumanApprovalAgent")
        print("  - ExecutionAgent")

        print_step(1, "RequestAgent → PolicyAgent: 経費申請を送信")
        request_envelope = Envelope.new(
            envelope_type="command",
            sender="https://company.local/@request-agent",
            recipient="https://company.local/@policy-agent",
            payload={
                "intent": "submit-expense",
                "amount_jpy": 150000,
                "description": "海外出張経費 ¥150,000の承認申請",
            },
            context=context.thread_id,
        )
        record_envelope(request_envelope)
        print("  → RequestAgentが申請を起票しました")

        amount_jpy = 150000
        requires_human = amount_jpy >= 100000
        policy_header = (
            "human-approval-required=true; amount-jpy=150000; threshold-jpy=100000"
        )
        governance_message = build_policy_message(policy_header, amount_jpy)
        decision = evaluate_policy(
            sender="policy-agent@company.local",
            message=governance_message,
            allowed_domain="company.local",
        )
        print("  → PolicyAgent判定: 10万円以上なので人間承認が必須です")
        print(f"  → X-Agent-Policy: {policy_header}")
        print(f"  → Governance Milterチェック: accepted={decision.accepted}, reason={decision.reason}")

        policy_envelope = Envelope.new(
            envelope_type="event",
            sender="https://company.local/@policy-agent",
            recipient="https://company.local/@human-approval-agent",
            payload={
                "intent": "policy-decision",
                "amount_jpy": amount_jpy,
                "requires_human_approval": requires_human,
                "headers": {"X-Agent-Policy": policy_header},
            },
            context=context.thread_id,
        )
        record_envelope(policy_envelope)

        approval_request = Envelope.new(
            envelope_type="command",
            sender="https://company.local/@policy-agent",
            recipient="https://company.local/@worker",
            payload={
                "intent": "request-approval",
                "description": "海外出張経費 ¥150,000の承認申請",
                "approver": "https://company.local/@manager",
                "callback_payload": {
                    "intent": "echo",
                    "text": "経費申請が承認されました",
                },
                "headers": {"X-Agent-Policy": policy_header},
            },
            context=context.thread_id,
        )
        pending_reply = send_and_wait(approval_request)
        if not pending_reply:
            raise RuntimeError("承認依頼への返信を受信できませんでした")
        print(f"  → HumanApprovalAgent応答: {pending_reply['payload']}")

        print_step(2, "承認待ち状態を list-pending-approvals で確認")
        pending_list_request = Envelope.new(
            envelope_type="command",
            sender="https://company.local/@request-agent",
            recipient="https://company.local/@worker",
            payload={"intent": "list-pending-approvals"},
            context=context.thread_id,
        )
        pending_list_reply = send_and_wait(pending_list_request)
        if not pending_list_reply:
            raise RuntimeError("承認待ち一覧を取得できませんでした")
        approval_id = show_pending_approvals(pending_list_reply)
        if not approval_id:
            raise RuntimeError("承認IDが見つかりませんでした")

        print_step(3, "監査・因果トレースを SQLite から表示")
        print("  → thread_id をキーに全工程を遡ります")
        display_audit_trail(context.thread_id)

        print_step(4, "Human-in-the-loop: 人間が approve / reject を入力")
        action = choose_approval_action()
        sent_count_before_human_action = len(sent_messages)
        if action == "approve":
            human_payload = {"intent": "approve", "approval_id": approval_id}
        else:
            human_payload = {
                "intent": "reject",
                "approval_id": approval_id,
                "reason": "予算超過",
            }
        human_action_envelope = Envelope.new(
            envelope_type="command",
            sender="https://company.local/@human-approval-agent",
            recipient="https://company.local/@worker",
            payload=human_payload,
            context=context.thread_id,
        )
        human_reply = send_and_wait(human_action_envelope)
        if not human_reply:
            raise RuntimeError("人間承認の結果を受信できませんでした")
        print(f"  → HumanApprovalAgent結果: {human_reply['payload']}")

        print_step(5, "承認後の後続処理と長期状態管理を確認")
        if action == "approve":
            callback_envelope = None
            if len(sent_messages) > sent_count_before_human_action:
                callback_envelope = sent_messages[-1]
            callback_reply = (
                wait_for_reply(callback_envelope.id, timeout=30)
                if callback_envelope is not None
                else None
            )
            if callback_envelope is not None and callback_reply:
                print("  → callback_payload が実行されました")
                print(f"     callback_envelope: {callback_envelope.id}")
                print(f"     実行結果: {callback_reply['payload']}")
                execution_envelope = Envelope.new(
                    envelope_type="event",
                    sender="https://company.local/@policy-agent",
                    recipient="https://company.local/@execution-agent",
                    payload={
                        "intent": "execution-complete",
                        "result": "経費申請が承認されました",
                        "source_callback_reply": callback_reply["id"],
                    },
                    context=context.thread_id,
                )
                record_envelope(execution_envelope)
                print("  → ExecutionAgent まで後続処理を接続しました")
            else:
                print("  → callback_payload の実行確認がタイムアウトしました")
        else:
            print("  → reject のため callback_payload は実行されません")

        print("  → DLQデモ用に一時失敗するEnvelopeを投入します")
        flaky_envelope = Envelope.new(
            envelope_type="command",
            sender="https://company.local/@execution-agent",
            recipient="https://company.local/@worker",
            payload={"intent": "demo-flaky-task"},
            context=context.thread_id,
        )
        enqueue_envelope(flaky_envelope)
        time.sleep(1.0)
        dlq_env = show_dlq_state()
        if dlq_env:
            print("  → DLQから同じEnvelopeをリトライします")
            retry_reply = retry_from_dlq(dlq_env)
            if retry_reply:
                print(f"     リトライ結果: {retry_reply['payload']}")

        print_step(6, "最終監査レポート")
        duration = time.perf_counter() - context.started_at
        processed_total = query_processed_count()
        approval_store = ApprovalStore(str(APPROVAL_DB_PATH))
        approval_state = approval_store.get(approval_id)
        print(f"  → 処理済みEnvelope総数: {processed_total}")
        print(f"  → approval_id: {approval_id}")
        print(f"  → 承認状態: {approval_state.status if approval_state else 'unknown'}")
        print(f"  → 所要時間: {duration:.2f}秒")
        print("  → thread_id 全工程トレース:")
        display_audit_trail(context.thread_id)
    finally:
        stop_event.set()
        worker_thread.join(timeout=2.0)
        agent_worker.send_envelope_via_smtp = original_send


if __name__ == "__main__":
    main()
