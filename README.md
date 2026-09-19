# ComfyUI-Jev

自然文を、ユーザー定義の意味スキーマに沿って解釈し、ComfyUIの制作パラメータへ変換します。

**Field → Interpret → Resolve → Read** の順に接続します。意味の定義と実際の値の対応表は別なので、画像・動画・音楽などに同じ仕組みを使えます。Jevは文章や画像を生成しません。画像・音声・動画は、別ノードの説明文や解析結果を入力してください。

## 導入

ComfyUI v0.36.0 / frontend 1.52.7で検証しています。このフォルダーを `custom_nodes/ComfyUI-Jev` に配置し、ComfyUIを起動するプロセスの環境変数に `TYPESAFE_API_KEY` を設定して再起動してください。追加のpipインストールは不要です。

```sh
export TYPESAFE_API_KEY="your-key"
# 同じ環境から、通常の方法でComfyUIを起動
```

ランチャーから起動する場合は、そのランチャーの環境変数設定を使います。`.env`は自動読込しません。APIキーはノードやワークフローに保存しません。

`Jev Interpret` の実行時に、入力状態・質問・使用する候補情報をTypeSafe APIへ送信します。モデル一覧ノード自体はローカル処理ですが、接続した候補のファイル名・説明は質問の一部になります。自動モデルダウンロードや起動時の問い合わせはありません。

## 最初の使用例

`examples/01_brief_to_parameters.workflow.json` をComfyUIに読み込みます。API形式の `*.api.json` もComfyUIのファイル読み込みに対応しています。

「縦長。動きは控えめ。長さは8秒、24fpsで。」から以下を別々に判断し、1リクエストにまとめます。

- `aspect`：単一選択 → 幅720・高さ1280などの寸法辞書
- `motion`：程度 → `0〜0.6` の強度
- `duration`：原文抽出 → 整数の秒数

Read Intの幅・高さは標準のEmpty Latent Imageへ接続済みです。強度と秒数は制作ノードへ接続できます。画像生成モデルは不要で、例の出力は値と空のlatentのプレビューです。

| ファイル | 内容 |
| --- | --- |
| `01_brief_to_parameters.api.json` | Fieldノードの組み立て、Autogrow、3種類の解釈と数値接続 |
| `02_asset_matching.api.json` | 説明付き候補から素材と追加要素を選び、既存のテキストノードへ接続 |
| `02b_local_model_candidates.api.json` | ローカルLoRA一覧を候補にする。環境に合わせてglobと説明を設定 |
| `03_compare_concepts.api.json` | 制作案の観点別評価、順位付け、重みの調整 |
| `04_staged_interpretation.api.json` | 最初の選択結果で次のスキーマをSwitchし、2段階で解釈 |

同名の `*.schema.json` と `*.bindings.json` は再利用用の定義です。ワークフローとして開かず、Interpret／Resolveの対応するJSON欄へ貼り付けます。例の題材、候補、質問、出力値は自由に交換できます。

## ノード

| ノード | 役割 |
| --- | --- |
| Jev Field | 1項目を定義。種類で入力欄が切り替わり、スキーマとJSONを出力 |
| Jev Interpret | 入力状態と複数のスキーマをまとめて解釈。判定結果とAPI応答JSONを出力 |
| Jev Resolve | 対応表・しきい値・既定値を適用。結果、値だけのDICT、値のJSONを出力 |
| Jev Read String / Int / Float / Boolean | 型付きの値を取得。Stringには同値のCOMBO出力もある |
| Jev Inspect Field | 値の有無、状態、判定詳細を取得 |
| Jev Model Candidates | ローカルのcheckpoints／diffusion_models／lorasを候補JSONへ変換 |
| Jev Rank | Score／Boolean項目を降順に並べる。指定順で同順位を解決 |
| Jev Weighted Score | Score／Boolean項目を重み付き平均 |

表示・保存は標準のPreview as Text／Save Text、数値計算はMath Expression、分岐はIf/Else Switchを使います。

## スキーマ

トップレベルは**項目ID → 定義**のJSONオブジェクトです。項目IDは結果の取得に使い、意味は`instructions`に書きます。JSONでは`instructions`と選択・評価基準に構造化された説明も使えます。

```json
{
  "texture": {
    "type": "choice",
    "instructions": "Which material best fits the visual brief?",
    "presence": "infer",
    "criteria": {
      "linen": "Natural woven fabric, soft tactile surface",
      "chrome": "Polished reflective metal, industrial surface"
    }
  },
  "motion": {
    "type": "score",
    "instructions": "How much movement is requested?",
    "criteria": ["Almost stationary", "Gentle movement", "Fast energetic movement"]
  },
  "duration": {
    "type": "extract",
    "instructions": "What duration in seconds is requested, not the frame rate?",
    "presence": "explicit"
  }
}
```

| `type` | 定義 |
| --- | --- |
| `choice` | `criteria`は候補ID → 説明のオブジェクト。説明不要ならnull |
| `multi_choice` | 同じ候補形式。候補ごとに独立したYes確率を求める |
| `boolean` | Yes/Noの質問。任意で`criteria: {"true": "...", "false": "..."}` |
| `score` | `criteria`は低→高の順に並べた評価基準の配列 |
| `extract` | 数値検出が既定。`pattern`で正規表現、`group`で抽出グループを指定可能 |

Fieldの`lines`形式では、Choice／複数選択は1行1候補ID、Scoreは1行1評価段階です。説明付き候補は`json`へ切り替えます。Model CandidatesはFieldの`criteria`へつなぎ、`format`を`json`にします。

`presence`の既定は`infer`（文脈から推定）。`explicit`では、その項目が入力に明記されているかを追加で判定します。存在しない項目の値を補うかどうかは、後段のResolveで設定します。

Interpretの`schema_json`と接続スキーマは結合されます。重複IDと未知の設定キーはエラーです。Autogrowは現行ComfyUIの上限である100接続まで表示されますが、JSON内の項目数にはこの上限を適用しません。

### 原文抽出

抽出候補はJevへ送る前にローカルで作ります。同じ文字列が複数回現れても位置を区別し、選択した箇所をそのままコピーします。候補なし／該当なしは未確定です。

- `source`：元データ内のJSON Pointer。通常のテキスト入力では空文字。JSON入力なら`/brief`など、文字列を指すパスを指定。
- `pattern`：Python正規表現。省略時は符号付き整数・小数・指数表記の検出。
- `group`：取得するグループ番号。既定0はマッチ全体。

```json
{
  "file": {
    "type": "extract",
    "instructions": "Which quoted filename is the requested output?",
    "source": "/brief",
    "pattern": "\"([^\"]+)\"",
    "group": 1
  }
}
```

「8秒」は8を候補にできますが、「八秒」や「2分」を自動で8／120に変換する機能はありません。単位換算は下流の計算ノードへ任せます。

## 値の対応表

対応表も**項目ID → 設定**です。Interpretの入力には入れず、Resolveに渡します。

```json
{
  "texture": {
    "values": {"linen": "woven linen, natural fibers", "chrome": "polished chrome"},
    "min_confidence": 0.6,
    "default": "matte paper"
  },
  "motion": {"range": [0, 0.6]},
  "duration": {"convert": "int", "default": 8}
}
```

| 設定 | 対象と動作 |
| --- | --- |
| `values` | Choice／複数選択の候補ID → 任意のJSON値。Booleanでは`"true"`／`"false"`キー。省略時は選択IDまたは真偽値 |
| `range` | Scoreの出力範囲。既定`[0, 1]`。降順範囲も可能 |
| `threshold` | Boolean／複数選択のしきい値。既定0.5、`>=`で選択 |
| `presence_threshold` | `explicit`の明記判定のしきい値。既定0.5 |
| `min_confidence` | Choice／Score／抽出の最低confidence。省略時は除外しない |
| `convert` | 抽出結果を`string`（既定）／`int`／`float`へ変換 |
| `default` | 未確定の場合に使う値。nullも明示的な既定値として使用可能 |

複数選択は候補の定義順の配列です。対応表を指定した場合、選択された候補のマッピングが欠けていればエラーになります。整数変換は小数を切り捨てません。

結果の`status`は`resolved`、`default`、`unresolved`。未確定項目は値だけのDICT／JSONから省略します。明示的なnullと未確定は別です。Inspect Fieldの`has_value`で分岐すれば、未確定側のReadを実行せずに既定経路へ進めます。

Readの`pointer`は確定した値の内側を指します。例：寸法辞書なら`/width`、配列の先頭なら`/0`。`/`と`~`を含むキーはそれぞれ`~1`と`~0`でエスケープします。

## キャッシュ・モデル・エラー

Interpretの`refresh`は**fixed／0**が既定です。入力が同じならComfyUIのキャッシュを使います。手動で値を変えると再問い合わせします。キャッシュはComfyUIの保持設定に従い、再起動やキャッシュ解放を越えて保持するものではありません。

Resolveの対応表、Rankの対象、Weighted Scoreの重みを変更しても、Interpretの入力は変わらないため再問い合わせしません。Model Candidatesの一覧が変わると下流も更新されます。

モデルは`jev-latest`が既定。`jev-preview`、`jev-1.13.0`、customで指定したIDも使えます。API応答JSONには実際のモデルIDとtoken usageを保持します。

429／529のみ最大2回再試行し、`Retry-After`を尊重します。1リクエストのタイムアウトは60秒。認証失敗、入力拒否、その他のHTTPエラー、回答欠落はエラーとして返します。原文や候補は自動で切り捨てません。APIのコンテキスト・候補数制限を超える場合は、フィルターや段階的な質問で分けてください。

Rank／Weighted ScoreはScoreを`score / (段階数 - 1)`で正規化し、BooleanはYes確率を使います。confidenceは加重しません。`explicit`の有無判定が0.5未満の項目は集計できません。重み付き平均は`Σ(value × weight) / Σweight`で、重み合計ゼロはエラーです。

## 検証

ComfyUIのPython環境から実行します。

```sh
../../venv/bin/python -m unittest discover -s tests -v
```

通常テストはAPI通信をモックします。ComfyUIの実際の型検証・実行エンジンを使い、使用例、Autogrow／DynamicCombo、旧形式COMBO入力への接続、遅延分岐、キャッシュとrefreshを検証します。実サービスの応答品質を検証するテストではありません。

設計の参考：[TypeSafe primitives](https://docs.typesafe.ai/primitives)、[原文候補の抽出](https://docs.typesafe.ai/cookbooks/pre_parsed_value_extraction_cookbook)、[Composite scoring](https://docs.typesafe.ai/patterns/composite-scoring)。
