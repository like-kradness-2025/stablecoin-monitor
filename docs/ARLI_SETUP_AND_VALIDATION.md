# Arli系モデル向け セットアップ・動作確認チェックリスト

対象ブランチ: `feat/v3-refactor-responsibilities`

目的: Arli系モデルまたはサブエージェントが、このブランチを読み込んだあとに、インストールから最小動作確認まで迷わず進めるようにする。

---

## 0. 前提

このリポジトリは `stablecoin-monitor v3.00` のCCXT public OHLCV版である。

デフォルトでは以下を使う。

```text
CCXT public OHLCV
APIキー不要
SQLite保存
Proxy CVD算出
PNGチャート生成
Discord通知は任意
```

注意:

```text
Proxy CVDはTrue CVDではない。
True CVDやWebSocket実装を勝手に追加しない。
```

---

## 1. 必ず最初に確認すること

```bash
git status
git branch --show-current
```

期待ブランチ:

```text
feat/v3-refactor-responsibilities
```

違う場合:

```bash
git checkout feat/v3-refactor-responsibilities
```

---

## 2. Python仮想環境の作成

### Linux / macOS / WSL / Termux

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

---

## 3. pytestを使う場合

テストを追加する作業では、必要なら `requirements.txt` に以下を追加する。

```text
pytest>=8.0
```

その後:

```bash
pip install -r requirements.txt
```

---

## 4. .env作成

```bash
cp .env.example .env
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

デフォルトではAPIキー不要。

Discord通知を使う場合だけ `.env` に以下を設定する。

```env
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

---

## 5. 静的確認

必ず実行する。

```bash
python -m compileall stablecoin_monitor stablecoin_monitor.py
```

PASS条件:

```text
compile errorがない
```

FAIL時は、実行を進めず原因を修正する。

---

## 6. import smoke test

必ず実行する。

```bash
python - <<'PY'
import stablecoin_monitor
from stablecoin_monitor.app import StablecoinMonitorApp
from stablecoin_monitor.config import load_settings
from stablecoin_monitor.market_registry import load_markets_config
from stablecoin_monitor.proxy_cvd import proxy_delta_from_ohlcv
from stablecoin_monitor.flow import classify_flow
print('import smoke OK')
PY
```

Windows PowerShellで上記が使いづらい場合:

```powershell
python -c "import stablecoin_monitor; import stablecoin_monitor.app; import stablecoin_monitor.config; import stablecoin_monitor.market_registry; import stablecoin_monitor.proxy_cvd; import stablecoin_monitor.flow; print('import smoke OK')"
```

---

## 7. 単体テスト

テストが存在する場合は必ず実行する。

```bash
pytest
```

まだテストが存在しない場合は、`docs/QWEN35_27B_WORK_INSTRUCTION.md` のPhase計画に従い、以下を追加する。

```text
tests/test_proxy_cvd.py
tests/test_market_registry.py
tests/test_flow.py
tests/test_config.py
tests/test_db.py
tests/test_ccxt_fetcher.py
```

注意:

```text
pytestでは外部ネットワークを叩かない。
CCXTはmock/fakeでテストする。
```

---

## 8. 最小動作確認

可能なら実行する。

```bash
python stablecoin_monitor.py --once
```

成功時に期待すること:

```text
- CCXT public OHLCVを取得する
- SQLite DBが作成される
- output/ にPNGチャートが生成される
- DISCORD_WEBHOOK_URL未設定なら標準出力にsummaryが出る
```

生成物の例:

```text
data/stablecoin_monitor.db
output/stablecoin_monitor_v3_YYYYMMDD_HHMMSS.png
```

---

## 9. --onceをSKIPできる条件

`python stablecoin_monitor.py --once` をSKIPできるのは、以下の外部要因がある場合のみ。

```text
- ネットワーク不可
- 依存インストール不可
- CCXT接続不可
- 実行環境のmatplotlib backend問題
- 取引所側の一時的なAPI制限
```

SKIPした場合は、必ず以下を書く。

```text
- SKIP理由
- 代替確認として実行したコマンド
- 人間が次に実行すべきコマンド
```

---

## 10. loop確認

長時間実行は任意。

可能なら短時間だけ確認する。

```bash
python stablecoin_monitor.py --loop
```

確認すること:

```text
- 起動ログが出る
- KeyboardInterruptで正常停止する
- 取引所ごとの失敗で全体停止しない
```

停止:

```text
Ctrl+C
```

---

## 11. 動作確認報告フォーマット

```markdown
## Setup / Validation Result

### Branch
- current branch: ...

### Install
- venv: PASS/FAIL
- pip install -r requirements.txt: PASS/FAIL

### Static Check
- compileall: PASS/FAIL
- import smoke: PASS/FAIL

### Tests
- pytest: PASS/FAIL/SKIPPED
- reason if skipped: ...

### Runtime Check
- python stablecoin_monitor.py --once: PASS/FAIL/SKIPPED
- generated DB: yes/no
- generated PNG: yes/no
- Discord: sent/not configured/failed

### Remaining Risks
- ...

### Next Action
- ...
```

---

## 12. 禁止事項

```text
- APIキー必須に戻す
- CMC依存を復活させる
- True CVDやWebSocketを勝手に追加する
- DBを破壊的に変更する
- market_key形式を変える
- pytestで外部ネットワークを叩く
- --onceを理由なくSKIPする
```
