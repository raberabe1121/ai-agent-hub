# AI Agent Hub Python SDK — APIリファレンス

## インストール

```bash
pip install -e .
```

## クイックスタート

```python
from ai_agent_hub.sdk import AgentHub

hub = AgentHub()  # デフォルト: http://localhost:8080

result = hub.send(intent="ping")
print(result.payload)  # {"pong": True}
```

完全なサンプルは [examples/quickstart.py](../examples/quickstart.py) を参照してください。

---

## AgentHub クラス

### コンストラクタ

```python
AgentHub(base_url: str | None = None)
```

| パラメータ | 型 | デフォルト | 説明 |
|-----------|-----|-----------|------|
| `base_url` | `str \| None` | `None` | APIサーバーのURL。Noneの場合は環境変数 `AI_AGENT_HUB_URL` を参照し、未設定なら `http://localhost:8080` を使用 |

**環境変数：**

```bash
export AI_AGENT_HUB_URL=http://192.168.1.1:8080
hub = AgentHub()  # 上記URLを使用
```

---

### send()

Envelopeを送信して返信を待ちます。

```python
hub.send(
    intent: str,
    text: str | None = None,
    model: str | None = None,
    wait: bool = True,
    timeout: int = 30,
) -> SendResult
```

| パラメータ | 型 | デフォルト | 説明 |
|-----------|-----|-----------|------|
| `intent` | `str` | 必須 | 実行するintentの名前 |
| `text` | `str \| None` | `None` | payloadのtextフィールド |
| `model` | `str \| None` | `None` | LLMモデル名（`llm-query`時のみ有効） |
| `wait` | `bool` | `True` | `True`の場合は返信を待つ |
| `timeout` | `int` | `30` | 返信を待つ秒数 |

**戻り値：** `SendResult`

```python
# 返信を待つ場合
result = hub.send(intent="ping")
print(result.envelope_id)  # "3e6abebf-..."
print(result.payload)      # {"pong": True}
print(result.status)       # "ok"

# 返信を待たない場合
result = hub.send(intent="ping", wait=False)
print(result.envelope_id)  # "3e6abebf-..."
print(result.payload)      # None
print(result.status)       # "queued"
```

**利用可能なintent一覧：**

| intent | 説明 | textの用途 |
|--------|------|-----------|
| `ping` | 疎通確認 | 不要 |
| `echo` | テキストをそのまま返す | 返すテキスト |
| `summarize` | テキストを短縮する | 短縮するテキスト |
| `llm-query` | LLMに質問する | 質問テキスト |
| `cli-skill` | CLIコマンドを実行する | JSON形式のスキル設定 |
| `cli-pipeline` | 複数CLIコマンドをパイプ実行 | JSON形式のステップ設定 |
| `request-approval` | 承認リクエストを送る | 不要（`request_approval()`を使用推奨） |
| `approve` | 承認する | 不要（`approve()`を使用推奨） |
| `reject` | 却下する | 不要（`reject()`を使用推奨） |
| `payment` | USDC決済を実行する | JSON形式の決済情報 |
| `entropy-check` | エントロピーを計算する | JSON形式のメッセージリスト |

---

### get_reply()

指定したEnvelopeIDへの返信を取得します。

```python
hub.get_reply(
    envelope_id: str,
    timeout: int = 30,
) -> dict | None
```

| パラメータ | 型 | デフォルト | 説明 |
|-----------|-----|-----------|------|
| `envelope_id` | `str` | 必須 | 返信を待つEnvelopeのID |
| `timeout` | `int` | `30` | 待つ秒数 |

```python
result = hub.send(intent="ping", wait=False)
reply = hub.get_reply(result.envelope_id, timeout=10)
print(reply)  # {"pong": True}
```

---

### logs()

処理済みEnvelopeのログを取得します。

```python
hub.logs(
    limit: int = 20,
    intent: str | None = None,
    thread_id: str | None = None,
) -> list[LogEntry]
```

| パラメータ | 型 | デフォルト | 説明 |
|-----------|-----|-----------|------|
| `limit` | `int` | `20` | 取得件数 |
| `intent` | `str \| None` | `None` | intentでフィルタ |
| `thread_id` | `str \| None` | `None` | スレッドIDでフィルタ |

```python
# 全ログを20件取得
logs = hub.logs()

# llm-queryのログのみ
logs = hub.logs(intent="llm-query", limit=5)

for log in logs:
    print(f"{log.time} | {log.intent} | {log.payload}")
```

**戻り値：** `list[LogEntry]`

---

### pending_approvals()

承認待ちのリストを取得します。

```python
hub.pending_approvals() -> list[ApprovalEntry]
```

```python
pending = hub.pending_approvals()
for p in pending:
    print(f"[{p.approval_id[:8]}] {p.description} ({p.status})")
```

**戻り値：** `list[ApprovalEntry]`

---

### approve()

承認リクエストを承認します。

```python
hub.approve(approval_id: str) -> dict
```

| パラメータ | 型 | 説明 |
|-----------|-----|------|
| `approval_id` | `str` | 承認するApprovalのID |

```python
result = hub.approve("7698e25a-306f-4f10-bb61-0d9976746a75")
print(result)  # {"status": "approved", "message": "承認しました"}
```

---

### reject()

承認リクエストを却下します。

```python
hub.reject(approval_id: str, reason: str) -> dict
```

| パラメータ | 型 | 説明 |
|-----------|-----|------|
| `approval_id` | `str` | 却下するApprovalのID |
| `reason` | `str` | 却下理由 |

```python
result = hub.reject("7698e25a-...", reason="予算超過")
print(result)  # {"status": "rejected", "reason": "予算超過"}
```

---

### request_approval()

承認リクエストを作成します。

```python
hub.request_approval(
    description: str,
    approver: str,
    callback: dict,
    thread_id: str | None = None,
) -> ApprovalEntry
```

| パラメータ | 型 | 説明 |
|-----------|-----|------|
| `description` | `str` | 承認内容の説明 |
| `approver` | `str` | 承認者のAgent ID |
| `callback` | `dict` | 承認後に実行するpayload |
| `thread_id` | `str \| None` | スレッドID（省略可） |

```python
approval = hub.request_approval(
    description="海外出張経費 ¥150,000の承認申請",
    approver="https://company.local/@manager",
    callback={"intent": "echo", "text": "承認されました"},
)
print(approval.approval_id)  # "dee6e4e0-..."
print(approval.status)       # "pending"

# 承認待ち確認
pending = hub.pending_approvals()

# 承認
hub.approve(approval.approval_id)
```

---

### health()

サービスの状態を確認します。

```python
hub.health() -> dict
```

```python
status = hub.health()
print(status)
# {
#   "status": "ok",
#   "services": {
#     "queue_dir": "/opt/ai-agent-hub/queue",
#     "queue_dir_exists": True,
#     ...
#   }
# }
```

---

## データクラス

### SendResult

```python
@dataclass
class SendResult:
    envelope_id: str      # EnvelopeのID
    payload: dict | None  # 返信のpayload（wait=Falseの場合はNone）
    status: str           # "ok" | "timeout" | "queued"
```

### LogEntry

```python
@dataclass
class LogEntry:
    id: str                    # EnvelopeのID
    time: str | None           # 処理時刻（ISO 8601）
    intent: str | None         # intentの名前
    sender: str | None         # 送信者のAgent ID
    recipient: str | None      # 受信者のAgent ID
    payload: dict | str | None # payloadの内容
    in_reply_to: str | None    # 返信元EnvelopeのID
    context: str | None        # スレッドID
```

### ApprovalEntry

```python
@dataclass
class ApprovalEntry:
    approval_id: str          # ApprovalのID
    description: str          # 承認内容の説明
    approver: str             # 承認者のAgent ID
    status: str               # "pending" | "approved" | "rejected"
    created_at: str | None    # 作成時刻（ISO 8601）
    decided_at: str | None    # 決定時刻（ISO 8601）
```

---

## 例外クラス

```python
from ai_agent_hub.sdk import AgentHubError, AgentHubConnectionError, AgentHubTimeoutError
```

| 例外クラス | 発生条件 |
|-----------|---------|
| `AgentHubError` | APIエラー（4xx/5xx） |
| `AgentHubConnectionError` | APIサーバーに接続できない |
| `AgentHubTimeoutError` | 返信待ちがタイムアウト |

```python
from ai_agent_hub.sdk import AgentHub, AgentHubConnectionError, AgentHubTimeoutError

hub = AgentHub()

try:
    result = hub.send(intent="llm-query", text="質問", timeout=10)
except AgentHubConnectionError:
    print("APIサーバーに接続できません。hub statusで確認してください。")
except AgentHubTimeoutError:
    print("返信がタイムアウトしました。")
except AgentHubError as e:
    print(f"APIエラー: {e}")
```

---

## 環境変数

| 環境変数 | デフォルト | 説明 |
|---------|-----------|------|
| `AI_AGENT_HUB_URL` | `http://localhost:8080` | APIサーバーのURL |
| `LLM_PROVIDER` | `ollama` | LLMプロバイダー（`ollama` or `openai`） |
| `OLLAMA_API_KEY` | なし | Ollama Cloud APIキー |
| `OPENAI_API_KEY` | なし | OpenAI APIキー |

---

## 使用例

### LLMへの質問

```python
from ai_agent_hub.sdk import AgentHub

hub = AgentHub()
result = hub.send(
    intent="llm-query",
    text="今日の横浜の天気は？",
    model="gemma3:4b",
    timeout=60,
)
print(result.payload["result"])
```

### CLIスキルの実行

```python
# curlでRSSを取得
result = hub.send(
    intent="cli-skill",
    text='{"skill": "curl", "args": ["-s", "https://news.ycombinator.com/rss"]}',
)
rss_content = result.payload["output"]

# grepでフィルタリング
result = hub.send(
    intent="cli-pipeline",
    text=f'{{"steps": [{{"skill": "grep", "args": ["-o", "<title>[^<]*</title>"]}}], "stdin": {repr(rss_content)}}}',
)
print(result.payload["output"])
```

### 承認フローの完全な例

```python
from ai_agent_hub.sdk import AgentHub

hub = AgentHub()

# 1. 承認リクエストを作成
approval = hub.request_approval(
    description="海外出張経費 ¥150,000の承認申請",
    approver="https://company.local/@manager",
    callback={"intent": "echo", "text": "経費が承認されました"},
)
print(f"承認待ち: {approval.approval_id}")

# 2. 承認待ちを確認
pending = hub.pending_approvals()
for p in pending:
    print(f"[{p.approval_id[:8]}] {p.description} - {p.status}")

# 3. 承認（または却下）
hub.approve(approval.approval_id)
# hub.reject(approval.approval_id, reason="予算超過")

# 4. ログで全工程を確認
logs = hub.logs(limit=10)
for log in logs:
    print(f"{log.time} | {log.intent} | {log.payload}")
```
