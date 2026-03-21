"""インテリジェント・ニュースルーム PoC"""
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ["AI_AGENT_HUB_QUEUE_DIR"] = "/opt/ai-agent-hub/queue"
os.environ["AI_AGENT_HUB_PROCESSED_DIR"] = "/opt/ai-agent-hub/processed"

from ai_agent_hub import Envelope
from ai_agent_hub.smtp_sender import send_envelope_via_smtp

PROCESSED_DIR = Path("/opt/ai-agent-hub/processed")


def wait_for_reply(original_id: str, timeout: int = 20) -> dict | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for f in PROCESSED_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                if data.get("inReplyTo") == original_id:
                    return data
            except Exception:
                continue
        time.sleep(0.5)
    return None


def send_and_wait(payload: dict, sender: str) -> dict | None:
    env = Envelope.new(
        envelope_type="command",
        sender=sender,
        recipient="https://agent.local/@worker",
        payload=payload,
    )
    send_envelope_via_smtp(env)
    print(f"  → 送信ID: {env.id}")
    result = wait_for_reply(env.id)
    if result:
        print(f"  → 返信受信: ✅")
    else:
        print(f"  → タイムアウト: ❌")
    return result


print("=" * 60)
print("🗞️  AI Agent Hub - インテリジェント・ニュースルーム PoC")
print("=" * 60)

# ステップ1: RSS取得
print("\n📡 ステップ1: RSSフィードを取得中...")
r1 = send_and_wait(
    payload={"intent": "cli-skill", "skill": "curl",
             "args": ["-s", "--max-time", "10",
                      "https://news.ycombinator.com/rss"]},
    sender="https://newsroom.local/@collector",
)
if not r1:
    print("失敗。終了します。"); exit(1)

rss = r1.get("payload", {}).get("output", "")
print(f"  → {len(rss)}文字取得")

# ステップ2: タイトル抽出
print("\n🔍 ステップ2: タイトルをフィルタリング中...")
r2 = send_and_wait(
    payload={"intent": "cli-pipeline",
             "steps": [
                 {"skill": "grep", "args": ["-o", "<title>[^<]*</title>"]},
                 {"skill": "grep", "args": ["-v", "Hacker News"]},
             ],
             "stdin": rss},
    sender="https://newsroom.local/@filter",
)

titles = []
if r2:
    raw = r2.get("payload", {}).get("output", "")
    titles = [t.replace("<title>","").replace("</title>","").strip()
              for t in raw.strip().split("\n") if t.strip()][:5]
    print(f"  → 上位5件:")
    for i, t in enumerate(titles, 1):
        print(f"     {i}. {t}")

# ステップ3: 要約
print("\n📝 ステップ3: 要約中...")
r3 = send_and_wait(
    payload={"intent": "summarize", "text": "\n".join(titles)},
    sender="https://newsroom.local/@summarizer",
)
if r3:
    print(f"  → 要約: {r3.get('payload', {}).get('summary', '')}")

print("\n" + "=" * 60)
print("✅ デモ完了！全工程がEnvelopeとして不変ログに記録されました")
print("=" * 60)
