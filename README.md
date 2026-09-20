# [WORK IN PROGRESS] ComfyUI-Jev

文章から制作案を生成し、Jevで意図に合う案を選んで、既存のComfyUIワークフローへ渡すカスタムノードです。

このパッケージのノードは **OpenRouter Text** と **Jev Interpret** の2つです。文章の結合・数値変換・分岐・画像生成は標準ノードを使います。スキーマや候補IDを手で書く必要はありません。

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

`Jev Interpret`の`provider`を`openrouter`にすると、両ノードでこの環境変数を共有します。各ノードの`api_key`欄へ直接入力しても使えます。直接入力が優先され、空欄なら環境変数を読みます。標準のTextノードから両方の`api_key`入力へ同じキーを接続することもできます。

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
| `model` | 既定は`openai/gpt-4.1-mini`。`openai/gpt-4.1`または`custom`で任意IDを指定 |
| `api_key` | 空欄なら`OPENROUTER_API_KEY` |
| `refresh` | 固定値。変更すると再生成 |
| `temperature` / `max_tokens` | 詳細設定。生成のばらつき・最大出力トークン数 |

候補モードではノード内部でStructured Outputsを指定し、候補の配列をSTRINGとして出力します。**1行1候補という制約はありません。** 各候補の改行・段落・空白を保ちます。候補数の不一致、重複、空の候補、途中で終了した応答は明示的なエラーにします。

候補モードにはStructured Outputs対応のモデルが必要です。`custom`でも対応モデルを指定してください。通常の`text`モードではStructured Outputsを要求しません。出力`response_json`にはモデル名・使用量を含む元のAPI応答が入ります。

## Jev Interpret

`state`に判断対象や制作意図、`instructions`に判断指示を書きます。候補は次のどちらでも渡せます。

- **自動生成**：OpenRouter Textの`output_mode`を`candidates`にして、`text`を`candidates_json`へ接続。
- **手持ちの案**：標準TextノードなどのSTRINGを、Autogrowの`candidates.candidate0`以降へ接続。1接続が1候補で、複数行でもそのまま扱います。

両方を接続した場合は、個別接続の候補を接続番号順、その後に生成候補を並べます。候補の名前やスキーマの記述は不要です。

| `task` | 判断と出力`result` |
| --- | --- |
| `choice` | 候補から1つ選び、その候補の全文をそのままSTRING出力。CLIP Text Encodeなどへ直結可能 |
| `multi_choice` | 各候補をNoulで独立判定し、該当した候補を定義順のJSON配列として出力 |
| `boolean` | 指示した条件への該当をNoulで判定し、`true` / `false`を文字列で出力 |
| `score` | 候補入力を「低い→高い」の評価段階として使用。Scoreを0〜1へ正規化した数値文字列を出力 |
| `extract` | 原文の数値候補を位置・周辺文脈とともにChoiceへ渡し、選ばれた元の文字列を出力。候補なし・該当なしはエラー |
| `suggest` | 説明で順位付けし、上位候補の本文を再判定。適合した候補の値をJSON配列で出力。該当なしは`[]` |

booleanとmulti_choiceの`threshold`は既定0.5、比較は`>=`です。詳細設定から変更できます。scoreのconfidenceを評価値へ混ぜません。数値が必要なら標準のConvert Number、真偽による分岐にはCompare TextとSwitchなどを使います。

出力は`result`（STRING）、`details`（DICT、判定の詳細）、`response_json`（STRING、生のAPI応答）です。JSON形式の解析結果も、必要ならstateへ文字列として渡せます。画像・音声・動画を直接Jevへ送るノードではありません。

Jevモデルは既定`jev-latest`。TypeSafeでは`jev-preview`、`jev-1.13.0`、任意IDにも対応します。OpenRouterでは`jev-latest`を`~typesafe/jev-latest`、`jev-1.13.0`を`typesafe/jev-1.13`として送信します。`jev-preview`は未対応です。任意IDは`custom`で指定します。

## 実際に画像生成するサンプル

`examples/*.workflow.json`をComfyUIへ読み込んでください。同名の`.api.json`も同梱しています。以下の3例は標準のCheckpoint Loader、CLIP Text Encode、Empty Latent Image、KSampler、VAE Decode、Save Imageまで接続済みです。ノードタイトルの独自変更はありません。

1. **[01_generate_and_select.workflow.json](examples/01_generate_and_select.workflow.json)** — 制作意図から撮影プロンプトを4案生成し、Jevが選んだ案で画像を生成します。最初に使う例です。
2. **[02_manual_candidates.workflow.json](examples/02_manual_candidates.workflow.json)** — 手持ちの複数行プロンプトを標準Textノードから渡し、Jevで選択。文章生成APIは使いません。
3. **[03_select_and_expand.workflow.json](examples/03_select_and_expand.workflow.json)** — 撮影コンセプトを生成・選択してから、OpenRouter Textの通常モードで具体的な画像プロンプトへ展開します。

実行前にCheckpoint Loaderの`YOUR_SD_OR_SDXL_CHECKPOINT.safetensors`を、インストール済みのSD 1.5 / SDXL系チェックポイントに変更してください。CLIP・VAEを含むモデルを想定しています。画像サイズ・seed・stepsなどは標準ノードで設定します。モデルのダウンロードは行いません。

従来のスキーマ手書き・縦横比判定の例と、Field / Resolve / Read / Inspect / Model Candidates / Rank / Weighted Scoreは削除しました。旧ワークフロー用の互換ノードは併設していません。

## AgentRuntimeのpromptに合うSkillを選ぶ

**[04_skill_suggestion.workflow.json](examples/04_skill_suggestion.workflow.json)** は、同じTextをJevの`state`とAgent Runtimeの`prompt`へ接続する実例です。READMEを読み、制作者が迷う説明を特定して差し替え文を作る依頼に対し、調査・文章作成のSkillを選び、それぞれの適用の強さも決めます。ComfyUI-Skills-LoaderとComfyUI-AgentRuntimeが必要です。

```text
Skill Catalog → Jev Interpret (suggest) → Skill Stack Loader → Agent Runtime → Preview Any
                    ↑                                            ↑
                    └────────── Text (今回のprompt) ──────────────┘
```

1. **Skill Catalog**の`directory`を指定します。サンプルはSkill Loader同梱のSkill集を使用します。`~/.agents/skills`、`~/.codex/skills`や任意のフォルダーへ変更できます。配下の`SKILL.md`から候補を作るので、候補やスキーマを書く必要はありません。
2. **Jev Interpret**の`task = suggest`で、そのpromptに役立つSkillを選びます。サンプルは`skill_strength = automatic`、`max_selections = 3`、`shortlist_size = 5`です。結果は実在する元パスと個別の`strength`を含む配列です。パスをモデルに生成させません。
3. **Skill Stack Loader**が選ばれたSkillを読み、本文・元パス・`strength`を**Agent Runtime**の`skill`へ渡します。該当なしの`[]`でも実行でき、元のpromptはそのまま届きます。

サンプルのAgent Runtimeは`codex`です。使用するCLIの認証と`cwd`を設定し、必要ならprovider・model・instruction presetを変更してください。実行するとJev APIとAgentRuntimeのプロバイダーが呼ばれます。OpenRouter Textはこの例では使いません。

[Skill suggestion Cookbook](https://docs.typesafe.ai/cookbooks/skill_suggestion)を基に、次の2段階で判断します。

- 1回目：全候補の説明をChoiceで比較し、同時に3つのNoulで専門的な手順・制作上の指針が必要かを確認します。不要ならここで終了します。
- 2回目：上位候補の説明と本文を渡し、候補ごとのNoulで具体的な適合を確認します。`skill_strength = preserve`ではChoiceで比較し直し、`automatic`では候補ごとのScoreで今回の依頼における役割の大きさを評価します。候補本文の指示を実行する段階ではありません。

詳細設定の`shortlist_size`は既定3、`max_selections`は既定1です。複数のSkillを組み合わせる場合は`max_selections`を増やします。`gate_threshold`は専門的な指針の必要性（既定0.3）、`threshold`は各候補の適合（既定0.5）のしきい値です。confidenceを適合度として扱いません。

制作向けに、文章だけの成果物でも固有の作風や手順が役立つかを確認する質問へ調整しています。各候補自身が適合基準を満たさなければ採用しません。`preserve`で1件だけ選ぶ場合は、Choiceが選んだ候補の適合が足りなければ該当なしになります。説明・本文は文字数で切り詰めず、1回目は説明、2回目は上位候補の全文だけを送ります。descriptionがないSkillは本文を説明にも使います。

候補の内部形式は`description`・`content`・`value`を持つJSON配列です。Skill Catalogがこれを生成し、Jevは選択した`value`をコピーします。`automatic`の場合だけ、コピーした値の`strength`を判定結果で上書きします。他のカタログには既定の`preserve`を使えます。既存の`choice`・`multi_choice`でも説明と出力値を分けた候補を利用できます。

### プロンプトによるSkillの適用度

`Jev Interpret`の詳細設定で`skill_strength = automatic`にすると、候補ごとに「使わない／任意の細部を補助／一部分に通常適用／主要部分を導く／成果物の主軸」という5段階を[Score](https://docs.typesafe.ai/primitives/score)で評価し、0〜2の`strength`へ線形変換します。段階の中間値もそのまま使います。評価基準はノード内部にあるので、手書きスキーマは不要です。

たとえば「調査を主軸に、説明は短く」なら調査用Skillに1.8、文章用Skillに0.6という組み合わせを返せます。これは出力形式の例で、実際の値はプロンプトと候補内容によって変わります。

- 各Skillを独立に評価します。合計を1に揃える配分ではないため、複数のSkillを強く適用することもできます。
- Noulの適合しきい値を満たし、Scoreが0より大きい候補から、適用度順で最大`max_selections`件を選びます。同点は元の候補順です。評価対象は`shortlist_size`件までなので、組み合わせたい候補が多いときは増やしてください。
- 不適合・適用度0はStackへ渡しません。負のstrengthは自動生成しません。confidenceやNoulの確率をstrengthへ掛け合わせません。
- Scoreは2回目のリクエストへまとめるため、問い合わせの往復は通常と同じ最大2回です。質問が増える分のトークンは使います。
- `preserve`は元のstrengthを保ちます。既存ワークフローで未設定の場合も`preserve`です。

strengthは**エージェントに渡す適用の強さ・優先度の指示**です。Skill Stack LoaderとAgentRuntimeの既存の仕組みを使用し、モデル内部の数値重みを制御するものではありません。`details`には候補ごとの`applicability`（0〜1）と採用した`strengths`が入ります。

Skillの追加・削除・本文変更はCatalogのキャッシュへ反映します。AgentRuntime側の設定だけを変えた場合、Jevの判定は再利用されます。判定はQueue時に行います。

## キャッシュ・エラー・検証

ComfyUIのキャッシュを使用します。画像生成側のseed・幅・高さだけの変更では、文章生成もJevの問い合わせも増えません。Jevの判断指示だけを変えた場合は、生成済み候補を再利用できます。それぞれの`refresh`を変更すると、そのノードから再実行します。独自の永続キャッシュはありません。

入力・モデル・キー・しきい値など、問い合わせノード自身の設定変更はキャッシュ更新の対象です。APIは1回60秒、429 / 529は`Retry-After`に従い最大2回再試行します。それ以外の失敗や不正な応答はエラーとして返します。

```sh
../../venv/bin/python -m unittest discover -s tests -v
```

通常テストは有料APIを呼びません。モック応答から候補生成・選択・再利用を確認し、実際のComfyUI実行エンジンで標準ノードを通してSave Imageまで実行します。チェックポイント読込・学習済みモデルの計算はモックなので、画質や実APIでの判断精度を検証するテストではありません。

Skill連携のテストは、隣接するSkill LoaderとAgentRuntimeの実装で、選ばれた本文・元パス・元のpromptがAgentRequestへ届くこと、該当なしでの実行、promptとSkill本文の変更を確認します。Jev APIとAgentのCLI実行はモックです。両パッケージがない環境では、この連携テストだけスキップします。

API仕様: [TypeSafe](https://docs.typesafe.ai/introduction) / [OpenRouter Chat Completions](https://openrouter.ai/docs/api/api-reference/chat/create-a-chat-completion) / [Structured Outputs](https://openrouter.ai/docs/guides/features/structured-outputs)
