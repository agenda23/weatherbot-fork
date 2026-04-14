# WeatherBet / weatherbot-fork — プロジェクト調査レポート

**調査日**: 2026年4月14日（初版）／**最終更新**: 2026年4月15日（実装・ドキュメント同期）  
**対象**: `weatherbot-fork` リポジトリの構成・目的・実行方法・ドキュメントと実装の差分

---

## 1. 概要

本リポジトリは **Polymarket の気温系予測市場** を対象に、複数ソースの気象予報と市場価格を突き合わせ、**期待値（EV）・ケリー基準・スリッページ制限** などでエントリーを判断する **Python 製のトレーディング／シミュレーションボット** です。外部 SDK に依存せず `requests` と標準ライブラリで HTTP API を直接呼び出します。

ライセンスは **MIT**（Copyright 2026 agenda23）。フォーク元は [alteregoeth-ai/weatherbot](https://github.com/alteregoeth-ai/weatherbot)。

---

## 2. リポジトリ構成

| パス | 役割 |
|------|------|
| `README.md` | プロジェクト説明・インストール・設定例・利用 API 一覧 |
| `config.json` | 実行時パラメータ（残高上限、EV 閾値、スキャン間隔など） |
| `bot_v1.py` | **v1**: 米国 6 都市、NWS 予報中心のシンプル版 |
| `bot_v2.py` | **v2（フル機能）**: 20 都市、ECMWF / HRRR / METAR、ケリー・キャリブレーション、CLOB/ウォレット周辺 CLI 等 |
| `sim_dashboard_repost.html` | Chart.js 利用の **Kelly シミュレーション用ダッシュボード**（単体 HTML） |
| `LICENSE` | MIT |
| `.gitignore` | Python 一般的除外 + `docs/_build/`（Sphinx 想定）など |

**依存関係ファイル**（`requirements.txt` / `pyproject.toml` 等）はリポジトリに含まれていません。README では `pip install requests` を基本とし、任意で `eth-account`（署名検証用）を記載。開発用に `pytest` は `.deps/` へローカル導入する運用例あり。

---

## 3. バージョン別の違い

### 3.1 `bot_v1.py`（ベース）

- **対象都市**: 米国 6（NYC, Chicago, Miami, Dallas, Seattle, Atlanta）。空港近傍座標で Polymarket の実測地点に寄せている。
- **予報**: NOAA **NWS** の gridpoint hourly エンドポイント（都市ごとに固定 URL）。
- **CLI**: `argparse` ベース。`--live`（仮想残高でのシミュ取引）、`--reset`、`--positions` など。
- **状態ファイル**: ルートの `simulation.json`（仮想残高・ポジション）。
- **`config.json` の想定キー例**: `entry_threshold`, `exit_threshold`, `max_trades_per_run`, `min_hours_to_resolution`, `locations` など（v2 とは **スキーマが異なる**）。

### 3.2 `bot_v2.py`（現行フルロジック相当）

- **対象都市**: **20**（US / EU / Asia / CA / SA / Oceania）。各都市に **ICAO 空港局**、摂氏／華氏、地域（`region`）が定義されている。
- **予報ソース**:
  - **ECMWF**: Open-Meteo（`ecmwf_ifs025`, bias correction）
  - **HRRR**: 米国向け（Open-Meteo の別モデル）
  - **METAR**: Aviation Weather 系（当日観測）
- US では HRRR が取れれば「best」に採用、それ以外は ECMWF を優先するロジックがある。
- **数学**: 正規分布近似によるバケット確率 `bucket_prob`、EV `calc_ev`、分数ケリー `calc_kelly`、ベットサイズ上限 `MAX_BET`。
- **キャリブレーション**: 解決済みマーケットの実測と予報誤差から **シグマ（不確実性）** を都市×ソース単位で更新し `data/calibration.json` に保存（サンプル数下限 `calibration_min`）。
- **リスク管理**:
  - 初期ストップは **sigma 連動の動的ストップロス**（単位ごとの baseline sigma に対するスケール、損失幅はクランプあり）
  - 含み益 **+20%** 超でストップを **建値（ブレークイブン）** に引き上げ（トレーリング）
  - **日次損失制限**: `daily_loss_limit_pct` 超過時はフルスキャンをスキップ
  - **相関ガード**: 同一都市・同一日付でオープン済みなら新規エントリーを明示スキップ
  - **モニタリング**（10 分間隔）では Polymarket Gamma の **bestBid** を取得し、残り時間に応じた **連続テイクプロフィット閾値**（線形補間）を評価
  - フルスキャン内では予報がポジションのバケットから大きく外れた場合に **「forecast_changed」** でクローズするロジックあり
- **データ永続化**: `data/state.json`（残高）、`data/markets/`（市場ごと JSON）、スナップショット履歴を蓄積。構造化ログは `data/logs/weatherbet.log`。
- **CLI**: `run`（省略時） / `status` / `report` / `dashboard` / CLOB・ウォレット関連サブコマンド（`clob-book` 等）。Usage は `python bot_v2.py` に統一。

---

## 4. 設定ファイル `config.json`（リポジトリ同梱値）

現在のリポジトリ内の例は **v2 向け** のキー構成です。

| キー | 例 | 意味（要約） |
|------|-----|----------------|
| `balance` | 10000.0 | 初期残高 |
| `max_bet` | 20.0 | 1 トレードあたりの最大ベット |
| `min_ev` | 0.1 | 最小期待値 |
| `max_price` | 0.45 | エントリー許容の最大価格 |
| `min_volume` | 500 | 最小出来高 |
| `min_hours` / `max_hours` | 2.0 / 72.0 | 決済までの時間ウィンドウ |
| `kelly_fraction` | 0.25 | ケリー乗数 |
| `scan_interval` | 3600 | フルスキャン周期（秒） |
| `calibration_min` | 30 | キャリブレーションに必要な最小サンプル数 |
| `max_slippage` | 0.03 | 許容スプレッド |
| `vc_key` | プレースホルダ | Visual Crossing（解決後の実測取得など）用 API キー |
| `daily_loss_limit_pct` 等 | 追加キー多数 | リスク・通知・CLOB・ウォレット・署名モード等（`config.json` 参照） |

**注意**: `vc_key` が未設定のままだと、実測取得や解決フローに影響する可能性があります。運用前に README の手順でキーを設定してください。

---

## 5. Polymarket との接続（Gamma API + CLOB 補助）

### 5.1 接続方式の要点

- **Gamma API**（ベース URL: `https://gamma-api.polymarket.com`）でイベント・マーケットの読み取り（GET）を行う。スキャン・解決・価格再取得の主経路。
- **CLOB REST**（設定 `clob_base_url`、既定 `https://clob.polymarket.com`）向けクライアントを追加。**板取得（GET）** と **注文送信（POST、`live_trading_enabled` が true かつ dry-run でない場合のみ）**、**注文ステータス（GET）** を CLI から利用可能。
- **メインのペーパートレード**は従来どおり **`data/state.json` と `data/markets/`** に保存。CLOB は実取引向けの補助レイヤー（デフォルトは dry-run / live 無効）。

### 5.2 呼び出しエンドポイント（`bot_v2.py` / `bot_v1.py` 共通の考え方）

| メソッド | URL パターン | 用途 |
|----------|----------------|------|
| GET | `https://gamma-api.polymarket.com/events?slug={slug}` | 対象日・都市の **イベント**（配下の複数マーケットと質問文・出来高など）を取得 |
| GET | `https://gamma-api.polymarket.com/markets/{market_id}` | **単一マーケット**の詳細（解決判定、モニタリング時の bestBid 再取得、エントリー直前の bestAsk 確認など） |

イベントの `slug` は v2 で次の規則で組み立てられます（`get_polymarket_event`）。

`highest-temperature-in-{city_slug}-on-{month}-{day}-{year}`

例: `highest-temperature-in-chicago-on-march-7-2026` のように、**英語の月名・日・年**が URL クエリにそのまま入ります。Polymarket 側のイベント slug と一致しない場合、その日・都市のイベントは取得できません。

### 5.3 コード内で Polymarket データが使われる場面（要約）

- **スキャン**: 上記 slug でイベントを引き、各マーケットの `question` から気温レンジを正規表現でパース（`parse_temp_range`）。`outcomePrices` から bid/ask 相当の価格とスプレッドを算出。
- **エントリー判断後**: 直前に再度 `markets/{id}` を GET し、`bestAsk` / `bestBid` でスリッページ検証・建玉情報を確定。
- **モニタリング**: 10 分ごとのループでオープンポジションの `market_id` に対し `bestBid` を取得し、ストップ・テイクプロフィット判定に使用。
- **解決確認**: `check_market_resolved` で `closed` と `outcomePrices`（YES 側が ~1 または ~0）から勝敗を推定。

### 5.4 本リポジトリに「まだ無い／未完了」もの

- **本番互換の EIP-712 署名**（現状は `stub` / `eth_sign` による検証用パス）
- **オンチェーン約定の直接監視**（注文ステータス API によるポーリングはあるが、トランザクションレシート追跡は未）
- **Relayer / Builder** 等の本番運用フルセット
- **法規制・居住地域に応じた利用可否の自動判定**

実運用で本番注文を出す場合は、Polymarket 公式の最新仕様に合わせた署名・認証の確定が必要です。

---

## 6. その他の外部 API（README・コードの対応）

| API | 主な用途 |
|-----|-----------|
| Open-Meteo | ECMWF / HRRR 予報 |
| Aviation Weather（METAR） | 観測 |
| Visual Crossing | 解決用の実測気温（キー必須） |

（Polymarket は上記 **第5節** を参照。）

---

## 7. README と実装の整合

README・`bot_v2.py` の表記は **`python bot_v2.py`** に統一済み。v1 を試す場合は `python bot_v1.py` およびそのオプションを利用します（設定キーは v2 用 `config.json` と完全には互換ではありません）。

---

## 8. 付属 HTML `sim_dashboard_repost.html`

単体の **ダッシュボード UI**（レトロなターミナル風デザイン、Chart.js CDN）。`python bot_v2.py dashboard` で `data/dashboard.json` を生成し HTML を開く連携は実装済み。HTML が JSON を fetch する完全自動表示は未。

---

## 9. データディレクトリ（実行後）

ボット実行後に生成される想定パス（`.gitignore` に `data/`・`simulation.json` を追加済み）。

- `data/state.json`
- `data/markets/*.json`
- `data/calibration.json`
- `data/logs/weatherbet.log`
- `data/dashboard.json`

v1 単体利用時は `simulation.json` がルートに生成されます。

---

## 10. まとめ

- 本プロジェクトは **Polymarket の気温市場** に対し、**Gamma API 経由の読み取り**を主とし、**空港基準の座標・複数予報源・EV／ケリー・キャリブレーション** でポジションを管理する **研究・シミュレーション向けボット** です。CLOB 連携は実取引向けの拡張枠組み。
- **v1/v2 の設定スキーマ差** は、新規利用者がつまずきやすいポイントです。
- 本レポートはリポジトリ現状のファイル一覧と主要コードの読み取りに基づきます。実ネットワーク上の API 挙動や市場の有効性は、実行環境・時期により変わります。

---

## 11. 参考（主要エントリポイント）

`bot_v2.py` の CLI は `run` / `status` / `report` / `dashboard` / CLOB・ウォレット系サブコマンドを含みます。末尾の Usage 行は `python bot_v2.py [...]` に統一されています（該当箇所は `bot_v2.py` の `if __name__ == "__main__":` ブロック末尾を参照）。
