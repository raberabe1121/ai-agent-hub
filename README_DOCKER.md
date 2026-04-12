# クイックスタート

## 必要なもの
- Docker Desktop
- Docker Compose

## 起動方法

```bash
git clone https://github.com/raberabe1121/ai-agent-os.git
cd ai-agent-os
cp .env.example .env
# .envにOLLAMA_API_KEYを設定

docker compose up -d
```

## 動作確認

```bash
# Envelopeを送信
curl -X POST http://localhost:8080/envelopes \
  -H "Content-Type: application/json" \
  -d '{"intent": "ping"}'

# 返信を確認
curl http://localhost:8080/envelopes/{envelope_id}/reply
```

## systemd利用時の注意（承認DBの共有）

`api_server.service` と `agent_worker.service` の両方で、同じ承認DBパスを参照する必要があります。  
どちらか片方だけに設定すると、承認作成と承認参照でDBが分かれて `404` になる場合があります。

以下を両サービスに追加してください。

```ini
Environment=AI_AGENT_HUB_APPROVAL_DB=/opt/ai-agent-hub/approvals.db
```

対象ファイル：
- docker-compose.yml（新規）
- Dockerfile（新規）
- ai_agent_hub/api_server.py（新規）
- tests/test_api_server.py（新規）
- .env.example（新規）
- .dockerignore（新規）
- README_DOCKER.md（新規）
