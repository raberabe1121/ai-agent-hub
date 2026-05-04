install:
	python3 -m venv venv
	./venv/bin/pip install -e .
	./venv/bin/pip install fastapi uvicorn httpx openai click
	@echo ""
	@echo "✅ インストール完了"
	@echo "次のコマンドで環境を有効化してください："
	@echo "  source venv/bin/activate"
	@echo ""
	@echo "動作確認："
	@echo "  make start"
	@echo "  make demo"

start:
	@echo "🚀 AI Agent Hub を起動します..."
	./venv/bin/python -m ai_agent_hub.lmtp_server &
	sleep 1
	./venv/bin/python -m ai_agent_hub.agent_worker &
	sleep 1
	./venv/bin/python -m ai_agent_hub.api_server &
	sleep 2
	@echo "✅ 起動完了"
	@echo "  API: http://localhost:8080/health"

stop:
	@echo "🛑 AI Agent Hub を停止します..."
	pkill -f "ai_agent_hub.lmtp_server" || true
	pkill -f "ai_agent_hub.agent_worker" || true
	pkill -f "ai_agent_hub.api_server" || true
	@echo "✅ 停止完了"

demo:
	./venv/bin/python examples/quickstart.py

demo-newsroom:
	./venv/bin/python demo/demo_newsroom.py

demo-governance:
	./venv/bin/python demo/demo_expense_approval.py

test:
	./venv/bin/pytest tests/ -v

status:
	./venv/bin/hub status

logs:
	./venv/bin/hub logs --limit 20

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	rm -f /tmp/lmtp_debug.log

cleanup-processed:
	./venv/bin/python -m ai_agent_hub.cleanup --days 1

cleanup-processed-dry:
	./venv/bin/python -m ai_agent_hub.cleanup --days 1 --dry-run

help:
	@echo "AI Agent Hub - 利用可能なコマンド"
	@echo ""
	@echo "  make install      依存パッケージをインストール"
	@echo "  make start        全サービスをバックグラウンドで起動"
	@echo "  make stop         全サービスを停止"
	@echo "  make demo         クイックスタートデモを実行"
	@echo "  make demo-newsroom インテリジェント・ニュースルームデモ"
	@echo "  make demo-governance 経費承認フローデモ（5つのガバナンス機能）"
	@echo "  make test         テストを実行"
	@echo "  make status       サービス状態を確認"
	@echo "  make logs         最新ログを表示"
	@echo "  make clean        一時ファイルを削除"

.PHONY: install start stop demo demo-newsroom demo-governance test status logs clean cleanup-processed cleanup-processed-dry help
