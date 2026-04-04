FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    curl \
    grep \
    jq \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .
RUN pip install -e . && \
    pip install fastapi uvicorn httpx openai

CMD ["python", "-m", "ai_agent_hub.lmtp_server"]
