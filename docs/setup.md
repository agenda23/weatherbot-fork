# WeatherBet 初期セットアップガイド

**対象**: `weatherbet.py`（フル機能ボット）  
**前提環境**: Python 3.9 以上、インターネット接続

---

## 目次

1. [Python 環境の準備](#1-python-環境の準備)
2. [リポジトリの取得](#2-リポジトリの取得)
3. [依存ライブラリのインストール](#3-依存ライブラリのインストール)
4. [API キーの取得](#4-api-キーの取得)
5. [config.json の設定](#5-configjson-の設定)
6. [動作確認](#6-動作確認)
7. [初回起動](#7-初回起動)
8. [トラブルシューティング](#8-トラブルシューティング)

---

## 1. Python 環境の準備

### バージョン確認

```bash
python --version
# Python 3.9.x 以上であることを確認
```

Python 3.9 未満の場合は [python.org](https://www.python.org/downloads/) から最新版を取得する。

### 仮想環境（推奨）

```bash
# 仮想環境を作成（venv）
python -m venv .venv

# 有効化（Mac / Linux）
source .venv/bin/activate

# 有効化（Windows）
.venv\Scripts\activate
```

> プロンプトの先頭に `(.venv)` が表示されれば有効化されている。

---

## 2. リポジトリの取得

```bash
git clone https://github.com/agenda23/weatherbot-fork
cd weatherbot-fork
```

ディレクトリ構成を確認する:

```
weatherbot-fork/
├── weatherbet.py           ← エントリーポイント
├── config.example.json     ← 設定テンプレート（git 管理・コミット対象）
├── config.json             ← 実際の設定（git 管理外・clone 直後は存在しない）
├── requirements.txt
├── sim_dashboard_repost.html
├── src/weatherbet/         ← 実装パッケージ
└── tests/
```

`config.json` は git 管理対象外のため、clone した直後には存在しない。次のコマンドでテンプレートからコピーする:

```bash
cp config.example.json config.json
```

---

## 3. 依存ライブラリのインストール

```bash
pip install -r requirements.txt
```

インストールされる主なライブラリ:

| ライブラリ | 用途 |
|------------|------|
| `requests` | HTTP API 呼び出し全般（必須） |
| `pytest` | テスト実行（開発用、任意） |

### オプション: 実取引署名ライブラリ

実際に Polymarket へ注文を送信する場合（デフォルトは無効）に必要。

```bash
# requirements.txt 内のコメントを外してから
pip install eth-account>=0.8
```

> ペーパートレード（シミュレーション）のみの場合はインストール不要。

---

## 4. API キーの取得

このボットが使用する API は 5 種類。そのうち**認証が必要なのは Visual Crossing のみ**（無料）。残りはすべて認証なしで利用できる。

### 4-1. Visual Crossing（必須度：高）

**用途**: マーケット解決後に実測気温を取得する。`actual_temp` の記録とキャリブレーション機能に必要。

**無料プランで可能なこと**:
- 1日 1,000 件のリクエスト（ボットの利用量は通常数件/日）
- 過去・現在の気温データ取得

**取得手順**:

1. [https://www.visualcrossing.com/](https://www.visualcrossing.com/) にアクセス
2. 右上の **「Sign Up」** をクリック
3. メールアドレス・パスワードを入力して無料アカウントを作成
4. メール認証を完了
5. ダッシュボード右上のアカウント名 → **「Account」** をクリック
6. **「API Key」** セクションに表示されているキー（`xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` 形式）をコピー

```
例: ABC123DEF456GHI789JKL012MNO345PQ
```

> キーは `config.json` の `vc_key` フィールドに設定する（次節参照）。

**vc_key なしの場合の挙動**:
- ボット自体は起動する
- 解決済みマーケットに `actual_temp` が記録されない
- `calibration_min` 件に達してもキャリブレーションが実行されない
- `backtest.py --forward` が使えない

---

### 4-2. Polymarket Gamma API（認証不要）

**用途**: マーケットデータの読み取り（価格・出来高・解決状態）。

認証なしで利用可能。設定不要。

ボットが自動で以下のエンドポイントを呼び出す:

```
https://gamma-api.polymarket.com/events?slug=highest-temperature-in-{city}-on-{month}-{day}-{year}
https://gamma-api.polymarket.com/markets/{market_id}
```

---

### 4-3. Open-Meteo（認証不要）

**用途**: ECMWF・HRRR 気象予報の取得。全 20 都市に対応。

認証なしで利用可能。設定不要。

---

### 4-4. Aviation Weather / METAR（認証不要）

**用途**: 空港観測局のリアルタイム気温（当日の METAR データ）。

認証なしで利用可能。設定不要。

---

### 4-5. Polymarket CLOB API（任意・実取引のみ）

**用途**: 実際の注文送信と板情報の取得。ペーパートレードには不要。

デフォルトでは `live_trading_enabled: false` となっており、実注文は送信されない。

実取引を有効にする場合のみ設定する:

1. [https://polymarket.com/](https://polymarket.com/) でアカウント作成
2. Polygon ウォレット（MetaMask 等）を接続
3. CLOB API キーを Polymarket の設定画面から取得
4. Polygon ウォレットの秘密鍵を用意

> **警告**: 実取引は居住地域によって利用不可の場合がある。法規制を確認してから有効にすること。秘密鍵は絶対に他人に共有しない。

---

## 5. config.json の設定

`config.json` は API キー・秘密鍵などの機密情報を含むため **git 管理対象外**（`.gitignore` に記載済み）。

### 5-1. ファイルの作成

リポジトリに含まれる `config.example.json` をコピーして `config.json` を作成する:

```bash
cp config.example.json config.json
```

```
weatherbot-fork/
├── config.example.json   ← テンプレート（git 管理・コミット対象）
└── config.json           ← 実際の設定（git 管理外・絶対にコミットしない）
```

> **注意**: `config.json` をコミットすると API キーや秘密鍵がリポジトリに残る。`.gitignore` によって保護されているが、`git add -f` などの強制追加は行わないこと。

### 5-2. 全設定項目リファレンス

`config.example.json` の内容（コピー後に実際の値に書き換える）:

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
  "max_slippage": 0.03,
  "scan_interval": 3600,
  "calibration_min": 30,
  "vc_key": "YOUR_VISUAL_CROSSING_API_KEY",
  "sigma_f": 2.0,
  "sigma_c": 1.2,
  "max_open_positions": 10,
  "daily_loss_limit_pct": 0.1,
  "api_failure_alert_threshold": 3,
  "discord_webhook_url": "",
  "clob_base_url": "https://clob.polymarket.com",
  "clob_api_key": "",
  "polygon_wallet_address": "",
  "polygon_private_key": "",
  "clob_signing_mode": "stub",
  "live_trading_enabled": false,
  "dashboard_port": 8000
}
```

---

#### カテゴリ別説明

**資金管理**

| キー | 型 | デフォルト | 説明 |
|------|----|-----------|------|
| `balance` | float | 10000.0 | 仮想残高の初期値（ドル）。実際の資金量に合わせて設定する |
| `max_bet` | float | 20.0 | 1 トレードあたりの最大投資額（ドル）。Kelly が大きくてもこの値を超えない |
| `kelly_fraction` | float | 0.25 | ケリー乗数。0.25 = フルケリーの 25%（保守的）、0.5 = ハーフケリー |
| `daily_loss_limit_pct` | float | 0.1 | 1 日の損失がこの割合を超えたらその日のスキャンを停止する（0.1 = 残高の 10%）|

**エントリーフィルター**

| キー | 型 | デフォルト | 説明 |
|------|----|-----------|------|
| `min_ev` | float | 0.1 | 最低期待値。0.10 = EV 10% 以上のマーケットのみ参加 |
| `max_price` | float | 0.45 | エントリー価格の上限（ドル）。0.45 = 45 セント以下で買う |
| `min_volume` | float | 500 | マーケットの最低出来高（ドル）。流動性の低いマーケットを除外 |
| `max_slippage` | float | 0.03 | 許容する最大スプレッド（ask - bid）。0.03 = 3 セント以内 |
| `max_open_positions` | int | 10 | 同時保有ポジションの上限数 |

**時間フィルター**

| キー | 型 | デフォルト | 説明 |
|------|----|-----------|------|
| `min_hours` | float | 2.0 | 解決まで最低この時間がないと参加しない（直前すぎるマーケットを除外）|
| `max_hours` | float | 72.0 | 解決までこれより長いと参加しない（3 日以上先は不確実性が高い）|
| `scan_interval` | int | 3600 | フルスキャンの間隔（秒）。3600 = 1 時間ごと |

**予報モデル**

| キー | 型 | デフォルト | 説明 |
|------|----|-----------|------|
| `sigma_f` | float | 2.0 | 華氏都市（US）の予報誤差の初期値（°F）。キャリブレーション後に自動更新される |
| `sigma_c` | float | 1.2 | 摂氏都市（EU / Asia 等）の予報誤差の初期値（°C）。キャリブレーション後に自動更新される |
| `calibration_min` | int | 30 | キャリブレーション発動に必要な解決済みマーケット数 |
| `vc_key` | string | `""` | Visual Crossing の API キー（実測気温取得・キャリブレーションに必須）|

**通知**

| キー | 型 | デフォルト | 説明 |
|------|----|-----------|------|
| `discord_webhook_url` | string | `""` | Discord の Webhook URL。空欄なら通知しない |
| `api_failure_alert_threshold` | int | 3 | API が何回連続失敗したら Discord に通知するか |

**CLOB・実取引（ペーパートレードでは不要）**

| キー | 型 | デフォルト | 説明 |
|------|----|-----------|------|
| `clob_base_url` | string | `https://clob.polymarket.com` | CLOB API のベース URL |
| `clob_api_key` | string | `""` | Polymarket の CLOB API キー |
| `polygon_wallet_address` | string | `""` | Polygon ウォレットのアドレス |
| `polygon_private_key` | string | `""` | Polygon ウォレットの秘密鍵（環境変数 `POLYGON_PRIVATE_KEY` でも設定可）|
| `clob_signing_mode` | string | `"stub"` | 署名方式。`"stub"`（テスト用）/ `"eth_sign"`（本番用）|
| `live_trading_enabled` | bool | `false` | `true` にすると実注文を送信する。デフォルトは無効 |

**ダッシュボード**

| キー | 型 | デフォルト | 説明 |
|------|----|-----------|------|
| `dashboard_port` | int | 8000 | HTTP サーバーのポート番号 |

---

### 5-3. 初回セットアップ用の最小設定

まず以下の 2 点だけ編集すれば動作する:

```json
{
  "balance": 1000.0,
  "vc_key": "取得した Visual Crossing キーをここに貼る"
}
```

残りはデフォルト値で問題ない。

### 5-4. 少額から始める場合の推奨設定

$50〜$500 程度の少額でシミュレーションを始める場合:

```json
{
  "balance": 100.0,
  "max_bet": 5.0,
  "min_ev": 0.12,
  "max_price": 0.35,
  "kelly_fraction": 0.20,
  "max_open_positions": 3,
  "daily_loss_limit_pct": 0.08,
  "vc_key": "YOUR_KEY_HERE"
}
```

### 5-5. Discord 通知を有効にする場合

1. Discord でサーバーを作成（または既存サーバーを使用）
2. 通知を送りたいチャンネルの設定 → **「連携サービス」** → **「ウェブフック」** → **「新しいウェブフック」**
3. 表示された Webhook URL をコピー
4. `config.json` に設定:

```json
"discord_webhook_url": "https://discord.com/api/webhooks/xxxxxxxxxx/xxxxxxxxxxxxxxxx"
```

通知が届くタイミング:
- ポジション開始時
- ストップロス発動時
- API が連続 `api_failure_alert_threshold` 回失敗したとき

---

### 5-6. 秘密鍵の安全な管理（実取引時のみ）

`polygon_private_key` は `config.json` に直接書く代わりに、環境変数で渡すことを推奨する:

```bash
# Mac / Linux
export POLYGON_PRIVATE_KEY="0xYOUR_PRIVATE_KEY"

# Windows（PowerShell）
$env:POLYGON_PRIVATE_KEY = "0xYOUR_PRIVATE_KEY"
```

環境変数が設定されている場合は `config.json` の値より優先される。`config.json` は `.gitignore` によって git 管理対象外となっているが、`git add -f` で強制追加した場合は秘密鍵がリポジトリに残るため注意すること。

---

## 6. 動作確認

### 6-1. テスト実行

```bash
pip install pytest
pytest tests/ -v
```

44 テストすべてが `PASSED` になれば環境は正常。

```
tests/test_core_functions.py::test_parse_temp_range_standard PASSED
tests/test_core_functions.py::test_bucket_prob_normal PASSED
...
44 passed in X.XXs
```

### 6-2. ステータス確認（データなし状態）

```bash
python weatherbet.py status
```

初回は `data/` が存在しないため、デフォルト状態が表示される:

```
Balance: $10,000.00
Open positions: 0
Total trades: 0  Wins: 0  Losses: 0
```

### 6-3. API 疎通確認

ボット起動前に手動で API を叩いて疎通を確認できる（任意）:

```bash
# Open-Meteo（認証なし）
curl "https://api.open-meteo.com/v1/forecast?latitude=41.97&longitude=-87.91&hourly=temperature_2m&forecast_days=1&models=ecmwf_ifs025"

# Polymarket Gamma（認証なし）
curl "https://gamma-api.polymarket.com/events?slug=highest-temperature-in-chicago-on-april-16-2026"

# Aviation Weather METAR（認証なし）
curl "https://aviationweather.gov/api/data/metar?ids=KORD&format=json"
```

それぞれ JSON レスポンスが返ってくれば正常。

---

## 7. 初回起動

### 7-1. ボット起動

```bash
python weatherbet.py
```

起動直後のコンソール出力:

```
=======================================================
  WEATHERBET — STARTING
=======================================================
  Cities:     20
  Balance:    $1,000 | Max bet: $5.0
  Scan:       60 min | Monitor: 10 min
  Sources:    ECMWF + HRRR(US) + METAR(D+0)
  Dashboard:  http://localhost:8000/sim_dashboard_repost.html
  Ctrl+C to stop

[2026-04-15 12:00:00] FULL SCAN started
[2026-04-15 12:00:01] Fetching forecasts for 20 cities...
...
```

### 7-2. ダッシュボードを開く

ブラウザで以下の URL を開く:

```
http://localhost:8000/sim_dashboard_repost.html
```

30 秒ごとに自動更新される。ボットが稼働中であれば緑のインジケーターが表示される。

### 7-3. 状態確認コマンド

ボット動作中に別のターミナルから実行できる:

```bash
# 残高とオープンポジションを確認
python weatherbet.py status

# 解決済みマーケットの詳細レポート
python weatherbet.py report

# dashboard.json を手動生成してブラウザで開く
python weatherbet.py dashboard
```

### 7-4. 停止

`Ctrl+C` でボットを停止する。状態は `data/state.json` に保存されているため、再起動時に引き継がれる。

---

## 8. トラブルシューティング

### `config.json` が見つからない

```
FileNotFoundError: config.json
```

`config.json` は git 管理対象外のため、clone 直後には存在しない。テンプレートからコピーして作成する:

```bash
cp config.example.json config.json
```

コピー後、`vc_key` に取得した Visual Crossing API キーを設定してから再起動する。

---

### `vc_key` を設定したのに実測気温が取得されない

Visual Crossing は解決後のデータ取得に使われる。マーケットがまだ解決されていない場合は `actual_temp` は記録されない。解決（翌日以降）を待つ。

---

### ポート 8000 が使用中でダッシュボードが起動しない

```
Dashboard server could not start (port 8000 busy?): ...
```

`config.json` でポートを変更する:

```json
"dashboard_port": 8001
```

---

### `requests.exceptions.ConnectionError` が頻発する

外部 API への接続に失敗している。原因の切り分け:

1. インターネット接続を確認
2. [6-3 節](#6-3-api-疎通確認) の curl コマンドで各 API を個別に確認
3. ファイアウォール・プロキシ設定を確認

---

### `ModuleNotFoundError: No module named 'weatherbet'`

`src/` パッケージが Python パスに含まれていない。`weatherbet.py`（ルート）経由で起動しているか確認:

```bash
# 正しい起動方法（ルートから）
python weatherbet.py

# 誤り（src/ の中から起動しない）
cd src && python -m weatherbet.cli
```

---

### テストが失敗する

```bash
pytest tests/ -v 2>&1 | head -50
```

エラーメッセージを確認し、`config.json` が正しく配置されているか確認する。テストは `config.json` の存在に依存している。

---

## 付録: ファイル管理の全体像

### git 管理の対象・対象外

| ファイル | git 管理 | 備考 |
|----------|----------|------|
| `config.example.json` | ✅ 対象 | テンプレート。プレースホルダー値のみ |
| `config.json` | ❌ 対象外 | 各自が `cp config.example.json config.json` で作成 |
| `data/` | ❌ 対象外 | 実行時生成（`.gitignore` 記載済み） |
| `simulation.json` | ❌ 対象外 | 旧 v1 の成果物（`.gitignore` 記載済み） |

### 起動時に生成されるファイル

初回起動後、以下のファイルが `data/` に自動生成される（`.gitignore` 対象済み）:

```
data/
├── state.json              # 残高・勝敗カウント
├── calibration.json        # 都市×ソース別の予報誤差 sigma（30件解決後に生成）
├── dashboard.json          # ダッシュボード用 JSON（毎時スキャン・10分監視ごとに更新）
├── balance_history.json    # 残高の時系列データ（最大500件、セッションをまたいで継続）
├── markets/
│   ├── chicago_2026-04-15.json
│   ├── nyc_2026-04-15.json
│   └── ...                 # 都市×日付ごとの予報・価格・ポジション・解決結果
└── logs/
    └── weatherbet.log      # 構造化ログ（JSON Lines 形式）
```

これらのファイルを削除するとボットの状態がリセットされる。`balance_history.json` と `calibration.json` を削除すると残高履歴とキャリブレーションデータが失われる。
