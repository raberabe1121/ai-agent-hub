# Governance Milter Setup

AI Agent Hub の Governance Milter は Postfix の milter インターフェースで配送前ポリシーを検査し、`X-Agent-*` ヘッダーと MIME ボディ内のフラグに基づいて配送可否を決定します。

## 実装内容

- `X-Agent-Policy: confidential=block` の場合、SMTP envelope sender が許可ドメイン外なら拒否します。
- `X-Agent-Cost-Center` はすべてログへ記録します。
- `X-Agent-Workflow: spec-approval-required=true` の場合、MIME ボディ JSON に `spec_approved: true` がなければ拒否します。
- 拒否時は `550 Policy violation: <理由>` を返します。

## 依存関係

```bash
pip install pymilter
```

## 起動方法

```bash
export AI_AGENT_HUB_MILTER_PORT=8025
export AI_AGENT_HUB_ALLOWED_DOMAIN=agent.local
python -m ai_agent_hub.governance_milter
```

- `AI_AGENT_HUB_MILTER_PORT` を省略した場合のデフォルトは `8025` です。
- `AI_AGENT_HUB_ALLOWED_DOMAIN` を省略した場合のデフォルトは `agent.local` です。

## systemd サービスファイル

以下の内容を `/etc/systemd/system/governance_milter.service` として配置してください。

```ini
[Unit]
Description=AI Agent Hub Governance Milter
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/ai-agent-hub
ExecStart=/opt/ai-agent-hub/venv/bin/python -m ai_agent_hub.governance_milter
Restart=always
RestartSec=2
User=opc
Group=opc

[Install]
WantedBy=multi-user.target
```

配置後は以下を実行します。

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now governance_milter.service
```

## Postfix 設定

`main.cf` に以下を追加します。

```ini
smtpd_milters = inet:localhost:8025
non_smtpd_milters = inet:localhost:8025
milter_default_action = accept
```

反映後は Postfix を再起動してください。

```bash
sudo systemctl restart postfix
```
