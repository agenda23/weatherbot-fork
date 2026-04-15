# WeatherBet TODO リスト

**最終更新**: 2026-04-15（ダッシュボード完全接続・モジュール分割完了）  
優先度: 🔴 高 / 🟡 中 / 🟢 低

---

## 即時対応（コード変更不要 or 最小限）

| 優先度 | タスク | 備考 |
|--------|--------|------|
| ✅ | `.gitignore` に `data/` と `simulation.json` を追加 | 完了 |
| ✅ | `README.md` の `weatherbet.py` → `weatherbet.py` に修正 | 完了 |
| ✅ | `weatherbet.py` の先頭 docstring と CLI usage の `weatherbet.py` 表記を修正 | 完了 |

---

## Phase 1: 基盤整備

### テスト導入 🔴

テスト対象（優先度順）:

- `parse_temp_range` — 正規表現パース（全パターン: 通常・`or below`・`or higher`・セ氏）
- `bucket_prob` — エッジケース: t_low=-999、t_high=999、sigma=0
- `calc_ev` / `calc_kelly` / `bet_size` — 純粋関数、値の範囲チェック
- `in_bucket` — single-degree バケット、境界値
- `blend_forecast` — 単一ソース時のフォールバック、逆分散計算の数値検証

```bash
# 導入コマンド例（実行後に pytest.ini / pyproject.toml に設定追加）
pip install pytest
pytest tests/ -v
```

進捗: ✅ `tests/test_core_functions.py` を追加し、主要純粋関数テストを実装済み（現状 44 pass）。

### 構造化ログ 🟡

- コンソール: 既存の色付き `print()` をカスタムフォーマッタに移行
- ファイル: `data/logs/weatherbet.log`（JSON Lines 形式）
- レベル: トレード実行 → INFO、API エラー → WARNING、致命的 → ERROR

進捗: ✅ `log_event()` を実装し、主要イベントで構造化ログ出力に対応済み。

### モジュール分割 ✅

`weatherbet.py` を `src/weatherbet/` パッケージへ分割済み。`weatherbet.py` は薄いエントリーポイントとして残存。

```
src/weatherbet/
├── config.py
├── calibration.py
├── notify.py
├── scanner.py / monitor.py / report.py / cli.py / clob.py
├── forecast/  (ecmwf.py, hrrr.py, metar.py, blend.py)
├── market/    (polymarket.py, parser.py)
├── strategy/  (probability.py, kelly.py, risk.py)
└── storage/   (state.py, markets.py)
```

---

## Phase 2: 確率モデルの改善

### キャリブレーション sigma の RMSE 化 🟡

- **現状**: MAE（平均絶対誤差）を sigma に代入
- **問題**: 正規分布に対して MAE は sigma を約20%過小推定（`RMSE ≈ MAE × √(π/2)`）
- **改善**: RMSE（二乗平均平方根誤差）を使用 → 正規分布の sigma の最尤推定量

```python
# 現状 (MAE)
mae = sum(errors) / len(errors)

# 改善案 (RMSE)
import math
rmse = math.sqrt(sum(e**2 for e in errors) / len(errors))
```

対象関数: `run_calibration()` 内の `mae = ...` の行

進捗: ✅ `run_calibration()` を RMSE ベースに変更済み。

---

## Phase 3: リスク管理の強化

### 日次損失制限 🟡

- 設定: `daily_loss_limit_pct`（例: 0.10 = 残高の10%）
- ロジック: `scan_and_update()` 入口で当日の確定損失を集計し、制限超過時はスキャンをスキップ
- 対象ファイル: `weatherbet.py` + `config.json`

進捗: ✅ 実装済み（`get_today_realized_loss()` + 入口ガード）。

### 相関制限の明示化 🟢

- 同一日・同一都市への重複エントリー防止（現状は `scan_and_update()` のマーケット検索で自然にスキップされているが、明示的なチェックがない）

進捗: ✅ 明示的な重複防止ガードを実装済み。

### 動的ストップロス 🟢

- 現状: 固定 20% ストップ（`current_price <= entry_price * 0.80`）
- 改善: sigma に連動（不確実性が高い都市は広めにストップを設定）
- 例: `stop_threshold = entry_price * (1 - 0.20 * (sigma / 2.0))`

進捗: ✅ `calc_dynamic_stop_price()` を実装し、監視ロジックへ適用済み。

### テイクプロフィットの連続化 🟢

- 現状: 3段階（48h+: $0.75、24–48h: $0.85、24h-: hold）
- 改善: 時間の連続関数として計算（例: 線形補間 or sigmoid）

進捗: ✅ `calc_take_profit_threshold()` による連続関数へ移行済み。

---

## Phase 5: ダッシュボード連携

### データエクスポート 🟢

```python
def export_dashboard_data():
    """data/dashboard.json を生成（ポジション、PnL、予報概要）"""
    ...
```

進捗: ✅ 実装済み。

### リアルタイム監視ダッシュボード ✅

`python weatherbet.py run` だけでダッシュボードが起動する。

- `export_dashboard_data()` が毎時スキャン・10 分監視のたびに `data/dashboard.json` を自動更新
- `start_dashboard_server()` が `http.server` を daemon スレッドで起動（デフォルト port 8000）
- `sim_dashboard_repost.html` が `/data/dashboard.json` を 30 秒ごとにポーリング
- 表示内容: 残高チャート（永続）・都市別成績・日次 PnL バーチャート・オープンポジション・ボットログ

```bash
python weatherbet.py run        # 自動で http://localhost:8000/sim_dashboard_repost.html を起動
python weatherbet.py dashboard  # 1 回エクスポートしてブラウザを開く
```

---

## Phase 6: 通知・アラート

| 優先度 | チャネル | トリガー例 |
|--------|----------|------------|
| ✅ | Discord Webhook | ポジション開始、ストップロス発動（実装済み） |
| 🟢 | メール（SMTP） | 日次サマリー、API障害 |
| 🟢 | OS ネイティブ通知 | ローカル実行時の簡易通知 |

通知トリガー:

| イベント | 緊急度 |
|----------|--------|
| ポジション開始 | 低 |
| ストップロス発動 | 中 |
| API 障害（N回連続失敗） | 高（実装済み） |
| 残高が初期値の M% 以下 | 高 |
| 日次サマリー | 低 |

---

## Phase 7: 実取引対応（最終目標）

現状のボットは Polymarket Gamma API の読み取りのみ。実取引には以下が必要:

- [x] Polymarket CLOB API クライアントの実装（read-only + order/status client）
- [x] Polygon ウォレット統合（秘密鍵管理）
- [x] 注文署名・送信ロジック（stub / eth_sign、dry-run + live gated）
- [x] オンチェーン約定確認（order status polling）
- [ ] 法規制確認（居住地域による制約）

**Phase 1〜6 が安定してから着手する。**

---

## 既知の制限・注意事項（バグではない）

| 項目 | 内容 |
|------|------|
| キャリブレーション有効化に 30 件以上の解決済みマーケットが必要 | `calibration_min` デフォルト値。初期はデフォルト sigma (2.0F / 1.2C) で動作 |
| バックテスト（`--forward` なし）はポジションを取った市場のみ対象 | スキップした市場の勝敗は `--forward` モードで確認 |
| ダッシュボードはローカルホストのみ（127.0.0.1）にバインド | `dashboard_port` で変更可。外部公開したい場合は nginx 等でリバースプロキシを追加 |
| v1 は v2 の `config.json` と互換性なし | v1 はキーが見つからない場合はハードコードされたデフォルトにフォールバック |
