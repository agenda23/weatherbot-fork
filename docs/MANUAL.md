# WeatherBet 操作マニュアル

**対象バージョン**: 2026-04-14 時点のリポジトリ
**前提**: Python 3.9 以上

---

## 目次

1. [セットアップ](#1-セットアップ)
2. [設定ファイル](#2-設定ファイル-configjson)
3. [ベースボット（weatherbet_v1.py）](#3-ベースボット-weatherbet_v1py)
4. [フルボット（weatherbet.py）](#4-フルボット-weatherbetpy)
5. [バックテスト（backtest.py）](#5-バックテスト-backtestpy)
6. [Kellyシミュレーター](#6-kelly-シミュレーター-sim_dashboard_reposthtml)
7. [データ構造](#7-データ構造)
8. [未実装・制限事項](#8-未実装制限事項)

---

## 1. セットアップ

```bash
git clone https://github.com/agenda23/weatherbot-fork
cd weatherbot-fork
pip install requests
```

依存ライブラリは `requests` のみ。標準ライブラリ以外に追加インストールは不要。

### Visual Crossing API キーの取得

キャリブレーション機能（実測気温の取得）に使用する。無料プランで取得可能。

1. [visualcrossing.com](https://www.visualcrossing.com) でアカウント作成
2. APIキーを取得
3. `config.json` の `vc_key` に設定

```json
"vc_key": "YOUR_ACTUAL_KEY_HERE"
```

このキーはマーケット解決後に実測気温を取得し `actual_temp` を記録するために使われる。`actual_temp` がないとキャリブレーション（sigma 自動更新）とバックテストの全件評価が機能しない。

---

## 2. 設定ファイル `config.json`

ボット起動前に `config.json` を確認・調整する。変更はボット次回起動時に反映される（実行中の変更は再起動まで無効）。

```json
{
  "balance": 10000.0,
  "max_bet": 20.0,
  "min_ev": 0.1,
  "max_price": 0.45,
  "min_volume": 500,
  "min_hours": 2.0,
  "max_hours": 72.0,
  "kelly_fraction": 0.25,
  "scan_interval": 3600,
  "calibration_min": 30,
  "vc_key": "YOUR_KEY_HERE",
  "max_slippage": 0.03,
  "sigma_f": 2.0,
  "sigma_c": 1.2,
  "max_open_positions": 10
}
```

### パラメータ一覧

| キー | 型 | 説明 |
|------|----|------|
| `balance` | float | 初期仮想残高（ドル） |
| `max_bet` | float | 1トレードあたりの最大投資額（ドル） |
| `min_ev` | float | エントリー最低期待値（例: 0.10 = EV 10%以上で買う） |
| `max_price` | float | エントリー最高価格（例: 0.45 = 45セント以下で買う） |
| `min_volume` | float | マーケットの最小取引量（ドル） |
| `min_hours` | float | 解決までの最短時間（これより短いと参加しない） |
| `max_hours` | float | 解決までの最長時間（これより長いと参加しない） |
| `kelly_fraction` | float | ケリー乗数（0.25 = フルケリーの25%） |
| `scan_interval` | int | フルスキャン間隔（秒） |
| `calibration_min` | int | キャリブレーション発動に必要な解決済み市場数 |
| `vc_key` | string | Visual Crossing APIキー（実測気温取得・キャリブレーションに必須） |
| `max_slippage` | float | 許容する最大スプレッド（ask - bid） |
| `sigma_f` | float | 華氏都市のデフォルト気温予報誤差（標準偏差、°F） |
| `sigma_c` | float | 摂氏都市のデフォルト気温予報誤差（標準偏差、°C） |
| `max_open_positions` | int | 同時保有ポジションの上限数 |

### 推奨初期設定（保守的）

リスクを抑えたい場合:

```json
"max_bet": 10.0,
"min_ev": 0.15,
"max_price": 0.35,
"kelly_fraction": 0.15,
"max_open_positions": 5
```

---

## 3. ベースボット `weatherbet_v1.py`

米国6都市（NYC / Chicago / Miami / Dallas / Seattle / Atlanta）に特化したシンプル版。
NWS（NOAA）の予報のみを使用。EV / Kelly なし。固定5%サイジング。

### コマンド

```bash
# ペーパーモード（デフォルト）— シグナルを表示するだけ、残高を変えない
python weatherbet_v1.py

# ライブモード — 仮想残高でトレードを実行（simulation.json に保存）
python weatherbet_v1.py --live

# オープンポジションの確認
python weatherbet_v1.py --positions

# シミュレーションリセット（残高を$1,000に戻す）
python weatherbet_v1.py --reset
```

### 状態ファイル

| ファイル | 内容 |
|----------|------|
| `simulation.json` | 仮想残高、オープンポジション、トレード履歴 |

### v1 専用の config キー

v1 は `config.json` の一部キーしか読まない。不足時はデフォルト値を使用。

| キー | v1 デフォルト | 意味 |
|------|--------------|------|
| `entry_threshold` | `0.15` | この価格以下で買う |
| `exit_threshold` | `0.45` | この価格以上で売る |
| `max_trades_per_run` | `5` | 1回の実行あたり最大エントリー数 |
| `min_hours_to_resolution` | `2` | 解決まで最短N時間 |
| `locations` | 6都市 | カンマ区切りの都市スラッグ |

> **注意**: 現在のリポジトリの `config.json` は v2 向けキー構成のため、v1 では上記キーが読まれずデフォルト値が使われる。v1 専用に設定したい場合は `config.json` に上記キーを追記する。

---

## 4. フルボット `weatherbet.py`

20都市（US / EU / Asia / CA / SA / OC）対応の本番ボット。
予報ソース: ECMWF + HRRR（逆分散アンサンブル合成） + METAR（当日観測）。
EV フィルタ / ケリー基準 / ストップロス / トレーリングストップ / 自動解決確認 / キャリブレーション搭載。

### コマンド

```bash
# メインループ起動（Ctrl+C で停止）
python weatherbet.py

# 残高とオープンポジション確認
python weatherbet.py status

# 解決済みマーケットの全履歴レポート
python weatherbet.py report
```

### 動作サイクル

```
起動
  ↓
フルスキャン（SCAN_INTERVAL 秒ごと、デフォルト60分）
  ├─ 全都市 × 4日分の予報取得（ECMWF + HRRR → アンサンブル合成）
  ├─ Polymarket のマーケット価格取得
  ├─ EV / Kelly 計算 → エントリー判断
  ├─ オープンポジションのストップ / テイクプロフィット確認
  └─ 解決済みマーケットの確認 → 残高更新
  ↓
モニタリング（10分ごと）
  └─ オープンポジションの bestBid 取得 → ストップ / テイクプロフィット確認
```

### エントリーロジック

エントリーは以下の**全条件**を満たす場合のみ:

1. 予報気温が Polymarket のいずれかのバケット内に入る
2. `volume >= min_volume`
3. `ask < max_price`
4. `spread <= max_slippage`
5. `EV >= min_ev`
6. `kelly * balance >= $0.50`（最低投資額）
7. オープンポジション数 < `max_open_positions`

### ストップロス / テイクプロフィット

| トリガー | 条件 | 挙動 |
|---------|------|------|
| ストップロス | `current_price <= entry * 0.80` | ポジションをクローズ |
| トレーリングストップ | 含み益 +20% 以上で発動 | ストップを建値に引き上げ |
| テイクプロフィット（48h+） | `current_price >= 0.75` | クローズ |
| テイクプロフィット（24-48h） | `current_price >= 0.85` | クローズ |
| テイクプロフィット（24h未満） | — | 解決まで保持 |
| 予報ドリフト | 予報がバケットから2°以上外れた場合 | クローズ |

### 対応都市と空港ステーション

| 都市 | ステーション | 単位 |
|------|------------|------|
| New York City | KLGA（LaGuardia） | °F |
| Chicago | KORD（O'Hare） | °F |
| Miami | KMIA | °F |
| Dallas | KDAL（Love Field） | °F |
| Seattle | KSEA（Sea-Tac） | °F |
| Atlanta | KATL（Hartsfield） | °F |
| London | EGLC（London City） | °C |
| Paris | LFPG（CDG） | °C |
| Munich | EDDM | °C |
| Ankara | LTAC | °C |
| Seoul | RKSI（仁川） | °C |
| Tokyo | RJTT（羽田） | °C |
| Shanghai | ZSPD（浦東） | °C |
| Singapore | WSSS | °C |
| Lucknow | VILK | °C |
| Tel Aviv | LLBG（ベングリオン） | °C |
| Toronto | CYYZ | °C |
| Sao Paulo | SBGR | °C |
| Buenos Aires | SAEZ | °C |
| Wellington | NZWN | °C |

### キャリブレーション

解決済みマーケットが `calibration_min`（デフォルト30）件に達すると、都市×ソース単位で気温予報誤差（sigma）を自動更新する。更新された sigma は `data/calibration.json` に保存され、以降のエントリー確率計算とアンサンブル合成に使われる。

---

## 5. バックテスト `backtest.py`

`data/markets/` に蓄積した解決済みマーケットデータを使い、設定パラメータを変えた場合の結果を再計算する。

> **前提**: `weatherbet.py` を一定期間動かして `data/markets/` にデータが溜まってから使う。
> 解決済みマーケット（`"status": "resolved"`）が0件の場合、バックテストは実行できない。

### フォワードテストモード（推奨）

```bash
# 全発見市場を actual_temp で評価（ボットがスキップした市場も含む）
python backtest.py --forward

# パラメータスイープをフォワードテストで実施
python backtest.py --forward --sweep min_ev

# パラメータ上書き + フォワードテスト
python backtest.py --forward --param min_ev=0.05 max_price=0.50 --verbose
```

通常の `--forward` なしバックテストとの違い:

| | バックテスト（デフォルト） | フォワードテスト（`--forward`） |
|---|---|---|
| 評価対象 | ボットが実際にエントリーした市場のみ | `actual_temp` が記録された全市場 |
| 勝敗判定 | Polymarket の `resolved_outcome` | `actual_temp` がバケット内に入るか |
| vc_key 要否 | 不要 | **必須** |
| min_ev 緩和の効果 | 既存トレードのみ再評価 | スキップした市場も評価対象に加わる |

フォワードテストを有効に使うには、`vc_key` を設定して `weatherbet.py` を動かし続けること。クローズした市場の `actual_temp` が自動取得されるたびにフォワードテストの母集団が増える。

### データ蓄積の仕組みと制約

`weatherbet.py` がスキャン → ポジション取得 → 翌日解決確認、という流れで `data/markets/*.json` を自動更新する。**特別なデータ収集操作は不要。ボットを動かし続けるだけで蓄積される。**

ただし現在のバックテストは**実際にポジションを取った市場のみ**を対象とする（ポジションなし市場には `resolved_outcome` が記録されない）。これにより:

| 検証できる | できない |
|---|---|
| kelly_fraction を変えたときのPnL変化 | 実際にスキップした市場が勝ちだったか |
| sigma を変えたときのEV・確率変化 | min_ev を緩めたら増えたはずのトレード |

**対処法**: 初期は緩いパラメータ（`min_ev=0.05`, `max_price=0.50`）で動かしてデータを蓄積し、バックテストで最適値を探してから締める。最初から厳しい条件にするとデータが溜まらない。

**フォワードテストとの使い分け**: `--forward` フラグを使うと、ポジションを取っていない市場（スキップした市場）も含めて全件評価できる。スキップした市場が実際に勝ちだったかどうかを確認でき、`min_ev` などのパラメータ最適化に有効。`weatherbet.py` は vc_key がある場合、クローズした全市場の `actual_temp` を自動取得する。

### 基本実行

```bash
# config.json のデフォルト設定でバックテスト
python backtest.py

# パラメータを上書きして実行
python backtest.py --param min_ev=0.15 max_price=0.40 kelly_fraction=0.20

# 個別トレードを全表示
python backtest.py --verbose
```

### パラメータスイープ（感度分析）

```bash
# min_ev を複数の値で比較（デフォルトレンジ: 0.05, 0.08, 0.10, 0.12, 0.15, 0.20）
python backtest.py --sweep min_ev

# 値を指定してスイープ
python backtest.py --sweep min_ev 0.05 0.10 0.15 0.20 0.25

# max_price のスイープ
python backtest.py --sweep max_price

# kelly_fraction のスイープ
python backtest.py --sweep kelly_fraction

# sigma_f のスイープ（予報誤差の感度）
python backtest.py --sweep sigma_f
```

スイープデフォルトレンジ一覧:

| パラメータ | デフォルト値の列 |
|------------|----------------|
| `min_ev` | 0.05, 0.08, 0.10, 0.12, 0.15, 0.20 |
| `max_price` | 0.30, 0.35, 0.40, 0.45, 0.50 |
| `kelly_fraction` | 0.10, 0.15, 0.20, 0.25, 0.30 |
| `sigma_f` | 1.0, 1.5, 2.0, 2.5, 3.0 |
| `sigma_c` | 0.5, 0.8, 1.0, 1.2, 1.5, 2.0 |
| `max_slippage` | 0.02, 0.03, 0.04, 0.05 |
| `min_volume` | 200, 500, 1000, 2000 |

### 都市フィルタ

```bash
# 特定の都市のみでバックテスト
python backtest.py --city chicago nyc

# キャリブレーション済み sigma を使用
python backtest.py --use-calibration

# 組み合わせ
python backtest.py --city chicago --use-calibration --sweep min_ev
```

### 出力例

```
Loaded 120 markets (87 resolved) from data/markets

================================================================
  min_ev=0.1  max_price=0.45  kelly=0.25  σ_F=2.0  σ_C=1.2
================================================================
  Trades:       31 | Wins: 22 | WR: 71%
  PnL:         +$184.30  (ROI: +1.8%)
  Final bal:   $10,184.30
  Expectancy:  +$5.9452 / trade
  Max drawdown: 4.2%
  Sharpe:       1.423

  By city:
    chicago          8/11 (73%)  PnL: +62.40
    nyc              6/8  (75%)  PnL: +45.10
    ...
```

### sigma の意味と調整方針

`sigma_f` / `sigma_c` は予報の不確実性（標準偏差）を表す。値が小さいほど「予報が正確」と仮定し、バケット的中確率が高くなり、より積極的にエントリーする。

- sigma が実際より小さい → 過信、低EV市場にもエントリーして損失増
- sigma が実際より大きい → 保守的、高EV市場でもスキップが増える

`--sweep sigma_f` でデータに合った値を探すことを推奨する。`--use-calibration` で解決済みデータから自動推定された値を使うのが最終的な目標。

---

## 6. Kelly シミュレーター `sim_dashboard_repost.html`

ブラウザで直接開くスタンドアロンの HTML ダッシュボード。Kelly 基準のシミュレーションを視覚的に確認するためのツール。

```bash
# ブラウザで開く（OS によって異なる）
open sim_dashboard_repost.html          # macOS
xdg-open sim_dashboard_repost.html      # Linux
start sim_dashboard_repost.html         # Windows
```

> ⚠️ **未接続**: このダッシュボードは `data/` のボットデータとは**連携していない**。
> スタンドアロンのシミュレーターとして機能するが、実際のトレード履歴は反映されない。
> → [未実装: ダッシュボードへのデータ連携](#ダッシュボードのデータ連携)

---

## 7. データ構造

### `data/` ディレクトリ（weatherbet.py 実行後に生成）

```
data/
├── state.json             # 残高・勝敗カウント
├── calibration.json       # 都市×ソース別の sigma（30件解決後に生成）
└── markets/
    ├── chicago_2026-04-14.json
    ├── nyc_2026-04-15.json
    └── ...
```

### `data/markets/{city}_{date}.json` の構造

```json
{
  "city": "chicago",
  "date": "2026-04-14",
  "unit": "F",
  "status": "resolved",
  "resolved_outcome": "win",
  "actual_temp": 74,
  "pnl": 12.30,
  "position": {
    "market_id": "...",
    "bucket_low": 73, "bucket_high": 74,
    "entry_price": 0.09,
    "shares": 22.2,
    "cost": 2.0,
    "p": 0.34,
    "ev": 0.72,
    "forecast_temp": 74,
    "forecast_src": "blend",
    "sigma": 1.732,
    "status": "closed",
    "close_reason": "resolved"
  },
  "forecast_snapshots": [
    {
      "ts": "2026-04-13T10:00:00Z",
      "hours_left": 24.0,
      "ecmwf": 73,
      "hrrr": 75,
      "metar": null,
      "blended": 74,
      "blended_sigma": 1.732,
      "best": 74,
      "best_source": "blend"
    }
  ],
  "all_outcomes": [
    {"question": "...", "market_id": "...", "range": [73, 74], "bid": 0.08, "ask": 0.09, "spread": 0.01, "volume": 1200}
  ]
}
```

### `simulation.json`（weatherbet_v1.py 専用、ルートに生成）

```json
{
  "balance": 950.00,
  "starting_balance": 1000.0,
  "positions": {},
  "trades": [],
  "total_trades": 5,
  "wins": 3,
  "losses": 2,
  "peak_balance": 1020.00
}
```

---

## 8. 未実装・制限事項

### 実際のオンチェーン取引

> 🟡 **一部実装済み**

現在は **Polymarket Gamma API の読み取り**に加え、CLOB クライアント（book/order/status）、ウォレット資格情報管理、`clob-order`（dry-run + live gated）、`clob-order-status`（ポーリング）まで実装済み。  
ただし本番互換の EIP-712 署名と規制面の確認は未完了。

残タスク:
- EIP-712 署名への移行（現状は `stub` / `eth_sign`）
- 法規制の確認（利用者の居住地域による制約）

### ダッシュボードのデータ連携

> 🟡 **基盤実装済み**

`python weatherbet.py dashboard` で `data/dashboard.json` の生成と HTML オープンは実装済み。  
HTML 側の fetch 連携は追加実装余地がある。

利用コマンド:
```bash
python weatherbet.py dashboard    # data/dashboard.json を生成してブラウザで開く
```

### 通知・アラート

> 🟡 **一部実装済み**

Discord Webhook は実装済み（ポジション開始、ストップロス発動、API障害の連続失敗）。  
メール / OS 通知、日次サマリー通知は未実装。

### モジュール分割

> 🚧 **未実装**

現在 `weatherbet.py` は設定・API・数学・状態管理・CLI が1ファイルに混在（約1050行）。
`src/weatherbet/` パッケージへの分割は IMPLEMENTATION_PLAN.md Phase 1 に記載。

### キャリブレーションに必要なデータ量

`calibration_min`（デフォルト30）件の解決済みマーケットが溜まるまでキャリブレーションは発動しない。起動直後はデフォルト sigma（`sigma_f=2.0` / `sigma_c=1.2`）が使われる。

`backtest.py --sweep sigma_f` でデータに合った初期値を探し、`config.json` に設定することを推奨する。

### `weatherbet.py` というファイル名

この表記ズレは修正済み。現在のエントリポイント表記は `python weatherbet.py` に統一されている。

---

## 付録: よくある操作フロー

### 初めて動かす

```bash
# 1. 設定を確認・調整
nano config.json

# 2. v1 でシグナルを確認（残高変更なし）
python weatherbet_v1.py

# 3. v2 をペーパートレードで起動（Ctrl+C で停止）
python weatherbet.py

# 4. 数日後、状況確認
python weatherbet.py status
python weatherbet.py report
```

### パラメータを調整する

```bash
# 1. データが溜まってからバックテスト
python backtest.py --verbose

# 2. min_ev の感度を確認
python backtest.py --sweep min_ev

# 3. 有望な設定でもう一度確認
python backtest.py --param min_ev=0.12 max_price=0.40 --verbose

# 4. config.json を更新してボット再起動
```

### キャリブレーションを活用する

```bash
# 解決済みデータで calibration.json が生成されているか確認
ls data/calibration.json

# キャリブレーション済み sigma でバックテスト
python backtest.py --use-calibration

# calibration.json の sigma と config.json の sigma_f/sigma_c を比較し、
# 大きくずれている場合は config.json を手動更新する
```
