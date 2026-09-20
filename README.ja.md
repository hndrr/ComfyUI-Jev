# [WORK IN PROGRESS] ComfyUI-Jev

[English](README.md) | 日本語

英語版のREADMEとサンプルワークフローを標準としています。このファイルは日本語版ドキュメントです。

文章から制作案を生成し、Jevで意図に合う案を選んで、既存のComfyUIワークフローへ渡すカスタムノードです。

文章生成の **OpenRouter Text**、判定の **Jev Interpret**、ローカルSkill選択の **Jev Skill Choice** を提供します。文章の結合・数値変換・分岐・画像生成は標準ノードを使います。スキーマや候補IDを手で書く必要はありません。

```text
Text → Format Text → OpenRouter Text (candidates)
  └─────────────────────────────┐
                               Jev Interpret → CLIP Text Encode → KSampler → VAE Decode → Save Image
```

OpenRouterの文章生成モデルが異なる制作案を出し、Jevが判断指示に沿って比較します。たとえば「上質だが冷たくない、手に取る日常を想像できる広告写真」という意図に、候補の光・素材・構図が合うかを判断します。

## 導入とAPIキー

ComfyUI v0.36.0のV3 APIを使用します。このディレクトリを`custom_nodes/ComfyUI-Jev`へ配置し、ComfyUIを再起動してください。独自JavaScriptや追加の依存ライブラリはありません。

OpenRouterを使う場合は、ComfyUIを起動する環境にキーを設定します。

```sh
export OPENROUTER_API_KEY="your-key"
```

`Jev Interpret`または`Jev Skill Choice`の`provider`を`openrouter`にすると、OpenRouter Textと同じ環境変数を使えます。各ノードの`api_key`欄へ直接入力しても使えます。直接入力が優先され、空欄なら環境変数を読みます。標準のTextノードから両方の`api_key`入力へ同じキーを接続することもできます。

TypeSafeへ直接問い合わせる場合は`Jev Interpret`の`provider`を`typesafe`にし、`TYPESAFE_API_KEY`を設定します。OpenRouter Textは常にOpenRouterを使います。

直接入力したキーはワークフロー・実行履歴にも保存されます。共有する場合はキーを消してください。環境変数を設定した後はComfyUIの再起動が必要です。

## OpenRouter Text

普通の文章生成にも、Jevへ渡す候補生成にも使えます。

| 入力 | 内容 |
| --- | --- |
| `prompt` | 自然文の生成指示。標準TextやFormat Textからも接続可能 |
| `system` | 任意の追加指示。空欄でも利用可能 |
| `output_mode = text` | 通常の文章生成。出力`text`をそのまま既存のSTRING入力へ接続 |
| `output_mode = candidates` | `count`個の異なる候補を生成。出力`text`をJev Interpretの`candidates_json`へ接続 |
| `model` | OpenRouterのカタログから文章生成モデルを選択。`custom`で任意IDも指定可能 |
| `api_key` | 空欄なら`OPENROUTER_API_KEY` |
| `refresh` | 固定値。変更すると再生成 |
| `temperature` / `max_tokens` | 詳細設定。生成のばらつき・最大出力トークン数 |

モデル一覧はComfyUI起動時に[OpenRouterのカタログ](https://openrouter.ai/docs/api/api-reference/models/list-all-models-and-their-properties)から取得します。一覧の取得にAPIキーは不要です。取得に失敗した場合は前回保存した一覧を使い、保存済みの一覧がなければ`custom`でIDを入力できます。一覧の更新にはComfyUIを再起動してください。

候補モードではノード内部でStructured Outputsを指定し、候補の配列をSTRINGとして出力します。**1行1候補という制約はありません。** 各候補の改行・段落・空白を保ちます。候補数の不一致、重複、空の候補、途中で終了した応答は明示的なエラーにします。

候補モードにはStructured Outputs対応のモデルが必要です。`custom`でも対応モデルを指定してください。通常の`text`モードではStructured Outputsを要求しません。出力`response_json`にはモデル名・使用量を含む元のAPI応答が入ります。

## Jev Interpret

`state`に判断対象や制作意図、`instructions`に判断指示を書きます。`choice`・`multi_choice`・`score`・`suggest`の候補は、次のどちらでも渡せます。

- **自動生成**：OpenRouter Textの`output_mode`を`candidates`にして、`text`を`candidates_json`へ接続。
- **手持ちの案**：標準TextノードなどのSTRINGを、Autogrowの`candidates.candidate0`以降へ接続。1接続が1候補で、複数行でもそのまま扱います。

両方を接続した場合は、個別接続の候補を接続番号順、その後に生成候補を並べます。候補の名前やスキーマの記述は不要です。

| `task` | 判断と出力`result` |
| --- | --- |
| `choice` | 候補から1つ選び、その候補の全文をそのままSTRING出力。CLIP Text Encodeなどへ直結可能 |
| `multi_choice` | 各候補を独立に判定し、該当した候補を定義順のJSON配列として出力 |
| `boolean` | 指示した条件への該当を判定し、`true` / `false`を文字列で出力。候補入力は不要 |
| `score` | 2つ以上の候補を「低い→高い」の評価段階として使用。0〜1の数値文字列を出力 |
| `extract` | `state`内の数値から指示に合うものを選び、元の文字列を出力。候補入力は不要。該当なしはエラー |
| `suggest` | 説明で順位付けし、上位候補の本文を再判定。適合した候補の値をJSON配列で出力。該当なしは`[]` |

booleanとmulti_choiceの`threshold`は既定0.5、比較は`>=`です。詳細設定から変更できます。scoreのconfidenceを評価値へ混ぜません。数値が必要なら標準のConvert Number、真偽による分岐にはCompare TextとSwitchなどを使います。

出力は`result`（STRING）、`details`（DICT、判定の詳細）、`response_json`（STRING、生のAPI応答）です。JSON形式の解析結果も、必要ならstateへ文字列として渡せます。画像・音声・動画を直接Jevへ送るノードではありません。

Jevモデルは既定`jev-latest`。TypeSafeでは`jev-preview`、`jev-1.13.0`、任意IDにも対応します。OpenRouterでは`jev-latest`を`~typesafe/jev-latest`、`jev-1.13.0`を`typesafe/jev-1.13`として送信します。`jev-preview`は未対応です。任意IDは`custom`で指定します。

## 実際に画像生成するサンプル

`examples/*.workflow.json`をComfyUIへ読み込んでください。同名の`.api.json`も同梱しています。以下の3例は標準のCheckpoint Loader、CLIP Text Encode、Empty Latent Image、KSampler、VAE Decode、Save Imageまで接続済みです。

1. **[01_generate_and_select.workflow.json](examples/01_generate_and_select.workflow.json)** — 制作意図から撮影プロンプトを4案生成し、Jevが選んだ案で画像を生成します。最初に使う例です。
2. **[02_manual_candidates.workflow.json](examples/02_manual_candidates.workflow.json)** — 手持ちの複数行プロンプトを標準Textノードから渡し、Jevで選択。文章生成APIは使いません。
3. **[03_select_and_expand.workflow.json](examples/03_select_and_expand.workflow.json)** — 撮影コンセプトを生成・選択してから、OpenRouter Textの通常モードで具体的な画像プロンプトへ展開します。

実行前にCheckpoint Loaderの`YOUR_SD_OR_SDXL_CHECKPOINT.safetensors`を、インストール済みのSD 1.5 / SDXL系チェックポイントに変更してください。CLIP・VAEを含むモデルを想定しています。画像サイズ・seed・stepsなどは標準ノードで設定します。モデルのダウンロードは行いません。

## 候補の提案

`task = suggest`では、`state`に書いた依頼に役立つ手順や制作方針を候補から選びます。候補の文章を`candidates`または`candidates_json`へ渡し、`instructions`に選ぶ基準を指定してください。

まず専門的な手順や指針が必要かを判断し、候補の説明で順位付けした後、上位候補の本文を評価します。選ばれた候補はSTRINGの`result`へJSON配列として出力されます。指針が不要な場合や、適合する候補がない場合は`[]`になります。標準のPreview as Textへ接続して確認できます。この必要性の判定を挟まずに1案を選びたい場合は、`choice`を使ってください。

| 詳細設定 | 既定値 | 用途 |
| --- | --- | --- |
| `shortlist_size` | 3 | 本文を評価する候補数 |
| `max_selections` | 1 | 出力する候補数の上限 |
| `gate_threshold` | 0.3 | 専門的な手順や指針が必要と判断するしきい値 |
| `threshold` | 0.5 | 各候補を採用するための適合度のしきい値 |

候補の説明と出力を分けたい場合は、次のようなJSON配列を標準Textノードに入力し、`candidates_json`へ接続します。

```json
[
  {
    "description": "自然光で温かみと手触りを伝える商品写真",
    "content": "ボトルを窓際のリネンに置く。柔らかな横からの光を使い、背景は簡潔にする。",
    "value": "Amber perfume bottle on natural linen, soft window light, warm neutral tones"
  },
  {
    "description": "硬い光で立体感と緊張感を出す商品写真",
    "content": "ボトルを暗い石の上に置く。細く絞った横からの光で輪郭と反射を強調する。",
    "value": "Perfume bottle on dark stone, hard side lighting, deep shadows, sculptural composition"
  }
]
```

`description`には短い説明、`content`には評価する本文、`value`には選択後に出力したい文字列やJSONの値を指定します。`content`を省略した場合は`description`を使います。既定の設定では、選ばれた`value`をそのままJSON配列に入れて返します。ファイルの内容を評価したい場合も、その本文をテキストとして入力してください。

`choice`・`multi_choice`でもこの形式を使えます。`description`を比較し、選ばれた`value`を出力します。

## Jev Skill Choice

インストール済みのSkillから、依頼に合うものを選びます。`prompt`に依頼文を入力すると、適合するSkillの全文と適用度を出力します。エージェント共通の`.agents/skills`とClaudeの`.claude/skills`を、ユーザー領域とComfyUIプロジェクト内から自動検出します。`CLAUDE_CONFIG_DIR`が設定されている場合は、Claudeのユーザー領域だけその設定に従います。同じSkillファイルへのリンクは重複して読み込みません。

[04_skill_choice.workflow.json](examples/04_skill_choice.workflow.json)は、インストール済みSkillを自動検出し、標準のPreview as Textで本文とデータを確認するサンプルです。

```text
Text → Jev Skill Choice → Preview as Text
          ↑
     インストール済みのSkill
```

| 入力 | 用途 |
| --- | --- |
| `directory` | 検出したSkillの場所をコンボボックスで選択。`automatic`は検出した場所すべてが対象。`custom`を選ぶと任意パスの入力欄を表示。リンク先のSkillも含めて`SKILL.md`を再帰的に読み込み。相対パスはComfyUIのディレクトリが基準 |
| `prompt` | Skillを選ぶ対象の依頼文 |
| `instructions` | 選択基準。既定では依頼への有用性と前提条件を確認 |
| `max_selections` | 選択するSkill数の上限。既定3 |
| `strength_mode` | `automatic`は各Skillの役割を0〜2で評価し、0は除外。`uniform`は選んだSkillをすべて1に設定 |
| `shortlist_size` | 全文を評価するSkill数の上限。既定5 |
| `threshold` / `gate_threshold` | 適合度と専門的な指針の必要性のしきい値。既定0.5 / 0.3 |

`provider`・`model`・`api_key`はJev Interpretと同様に設定します。Skillの説明と上位候補のファイル内容は、依頼文とともに選択したAPIへ送信されます。読み込み対象は各`SKILL.md`で、そこから参照される別ファイルやスクリプトは対象に含みません。

YAMLのフロントマターに`name`と`description`を指定できます。省略した場合はフォルダー名とファイル全文を使います。ファイルの追加・削除・内容変更は、次の実行時の再判定に反映されます。同じ入力で選び直す場合は`refresh`を変更してください。

出力は`text`（選んだ本文と適用度を含むSTRING）、`skills`（`name`・`path`・`description`・`content`・`strength`を持つレコードの`skills`配列を含むDICT）、`details`（判定結果のDICT）、`response_json`（API応答のSTRING）です。該当なしの場合、`text`は空文字、`skills`は`{"skills": []}`になります。

`text`をOpenRouter Textの`system`へ接続すると、選んだ指針を文章生成に渡せます。生成の依頼文は別途`prompt`へ入力してください。適用度は指針をどの程度重視するかを伝える値で、モデル内部の重みは変更しません。

## キャッシュ・エラー・検証

ComfyUIのキャッシュを使用します。画像生成側のseed・幅・高さだけの変更では、文章生成もJevの問い合わせも増えません。Jevの判断指示だけを変えた場合は、生成済み候補を再利用できます。ノードの`refresh`を変更すると、次の実行時にそのノードへ再問い合わせします。

入力・モデル・キー・しきい値など、問い合わせノード自身の設定変更はキャッシュ更新の対象です。APIは1回60秒、429 / 529は`Retry-After`に従い最大2回再試行します。それ以外の失敗や不正な応答はエラーとして返します。

```sh
../../venv/bin/python -m unittest discover -s tests -v
```

通常テストは有料APIを呼びません。モック応答から候補生成・選択・再利用を確認し、実際のComfyUI実行エンジンで標準ノードを通してSave Imageまで実行します。チェックポイント読込・学習済みモデルの計算はモックなので、画質や実APIでの判断精度を検証するテストではありません。

API仕様: [TypeSafe](https://docs.typesafe.ai/introduction) / [OpenRouter Chat Completions](https://openrouter.ai/docs/api/api-reference/chat/create-a-chat-completion) / [Structured Outputs](https://openrouter.ai/docs/guides/features/structured-outputs)
