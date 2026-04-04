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

対象ファイル：
- docker-compose.yml（新規）
- Dockerfile（新規）
- ai_agent_hub/api_server.py（新規）
- tests/test_api_server.py（新規）
- .env.example（新規）
- .dockerignore（新規）
- README_DOCKER.md（新規）
