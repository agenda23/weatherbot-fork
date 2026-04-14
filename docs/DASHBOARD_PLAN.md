# リアルタイム監視ダッシュボード 実装計画書

**作成日**: 2026-04-14  
**対象**: `sim_dashboard_repost.html` + `src/weatherbet/report.py` + bot main loop

---

## 1. 現状分析と課題

### 1.1 既存実装の構成

| コンポーネント | 場所 | 内容 |
|---|---|---|
| ダッシュボード HTML | `sim_dashboard_repost.html` | Chart.js + matrix テーマ UI（美しいが未接続） |
| データエクスポート | `src/weatherbet/report.py:export_dashboard_data()` | `data/dashboard.json` を生成 |
| CLI トリガー | `src/weatherbet/cli.py` | `python weatherbet.py dashboard` でのみ呼び出し |

### 1.2 現状の断線ポイント（3 か所）

```
[Bot 稼働中]          [ファイルシステム]         [HTML]
scan_and_update()        data/dashboard.json    sim_dashboard_repost.html
  ↓ export 呼ばれない    ↑ 手動のみ生成          ↓ fetch("simulation.json") ← 間違い
monitor_positions()                              ↓ データ構造も v1 スキーマ
```

**断線①**: `export_dashboard_data()` がボット稼働中に一切呼ばれない  
**断線②**: HTML が `simulation.json`（v1 形式）を fetch しているが、v2 では `data/dashboard.json`  
**断線③**: HTML のデータパース（`sim.balance`, `sim.positions`, `sim.trades`）が v2 の JSON スキーマと不一致  
**断線④**: `file://` プロトコルでは fetch が CORS で失敗。HTTP サーバーが必要だが手動起動のみ  

### 1.3 `data/dashboard.json` の現在のスキーマ

```json
{
  "generated_at": "ISO8601",
  "state": { "balance", "starting_balance", "wins", "losses", ... },
  "summary": { "open_count", "resolved_count", "wins", "losses", "total_realized_pnl" },
  "open_positions": [{ "city", "city_name", "date", "bucket_low", "bucket_high",
                        "entry_price", "current_price", "shares", "cost",
                        "unrealized_pnl", "forecast_source" }],
  "recent_resolved": [{ "city", "city_name", "date", "pnl", "result" }]  // 最新30件
}
```

**不足データ**（ダッシュボードで有用だが現在エクスポートされていない）:
- 残高履歴（チャートに必要）
- 都市別勝率・PnL 集計
- フォアキャスト一覧（現在各都市の予報温度・sigma）
- ログ末尾（直近イベント）
- 日次 PnL 系列

---

## 2. 実装可能性の評価

**結論: 完全実装可能。外部依存ゼロ（Python 標準ライブラリのみ）。**

| 要件 | 手段 | 難易度 |
|---|---|---|
| HTTP サーバー | `http.server.SimpleHTTPRequestHandler` をスレッドで起動 | ★☆☆ |
| 自動エクスポート | `scan_and_update()` / `monitor_positions()` 末尾に 1 行追加 | ★☆☆ |
| HTML スキーマ修正 | JS の fetch 先・パース箇所を書き換え | ★★☆ |
| 残高履歴の永続化 | `data/balance_history.json` に追記 | ★☆☆ |
| 都市別集計 | `export_dashboard_data()` 内でループ集計 | ★☆☆ |
| フォアキャスト表 | `data/markets/` から最新スナップを読む | ★★☆ |
| ログ末尾取得 | `data/logs/weatherbet.log` の末尾 N 行を読む | ★☆☆ |

---

## 3. 実装アーキテクチャ

### 3.1 全体構成（完成形）

```
weatherbet.py run
  └─ cli.py:run_loop()
       ├─ [起動時] start_dashboard_server(port=8000)   ← バックグラウンドスレッド
       │     └─ SimpleHTTPRequestHandler serving /data/ + HTML
       ├─ scan_and_update()  → ... → export_dashboard_data()  ← 毎回呼ぶ
       └─ monitor_positions() → export_dashboard_data()        ← 毎回呼ぶ

ブラウザ: http://localhost:8000/dashboard
  └─ fetch("/data/dashboard.json?t=...")  ← 10 秒ポーリング
       └─ UI 更新
```

### 3.2 HTTP サーバー設計

- `http.server.SimpleHTTPRequestHandler` を `daemon=True` スレッドで起動
- serve するルートディレクトリ: リポジトリルート（HTML と `data/` を両方配信）
- ポートはデフォルト 8000、`config.json` の `dashboard_port` で変更可能
- `python weatherbet.py run` 起動時に自動で立ち上げ（CLI オプション `--no-dashboard` で無効化可）
- `python weatherbet.py dashboard` は引き続き利用可能（1 回エクスポートしてブラウザオープン）

### 3.3 データフロー（更新タイミング）

| タイミング | 更新頻度 | 内容 |
|---|---|---|
| `scan_and_update()` 完了後 | 1 時間ごと | 全データ再エクスポート |
| `monitor_positions()` 完了後 | 10 分ごと | 残高・ポジション状況を更新 |
| 残高変動時 | 都度 | `balance_history` に追記 |

---

## 4. データ拡張仕様

### 4.1 拡張後の `data/dashboard.json`

```json
{
  "generated_at": "2026-04-14T12:00:00Z",
  "state": {
    "balance": 10234.56,
    "starting_balance": 10000.0,
    "wins": 12,
    "losses": 5
  },
  "summary": {
    "open_count": 3,
    "resolved_count": 17,
    "wins": 12,
    "losses": 5,
    "total_realized_pnl": 234.56,
    "win_rate": 0.706,
    "roi_pct": 2.35
  },
  "open_positions": [
    {
      "city": "chicago",
      "city_name": "Chicago",
      "date": "2026-04-15",
      "bucket_low": 46,
      "bucket_high": 47,
      "entry_price": 0.08,
      "current_price": 0.12,
      "shares": 250,
      "cost": 20.0,
      "unrealized_pnl": 10.0,
      "forecast_source": "blended",
      "blended_temp": 46.7,
      "blended_sigma": 2.1,
      "ev": 0.18,
      "kelly_pct": 0.031,
      "hours_remaining": 14.2,
      "stop_price": 0.064,
      "take_profit_threshold": 0.85
    }
  ],
  "recent_resolved": [
    {
      "city": "chicago",
      "city_name": "Chicago",
      "date": "2026-04-13",
      "pnl": 18.5,
      "result": "win",
      "actual_temp": 46.0,
      "forecast_temp": 46.3
    }
  ],
  "city_stats": {
    "chicago": { "wins": 4, "losses": 1, "pnl": 55.2, "win_rate": 0.8 },
    "nyc":     { "wins": 3, "losses": 2, "pnl": -5.1, "win_rate": 0.6 }
  },
  "balance_history": [
    { "ts": "2026-04-14T06:00:00Z", "balance": 10050.0 },
    { "ts": "2026-04-14T12:00:00Z", "balance": 10234.56 }
  ],
  "daily_pnl": [
    { "date": "2026-04-12", "pnl": 42.0 },
    { "date": "2026-04-13", "pnl": -8.5 },
    { "date": "2026-04-14", "pnl": 24.0 }
  ],
  "log_tail": [
    { "ts": "2026-04-14T12:00:00Z", "level": "INFO",    "msg": "Chicago 46-47°F: entry @ $0.08" },
    { "ts": "2026-04-14T11:00:00Z", "level": "WARNING", "msg": "HRRR API timeout, using ECMWF only" }
  ]
}
```

### 4.2 `data/balance_history.json`（永続化）

```json
[
  { "ts": "2026-04-13T06:00:00Z", "balance": 10000.0 },
  { "ts": "2026-04-14T12:00:00Z", "balance": 10234.56 }
]
```

上限 500 エントリー（FIFO でローテーション）。`export_dashboard_data()` 内で追記。

---

## 5. HTML 修正仕様

### 5.1 修正点一覧

| 箇所 | 現状 | 修正後 |
|---|---|---|
| fetch URL | `simulation.json` | `/data/dashboard.json` |
| 残高参照 | `sim.balance` | `data.state.balance` |
| ポジション参照 | `sim.positions`（オブジェクト） | `data.open_positions`（配列） |
| トレード参照 | `sim.trades`（v1 形式） | `data.recent_resolved` + open_positions 組み合わせ |
| チャートデータ | ポーリングの都度追記（セッション内のみ） | `data.balance_history` から描画（永続） |
| ページタイトル | "Weather Bot — Kelly Simulation" | "WeatherBet — Live Monitor" |
| フッター参照 | `simulation.json` / `weather_bot_v2.py --live` | `data/dashboard.json` / `weatherbet.py` |
| ポーリング間隔 | 10 秒 | 30 秒（サーバー負荷軽減、更新頻度に対して十分） |

### 5.2 追加パネル

| パネル | 内容 | 配置 |
|---|---|---|
| 都市別成績 | 都市×勝率×PnL の横並びカード | stats グリッドの下 |
| 日次 PnL バーチャート | 直近 14 日の日次 PnL | 残高チャートの右横 or 下 |
| ログテール | 直近 20 件の構造化ログ | トレード履歴の下 |

### 5.3 ステータス表示改善

| 状態 | ドット色 | テキスト |
|---|---|---|
| データ取得成功 | 緑（点滅） | `LIVE · 更新: HH:MM:SS` |
| 古いデータ（5 分以上） | 黄 | `STALE · last: HH:MM:SS` |
| 取得失敗（サーバーなし） | 灰 | `OFFLINE — run: python weatherbet.py run` |

---

## 6. 実装フェーズ

### Phase A: コア接続修正（最小実装）

**目標**: ボット稼働中にダッシュボードがリアルタイムで動く状態にする

| タスク | 変更ファイル | 内容 |
|---|---|---|
| A-1 | `src/weatherbet/report.py` | `balance_history` 追記ロジック追加 |
| A-2 | `src/weatherbet/scanner.py` | `scan_and_update()` 末尾で `export_dashboard_data()` 呼び出し |
| A-3 | `src/weatherbet/monitor.py` | `monitor_positions()` 末尾で `export_dashboard_data()` 呼び出し |
| A-4 | `src/weatherbet/cli.py` | `run_loop()` 起動時に HTTP サーバーを daemon スレッドで起動 |
| A-5 | `sim_dashboard_repost.html` | fetch 先・データパースを v2 スキーマに修正 |
| A-6 | `src/weatherbet/config.py` | `dashboard_port` 設定項目追加 |
| A-7 | `config.json` | `"dashboard_port": 8000` 追加 |

**完了基準**: `python weatherbet.py run` だけでブラウザから残高・ポジション・勝敗が見える

---

### Phase B: データ拡張

**目標**: チャートと都市別分析を充実させる

| タスク | 変更ファイル | 内容 |
|---|---|---|
| B-1 | `src/weatherbet/report.py` | `city_stats` 集計を追加 |
| B-2 | `src/weatherbet/report.py` | `daily_pnl` 系列を `data/markets/` から集計 |
| B-3 | `src/weatherbet/report.py` | `log_tail` を `data/logs/weatherbet.log` から末尾 20 行読み込み |
| B-4 | `src/weatherbet/report.py` | open_positions に `ev`, `kelly_pct`, `hours_remaining`, `stop_price` 追加 |
| B-5 | `src/weatherbet/report.py` | `recent_resolved` に `actual_temp`, `forecast_temp` 追加 |

---

### Phase C: UI 強化

**目標**: 視認性を高め、本番稼働中の意思決定支援情報を追加する

| タスク | 変更ファイル | 内容 |
|---|---|---|
| C-1 | `sim_dashboard_repost.html` | 都市別成績カードを追加（stats グリッド下） |
| C-2 | `sim_dashboard_repost.html` | 日次 PnL バーチャートを追加 |
| C-3 | `sim_dashboard_repost.html` | ログテールパネルを追加 |
| C-4 | `sim_dashboard_repost.html` | open_positions に EV・残り時間・ストップ価格を表示 |
| C-5 | `sim_dashboard_repost.html` | ステータスドットの staleness 判定を追加 |
| C-6 | `sim_dashboard_repost.html` | ページタイトル・フッターのテキストを更新 |

---

### Phase D: オプション強化（優先度低）

| タスク | 内容 | 備考 |
|---|---|---|
| D-1 | SSE（Server-Sent Events） | polling → push に変更。Python `http.server` を拡張するか Flask を追加 |
| D-2 | 都市マップビュー | Leaflet.js でポジション保有都市をピン表示 | 外部 JS 1 ライブラリ追加 |
| D-3 | バックテスト結果パネル | `backtest.py` の出力 JSON をダッシュボードに統合 |
| D-4 | モバイル対応 | CSS グリッドをレスポンシブ化 |

---

## 7. 実装対象ファイル一覧

```
変更:
  src/weatherbet/report.py       # Phase A-1, B-1〜B-5
  src/weatherbet/scanner.py      # Phase A-2
  src/weatherbet/monitor.py      # Phase A-3
  src/weatherbet/cli.py          # Phase A-4
  src/weatherbet/config.py       # Phase A-6
  config.json                    # Phase A-7
  sim_dashboard_repost.html      # Phase A-5, C-1〜C-6

新規:
  data/balance_history.json      # 実行時生成（.gitignore 対象）
```

---

## 8. 作業優先順位

Phase A → Phase B → Phase C の順で実施。D は Phase A〜C 安定後に検討。

Phase A は 1 セッションで完了可能（変更箇所が明確・外部依存なし）。  
Phase B は Phase A 完了後に並行可能。  
Phase C は B のデータが揃ってから。

---

## 9. 非機能要件

| 項目 | 方針 |
|---|---|
| 依存ライブラリ | 標準ライブラリのみ（`http.server`, `threading`, `json`）。Flask 不使用 |
| パフォーマンス | `export_dashboard_data()` は 1 回 < 100 ms 想定（小規模 JSON 読み書き）|
| セキュリティ | ローカルホストのみ bind（`0.0.0.0` は使わない。ただし `--bind` オプションで変更可） |
| ポート衝突 | 起動時に `OSError` を捕捉し、警告ログを出してダッシュボードなしで続行 |
| `--no-dashboard` | このフラグがある場合、サーバー起動をスキップ |
