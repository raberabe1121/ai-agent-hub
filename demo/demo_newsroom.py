"""インテリジェント・ニュースルーム PoC"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from ai_agent_hub import Envelope
from ai_agent_hub.repository import save_envelope

PROCESSED_DIR = Path(os.environ.get("AI_AGENT_HUB_PROCESSED_DIR", "/opt/ai-agent-hub/processed"))
OLLAMA_CONFIG_PATH = Path("/etc/ai-agent-hub/config")


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
    save_envelope(env)
    print(f"  → 送信ID: {env.id}")
    result = wait_for_reply(env.id)
    if result:
        print("  → 返信受信: ✅")
    else:
        print("  → タイムアウト: ❌")
    return result


def _extract_llm_text(payload: dict) -> str:
    if not isinstance(payload, dict):
        return ""

    for key in ("result", "summary", "response", "output", "text"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    message = payload.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())
            if parts:
                return "\n".join(parts)

    return ""


def _iter_key_value_lines(path: Path) -> list[tuple[str, str]]:
    if not path.exists():
        return []

    pairs: list[tuple[str, str]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            pairs.append((key, value))
    return pairs


def _resolve_ollama_api_key() -> str | None:
    env_value = os.environ.get("OLLAMA_API_KEY")
    if isinstance(env_value, str) and env_value.strip():
        return env_value.strip()

    for key, value in _iter_key_value_lines(Path.home() / ".bashrc"):
        if key == "OLLAMA_API_KEY" and value:
            return value

    for key, value in _iter_key_value_lines(OLLAMA_CONFIG_PATH):
        if key == "OLLAMA_API_KEY" and value:
            return value

    return None


def main() -> None:
    print("=" * 60)
    print("🗞️  AI Agent Hub - インテリジェント・ニュースルーム PoC")
    print("=" * 60)

    # ステップ1: RSS取得
    print("\n📡 ステップ1: RSSフィードを取得中...")
    r1 = send_and_wait(
        payload={
            "intent": "cli-skill",
            "skill": "curl",
            "args": ["-s", "--max-time", "10", "https://news.ycombinator.com/rss"],
        },
        sender="https://newsroom.local/@collector",
    )
    if not r1:
        print("失敗。終了します。")
        raise SystemExit(1)

    rss = r1.get("payload", {}).get("output", "")
    print(f"  → {len(rss)}文字取得")

    # ステップ2: タイトル抽出
    print("\n🔍 ステップ2: タイトルをフィルタリング中...")
    r2 = send_and_wait(
        payload={
            "intent": "cli-pipeline",
            "steps": [
                {"skill": "grep", "args": ["-o", "<title>[^<]*</title>"]},
                {"skill": "grep", "args": ["-v", "Hacker News"]},
            ],
            "stdin": rss,
        },
        sender="https://newsroom.local/@filter",
    )

    titles = []
    if r2:
        raw = r2.get("payload", {}).get("output", "")
        titles = [
            t.replace("<title>", "").replace("</title>", "").strip()
            for t in raw.strip().split("\n")
            if t.strip()
        ][:5]
        print("  → 上位5件:")
        for i, title in enumerate(titles, 1):
            print(f"     {i}. {title}")

    # ステップ3: Ollamaによる知的な要約
    print("\n📝 ステップ3: AI (Gemma 3) が内容を分析して要約中...")
    prompt_text = (
        "以下のHacker Newsのタイトルを、日本のITエンジニア向けに要約してください。"
        "重要な3点に絞って、簡潔な日本語でお願いします：\n\n"
        + "\n".join(titles)
    )
    ollama_api_key = _resolve_ollama_api_key()
    if not ollama_api_key:
        print("  → 要約エラー: OLLAMA_API_KEY が demo 実行プロセスで解決できませんでした")
        print("    (確認元: os.environ, ~/.bashrc, /etc/ai-agent-hub/config)")
        print("\n" + "=" * 60)
        print("✅ デモ完了！全工程がEnvelopeとして不変ログに記録されました")
        print("=" * 60)
        return

    r3 = send_and_wait(
        payload={
            "intent": "llm-query",
            "text": prompt_text,
            "model": "gemma3:4b",
            "api_key": ollama_api_key,
        },
        sender="https://newsroom.local/@summarizer",
    )
    if r3:
        payload = r3.get("payload", {})
        summary_text = _extract_llm_text(payload)
        if summary_text:
            print(f"  → 要約: {summary_text}")
        else:
            error_text = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(error_text, str) and error_text.strip():
                print(f"  → 要約エラー: {error_text}")
            else:
                print(f"  → 要約: (空のレスポンス) payload={payload}")

    print("\n" + "=" * 60)
    print("✅ デモ完了！全工程がEnvelopeとして不変ログに記録されました")
    print("=" * 60)


if __name__ == "__main__":
    main()
