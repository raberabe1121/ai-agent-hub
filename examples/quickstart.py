"""AI Agent Hub Python SDK クイックスタート"""

from ai_agent_hub.sdk import AgentHub, AgentHubError

hub = AgentHub()

# 1. 疎通確認
result = hub.send(intent="ping")
print(f"ping: {result.payload}")  # {"pong": true}

# 2. LLMに質問
result = hub.send(
    intent="llm-query",
    text="今日の横浜の天気は？",
    model="gemma3:4b",
)
print(f"LLM回答: {result.payload['result']}")

# 3. ログ確認
logs = hub.logs(limit=5)
for log in logs:
    print(f"{log.time} | {log.intent} | {log.payload}")

# 4. 承認フロー
try:
    approval = hub.request_approval(
        description="経費申請 ¥150,000",
        approver="https://company.local/@manager",
        callback={"intent": "echo", "text": "承認されました"},
        thread_id="quickstart-approval-thread",
    )
    print(f"承認ID: {approval.approval_id}")

    # 承認待ち確認
    pending = hub.pending_approvals()
    for p in pending:
        print(f"[{p.approval_id[:8]}] {p.description}")

    # 承認
    hub.approve(approval.approval_id)
except AgentHubError as exc:
    print(f"承認フローでエラー: {exc}")
