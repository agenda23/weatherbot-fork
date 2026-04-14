# WeatherBet — Polymarket 気温市場トレーディングボット

Polymarket の気温予測市場を対象に、複数の気象予報ソースとケリー基準を活用してミスプライスを検出し、自動でペーパートレード（シミュレーション取引）を管理する Python 製ボットです。

外部 SDK 不要。`requests` と標準ライブラリのみで動作します。

---

## 概要

Polymarket では「シカゴの最高気温が 46〜47°F になるか？」のようなマーケットが日々生成されています。予報では 78% の確率なのに、市場価格が 8 セントで取引されていることがあります。

このボットは:

1. ECMWF・HRRR を Open-Meteo 経由で取得（無料・キー不要）
2. 空港 METAR 観測データでリアルタイム補正
3. Polymarket の対応バケットを自動検出
4. 期待値（EV）を算出し、正のエッジがある場合のみエントリー
5. 分数ケリー基準でポジションサイズを決定
6. 10 分間隔でストップ監視、1 時間ごとにフルスキャン
7. マーケット解決を Polymarket API で自動確認

---

## バージョン

### `weatherbet_v1.py` — ベースボット

米国 6 都市対象。NWS 予報と空港座標で基本的なシグナル検出を行うシンプル版。ロジックの理解に適しています。

### `weatherbet.py` — フル機能ボット（現行）

v1 のすべてに加え、以下を搭載:

| 機能 | 内容 |
|------|------|
| 対象都市 | 4 大陸 20 都市（US / EU / Asia / SA / Oceania） |
| 予報ソース | ECMWF（全都市）、HRRR/GFS（US）、METAR（当日観測） |
| 確率推定 | 正規分布 CDF によるバケット確率、逆分散アンサンブル合成 |
| ポジション管理 | EV・ケリー基準・スリッページフィルター |
| ストップロス | sigma 連動の動的ストップ + トレーリングストップ |
| テイクプロフィット | 残り時間に応じた線形補間の連続閾値 |
| リスク管理 | 日次損失制限、同一都市・日付の相関ガード |
| キャリブレーション | 都市×ソース別に RMSE で sigma を自動更新 |
| 通知 | Discord Webhook（ポジション開始 / ストップ / API 障害） |
| ログ | 構造化ログ（コンソール + `data/logs/weatherbet.log`、JSON Lines） |
| ダッシュボード | `data/dashboard.json` 生成 + HTML オープン |
| CLOB 連携 | 板取得・注文送信（dry-run / live）・注文ステータス確認 |
| ウォレット | 秘密鍵管理・署名検証（stub / eth_sign） |
| テスト | pytest 44 テスト（純粋関数・リスク・通知・CLOB・署名） |

---

## 空港座標の重要性

Polymarket の気温マーケットは特定の空港観測局で解決されます。ニューヨークは LaGuardia（KLGA）、ダラスは Love Field（KDAL）です。市街地中心と空港の差は 3〜8°F に達するため、1〜2°F 幅のバケットでは致命的な差になります。

| 都市 | ICAO | 空港名 |
|------|------|--------|
| NYC | KLGA | ラガーディア |
| Chicago | KORD | オヘア |
| Miami | KMIA | マイアミ国際 |
| Dallas | KDAL | ラブフィールド |
| Seattle | KSEA | シアトル・タコマ |
| Atlanta | KATL | ハーツフィールド |
| London | EGLC | ロンドン・シティ |
| Tokyo | RJTT | 羽田 |
| Seoul | RKSI | 仁川 |
| Paris | LFPG | シャルル・ド・ゴール |
| その他 10 都市 | ... | ... |

---

## インストール

```bash
git clone https://github.com/agenda23/weatherbot-fork
cd weatherbot-fork
pip install -r requirements.txt

# 任意: eth_sign 署名検証を使う場合（requirements.txt 内のコメントを外して再実行）
# pip install eth-account

# 任意: テスト実行
pip install pytest
```

---

## 設定

プロジェクトルートに `config.json` を作成します:

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
  "vc_key": "YOUR_VISUAL_CROSSING_KEY",
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
  "live_trading_enabled": false
}
```

### 主要設定項目

| カテゴリ | キー | 説明 |
|----------|------|------|
| 取引 | `balance` / `max_bet` / `min_ev` | 残高・最大ベット額・最小期待値 |
| フィルター | `max_price` / `min_volume` / `max_slippage` | 価格上限・出来高下限・スプレッド上限 |
| 時間 | `min_hours` / `max_hours` / `scan_interval` | 解決までの時間制限・スキャン間隔 |
| ケリー | `kelly_fraction` | 分数ケリー乗数（0.25 = 1/4 ケリー） |
| キャリブレーション | `calibration_min` / `sigma_f` / `sigma_c` | 最小サンプル数・初期 sigma |
| リスク | `daily_loss_limit_pct` / `max_open_positions` | 日次損失制限・同時ポジション上限 |
| 通知 | `discord_webhook_url` / `api_failure_alert_threshold` | Discord 通知・API 障害しきい値 |
| CLOB | `clob_base_url` / `clob_api_key` / `clob_signing_mode` | CLOB 接続・署名方式 |
| ウォレット | `polygon_wallet_address` / `polygon_private_key` | Polygon ウォレット（環境変数 `POLYGON_PRIVATE_KEY` 優先） |
| 実取引 | `live_trading_enabled` | `true` で実注文を許可（デフォルト: 無効） |
| API | `vc_key` | Visual Crossing キー（解決後の実測気温取得に必要） |

Visual Crossing の無料 API キーは [visualcrossing.com](https://www.visualcrossing.com/) で取得できます。

---

## 使い方

### 基本操作

```bash
python weatherbet.py              # ボット起動（毎時スキャン + 10分監視）
python weatherbet.py status       # 残高・オープンポジション表示
python weatherbet.py report       # 全解決マーケットの詳細レポート
python weatherbet.py dashboard    # data/dashboard.json 生成 + HTML オープン
```

### CLOB・ウォレット操作（実取引向け）

```bash
python weatherbet.py clob-book <token_id>                                    # 板情報取得
python weatherbet.py clob-order <token_id> <buy|sell> <price> <size>         # 注文 dry-run
python weatherbet.py clob-order <token_id> <buy|sell> <price> <size> --live  # 実注文送信
python weatherbet.py clob-order-status <order_id>                            # 注文ステータス
python weatherbet.py clob-order-status <order_id> --wait --timeout=90        # 約定までポーリング
python weatherbet.py wallet-status                                           # ウォレット設定確認
python weatherbet.py clob-sign-check <token_id> <buy|sell> <price> <size>    # 署名の自己検証
```

---

## データ保存

ボット実行後、以下が `data/` に生成されます（`.gitignore` 対象済み）:

| パス | 内容 |
|------|------|
| `data/state.json` | 残高・勝敗カウンタ |
| `data/markets/*.json` | 市場ごとの予報スナップショット・価格履歴・ポジション・解決結果 |
| `data/calibration.json` | 都市×ソース別の sigma |
| `data/logs/weatherbet.log` | 構造化ログ（JSON Lines） |
| `data/dashboard.json` | ダッシュボード用エクスポート |

蓄積データはキャリブレーションに使用され、予報精度の高いソースが自動的に重み付けされます。

---

## 利用 API

| API | 認証 | 用途 |
|-----|------|------|
| Open-Meteo | なし | ECMWF / HRRR 予報 |
| Aviation Weather | なし | METAR リアルタイム観測 |
| Polymarket Gamma | なし | マーケットデータ読み取り |
| Polymarket CLOB | API キー（任意） | 板取得・注文送信・ステータス確認 |
| Visual Crossing | 無料キー | 解決後の実測気温取得 |

---

## テスト

```bash
pytest tests/ -v
```

44 テストが以下をカバー:

- 温度範囲パース（`parse_temp_range`）: 全パターン
- バケット確率（`bucket_prob`）: エッジケース・sigma=0
- 数学関数（`calc_ev` / `calc_kelly` / `bet_size`）: 値域検証
- バケット判定（`in_bucket`）: 境界値・単一度数
- アンサンブル合成（`blend_forecast`）: 逆分散重み付け
- キャリブレーション（`run_calibration`）: RMSE 計算
- リスク管理: 日次損失集計
- 通知: Discord Webhook 送受信
- 構造化ログ: JSON Lines 出力
- API 障害追跡: 連続失敗カウント・リセット
- 相関ガード: 重複エントリー検出
- 動的ストップ: sigma スケーリング
- テイクプロフィット: 連続閾値
- ダッシュボード: JSON エクスポート
- CLOB クライアント: ヘッダー・板取得
- ウォレット: 秘密鍵検証・マスク表示
- 署名: stub / eth_sign / 無効鍵

---

## プロジェクト構成

```
weatherbot-fork/
├── weatherbet_v1.py                    # v1 ベースボット
├── weatherbet.py                    # v2 フル機能ボット（約 1,650 行）
├── backtest.py                  # バックテスト・パラメータスイープ
├── config.json                  # 設定ファイル
├── sim_dashboard_repost.html    # Kelly シミュレーター HTML
├── tests/
│   └── test_core_functions.py   # pytest テスト（44 テスト）
├── docs/
│   ├── TODO.md                  # 開発ロードマップ
│   ├── IMPLEMENTATION_PLAN.md   # 実装計画・フェーズ管理
│   ├── MANUAL.md                # 運用マニュアル
│   ├── PROJECT_REPORT.md        # プロジェクト調査レポート
│   └── logic_description.md     # ロジック詳細説明
└── data/                        # 実行時生成（.gitignore 対象）
    ├── state.json
    ├── markets/
    ├── calibration.json
    ├── logs/
    └── dashboard.json
```

---

## 開発状況

| フェーズ | 内容 | 状態 |
|----------|------|------|
| Phase 0 | `.gitignore`・ファイル名修正 | ✅ 完了 |
| Phase 1 | テスト導入・構造化ログ | ✅ 完了 |
| Phase 2 | キャリブレーション RMSE 化 | ✅ 完了 |
| Phase 3 | 日次損失制限・相関ガード・動的ストップ・連続テイクプロフィット | ✅ 完了 |
| Phase 5 | ダッシュボード連携 | ✅ 完了 |
| Phase 6 | Discord 通知・API 障害アラート | ✅ 完了（メール / OS 通知は未） |
| Phase 7 | CLOB クライアント・ウォレット・署名・約定確認 | ✅ 基盤完了（EIP-712 / 法規制は未） |
| - | モジュール分割 | 🔜 今後対応 |

詳細は [docs/TODO.md](docs/TODO.md) を参照してください。

---

## 免責事項

本ソフトウェアは研究・シミュレーション目的で提供されています。金融アドバイスではありません。予測市場には実質的なリスクが伴います。実資金を投入する前に、十分なシミュレーションを実施してください。

---

## ライセンス

MIT License（Copyright 2026 agenda23）

本リポジトリは [alteregoeth-ai/weatherbot](https://github.com/alteregoeth-ai/weatherbot) をフォークした開発用リポジトリです。
