# ポリシープリセット

`policies/standard/` には、AI Agent Hub ですぐに利用できる標準ポリシープリセットを配置しています。

## 各プリセットの説明

### strict_governance
- 目的: 外部送信と機密情報取り扱いを厳格化。
- 主な制御:
  - 外部送信時の承認必須
  - PII（個人情報）のマスキング
  - 機密情報の外部送信ブロック

### budget_saver
- 目的: コスト超過防止。
- 主な制御:
  - 1日あたりの予算上限（1 USD）
  - 1時間あたりのAPIコール数上限（10回）
  - 予算超過時のアラート

### anti_loop
- 目的: ループ/ハルシネーション抑止。
- 主な制御:
  - エントロピー閾値チェック
  - 類似メッセージの反復上限（3回）
  - ループ検知時のDLQ移送
  - 発散コンテキスト注入

## 使い方のコード例

```python
import json
from pathlib import Path

preset_path = Path("policies/standard/strict_governance.json")
policy = json.loads(preset_path.read_text(encoding="utf-8"))

# 例: リクエストヘッダへ適用
headers = {
    **policy.get("headers", {}),
}

# 例: 実行時ルールとして利用
rules = policy.get("rules", {})
print(policy["name"], headers, rules)
```

```bash
# 任意のプリセットを読み込んで起動時に適用する例（実装に応じて調整）
export AGENT_POLICY_FILE=policies/standard/budget_saver.json
./venv/bin/python -m ai_agent_hub.api_server
```

## カスタムポリシーの作り方

1. `policies/` 配下に JSON ファイルを新規作成します。
2. 以下の基本構造に従って定義します。

```json
{
  "name": "custom_policy",
  "description": "ポリシーの説明",
  "headers": {
    "X-Agent-Policy": "key=value"
  },
  "rules": {
    "your_rule": true
  }
}
```

3. 既存プリセット（`policies/standard/*.json`）をコピーして調整すると安全です。
4. アプリケーション側で `headers` と `rules` を読み取り、実行ポリシーとして適用してください。
