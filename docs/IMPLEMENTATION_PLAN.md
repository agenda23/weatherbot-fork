# WeatherBet 実装計画書

**作成日**: 2026-04-14
**最終更新**: 2026-04-14
**対象**: weatherbot-fork リポジトリ

---

## 凡例

| 記号 | 意味 |
|------|------|
| ✅ | 実装済み |
| 🔧 | 部分実装・改善余地あり |
| 🚧 | 未実装 |

---

## 1. 現状分析

### 1.1 構造上の課題

| 課題 | 状態 |
|------|------|
| 単一ファイル構成（`weatherbet.py` が約1060行） | ✅ `src/weatherbet/` へ分割済み |
| テスト不在 | ✅ `tests/test_core_functions.py` 実装済み（44 pass） |
| 依存管理ファイルなし（`requirements.txt` / `pyproject.toml`） | 🚧 未対応 |
| グローバル状態（`_cal`、config をインポート時に読み込み） | 🚧 未対応 |
| 構造化ログなし（全出力が `print()`） | ✅ `log_event()` + `data/logs/weatherbet.log` 実装済み |
| `data/` / `simulation.json` が `.gitignore` 対象外 | ✅ 対応済み |

### 1.2 ロジック上の課題

| 課題 | 状態 |
|------|------|
| `bucket_prob` が通常バケットで 0/1 の二値判定（EV・Kellyを無意味化）| ✅ 修正済み（CDF差分に変更） |
| 予報が単一ソース優先（HRRR > ECMWF）で不確実性を無視 | ✅ 修正済み（逆分散アンサンブルに変更） |
| キャリブレーションの sigma が MAE ベース（RMSE が統計的に正確） | ✅ RMSE 化済み |
| `run_calibration` が誤ったフィールド名でマーケットを参照し常に空リスト | ✅ 修正済み（`status=="resolved"`, `best_source`, `best`） |
| `forecast_snap` に `blended`/`blended_sigma` が保存されずバックテストで参照不可 | ✅ 修正済み（フィールド追加） |
| v1/v2 の設定スキーマ不一致 | 🔧 ドキュメントで注記済み |

### 1.3 動作していない・未接続の部分

| 項目 | 状態 |
|------|------|
| `sim_dashboard_repost.html` と `data/` の連携 | 🟡 `dashboard.json` 生成まで対応（HTML fetch は未） |
| Visual Crossing キーなし時のキャリブレーション | 🔧 キーがあれば動作する |
| README・docstring の `weatherbet.py` 表記 | ✅ `weatherbet.py` に統一済み |

---

## 2. 実装フェーズ

### Phase 0: 即時対応（コード構造変更なし）

| タスク | 状態 |
|--------|------|
| `.gitignore` に `data/`・`simulation.json` を追加 | ✅ |
| `bucket_prob` の CDF 化（通常バケットへの適用） | ✅ |
| `run_calibration` のフィールド名バグ修正 | ✅ |
| `forecast_snap` への `blended`/`blended_sigma` 追加 | ✅ |
| `blend_forecast` 追加（逆分散アンサンブル） | ✅ |
| `take_forecast_snapshot` のアンサンブル合成対応 | ✅ |
| `scan_and_update` のポートフォリオ上限追加 | ✅ |
| `config.json` への `sigma_f`・`sigma_c`・`max_open_positions` 追加 | ✅ |
| README の `bot_v2.py` 表記を `weatherbet.py` に統一 | ✅ |
| `weatherbet.py` の docstring・CLI usage 修正 | ✅ |

---

### Phase 1: 基盤整備（インフラ・品質）

**目的**: 安全にコードを変更できる土台を作る。

#### 1-1. プロジェクト構成の整備 🚧

```
weatherbot-fork/
├── pyproject.toml
├── src/
│   └── weatherbet/
│       ├── config.py
│       ├── models.py
│       ├── forecast/
│       │   ├── ecmwf.py
│       │   ├── hrrr.py
│       │   ├── metar.py
│       │   └── blend.py
│       ├── market/
│       │   ├── polymarket.py
│       │   ├── parser.py
│       │   └── resolver.py
│       ├── strategy/
│       │   ├── probability.py
│       │   ├── kelly.py
│       │   └── risk.py
│       ├── storage/
│       │   ├── state.py
│       │   └── markets.py
│       ├── calibration.py
│       ├── scanner.py
│       ├── monitor.py
│       └── cli.py
└── tests/
```

#### 1-2. テスト導入 ✅

`tests/test_core_functions.py` 実装済み（44 pass）。対象:
- `parse_temp_range`: 正規表現パース（全パターン網羅）
- `bucket_prob` / `calc_ev` / `calc_kelly` / `bet_size`: 純粋関数
- `in_bucket`: エッジケース（single-degree、`or below`、`or higher`）
- `blend_forecast`: 単一ソース時のフォールバック、逆分散計算

```bash
pytest tests/ -v
```

#### 1-3. ログ基盤 ✅

- `log_event()` 実装済み: コンソール出力 + `data/logs/weatherbet.log`（JSON Lines）
- レベル: トレード実行 → INFO、API エラー → WARNING、致命的 → ERROR

#### 1-4. .gitignore 追加 ✅

`data/`、`simulation.json`、`.deps/` を追加済み。

---

### Phase 2: 確率モデルの改善

#### 2-1. `bucket_prob` の CDF 化 ✅

全バケットで正規分布 CDF 差分を使用。`P = Φ((t_high - fc) / σ) - Φ((t_low - fc) / σ)`

#### 2-2. アンサンブル合成 ✅

ECMWF + HRRR を逆分散重み付きで合成。`blended_sigma = sqrt(1 / Σ(1/σi²))`

#### 2-3. キャリブレーション sigma の統計的改善 ✅

`run_calibration()` にて RMSE ベースに変更済み。正規分布の場合 RMSE が sigma の最尤推定量。

```python
rmse = math.sqrt(sum(e ** 2 for e in errors) / len(errors))
```

---

### Phase 3: リスク管理の強化

#### 3-1. ポートフォリオレベルのリスク制限 ✅（上限のみ）

- `max_open_positions`（デフォルト10）: 実装済み

追加実装済み:
- 1日あたりの最大損失制限（`daily_loss_limit_pct`） ✅ `get_today_realized_loss()` + `scan_and_update()` 冒頭ガード
- 相関制限（同一日・同一都市への重複エントリー防止） ✅ `has_open_position_for_city_date()`

#### 3-2. 動的ストップロス ✅

`calc_dynamic_stop_price()` 実装済み。sigma 連動で loss_pct を 10〜35% の範囲でスケール。

#### 3-3. テイクプロフィットの連続関数化 ✅

`calc_take_profit_threshold()` 実装済み。24〜48h 区間を線形補間（0.85 → 0.75）、48h 以上は 0.75 固定、24h 未満は hold。

---

### Phase 4: バックテスト基盤 ✅

#### 4-1. バックテストエンジン ✅

`backtest.py` を実装。`data/markets/` の JSON を使いパラメータを再評価。

```bash
python backtest.py                          # デフォルト設定
python backtest.py --sweep min_ev           # 感度分析
python backtest.py --param min_ev=0.15      # パラメータ上書き
python backtest.py --city chicago nyc       # 都市フィルタ
python backtest.py --use-calibration        # キャリブレーション済み sigma
```

#### 4-2. フォワードテストモード ✅

`--forward` フラグを実装。`actual_temp` がある全市場を対象に、バケット範囲との比較で勝敗をローカル判定。

```bash
python backtest.py --forward --sweep min_ev
```

`weatherbet.py` が `vc_key` を使い全クローズ市場の `actual_temp` を自動取得（ポジション有無に関わらず）。

#### 4-3. メトリクス出力 ✅

勝率、PnL、ROI、期待値、最大ドローダウン、Sharpe、都市別内訳。

---

### Phase 5: ダッシュボード連携 ✅

#### 5-1. データエクスポート ✅

```python
def export_dashboard_data():
    """data/dashboard.json を生成"""
    ...
```

#### 5-2. ローカル起動 ✅

```bash
python weatherbet.py dashboard   # dashboard.json を生成してブラウザで開く
```

---

### Phase 6: 通知・アラート 🟡（一部完了）

| チャネル | 状態 |
|----------|------|
| Discord Webhook | ✅ |
| メール（SMTP） | 🚧 |
| OS ネイティブ通知 | 🚧 |

通知トリガー:

| イベント | 緊急度 |
|----------|--------|
| ポジション開始 | 低 |
| ストップロス発動 | 中 |
| API 障害（N回連続失敗） | 高（✅ 実装済み） |
| 残高が初期値の M% 以下 | 高 |
| 日次サマリー | 低 |

---

### Phase 7: 実取引対応 🟡（実装進行中）

現状、読み取り専用に加え CLOB 連携の基盤実装が追加されている。残りは本番互換署名（EIP-712）と法規制確認。

- ✅ Polymarket CLOB API クライアントの実装（book/order/status）
- ✅ Polygon ウォレット統合（秘密鍵管理）
- ✅ 注文署名・送信ロジック（stub/eth_sign、dry-run + live gated）
- ✅ オンチェーン約定確認（order status polling）
- 法規制確認（居住地域による制約）

**Phase 1–6 が安定してから検討する。**

---

## 3. 優先順位（現状）

Phase 0〜6 の主要機能は実装済み。残りの課題:

| 順序 | Phase / タスク | 状態 |
|------|---------------|------|
| 1 | Phase 1: 依存管理（`requirements.txt`） | ✅ |
| 2 | Phase 1: モジュール分割（`src/weatherbet/`） | ✅ |
| 3 | Phase 6: メール通知（SMTP） | 🚧 |
| 4 | Phase 6: OS ネイティブ通知 | 🚧 |
| 5 | Phase 7: EIP-712 本番署名 | 🚧 |
| 6 | Phase 7: 法規制確認 | 🚧 |
