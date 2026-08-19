# MCPデータ実測調査（更新前の履歴）

> [2026年8月14日の更新後再監査](MCPデータ再調査_2026-08-14.md)を最新版として参照してください。本書は更新前の実測と、追加実装に至った経緯を残す履歴資料です。

> 訂正：コード監査の結果、板、銘柄情報、規制、優先市場、信用プレミアム、resolver等のGETは銘柄登録状態を変更しません。登録状態を変えるのは`PUT /register`、`PUT /unregister`、`PUT /unregister/all`だけです。本書内の「情報GETが自動登録し得る」という記述は更新前調査時の誤認です。

## 1. 調査の目的と範囲

この文書は、次の4つのChatGPT用SKILLを設計する前段として、利用可能なMCPから何を取得でき、どの用途に使え、どこに注意が必要かを実データで確認した結果です。

1. 日経225・TOPIXの現在時点基準予測と、単純方向・NT取引の提示
2. 日本株の最新下落率ランキングからの復活可能性評価と出口戦略
3. 市場拡大が見込まれる日本株の中長期成長評価と出口戦略
4. 特定銘柄の株価シナリオ予測

調査対象は次の4プラグインです。

- ニュース取得ツール
- 市場データ収集
- EDINET DB
- 政策DB

補足調査として、`work/kabu_STATION_API.yaml`の仕様に従い、`http://10.10.100.1:8080/kabusapi`へ`X-API-KEY`を付けずに読み取り専用GETを実行しました。注文、取消、登録、登録解除、口座・建玉取得などは実行していません。

実測時間は主に2026年8月13日23:43～23:59 JSTです。日付をまたいだため、文書作成日は2026年8月14日です。記載値は機能・鮮度の確認例であり、現在の売買推奨ではありません。

## 2. 先に結論

4つのSKILLはすべて構築できます。ただし、安全性と再現性を確保するには次の役割分担が必要です。

| データ層 | 主な取得元 | 役割 |
|---|---|---|
| 秒～分の執行データ | KabusControllerの先物・OP JSON | 現在価格、板、スプレッド、出来高、VWAP、入口価格 |
| 分～当日の市場概況 | 225225.jp | 現物指数、夜間先物、海外指数、為替、NT倍率、業種、日経寄与度、速報ランキング |
| 公式の日次・週次データ | J-Quants | 終値、OHLCV、調整株価、財務、信用、空売り、投資部門別、日次OP面 |
| 企業一次情報 | EDINET DB | 決算、会社予想、セグメント、開示イベント、IR資料、株主・ガバナンス |
| 直近材料の探索 | ニュース取得ツール | 材料候補、海外・マクロ環境、記事改訂の追跡 |
| 中長期の政策背景 | 政策DB | 政策テーマ、公的支出・採択、法令、白書、研究費、経済安保 |

重要な判断は次のとおりです。

- 先物JSONは実測で取得時刻との差が0～38秒で、現在時点基準の入口計算に使えます。
- OP JSONも通信自体はオンデマンドですが、登録されていた34銘柄は観測時に現在値・板が空でした。`CalcPrice`、IV、Greeksがあるだけでは取引可能なリアルタイム価格とは判定できません。
- J-Quantsは取得時刻が現在でも、データ自体は日次または週次です。`collected_at`をデータ生成時刻として扱ってはいけません。
- 下落率速報は225225.jpで上位20件、ユーザー指定のkabuステーション互換APIでは上位50件を取得できました。復活ランキングには後者の情報量が有用です。
- 現状の市場データ収集MCPは、任意の現物株の現在板を取得できません。個別株の現在時点基準の入口・出口には追加実装が必要です。
- 直近限月を両脚で個別に解決すると、日経225miniは2026年8月限、ミニTOPIXは2026年9月限になりました。NT取引では必ず明示的に同じ限月を解決する必要があります。
- EDINET DBとJ-Quantsで同じ開示のEPSが一致しない実例がありました。数値を平均せず、定義差を解決できない場合は数値予測の信頼度を下げるか停止します。
- 政策DBの金額は、同じ政策テーマ名でもエンドポイントごとに母集団と定義が異なります。横断加算、比率化、企業売上・利益への読み替えは禁止です。
- EDINET DBの`get_fair_value`は、ツール自身の条件により価格目標、現在株価との比較、ランキング、売買推奨へ転用できません。今回の4 SKILLでは使用しません。

## 3. 鮮度の共通管理

すべてのSKILLで、少なくとも次の時刻を分離して記録します。

| 項目 | 意味 |
|---|---|
| `as_of` | 分析の基準時刻 |
| `source_at` | 板時刻、約定時刻、対象日、週末日、開示日時、採択日など、データ自身の基準時刻 |
| `collected_at` | MCPが取得を完了した時刻 |
| `published_at` | ニュース・開示の公表時刻 |
| `fetched_at` | 上流またはNewsStoreが取得した時刻 |
| `market_state` | 現物・先物が取引中、休場、引け後、清算・SQ付近のいずれか |
| `stale_reason` | 古い、時刻不明、片側板なし、限月不一致などの失効理由 |

推奨する最低ルールは次のとおりです。

- 売買入口に使う先物板は、出力直前に再取得します。60秒を超えたら警告し、5分を超えたら入口提示に使いません。
- 取引時間中のランキングは5分を超えたら再取得します。引け後は最終終値との一致を確認します。
- 日足は最新営業日の`Date`を確認します。夜間に取得しても翌日の日足ではありません。
- 信用残は基準週末日、空売り残高は計算日と公表日、投資部門別は対象週と公表日を明記します。
- 財務は決算期、対象期間、開示日時を明記します。
- 政策は年度、採択日、支出定義を明記します。
- null、空配列、ゼロを相互に読み替えません。

## 4. ニュース取得ツール

### 4.1 取得できるもの

| ツール | 取得内容 | 主な用途 |
|---|---|---|
| `list_sources` | ソースID、名称、種別、有効状態 | 検索前のソース確認 |
| `search_articles` | 見出し、本文500文字以内の抜粋、URL、記事ID、公開・更新・取得時刻、改訂ID | 直近材料の探索、候補記事の絞り込み |
| `get_article` | 記事本文、オフセット、続きの有無、改訂情報 | 候補記事の詳細確認 |

実測時に有効だった8ソースは次のとおりです。

- ロイター日本
- ロイター経済
- Yahooニュース 経済
- Yahooニュース IT
- Yahooニュース 科学
- Yahooニュース 国際
- Yahooニュース 国内
- ITmedia NEWS 新着記事

### 4.2 実測した挙動

- `日経平均`は指定期間で10件取得でき、続きがありました。
- 半角`TOPIX`では0件、全角`ＴＯＰＩＸ`では7件でした。表記揺れを別検索する必要があります。
- 同一のロイター記事が別IDで保存され、`canonical_url`と`external_id`が同一の例がありました。
- 同じ記事の公開からNewsStore取得まで、実例では約6分でした。別レコードではより早い取得もありました。
- 本文は分割取得できます。検索段階で全記事本文を取る必要はありません。

### 4.3 使い方と制約

- 検索語は最大5語のAND条件です。指数、為替、金利、半導体、銘柄名などは別検索に分けます。
- `canonical_url`または`external_id`で重複排除し、`updated_at`と`revision_id`が新しい版を残します。
- ニュース0件を「材料なし」と判定しません。
- 現在のソース群に企業の公式IRは含まれません。ニュースは原因仮説の探索に使い、決算・増資・自社株買い・業績修正などはEDINET DBまたは会社原本で確認します。
- 記事の論調を価格予測へ直接点数化せず、事実イベントを抽出し、同じ材料をEDINETと二重加点しません。

## 5. 市場データ収集

`datalist`を実行すると、6プロバイダーが有効でした。SKILL実行時も最初に`datalist`を確認し、固定したデータセット名が今も有効かを確認します。

### 5.1 利用可能な全データセット

| プロバイダー | データセット |
|---|---|
| 225225.jp | `catalog`, `current`, `chart`, `japan_components`, `japan_contributors`, `japan_industries`, `japan_ranking`, `us_equities`, `us_industries`, `us_ranking`, `adr`, `fx_rates`, `crypto_assets` |
| J-Quants | `equities_master`, `equities_bars_daily`, `equities_investor_types`, `markets_margin_interest`, `markets_short_ratio`, `markets_short_sale_report`, `markets_margin_alert`, `markets_calendar`, `indices_bars_daily`, `indices_bars_daily_topix`, `fins_summary`, `fins_earnings_date`, `equities_earnings_calendar`, `derivatives_bars_daily_options_225`, `edinet_major_shareholders`, `edinet_cross_shareholdings`, `edinet_large_volume_shareholders`, `bulk_list`, `bulk_get` |
| KabusController | `future_registrations`, `option_registrations`, `market_data`, `future_market_data`, `option_market_data`, `symbol_market_data` |
| Polymarket | `search`, `events`, `event`, `markets`, `market`, `order_book`, `token_price`, `price_history`, `user_positions`, `user_activity`, `trades`, `closed_positions`, `holders`, `market_positions`, `position_value`, `traded_markets_count`, `open_interest`, `live_volume`, `leaderboard`, `tags`, `tag`, `related_tags`, `series`, `series_item`, `sports`, `sports_market_types`, `teams`, `comments`, `public_profile`, `server_time`, `spread`, `tick_size`, `fee_rate`, `negative_risk`, `clob_markets`, `clob_market`, `market_by_token` |
| yfinance | `quote`, `history`, `actions`, `financials`, `analysis`, `holders`, `options`, `news`, `search`, `download` |
| investpy | `search`, `recent`, `historical`, `information`, `overview`, `economic_calendar`, `technical_indicators`, `moving_averages`, `pivot_points` |

### 5.2 225225.jp

#### 取得できるもの

- 日経225、TOPIX、グロース250、REIT、日本国債利回り、日本VI、NT倍率
- 日経先物・CFD、海外株価指数、為替、商品、暗号資産
- 短期チャートと日足履歴
- 日経225構成銘柄、ウェイト、寄与度
- 寄与度上位・下位
- 東証33業種
- 日本株・米国株の値上がり、値下がり、出来高ランキング
- ADR、PTS、東証価格の比較

#### 実測結果

2026年8月13日23:57 JST付近の例です。

| 項目 | 値 | データ自身の時刻 |
|---|---:|---|
| 日経225現物 | 68,308.59 | 8月13日 |
| TOPIX現物 | 4,176.04 | 8月13日 |
| 日経225mini表示 | 69,535 | 23:57 |
| NT倍率 | 16.36 | 8月13日 |
| 日本VI | 31.65 | 8月13日 |

上流の`Last-Modified`は取得秒と一致し、`cache-control`はトップ画面10秒、ランキング30秒でした。ただし各系列の最終点は別時刻です。

- 夜間の日経先物チャートは取得秒近くまで更新されていました。
- 日経・TOPIX現物は15:30で停止していました。
- `section=japan`側の日経mini日中系列は15:45で停止していました。
- 夜間先物には`section=nikkei_futures`を使用する必要があります。
- 東証33業種と日経寄与度を併用すると、市場全体の広がりと値がさ株集中を分離できます。

#### ランキング

`japan_ranking(kind=losers, limit=30)`は、指定にかかわらず20件でした。先頭は4419 Finatext Holdings、1,269円、-23.09%でした。各行の`market_time`は`08/13`だけで時刻がないため、外側の`collected_at`、`fetched_at`、`last_modified`で鮮度を判断します。

J-Quantsの当日全銘柄データから普通株かつ終値・出来高・売買代金がある銘柄だけで再計算すると、上位20銘柄の順位、下落率、終値は一致しました。速報候補抽出には使えますが、全市場ランキングの母集団説明にはJ-Quants再計算が必要です。

#### 注意点

- `chart`の`max_points_per_series=10`は「直近10点」ではなく、全期間を間引いた10点になる場合があります。期間を`from_millis`で先に限定します。
- 現物、日中先物、夜間先物を同じ「現在値」として混ぜません。
- 二次配信なので、執行価格にはKabusControllerの板を優先します。

### 5.3 KabusController

#### 取得できるもの

- 登録済み先物・OP銘柄一覧
- 登録済み先物・OPの現在値、前日終値、OHLC、出来高、売買代金、VWAP
- 10本の買い・売り気配と数量
- OPの計算価格、IV、Delta、Gamma、Vega、Theta
- 登録済み1銘柄を指定した板

実測時の登録は先物11銘柄、OP34銘柄でした。先物は日経225、mini、micro、TOPIX、ミニTOPIX、グロース250、JPX400、NYダウ、日経VI、Core30、REITを含みました。

#### 鮮度

23:57:52 JSTの取得例では次のとおりでした。

| 銘柄 | 現在値 | 現在値時刻 | 取得時との差 |
|---|---:|---|---:|
| 日経225mini 2026年9月限 | 69,535 | 23:57:52 | ほぼ0秒 |
| 日経225micro 2026年9月限 | 69,540 | 23:57:52 | ほぼ0秒 |
| ミニTOPIX 2026年9月限 | 4,217.5 | 23:57:32 | 約20秒 |

別観測も含め、主要先物は取得時との差が0～38秒でした。これは現在時点基準の入口価格に使える鮮度です。ただし、取引所ティック直結の遅延保証を示すものではないため、出力直前に再取得します。

#### 板フィールドの重要な注意

返却例では、トップレベルの`AskPrice`が`Buy1.Price`、`BidPrice`が`Sell1.Price`と一致しました。一般的なBid/Askの名称感覚と逆です。

- 買い気配は`Buy1.Price`
- 売り気配は`Sell1.Price`

を正として使い、トップレベルの`AskPrice`と`BidPrice`から売買方向を推測しません。

#### OPの注意

23:57の登録OP34銘柄は、`CurrentPrice=null`、`Buy1=0`、`Sell1=0`でした。一方で`CalcPrice`、IV、Greeksは非nullでした。

したがって、次をすべて満たさないOPは「現在の市場価格」や「現在の市場IV」として使いません。

- 現在値または両側板がある
- 板時刻が新しい
- 出来高または建玉が十分にある
- 限月、SQ、権利行使価格が分析対象に合う

### 5.4 J-Quants

#### 取得できるもの

| 分類 | 主な内容 | 4 SKILLでの用途 |
|---|---|---|
| 銘柄マスター | 銘柄コード、名称、市場、商品区分、業種 | コード正規化、普通株・ETF等の除外 |
| 株価四本値 | OHLC、出来高、売買代金、調整OHLC・出来高、調整係数、時価総額、値幅制限、権利落ち | リターン、ATR、流動性、支持抵抗、バックテスト |
| 指数日足 | 日経系指数、TOPIXのOHLC | 指数履歴、NT履歴、類似局面 |
| 財務サマリー | 売上、利益、EPS、配当、会社予想、開示日時 | 業績モメンタム、会社予想、指数寄与銘柄分析 |
| 決算予定 | 決算発表日・時刻 | イベントリスク、保有期限 |
| 信用 | 週末信用残、日々公表信用残 | 需給と踏み上げ・投げリスク |
| 空売り | 33業種別比率、主体別残高報告 | 業種需給、報告対象主体の残高 |
| 投資部門別 | 海外、個人、信託銀行等の売買 | 中期的な市場需給 |
| OP日足 | 銘柄別OHLC、拡張OHLC、出来高、建玉、清算値、理論値、IV、原資産価格、金利、SQ、権利行使価格 | 過去の日次ボラティリティ面、建玉・出来高分布 |
| EDINET派生 | 大株主、政策保有株、大量保有 | 所有構造、需給・ガバナンス |
| Bulk | 配布キー一覧、署名付き取得URL | 大規模な再計算・履歴取得 |

#### 実測結果

- 23:57取得のTOPIX日足には8月13日のOHLCがあり、終値は4,176.04でした。
- 7203トヨタの日足にも8月13日終値がありました。引け後の同日分として使えますが、リアルタイム価格ではありません。
- 7203の`fins_summary`は41件で、最新は2026年8月4日14:00開示の2027年3月期1Qでした。
- 7203の信用週末残高は8月7日が最新でした。
- 8月13日時点の投資部門別最新は、対象週終了7月31日、公表8月6日でした。
- 日経225 OP日足は8月13日分だけで10,534銘柄行でした。OHLC、出来高、建玉、IV、理論価格などが取れましたが、売買のない行も多数あります。
- 8月13日の全銘柄日足は4,443件、普通株かつ終値・出来高・売買代金が非nullの対象は3,657件でした。

#### J-Quantsの更新タイミング

公式の更新目安は次のとおりです。時刻は保証ではなく変更される可能性があるため、実データの基準日も確認します。[J-Quants公式「提供データの更新タイミング」](https://jpx.gitbook.io/j-quants-pro-ja/data-update)

| データ | 頻度 | 公式の更新目安 |
|---|---|---|
| 上場銘柄一覧 | 日次 | 17:30頃、翌営業日8:00頃 |
| 株価四本値 | 日次 | 16:30頃 |
| 投資部門別 | 週次 | 第4営業日18:00頃 |
| 日々公表信用残 | 日次 | 16:30頃 |
| 信用週末残高 | 週次 | 第2営業日16:30頃 |
| 空売り残高報告 | 日次 | 17:30、18:00、19:00頃のいずれか |
| 財務情報 | 日次 | 18:00頃の速報、24:30頃の確報 |
| 決算発表予定 | 日次 | 10:00頃 |
| デリバティブ参加者別取引高 | 日次 | 17:30頃 |
| デリバティブ銘柄別建玉 | 日次 | 21:15頃 |

ここから導く実装ルールは次のとおりです。

- `collected_at`が23:58でも、OP日足は8月13日の日次データです。
- 信用週末残高や投資部門別を当日23:58の需給として扱いません。
- 更新予定時刻前の当日欠損は異常とは限りません。
- 更新予定時刻後でも基準日が古ければ、基準日を表示して信頼度を下げます。

#### 欠損とコードの注意

- 無商い銘柄の終値はnullです。nullを0にすると偽の-100%下落になります。
- 普通株に限定する場合は商品区分を確認します。
- J-Quantsの5桁コード例`72030`と画面用4桁コード`7203`を、末尾削除だけで雑に結合しません。銘柄マスターを経由します。
- Q1～Q3の実績は累計、会社予想は通期です。`実績÷予想`は「通期予想に対する進捗率」であり、サプライズ率ではありません。
- OP日足は`Vo`、`OI`、`LTD`、`SQD`、`CM`、`Strike`を確認し、ゼロ出来高の理論値・IVを現在市場の合意とみなしません。

### 5.5 yfinance

株価、履歴、コーポレートアクション、財務、アナリスト分析、保有者、OP、ニュース、検索、一括ダウンロードが取得できます。

実測では次の問題がありました。

- `^N225`は20分遅延表示でした。
- 5分足の最終点は15:25で、15:30の正式終値を含みませんでした。
- `^TOPX`は2019年時刻の`MUTUALFUND`種別を返し、TOPIXとして利用できませんでした。

したがって、日本指数・日本株では補助的クロスチェックに限定し、シンボル名だけで採用しません。

### 5.6 investpy

検索、直近・履歴価格、基本情報、市場概況、経済指標カレンダー、テクニカル、移動平均、ピボットを取得できる契約です。しかし実測では代表的な`search`と`recent`が`INVALID_ARGUMENT`でした。

現時点ではSKILLの必須経路にせず、失敗時に値を推測せず別ソースへ切り替えます。

### 5.7 Polymarket

イベント、予測市場、板、価格履歴、出来高、建玉、スプレッド、手数料、公開ウォレットのポジションなどを取得できます。日本株の直接価格ではありません。

金融政策、選挙、地政学などの補助的なイベント確率として使える余地はありますが、薄い市場や曖昧な決済条件の確率を株価確率へ直接変換しません。今回の4 SKILLでは任意の低ウェイト補助データとします。

## 6. EDINET DB

### 6.1 取得できるデータ群

| 分類 | 主なツール・内容 | 使い方 |
|---|---|---|
| 会社特定 | `search_companies`, `search_companies_batch`, `get_company`, `get_company_history`, `get_corporate_profile` | 銘柄コード、EDINETコード、会社名の正規化 |
| 財務時系列 | `get_financials`, `get_earnings`, `get_earnings_calendar`, `get_detailed_expenses` | 年次財務、最新決算、会社予想、費用構造 |
| 事業構造 | `get_segments`, `get_order_backlog`, `get_facilities`, `get_real_estate`, `get_main_customers` | 成長源、受注残、設備投資、顧客集中 |
| 開示イベント | `get_events`, `get_ir_documents`, `get_ir_pdf_url`, `list_ir_document_types` | 決算、増資、還元、業績修正、原本確認 |
| テキスト・KPI | `get_text_blocks`, `get_text_blocks_structured`, `get_ir_sections_by_company`, `get_ir_kpis_by_company`, `search_ir_kpis`, `search_ir_sections`, `search_qa_sections` | リスク、MD&A、戦略、KPI、質疑応答 |
| 株主・保有 | `get_major_shareholders`, `get_shareholders`, `get_shareholder_history`, `get_shareholder_categories`, `get_cross_shareholdings`, `get_activist_positions`, `search_shareholders` | 所有構造、政策保有、アクティビスト、需給 |
| ガバナンス | `get_directors`, `get_director_compensation`, `get_compensation_text`, `get_related_party_transactions` | 経営体制、報酬、関連当事者リスク |
| 企業関係 | `get_subsidiaries`, `get_gleif_subsidiaries`, `get_parent_company`, `get_parent_companies`, `get_appearances` | 親子関係、グループ露出 |
| スクリーニング | `screen_companies`, `get_ranking`, `compare_companies`, `get_industry_benchmark` | 同業比較、候補抽出 |
| ナレッジグラフ | `get_kg_company_summary`, `get_kg_kpi_track_record`, `search_kg_kpi_commitments`, `search_kg_strategies`, `find_peer_strategies` | 戦略・KPIの証拠付き整理 |
| 個人管理機能 | watchlist、dashboard、analysis、notification、data request | ユーザーが明示的に求めた場合だけ使用 |

### 6.2 実測例

三菱重工業で代表ツールを確認しました。

- 年次財務はFY2021～FY2026を取得でき、docID、提出日時、EDINET原本URLがありました。
- 最新決算は2026年8月4日のFY2027 1Qまで取得できました。
- セグメントはFY2025、受注残はFY2020～FY2025を取得できました。
- IR KPIは原文引用位置、ページ、confidenceを持つ一方、論理的に重複する行がありました。
- 受注残はLLM抽出を含むため、原本・引用・coverageを確認する必要があります。

下落銘柄では次の一次情報をニュース0件でも取得できました。

- 4419は8月12日1Q決算を確認できました。
- 6141は8月12日の海外募集による新株式発行と発行条件決定を確認できました。

`get_ir_documents`が空でも、`get_earnings`や`get_events`に開示がある実例がありました。単一ツールの空を「開示なし」とみなしません。

### 6.3 数値の不整合

同じ2026年8月4日開示の三菱重工1Qについて、EDINET DBの`get_earnings`はEPS 38.45、J-Quantsの`fins_summary`はEPS 40.08でした。売上と純利益は一致しました。

この種の差異には、加重平均株式数、希薄化、会計上の定義、調整処理などの可能性があります。SKILLでは次の順序を必須にします。

1. 対象期間、連結・単体、会計基準、単位を一致させる。
2. 純利益と加重平均株式数から算術検算する。
3. EDINET原本または決算短信PDFを確認する。
4. 解決できなければ両値と差を表示し、平均しない。
5. 価格目標、PER、EPS成長を使う数値予測は格下げまたは停止する。

### 6.4 重要な禁止事項

- `get_fair_value`の返り値を価格目標、現在株価との割高・割安比較、銘柄ランキング、売買推奨に使いません。これはツール自身の利用条件です。
- Q1～Q3累計実績と通期会社予想をサプライズ比較しません。
- EDINET内の時価総額、PER、PBRなどが提出時点ベースの場合、リアルタイム値とみなしません。
- AI抽出値は、原文引用・ページ・confidenceがない状態で主要スコアに使いません。

## 7. 政策DB

### 7.1 取得できるデータ群

| 分類 | 主なツール・内容 | 使い方 |
|---|---|---|
| 政策テーマ | `get_policy_themes`, `search_policy_themes`, `get_policy_theme_companies`, `search_policy_company_links` | 半導体、AI、防衛、GX、宇宙等と企業の接点探索 |
| 資金規模・経路 | `get_policy_theme_funding_scale`, `get_policy_budget_reach`, `get_policy_funding_path_mix`, `get_policy_gap_overview`, `get_ministry_yearly_cross`, `get_passthrough_agencies` | 予算規模と資金経路の背景理解 |
| 会社別公的資金 | `get_company_policies`, `get_company_public_funding`, `get_recent_funding`, `get_yoy_funding_change`, `get_top_listed`, `get_top_listed_by_policy` | 調達、補助、会社と政策の接点 |
| 法人・行政支出 | `get_houjin_cross_db_profile`, `get_houjin_public_procurement`, `get_houjin_public_spending`, `get_houjin_collection_funding`, `get_houjin_national_municipal_funding`, `get_spending_by_type`, `get_spending_top_by_entity` | 法人番号ベースの公的支出確認 |
| 経済安保・研究 | `get_economic_security_certifications`, `search_research_funds_by_theme`, `search_subsidies_open` | 認定、研究テーマ、公募中補助金 |
| 政策文書 | `get_white_paper_extracts`, `search_laws`, `search_diet_bills`, `search_diet_speeches`, `get_diet_top_speakers` | 市場拡大の制度根拠と変更リスク |
| 地方自治体 | `get_jichitai_plan`, `get_jichitai_shien`, `get_jichitai_spending`, `get_jichitai_ordinances`, `get_jichitai_ordinance_fulltext` | 地方施策、支援、条例、支出 |
| 税・GPIF | `list_tax_expenditure_measures`, `get_tax_expenditure`, `get_gpif_holdings`, `get_gpif_es_material_summary` | 税制支援、機関保有、ESG資料 |

### 7.2 実測した定義差

同じ「防衛」テーマでも、次の数値になりました。

| エンドポイント | 実測値 | 主な定義 |
|---|---:|---|
| `get_policy_themes` | 632.4億円 | テーマ集計 |
| `get_policy_theme_companies`の三菱重工 | 716.4億円 | 会社別corporate-net |
| `get_policy_budget_reach` | 3,627.5億円 | 別母集団のcorporate-net |
| `get_policy_funding_path_mix` | 633.1億円 | 資金経路別集計 |

これは単純なデータ誤りとは限らず、別テーブル、別期間、別マッチング、別金額基準です。同名テーマだからといって横断整合する総額ではありません。

さらに、`get_company_policies.total_oku`は行政事業レビューの事業単位支出で、会社が支出先として記録された金額です。下請けへの通過分を含み得ます。`get_company_public_funding`のaward型調達・補助金額とは別物です。

### 7.3 使い方と制約

- 金額を横断加算しません。
- 異なるエンドポイント間で割り算して到達率や依存率を作りません。
- 受注額、公的支出、補助金を売上、利益、企業価値へ読み替えません。
- テーマに出る会社を、そのテーマの専業・代表銘柄とみなしません。
- `endpoint`、`amount_basis`、対象期間、法人番号・証券コード、原典を証拠台帳に残します。
- 会社売上に対する規模を算出する場合も、同じ会社に帰属するaward型金額など、比較可能性を確認した指標だけを使います。
- 8月13日に「直近」を取得しても最新採択日が5月14日の例がありました。短期予測には低ウェイト、中長期テーマ評価に使います。
- 市場のパイ拡大を示すには、政策支出だけでなく、白書、統計、業界市場規模、需要量、設備計画など独立したTAM証拠が必要です。

## 8. ユーザー指定のkabuステーション互換API調査

この節の直接アクセスは調査専用です。SKILLにはサーバーURLや直接リクエストを書かず、必要なものを市場データ収集MCPへ実装する前提です。

### 8.1 読み取り専用で確認したもの

| API | 実測した返り値 | SKILLで必要な理由 |
|---|---|---|
| `/ranking?Type=2&ExchangeDivision=ALL` | 値下がり率50件、現在値、下落率、時刻、出来高、売買代金、市場、業種、順位推移 | 復活ランキングの母集団と流動性評価 |
| `/exchange/usdjpy` | Bid、Ask、スプレッド、時刻 | 指数・輸出株の現在為替 |
| `/symbolname/future` | 商品・限月から銘柄コードを解決 | 同限月のNT両脚作成 |
| `/symbolname/option` | 限月、Call/Put、権利行使価格、ATMから銘柄コード解決 | 現在のOP面作成 |
| `/regulations/{symbol}` | 規制・空売り規制 | 急落銘柄の取引制約とリスク除外 |
| `/apisoftlimit` | 株、信用、先物、mini、micro、OPの上限、kabuステーション版 | 登録上限と収集設計 |

実測したランキングは50件で、4419 Finatextが1位、1,269円、-23.09%、15:30でした。225225.jpの20件より、出来高・売買代金・市場・業種も含むため復活ランキングに適しています。

4419の規制情報では空売り規制を取得できました。これは下落率と価格履歴だけでは判断できない実務上の情報です。

### 8.2 同限月問題

`DerivMonth=0`で個別に解決した結果は次のとおりでした。

- 日経225mini: 2026年8月限
- ミニTOPIX: 2026年9月限

したがって、NT取引で両脚に`0`を指定してはいけません。分析時に共通して取引可能な限月を明示し、両脚の銘柄コード、取引最終日、板、出来高を確認する必要があります。

### 8.3 仕様上は取得可能だが直接実行しなかったもの

- `/board/{symbol}`: 現物・先物・OPの板。仕様上、情報取得時に銘柄登録が発生し得るため未実行。
- `/symbol/{symbol}`: 銘柄情報、追加情報。情報取得時に登録が発生し得るため未実行。
- `/primaryexchange/{symbol}`: 優先市場。
- `/margin/marginpremium/{symbol}`: プレミアム料。

注文、取消、登録、登録解除、口座余力、注文一覧、建玉一覧は、分析データ収集の対象外です。

## 9. 市場データ収集MCPへの追加提案

### P0: 4 SKILLの安全性に直結

1. `kabus-ranking`
   - `Type=1～15`と市場区分を受け取る読み取り専用データセット
   - 値下がり率50件、出来高、売買代金、業種、時刻、順位推移を返す
   - クリア時間帯と信用ランキング更新日をメタデータに含める

2. `kabus-regulations`
   - 現物銘柄の取引規制・空売り規制を取得
   - 復活候補の除外・警告条件に使う

3. `derivative-symbol-resolver`
   - 商品、明示限月、Call/Put、権利行使価格、週次を受け取る
   - NT両脚が同限月かをサーバー側でも検証する
   - `DerivMonth=0`の両脚不一致をエラーまたは警告として返す

4. `arbitrary-board-snapshot`
   - 任意の現物・先物・OP板を読み取り専用で返す
   - 内部で登録が必要なら、上限管理、既存登録保護、自動解除、失敗時清掃をサーバー側で制御する
   - SKILL側から登録・解除APIを直接呼ばせない

### P1: 予測精度と出口設計を改善

5. `option-chain-snapshot`
   - 指定限月のATM周辺をまとめ、現在値、両側板、時刻、出来高、建玉、IV、Greeksを返す
   - 片側ゼロ、時刻なし、出来高なしを明示する

6. `kabus-symbol-info`
   - 銘柄基本情報、優先市場、発行株式数、時価総額、決算期を返す
   - J-Quants銘柄マスターとのコード整合を付ける

7. `kabus-fx-snapshot`
   - USD/JPY等のBid、Ask、スプレッド、時刻を返す

8. `kabus-margin-premium`
   - 対象銘柄のプレミアム料を取得し、ショート可能性・コストを警告する

### P2: 運用監視

9. `kabus-api-capacity`
   - ソフトリミット、現在登録数、残枠、バージョンを返す

10. すべてのリアルタイム系返り値に共通メタデータを追加
    - `source_at`
    - `collected_at`
    - `age_seconds`
    - `market_state`
    - `contract_month`
    - `is_stale`
    - `stale_reason`

## 10. 4 SKILLへの適用表

| データ | 指数・NT | 復活ランキング | 中長期成長 | 特定銘柄予測 |
|---|:---:|:---:|:---:|:---:|
| KabusController先物板 | 必須 | 補助 | 不要 | 指数連動の地合い |
| 現物株の任意板追加 | 補助 | 必須 | 出口時に有用 | 必須 |
| 225225.jp市場概況 | 必須 | 地合い | 地合い | 地合い |
| 225225.jp日経寄与・33業種 | 必須 | 業種比較 | 業種比較 | 指数感応度 |
| J-Quants日足・銘柄マスター | 必須 | 必須 | 必須 | 必須 |
| J-Quants信用・空売り | 需給補助 | 必須 | 補助 | 需給補助 |
| J-Quants OP日足 | ボラ履歴 | 不要 | 不要 | イベントリスク補助 |
| ニュース | 必須 | 原因探索 | テーマ更新 | 必須 |
| EDINET最新決算・イベント | 寄与銘柄 | 必須 | 必須 | 必須 |
| EDINET年次・セグメント・KPI | 寄与銘柄 | 財務健全性 | 必須 | 必須 |
| 政策DB | 低ウェイト | 低ウェイト | 重要な補助 | 該当時のみ |
| 独立したTAM・市場規模証拠 | 補助 | 不要 | 必須 | 中長期時に必要 |

### 10.1 日経225・TOPIX・NT

実装可能です。現在時点の基準価格は、現物取引中なら現物の最新値、引け後や夜間なら同限月の取引可能な先物板を使います。前日終値は当日説明にだけ使い、予測起点にはしません。

NT枚数は毎回、同限月の板とJPX公式乗数から再計算します。現行の商品仕様は、日経225miniが指数×100円、日経225microが指数×10円、ミニTOPIXが指数×1,000円です。仕様変更に備え、実行時にJPX公式を確認します。[日経225mini](https://www.jpx.co.jp/derivatives/products/domestic/225mini/01.html)、[日経225micro](https://www.jpx.co.jp/derivatives/products/domestic/225micro-futures/)、[ミニTOPIX](https://www.jpx.co.jp/derivatives/products/domestic/mini-topix-futures/01.html)

不足は、同限月解決と任意限月・OP面の動的取得です。

### 10.2 復活可能性ランキング

実装可能です。既存`work/rank-japan-stock-rebounds`は考え方の参考に留め、新SKILLは次を追加します。

- 速報ランキングをJ-Quants当日全銘柄で再計算
- 普通株、終値・出来高・売買代金非null、取引規制、整理・監理銘柄などのフィルター
- 急落原因をEDINETイベントと会社原本で確認
- 出来高・売買代金の直近中央値比、ATR、窓、支持帯、急落前価格
- 信用・空売りは基準日を分けて評価
- 入口、損切り、第1・第2利確、時間撤退、材料撤退
- 翌日ギャップと1ティック比率が大きい低位株を別扱い

不足は、50件ランキング、任意現物板、規制情報です。いずれも上記P0追加で補えます。

### 10.3 市場拡大・中長期成長銘柄

実装可能ですが、政策予算だけで「市場のパイ拡大」を証明できません。

最低でも次の4層を別スコアにします。

1. 市場規模・需要量・設備計画の独立証拠
2. 会社の売上成長、利益率、CF、投資、受注残、セグメントKPI
3. 競争優位、顧客集中、規制、希薄化、ガバナンス
4. 政策・補助・認定は追い風の補助証拠

政策DBの白書・法令・研究費は有用ですが、TAM時系列が常に揃うわけではありません。必要時は公式統計や業界一次資料をWebで補います。

出口は、業績前提の破綻、KPI鈍化、受注残減少、利益率低下、バリュエーション上限、時間経過を組み合わせます。単一の固定目標株価にしません。

### 10.4 特定銘柄の株価予測

日次基準のシナリオ予測は現状でも可能です。ただし、実行時点の現在価格から入口・出口を出すには任意現物板が必要です。

予測は点推定ではなく、1・5・20営業日などの期間別に、弱気・基本・強気の価格帯、確率または信頼度、無効化条件を提示します。数値ソース不一致、決算直前、板欠損、流動性不足では具体的価格の信頼度を下げます。

## 11. 全SKILL共通の品質ルール

1. 最初に`datalist`とニュースソース一覧を確認する。
2. 銘柄コードはマスターを経由して、4桁、5桁、EDINETコード、法人番号を対応付ける。
3. 現在価格、日足、週次需給、開示、政策を同じ時点として混ぜない。
4. null、ゼロ、対象外、未公表、取得失敗を区別する。
5. 同一記事、同一開示、同一政策イベントの二重加点をしない。
6. 二次情報で原因候補を探し、重要事実は一次情報へ遡る。
7. 数値の期間、単位、連結・単体、会計基準を検算する。
8. 複数ソースが不一致なら、差異を表示し、平均しない。
9. 実行可能な板がない場合、具体的な入口価格を出さない。
10. 予測には必ず有効期限と再取得条件を付ける。
11. 手数料、スプレッド、スリッページ、証拠金、片脚約定を無視して利益を断定しない。
12. 自動発注、ナンピン、保証表現を行わない。

## 12. SKILL作成方針

OpenAI公式では、SKILLは繰り返し可能なワークフローを教える指示・リソースのフォルダであり、MCPはライブデータを提供する層です。今回も、直接APIアクセスや認証・登録管理をSKILLへ埋め込まず、MCPがデータ取得を担当し、SKILLが取得順、鮮度判定、検算、スコア、出口、出力形式を担当する構成にします。[OpenAI公式「Skills」](https://developers.openai.com/plugins/concepts/skills)、[OpenAI公式「Build skills」](https://developers.openai.com/plugins/build/skills)

作成候補名は次の4つです。

- `pnd-forecast-japan-index-nt`
- `pnd-rank-japan-stock-rebounds-v2`
- `pnd-rank-japan-growth-markets`
- `pnd-forecast-japan-stock`

次段階では、この調査文書を共通根拠として、各フォルダをこのプロジェクト直下に作成します。
