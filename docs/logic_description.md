# WeatherBet ロジック詳細説明

**対象ファイル**: `weatherbet.py`（エントリーポイント）+ `src/weatherbet/` パッケージ
**作成日**: 2026-04-14／**更新**: 2026-04-15

---

## 目次

1. [全体構造](#1-全体構造)
2. [予報取得](#2-予報取得)
3. [予報アンサンブル合成](#3-予報アンサンブル合成)
4. [マーケット発見とバケット解析](#4-マーケット発見とバケット解析)
5. [確率推定](#5-確率推定)
6. [期待値計算](#6-期待値計算)
7. [Kelly基準とポジションサイジング](#7-kelly基準とポジションサイジング)
8. [エントリーフィルター（全条件）](#8-エントリーフィルター全条件)
9. [エントリー実行（スリッページ検証）](#9-エントリー実行スリッページ検証)
10. [ポジション管理（スキャン内）](#10-ポジション管理スキャン内)
11. [ポジション監視（モニタリングループ）](#11-ポジション監視モニタリングループ)
12. [自動解決確認](#12-自動解決確認)
13. [キャリブレーション](#13-キャリブレーション)
14. [メインループのタイミング制御](#14-メインループのタイミング制御)
15. [データ永続化](#15-データ永続化)
16. [利益を生む流れ](#16-利益を生む流れ)
17. [運用フロー](#17-運用フロー)
18. [調整方法](#18-調整方法)
19. [エラー対応](#19-エラー対応)
20. [リスク排除のチェックリスト](#20-リスク排除のチェックリスト)

---

## 1. 全体構造

### 1-1. モジュール構成

`weatherbet.py` は 1 行のエントリーポイントで、実装は `src/weatherbet/` パッケージに分割されている。

```
weatherbet.py
  └─ src/weatherbet/
       ├─ cli.py          — メインループ・CLI コマンドディスパッチ
       ├─ config.py       — 設定読み込み・定数・パス定義
       ├─ scanner.py      — 毎時フルスキャン
       ├─ monitor.py      — 10 分ごとの監視
       ├─ report.py       — ステータス表示・ダッシュボード JSON 出力
       ├─ calibration.py  — sigma 更新（RMSE ベース）
       ├─ notify.py       — 構造化ログ・Discord 通知・API 失敗追跡
       ├─ clob.py         — CLOB API クライアント・署名・ウォレット
       ├─ forecast/       — ecmwf.py / hrrr.py / metar.py / blend.py
       ├─ market/         — polymarket.py / parser.py
       ├─ strategy/       — probability.py / kelly.py / risk.py
       └─ storage/        — state.py / markets.py
```

### 1-2. 実行フロー

```
run_loop()  ←─ start_dashboard_server() をバックグラウンドスレッドで起動
  │
  ├─ [毎時: SCAN_INTERVAL=3600s] scan_and_update()
  │     ├─ 全都市 × 4日分 (D+0〜D+3)
  │     │     ├─ take_forecast_snapshot()   予報取得 + アンサンブル合成
  │     │     ├─ get_polymarket_event()     マーケット発見
  │     │     ├─ ストップロス / トレーリング / 予報ドリフト判定  ← 既存ポジション
  │     │     └─ エントリー判断 (EV・Kelly・フィルター)         ← ポジションなし
  │     ├─ AUTO-RESOLUTION                 全ポジションの解決確認
  │     ├─ actual_temp 取得（クローズ済み全市場、vc_key 必須）
  │     ├─ run_calibration()               30 件以上解決済みで発動
  │     └─ export_dashboard_data()         data/dashboard.json を更新
  │
  └─ [10分ごと: MONITOR_INTERVAL=600s] monitor_positions()
        ├─ オープンポジションの bestBid 取得 → ストップ / テイクプロフィット判定
        └─ export_dashboard_data()          data/dashboard.json を更新
```

**補足（運用・リスク）**:

- `scan_and_update()` 冒頭で **日次損失制限**（`daily_loss_limit_pct`）を評価し、超過時は当該サイクルのスキャンをスキップ。
- 新規エントリー前に **同一都市・同一日付のオープン重複**を明示ブロック（相関ガード）。
- 主要イベントは **`log_event()`** によりコンソール + `data/logs/weatherbet.log`（JSON Lines）へ出力。
- Discord Webhook 設定時は **新規ポジション・ストップロス・API 連続失敗** などで通知。

---

## 2. 予報取得

### 2-1. ECMWF (`get_ecmwf`)

- **API**: Open-Meteo (`api.open-meteo.com/v1/forecast`)
- **モデル**: `ecmwf_ifs025`（ECMWF IFS 0.25° 解像度）
- **オプション**: `bias_correction=true`（Open-Meteo のバイアス補正を適用）
- **取得値**: `temperature_2m_max`（地上2m 日最高気温）
- **ホライズン**: 7日分（`forecast_days=7`）
- **対象**: 全20都市
- **タイムゾーン**: 都市ごとのローカルタイムゾーンを指定し、日付区切りを現地時刻基準にする
- **リトライ**: 最大3回、失敗時は3秒待機

```python
url = (f"https://api.open-meteo.com/v1/forecast"
       f"?latitude={lat}&longitude={lon}"
       f"&daily=temperature_2m_max&temperature_unit={temp_unit}"
       f"&forecast_days=7&timezone={tz}"
       f"&models=ecmwf_ifs025&bias_correction=true")
```

### 2-2. HRRR / GFS (`get_hrrr`)

- **API**: Open-Meteo（同上）
- **モデル**: `gfs_seamless`（HRRR + GFS のシームレス合成、米国向け高解像度）
- **ホライズン**: 3日分（`forecast_days=3`）、D+2以降は取得範囲外のため `None`
- **対象**: `region == "us"` の6都市のみ（非米国都市では空辞書を返す）
- **単位**: 常に `fahrenheit`（LOCATIONS の unit が F の都市のみ対象なので問題なし）

### 2-3. METAR (`get_metar`)

- **API**: Aviation Weather (`aviationweather.gov/api/data/metar`)
- **取得値**: `temp`（摂氏での現在気温）
- **対象**: D+0（当日）のみ。D+1以降は `None` を返す
- **変換**: 摂氏→華氏（`unit == "F"` の都市）: `round(temp_c * 9/5 + 32)`
- **用途**: スナップショットに記録されるが、現在の実装ではアンサンブル合成には使わない（日最高気温の予測ではなく、瞬時観測値のため）

### 2-4. 実測気温取得 (`get_actual_temp`)

- **API**: Visual Crossing (`weather.visualcrossing.com`)
- **取得値**: `days[0].tempmax`（日最高気温の実績値）
- **タイミング**: マーケットがクローズ後（`status in ("closed", "resolved")`）かつ `actual_temp is None` のときに `vc_key` があれば取得
- **用途**: キャリブレーションの誤差計算、フォワードテストの勝敗判定

---

## 3. 予報アンサンブル合成

### 3-1. 逆分散重み付き合成 (`blend_forecast`)

ECMWF と HRRR（利用可能な場合）を各ソースの精度（sigma）に基づいて合成する。

**重みの定義**（逆分散重み付け）:

$$w_i = \frac{1}{\sigma_i^2}$$

**合成気温**:

$$T_{blend} = \frac{\sum_i w_i \cdot T_i}{\sum_i w_i}$$

**合成後の sigma**（統計的に正確な不確実性の伝播）:

$$\sigma_{blend} = \sqrt{\frac{1}{\sum_i w_i}}$$

**実装**:

```python
weights      = [1.0 / (s ** 2) for _, s in sources]
total_w      = sum(weights)
blended_temp = sum(t * w for (t, _), w in zip(sources, weights)) / total_w
blended_sig  = math.sqrt(1.0 / total_w)
```

**例**（ECMWF=73°F, σ=2.0; HRRR=75°F, σ=1.5）:

$$w_{ECMWF} = 1/4.0 = 0.25, \quad w_{HRRR} = 1/2.25 = 0.444$$

$$T_{blend} = \frac{73 \times 0.25 + 75 \times 0.444}{0.25 + 0.444} = \frac{18.25 + 33.3}{0.694} \approx 74.2°F$$

$$\sigma_{blend} = \sqrt{1/0.694} \approx 1.20°F$$

→ 精度の高いHRRRに引き寄せられた気温と、単独ソースより小さな不確実性が得られる。

### 3-2. フォールバック動作

| 状況 | best フィールド | best_source | blended_sigma |
|------|----------------|-------------|---------------|
| 両ソースあり | 合成値 | `"blend"` | 計算値 |
| ECMWFのみ | ECMWF値 | `"ecmwf"` | `None`（get_sigma を使用） |
| HRRRのみ | HRRR値 | `"hrrr"` | `None` |
| 両方なし | `None` | `None` | `None` |

---

## 4. マーケット発見とバケット解析

### 4-1. スラッグ生成 (`get_polymarket_event`)

Polymarket の URL スラッグは固定フォーマット:

```
highest-temperature-in-{city_slug}-on-{month}-{day}-{year}
```

例: `highest-temperature-in-chicago-on-april-14-2026`

- `month`: 小文字の英語月名（`MONTHS` リスト）
- `day`: ゼロ埋めなし整数
- `year`: 4桁

このスラッグで `gamma-api.polymarket.com/events?slug=...` を叩き、該当日・都市のイベント（複数のバケットマーケットを含むグループ）を取得する。

### 4-2. バケット解析 (`parse_temp_range`)

マーケットの `question` フィールドから温度レンジを正規表現で抽出。対応パターン:

| 質問文のパターン | 正規表現 | 戻り値 |
|---|---|---|
| `"be 74°F or below"` | `(\d+)°?[FC] or below` | `(-999.0, 74.0)` |
| `"be 90°F or higher"` | `(\d+)°?[FC] or higher` | `(90.0, 999.0)` |
| `"between 74-75°F"` | `between (-?\d+)-(-?\d+)°?[FC]` | `(74.0, 75.0)` |
| `"be 74°F on"` | `be (-?\d+)°?[FC] on` | `(74.0, 74.0)` |

`-999` / `999` はエッジバケット（下限なし / 上限なし）を示す sentinel 値。

### 4-3. `outcomePrices` の解釈

Polymarket Gamma API の `outcomePrices` は JSON 文字列のリスト:

```json
"outcomePrices": "[\"0.08\", \"0.92\"]"
```

- `prices[0]` → YES の価格（bid 相当）
- `prices[1]` → NO の価格

スプレッドはイベントレベルの `outcomePrices` では厳密には bid/ask スプレッドではなく、YES と NO の価格の差。エントリー前の最終確認では `markets/{id}` から `bestAsk` / `bestBid` を取得する（後述）。

---

## 5. 確率推定

### 5-1. 正規分布仮定

実際の日最高気温は予報値を中心に正規分布に従うと仮定する:

$$T_{actual} \sim \mathcal{N}(T_{forecast}, \sigma^2)$$

### 5-2. バケット確率 (`bucket_prob`)

各バケットに実際の気温が入る確率を正規分布 CDF の差分として計算:

**通常バケット** `[t_low, t_high]`:

$$P = \Phi\!\left(\frac{t_{high} - T_{fc}}{\sigma}\right) - \Phi\!\left(\frac{t_{low} - T_{fc}}{\sigma}\right)$$

**下限エッジバケット** (`t_low = -999`、「X°F以下」):

$$P = \Phi\!\left(\frac{t_{high} - T_{fc}}{\sigma}\right)$$

**上限エッジバケット** (`t_high = 999`、「X°F以上」):

$$P = 1 - \Phi\!\left(\frac{t_{low} - T_{fc}}{\sigma}\right)$$

ここで $\Phi$ は標準正規分布 CDF = `norm_cdf(x) = 0.5 * (1 + erf(x / √2))`

**具体例**（予報 74°F、バケット 74-75°F、σ=2.0°F）:

$$P = \Phi\!\left(\frac{75 - 74}{2.0}\right) - \Phi\!\left(\frac{74 - 74}{2.0}\right) = \Phi(0.5) - \Phi(0) \approx 0.691 - 0.500 = 0.191$$

→ 予報中心が 74°F でもこのバケットに入る確率は約 19%。他のバケットも確率を持ち、合計は1になる。

### 5-3. sigma の決定ロジック

エントリー時の sigma は以下の優先順位で決まる:

1. `snap["blended_sigma"]` — アンサンブル合成で算出された合成後 sigma
2. `calibration[f"{city}_{source}"]["sigma"]` — キャリブレーション済みの実績誤差
3. `SIGMA_F`（2.0°F）または `SIGMA_C`（1.2°C）— デフォルト値

---

## 6. 期待値計算

### 6-1. EV の定義 (`calc_ev`)

YES 側を価格 `price` で 1 株買った場合の期待利益（1株あたり）:

$$EV = p \times \left(\frac{1}{price} - 1\right) - (1-p)$$

- $p \times (1/price - 1)$: 的中したときの利益（残額を受け取る）
- $(1-p)$: 外れたときの損失（投資額全損）

**具体例**（p=0.30、price=$0.09）:

$$EV = 0.30 \times \left(\frac{1}{0.09} - 1\right) - 0.70 = 0.30 \times 10.11 - 0.70 = 3.03 - 0.70 = 2.33$$

→ EV 233%。市場価格 9 セントに対し確率 30% でも大きな正のEVが出る。

**MIN_EV フィルター**: `ev >= MIN_EV`（デフォルト 0.10 = EV 10%以上）

---

## 7. Kelly基準とポジションサイジング

### 7-1. フルKelly (`calc_kelly`)

YES が $b:1$ のオッズ（`b = 1/price - 1`）のとき、最適投資比率:

$$f^* = \frac{p \cdot b - (1-p)}{b}$$

### 7-2. 分数Kelly

実際には `KELLY_FRACTION`（デフォルト 0.25）を乗じた分数Kellyを使用:

$$f = \max(0,\ f^*) \times kelly\_fraction$$

これにより理論最適の 25% だけを投資し、Kelly オーバーベットによる過大なドローダウンリスクを抑制する。

### 7-3. ベットサイズ上限 (`bet_size`)

```python
raw  = kelly * balance
size = min(raw, MAX_BET)    # MAX_BET でキャップ
```

`MAX_BET`（デフォルト $20）による絶対上限で、大残高時のバケット一点集中を防ぐ。

**具体例**（p=0.30, price=$0.09, balance=$10,000, KELLY_FRACTION=0.25）:

$$b = 1/0.09 - 1 = 10.11$$
$$f^* = \frac{0.30 \times 10.11 - 0.70}{10.11} = \frac{3.033 - 0.70}{10.11} = 0.2308$$
$$f = 0.2308 \times 0.25 = 0.0577$$
$$raw = 0.0577 \times 10000 = \$577 \quad \xrightarrow{\text{cap}} \quad size = \$20$$

---

## 8. エントリーフィルター（全条件）

以下の**全条件**を順番に評価し、1つでも失敗するとそのマーケットをスキップする。

```
1. ポジション未保有            mkt["position"] is None
2. 予報気温が存在              forecast_temp is not None
3. 時間ウィンドウ内            MIN_HOURS <= hours_left <= MAX_HOURS
4. ポートフォリオ上限          open_pos_count + new_pos < MAX_OPEN_POS
5. バケットマッチ              in_bucket(forecast_temp, t_low, t_high)
6. 最小出来高                  volume >= MIN_VOLUME
7. 確率計算                    p = bucket_prob(forecast_temp, t_low, t_high, sigma)
8. EV フィルター               calc_ev(p, ask) >= MIN_EV
9. Kelly サイジング            size >= $0.50（最低投資額）
10. 最終価格確認               entry_price < MAX_PRICE（実 bestAsk で再確認）
11. スリッページ確認            real_spread <= MAX_SLIPPAGE（実 bestAsk-bestBid）
```

**条件3の時間ウィンドウ**:
- `MIN_HOURS`（デフォルト 2h）: 直前すぎる市場は予報の更新余地がなく参加しない
- `MAX_HOURS`（デフォルト 72h）: 遠すぎる市場は予報精度が低く参加しない

**条件4のポートフォリオ上限**:
スキャン開始時に全オープンポジション数を集計し、今回のスキャンで新規に開いた数（`new_pos`）と合算して上限チェック。

---

## 9. エントリー実行（スリッページ検証）

EV・Kelly フィルターを通過した後、イベント API の `outcomePrices` から得た価格はリアルタイムの bid/ask ではない。エントリー直前に対象マーケットの個別エンドポイントを叩いて実際の注文帳情報を取得する:

```
GET gamma-api.polymarket.com/markets/{market_id}
  → bestAsk  (実際に買える価格)
  → bestBid  (実際に売れる価格)
```

**再チェック**:
- `real_spread = bestAsk - bestBid > MAX_SLIPPAGE` → スキップ
- `bestAsk >= MAX_PRICE` → スキップ

通過した場合、`bestAsk` でエントリー価格と株数を確定する。

---

## 10. ポジション管理（スキャン内）

フルスキャン（毎時）の都市ループ内で、既存オープンポジションに対して2種類の管理を行う。

### 10-1. ストップロス / トレーリングストップ

価格は `outcomes` リスト（イベント API の `outcomePrices`）から取得し、`bid` 側（売却可能価格）を使用。

**トレーリングストップの発動条件**:

```python
if current_price >= entry * 1.20 and stop < entry:
    pos["stop_price"] = entry     # ストップを建値に引き上げ
    pos["trailing_activated"] = True
```

**クローズ条件**:

```python
if current_price <= stop:
    close_reason = "stop_loss"    # if current_price < entry
    close_reason = "trailing_stop" # if current_price == entry
```

デフォルトのストップ: **`calc_dynamic_stop_price(entry, sigma, unit)`** により sigma に連動（単位ごとの baseline sigma に対して損失幅をスケールし、上下限でクランプ）。従来の固定 `entry * 0.80` は置換済み。

### 10-2. 予報ドリフトによるクローズ (`forecast_changed`)

保有バケットから予報が大きく外れた場合は、解決前でも即時クローズ。

**判定ロジック**:

```python
mid_bucket = (t_low + t_high) / 2
buffer = 2.0 if unit == "F" else 1.0  # 小さな予報変動での誤クローズを防ぐバッファ
forecast_far = abs(forecast_temp - mid_bucket) > (abs(mid_bucket - t_low) + buffer)

# 両条件が揃った場合にクローズ
if not in_bucket(forecast_temp, t_low, t_high) and forecast_far:
    → クローズ (close_reason = "forecast_changed")
```

バケット幅の半分 + buffer 以上、バケット中心から離れた場合のみクローズ。1°F 程度の予報ブレでは反応しない設計。

---

## 11. ポジション監視（モニタリングループ）

毎10分実行の `monitor_positions()` はフルスキャンと異なり、予報取得を行わず価格のみを確認する高頻度チェック。

### 11-1. 価格取得

```
GET gamma-api.polymarket.com/markets/{market_id}
  → bestBid  (最優先取得)
  → 失敗時: all_outcomes キャッシュの bid を使用
```

### 11-2. テイクプロフィット（時間依存・連続化）

`calc_take_profit_threshold(hours_left)` で閾値を算出する。

- **< 24h**: なし（解決まで保持）
- **24h〜48h**: **$0.85 → $0.75** を線形補間（例: 36h では $0.80）
- **≥ 48h**: $0.75

**背景**: 解決に近づくほど YES 価格は 1.0 に収束しやすく、早期ほど段階的に利確基準を調整する意図。旧実装の3段階テーブルはこの連続関数へ置換済み。

### 11-3. トレーリングストップ（モニタリング側）

スキャン内と同じロジック。モニタリング内でも `entry * 1.20` を超えたらストップを建値に引き上げ。

---

## 12. 自動解決確認

スキャン終了後、全オープンポジションに対して解決確認を行う (`check_market_resolved`)。

### 12-1. 解決判定ロジック

```
GET gamma-api.polymarket.com/markets/{market_id}
  → closed = True  かつ
  → YES価格 >= 0.95  → WIN
  → YES価格 <= 0.05  → LOSS
  → それ以外         → 未確定（スキップ）
```

YES 価格が 0.95 未満かつ 0.05 超の中間状態は「未確定」として次サイクルに持ち越す。

### 12-2. PnL 計算（解決時）

```python
# WIN: shares * (1 - entry_price) が利益（残額受け取り）
pnl = round(shares * (1 - entry_price), 2)

# LOSS: 投資額全損
pnl = round(-size, 2)
```

---

## 13. キャリブレーション

### 13-1. 発動条件

解決済みマーケット数が `CALIBRATION_MIN`（デフォルト30）を超えた場合、`run_calibration()` が呼ばれる。

### 13-2. sigma の再計算

各ソース（ecmwf / hrrr / metar）× 都市の組み合わせで、過去の予報誤差から sigma を更新する。

**現在の実装（RMSE ベース）**:

```python
errors = [abs(snap["temp"] - m["actual_temp"]) for m in group]
rmse = math.sqrt(sum(e**2 for e in errors) / len(errors))
cal[key]["sigma"] = round(rmse, 3)
```

RMSE（二乗平均平方根誤差）を sigma の推定値として使用する。サンプル数が `CALIBRATION_MIN` 以上の場合のみ更新（少数データでの過学習を防ぐ）。

> **注**: 正規分布に近い誤差分布では、RMSE は sigma の最尤推定に整合しやすい。旧 MAE ベースは sigma を過小推定しやすかった。

### 13-3. 更新の閾値

```python
if abs(new - old) > 0.05:
    print(f"[CAL] {city} {source}: {old:.2f}->{new:.2f}")
```

0.05 未満の変化はログを出さない（無意味な出力を抑制）。

### 13-4. キャリブレーション済み sigma の利用

`get_sigma(city_slug, source)` がキャリブレーションファイルを参照。エントリー時のアンサンブル合成の重み計算（逆分散）にも使われるため、精度の高いソースが自動的に重く扱われるようになる。

---

## 14. メインループのタイミング制御

```python
SCAN_INTERVAL    = 3600  # 1時間
MONITOR_INTERVAL = 600   # 10分

last_full_scan = 0
while True:
    if time.time() - last_full_scan >= SCAN_INTERVAL:
        scan_and_update()           # 全都市スキャン（重い）
        last_full_scan = time.time()
    else:
        monitor_positions()         # 価格チェックのみ（軽い）
    time.sleep(MONITOR_INTERVAL)
```

`scan_and_update` の実行時間がスキャン間隔に含まれないことに注意。スキャンに5分かかった場合、次のフルスキャンは約65分後になる（`time.time() - last_full_scan` が経過時間を正確に測定するため）。

---

## 15. データ永続化

### 15-1. state.json（残高管理）

```json
{
  "balance": 9850.0,
  "starting_balance": 10000.0,
  "total_trades": 12,
  "wins": 8,
  "losses": 4,
  "peak_balance": 10220.0
}
```

残高の変動は以下のタイミングで発生:
- **エントリー**: `balance -= size`
- **ストップ / クローズ**: `balance += cost + pnl`
- **解決（WIN）**: `balance += cost + shares * (1 - entry_price)`
- **解決（LOSS）**: `balance += 0`（cost は既に引かれている）

### 15-2. markets/{city}_{date}.json（マーケット管理）

マーケットファイルは1都市×1日の単位で管理。以下の情報を蓄積する:

| フィールド | 書き込みタイミング | 内容 |
|---|---|---|
| `all_outcomes` | 毎スキャン | 全バケットの価格・出来高（最新値で上書き） |
| `forecast_snapshots` | 毎スキャン（追記） | ECMWF/HRRR/METAR/blend の時系列 |
| `market_snapshots` | 毎スキャン（追記） | 最高値バケットの価格時系列 |
| `position` | エントリー時 | ポジション詳細（価格・Kelly・EV・close情報） |
| `status` | 状態変化時 | `open` → `closed` → `resolved` |
| `actual_temp` | クローズ後 | Visual Crossing から取得した実測値 |
| `resolved_outcome` | 解決時 | `"win"` または `"loss"` |
| `pnl` | 解決時 | 最終損益 |

### 15-3. balance_history.json（残高時系列）

```json
[
  {"ts": "2026-04-14T06:00:00Z", "balance": 10050.0},
  {"ts": "2026-04-14T12:00:00Z", "balance": 10234.56}
]
```

- `export_dashboard_data()` 呼び出しのたびに末尾へ追記（最大 500 件、古いものから削除）
- ダッシュボードの残高チャートはこのファイルから描画されるため、セッションをまたいで履歴が維持される
- `data/` 以下に生成。`.gitignore` 対象

### 15-4. dashboard.json（ダッシュボード配信データ）

`export_dashboard_data()` が生成。毎時スキャン後・10 分監視後に自動更新。

| フィールド | 内容 |
|---|---|
| `state` | `state.json` の全フィールド（残高・勝敗数） |
| `summary` | open_count / resolved_count / win_rate / roi_pct / total_realized_pnl |
| `open_positions` | オープンポジション一覧（EV・残り時間・stop_price・take_profit_threshold 含む） |
| `recent_resolved` | 直近 30 件の解決済みトレード（actual_temp・forecast_temp 含む） |
| `city_stats` | 都市別勝率・PnL 集計 |
| `balance_history` | 直近 60 点（チャート用） |
| `daily_pnl` | 日次 PnL 時系列（直近 30 日） |
| `log_tail` | `weatherbet.log` 末尾 20 件 |

ブラウザは `http://localhost:8000/sim_dashboard_repost.html` で 30 秒ごとにポーリング。HTTP サーバーは `run_loop()` 起動時に自動で立ち上がる（`dashboard_port` で変更可、デフォルト 8000）。

### 15-5. calibration.json（sigma管理）

```json
{
  "chicago_ecmwf": {"sigma": 1.82, "n": 45, "updated_at": "2026-04-10T..."},
  "chicago_hrrr":  {"sigma": 1.43, "n": 38, "updated_at": "2026-04-10T..."},
  "london_ecmwf":  {"sigma": 0.95, "n": 31, "updated_at": "2026-04-08T..."}
}
```

`n` はサンプル数。`CALIBRATION_MIN` 未満のエントリーは作成されない。

---

## 16. 利益を生む流れ

このボットの利益源は「気温が当たること」そのものではなく、**予報から計算した確率と市場価格のズレ**を安く買うことにある。

### 16-1. 期待値優位の発生源

1. ECMWF / HRRR から対象日の最高気温を推定する
2. `bucket_prob()` で「そのバケットに入る確率」を出す
3. `calc_ev()` で市場の ask 価格と比較する
4. **`EV >= MIN_EV`** かつ **`ask < MAX_PRICE`** のときだけ入る

要するに、

- 市場が過小評価している YES を拾う
- 高すぎる価格は見送る
- スプレッドが広い市場は見送る

という3点で、価格ミスプライシングだけを狙う設計になっている。

### 16-2. 利益確定の経路

利益は主に次の3経路で実現される。

| 経路 | 発動条件 | 意味 |
|---|---|---|
| `take_profit` | モニタリング時に価格が利確閾値以上 | 市場価格の先行織り込みで早めに利益確定 |
| `trailing_stop` | 一度含み益が乗った後、建値まで戻る | 利益機会を残しつつ大きな逆行を防ぐ |
| `resolved = win` | 解決時に YES が的中 | 1株あたり `1 - entry_price` を回収 |

### 16-3. 何で負けるのか

損失要因を理解しておかないと、設定調整を誤る。

| 損失要因 | 発生箇所 | 抑制に使うもの |
|---|---|---|
| 予報そのものの外れ | `forecast_changed` / 解決 loss | `sigma_*`, `calibration`, `MIN_HOURS`, `MAX_HOURS` |
| 高値掴み | エントリー直前 | `MAX_PRICE`, `MAX_SLIPPAGE` |
| 薄い板に入ること | マーケット選別 | `MIN_VOLUME`, `bestAsk/bestBid` 再確認 |
| 同日に偏ること | 新規エントリー時 | 相関ガード、`MAX_OPEN_POS` |
| 連敗の継続 | スキャン開始時 | `daily_loss_limit_pct` |

### 16-4. 運用上の重要原則

- 価格が安いだけでは入らない。必ず **EV とスプレッドを同時に見る**。
- 勝率よりも **損益分布** を見る。低価格バケットは負け回数が多くてもトータルで勝てる一方、過大ベットするとドローダウンが急拡大する。
- 利益の再現性は `sigma` の妥当性に依存する。`vc_key` 未設定で実測値が溜まらない状態は、長期的には優位性の劣化につながる。

---

## 17. 運用フロー

### 17-1. 起動前

起動前に最低限確認する項目:

| 項目 | 確認内容 | リスク |
|---|---|---|
| `config.json` | `max_bet`, `min_ev`, `max_price`, `kelly_fraction` | 過大ベット・過剰エントリー |
| `vc_key` | 実測気温を取得できるか | キャリブレーション停止 |
| `daily_loss_limit_pct` | 日次停止ラインが妥当か | 連敗日の損失拡大 |
| `discord_webhook_url` | 通知が必要なら設定 | 障害見逃し |
| `live_trading_enabled` | 本当にライブ注文を有効にするか | 想定外の本番発注 |

### 17-2. 稼働中

通常運用では以下の順で見る。

1. `http://localhost:8000/sim_dashboard_repost.html` — ブラウザでリアルタイム確認（`run` 起動時に自動起動）
2. `python weatherbet.py status` — CLI で残高・オープンポジション数を確認
3. `data/logs/weatherbet.log` — `WARNING` / `ERROR` を確認（ダッシュボードの Bot Log パネルにも表示）
4. `data/markets/*.json` — 個別マーケットの `close_reason`, `actual_temp`, `resolved_outcome` を確認
5. Discord 通知がある場合は `STOP LOSS` と API 連続失敗を優先確認

### 17-3. 1日の監視ポイント

| タイミング | 見るもの | 判断 |
|---|---|---|
| 起動直後 | 残高、設定値、対象都市数 | 誤設定がないか |
| 初回フルスキャン後 | `new`, `closed`, `resolved` 件数 | 取引頻度が想定通りか |
| 日中 | stop / take profit の発生数 | 値動きが荒すぎないか |
| 日次終了後 | 実現損益、連敗数、解決結果 | 翌日の設定調整が必要か |

### 17-4. 運用停止の目安

以下に該当したら、設定を触る前にいったん停止して原因を切り分ける。

- `daily_loss_limit_pct` に達してスキャン停止が出た
- API 失敗通知が連続している
- `forecast_changed` によるクローズが短期間に多発した
- `actual_temp` が長時間入らずキャリブレーションが進まない
- 想定よりエントリー数が極端に多い、または少ない

---

## 18. 調整方法

設定は一度に多く触らず、**1回の変更で 1〜2 項目まで**に留める。そうしないと、何が効いたのか判別できない。

### 18-1. 基本の調整順序

1. まず `kelly_fraction` と `max_bet` で損失速度を抑える
2. 次に `min_ev` と `max_price` でエントリー品質を調整する
3. その後 `min_volume`, `max_open_positions`, `daily_loss_limit_pct` で運用安定性を整える
4. 最後に `sigma_f`, `sigma_c`, `calibration_min` を見直す

### 18-2. 症状別の調整ガイド

| 症状 | まず触る項目 | 期待される効果 | 注意点 |
|---|---|---|---|
| 連敗時の減りが速い | `kelly_fraction` を下げる | 1回あたり損失を縮小 | 利益成長も遅くなる |
| 1件の損失が大きい | `max_bet` を下げる | 最大被害を固定化 | 小口すぎると優位性が活きにくい |
| エントリー数が多すぎる | `min_ev` を上げる | より厳しい選別 | 機会損失も増える |
| 高値掴みが多い | `max_price` を下げる | 割高な約定を減らす | 約定数が減る |
| 板が薄く滑りやすい | `min_volume` を上げる | 流動性の低い市場を除外 | 対象市場が減る |
| 同時保有で不安定 | `max_open_positions` を下げる | 分散の上限を明確化 | 機会数が減る |
| 連敗日がつらい | `daily_loss_limit_pct` を下げる | 日次損失の強制停止が早まる | その日の反発は取り逃す |

### 18-3. sigma 調整の考え方

`sigma_f` / `sigma_c` は「キャリブレーションが十分に溜まる前の仮置き誤差」である。

- 実績より sigma が小さすぎる: 確率を過信しやすくなり、EV が過大評価される
- 実績より sigma が大きすぎる: エントリーが減り、優位性を取りこぼしやすい

そのため、

1. まず `vc_key` を有効にして `actual_temp` を確実に蓄積する
2. `CALIBRATION_MIN` 件を超えた後は `calibration.json` を優先して使う
3. デフォルト sigma は「初期値」と割り切り、恒久値として固定しない

### 18-4. 安全な変更単位

保守的に変えるなら以下が目安:

- `kelly_fraction`: 0.25 → 0.20 → 0.15
- `max_bet`: 20 → 15 → 10
- `min_ev`: 0.10 → 0.12 → 0.15
- `max_price`: 0.45 → 0.40 → 0.35
- `daily_loss_limit_pct`: 0.10 → 0.07 → 0.05

---

## 19. エラー対応

### 19-1. API 取得失敗

予報 API / Polymarket API / Visual Crossing API はいずれも失敗しうる。現行実装では個別 API の失敗時に `WARNING` ログを残し、可能な範囲でスキップまたはフォールバックする。

| 事象 | 実装の挙動 | 運用者の対応 |
|---|---|---|
| ECMWF / HRRR 失敗 | 当該ソースを `None` 扱い | 一時的か継続的かをログで確認 |
| METAR 失敗 | 観測値なしで継続 | 当日バケット判定への影響は限定的 |
| Polymarket 個別価格取得失敗 | 監視ではキャッシュ価格へフォールバック | 価格鮮度低下に注意 |
| Visual Crossing 失敗 | `actual_temp` 未取得のまま | キャリブレーション停滞を確認 |

### 19-2. API 連続失敗アラート

`track_api_result()` により API ごとの連続失敗回数を数え、`api_failure_alert_threshold` 回に達すると `ERROR` ログと Discord 通知を出す。

この通知が出たら確認すべき順序:

1. 一時的なネットワーク断か
2. 特定 API だけ失敗しているか
3. 数十分継続しているか
4. 再開後に `new` / `closed` / `resolved` が正常化したか

### 19-3. 日次損失上限に達した場合

`scan_and_update()` 冒頭で当日実現損失を確認し、上限到達時はそのサイクルのフルスキャンを止める。

- 新規エントリーを無理に再開しない
- まず当日の `close_reason` を確認する
- `stop_loss` 連発なのか、`forecast_changed` が多いのかを切り分ける
- 翌日も同じ状況なら設定ではなくデータ品質を疑う

### 19-4. 実測値が入らない場合

`actual_temp` が増えない場合は、たいてい次のどれか:

- `vc_key` が未設定または無効
- 市場がまだ `closed` / `resolved` になっていない
- Visual Crossing API が失敗している

この状態では `calibration.json` の更新が止まり、長期的に `sigma` が古くなる。

### 19-5. 手動確認が必要なファイル

障害時は次を優先して見る。

| ファイル | 用途 |
|---|---|
| `data/logs/weatherbet.log` | 失敗 API、停止理由、クローズ理由の確認 |
| `data/state.json` | 残高、勝敗数、ピーク残高の確認 |
| `data/markets/{city}_{date}.json` | 個別マーケットのスナップショットとポジション状態 |
| `data/calibration.json` | sigma が更新されているか |

---

## 20. リスク排除のチェックリスト

最後に、実運用で最低限守るべき点をまとめる。

- 初期は `live_trading_enabled = false` のまま挙動を確認する
- `kelly_fraction` と `max_bet` は小さめから始める
- `vc_key` を設定し、`actual_temp` が継続的に溜まることを確認する
- `weatherbet.log` の `WARNING` / `ERROR` を日次で確認する
- `daily_loss_limit_pct` を必ず設定し、連敗日の自動停止を有効にする
- `min_ev` を緩めすぎて「何でも買う」状態にしない
- `max_price` と `max_slippage` を超えた市場は追いかけない
- `max_open_positions` を大きくしすぎて同日相関を増やさない
- 設定変更は少数項目ずつ行い、変更前後の成績を分けて見る
- API 障害時はそのまま放置せず、データ欠損が何に影響するかまで確認する

---

## 付録 A. $50 スタート時の想定動作

デフォルト設定（`max_bet=20`, `kelly_fraction=0.25`, `min_ev=0.10`, `daily_loss_limit_pct=0.10`）で残高 $50 から始めた場合の具体的な挙動を示す。

---

### A-1. ポジションサイジングの実例

$50 残高では **Kelly が `max_bet` より先に上限となる**。`max_bet=$20` が効くのは残高が約 $345 を超えてから。

以下は代表的な3ケースの計算（`kelly_fraction=0.25`, `sigma=2.0°F`）。

#### ケース 1: 低価格バケット（予報が端に近い）

| パラメータ | 値 |
|---|---|
| 予報気温 | 46.7°F、バケット 46–47°F |
| バケット確率 `p` | ≈ 0.191（CDF差分） |
| 市場価格 (ask) | $0.08 |
| EV | `0.191 × (1/0.08 − 1) − 0.809 = +1.39`（139%） |
| フルKelly `f*` | `(0.191 × 11.5 − 0.809) / 11.5 ≈ 0.121` |
| 分数Kelly `f` | `0.121 × 0.25 ≈ 0.030` |
| ベット額 | `0.030 × $50 = $1.51` |
| 購入株数 | `$1.51 / $0.08 = 18 株` |
| 勝ち時損益 | `18 × (1 − 0.08) = +$16.56`（残高 $65.05） |
| 負け時損益 | `−$1.51`（残高 $48.49） |
| ストップ価格 | `$0.08 × (1 − 0.35) = $0.052`（sigma=2.0°F の場合） |

#### ケース 2: 中価格バケット（予報がバケット中心付近）

| パラメータ | 値 |
|---|---|
| 予報気温 | 47.5°F、バケット 47–48°F |
| バケット確率 `p` | ≈ 0.383（CDF差分、予報がほぼ中心） |
| 市場価格 (ask) | $0.20 |
| EV | `0.383 × (1/0.20 − 1) − 0.617 = +0.915`（91%） |
| フルKelly `f*` | `(0.383 × 4.0 − 0.617) / 4.0 ≈ 0.229` |
| 分数Kelly `f` | `0.229 × 0.25 ≈ 0.057` |
| ベット額 | `0.057 × $50 = $2.87` |
| 購入株数 | `$2.87 / $0.20 = 14 株` |
| 勝ち時損益 | `14 × 0.80 = +$11.20`（残高 $59.33） |
| 負け時損益 | `−$2.87`（残高 $47.13） |
| ストップ価格 | `$0.20 × 0.65 = $0.130` |

#### ケース 3: EV ギリギリの市場（エッジケース）

| パラメータ | 値 |
|---|---|
| 確率 `p` | 0.20 |
| 市場価格 (ask) | $0.17 |
| EV | `0.20 × 4.88 − 0.80 = +0.176`（17.6%、MIN_EV を通過） |
| 分数Kelly `f` | `0.20 × 0.25 / 4 ≈ 0.013` |
| ベット額 | `0.013 × $50 = $0.65` |
| 株数 | `$0.65 / $0.17 = 3 株` |
| 勝ち時損益 | `3 × 0.83 = +$2.49` |
| 負け時損益 | `−$0.65` |

> **`min_ev` による自然なフィルタ**: 確率が固定なら `price <= p / 1.10` を超えるとエントリーしない。  
> `p=0.20` の場合、$0.182 より高い市場は除外される。`p=0.30` では $0.273 が上限。

---

### A-2. 日次損失制限の実際

```
daily_loss_limit = $50 × 0.10 = $5.00
```

ケース 1（ベット額 $1.51）で連続負けした場合:

| 負け回数 | 累計損失 | 制限到達 |
|---|---|---|
| 1 回 | $1.51 | — |
| 2 回 | $3.02 | — |
| 3 回 | $4.53 | — |
| 4 回 | $6.04 | **超過 → スキャン停止** |

ケース 2（ベット額 $2.87）では 2 連敗（$5.74）で制限に達する。残高 $50 で `daily_loss_limit_pct=0.10` は非常にタイトな設定であることに注意。

---

### A-3. 同時ポジション数の上限

`max_open_positions=10`（デフォルト）だが、$50 残高で実際に保有できる数は資金量で制約される。

| ベット額/件 | 同時保有上限（資金ベース） | `max_open_positions` との比較 |
|---|---|---|
| $1.51（ケース 1） | 約 33 件（$50 / $1.51） | → 設定の 10 件が先に効く |
| $2.87（ケース 2） | 約 17 件 | → 設定の 10 件が先に効く |
| $3.50（高EV案件） | 約 14 件 | → 設定の 10 件が先に効く |

$50 規模では `max_open_positions` の上限に達する前に予算を使い切ることはほぼない。  
ただし **$5 日次損失制限**が先に発動するため、活発なエントリーが続く日は午前中に停止する可能性がある。

---

### A-4. 残高推移シナリオ（モンテカルロ的試算）

前提: ケース 1 相当のトレードを週 5 件、勝率 25%（`p=0.191` よりやや悲観的）。

| 期間 | 楽観シナリオ（勝率 30%） | 中立シナリオ（勝率 25%） | 悲観シナリオ（勝率 18%） |
|---|---|---|---|
| 1 週（5 件） | +$11〜+$25 | ±$0〜+$10 | −$5〜−$8（制限停止あり） |
| 1 ヶ月（20 件） | $80〜$120 | $45〜$60 | $30〜$40 |
| 3 ヶ月（60 件） | $200〜$400 | $55〜$80 | $15〜$30（残高激減） |

> **ハイリスク構造の注意点**  
> 低価格バケット（$0.08〜$0.12）を狙う場合、1 件の勝ちが複数の負けを補填する高ペイオフ構造になる。  
> 勝率が「確率計算通り」に実現するまで数十トレードの試行が必要であり、**短期間の結果（10 件未満）は統計的に意味がない**。

---

### A-5. 残高がさらに減った場合の自動スケールダウン

Kelly は残高比例のため、残高が減ると自動的にベット額も縮小する。

| 残高 | ケース 2 相当のベット額 | 最小ベット閾値（$0.50）到達まで |
|---|---|---|
| $50 | $2.87 | — |
| $30 | $1.72 | — |
| $15 | $0.86 | — |
| $9 | $0.51 | ギリギリ通過 |
| $8 | $0.46 | **エントリー停止**（`size < $0.50` フィルタ） |

残高 $8〜$9 以下になると、Kelly が産出するベット額が最小閾値 $0.50 を下回り、新規エントリーが実質的に止まる。**ルーン割れ**的な状態はこの水準が目安。

---

### A-6. $50 スタート向けの設定調整案

デフォルトはより大きな資金（$1,000〜$10,000）を想定した設定のため、$50 では一部パラメータが合わない。

| 設定項目 | デフォルト | $50 向け推奨 | 理由 |
|---|---|---|---|
| `daily_loss_limit_pct` | 0.10（$5） | **0.15〜0.20**（$7.5〜$10） | $5 は 2〜3 連敗で停止しすぎる |
| `min_ev` | 0.10 | **0.10〜0.12** | 現状維持か若干厳しめに（ノイズ排除） |
| `max_open_positions` | 10 | **5〜7** | 資金分散しすぎると1件あたりが小さくなる |
| `kelly_fraction` | 0.25 | **0.20〜0.25** | 現状維持。$50 ではもともとベットが小さい |
| `max_bet` | 20 | **5〜10** | $50 残高での Kelly上限を明示的に設定 |
| `calibration_min` | 30 | 30（変更不要） | キャリブレーション品質は同じ |

> **`max_bet` を下げる主な理由**:  
> デフォルトの $20 は、残高が大幅に増えたとき（例: $500 → Kelly が $28.8）に初めて効く。  
> $50 の段階では Kelly が先に上限となるため、`max_bet=10` に下げても実質的なベット額は変わらない。  
> ただし「将来的な暴走防止」として小さく設定しておくことで、残高が急増したときの過大ベットを抑制できる。
