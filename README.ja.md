# ComfyUI-Jev

<sub>WORK IN PROGRESS</sub>

[English](README.md) | 日本語

Jevによる文章の解釈・判定をComfyUIで使うためのカスタムノードです。自然文の指示に基づいて候補の選択、条件の判定、採点、数値の抽出などを行い、結果を他のノードへ渡せます。判定には既定でTypeSafeのAPIを使います。

| ノード | できること |
| --- | --- |
| **Jev Interpret** | 文章の選択・判定・採点・数値の抽出 |
| **Jev Skill Choice** | ローカルのSkill（`SKILL.md`）から依頼に合うものを選択 |
| **OpenRouter Text** | OpenRouterで文章や選択候補を生成 |

[インストール](#インストール) · [APIキーの設定](#apiキーの設定) · [まず試す](#まず試す) · [サンプル](#サンプルワークフロー) · [ノードの詳細](docs/nodes.ja.md)

## インストール

ComfyUI v0.36.0以降とPython 3.10以降が必要です。次のどちらかの方法で導入してください。

### ComfyUI Managerから入れる

1. ComfyUIの **Manager** を開き、`ComfyUI-Jev`を検索します。
2. **ComfyUI-Jev** の **Install** を押します。
3. インストール後、ComfyUIを再起動します。

### git cloneで入れる

Gitをインストールした環境で、ComfyUIの`custom_nodes`ディレクトリへ移動して実行します。パスは自分のComfyUIの場所に置き換えてください。

```sh
cd /path/to/ComfyUI/custom_nodes
git clone https://github.com/hndrr/ComfyUI-Jev.git
```

依存ライブラリは`aiohttp`と`PyYAML`です。標準のComfyUI環境には含まれています。不足している場合は、**ComfyUIが使うPython環境**で次を実行してください。

```sh
python -m pip install "aiohttp>=3.11.8" PyYAML
```

Windows Portableの場合は、`ComfyUI_windows_portable`ディレクトリから同梱Pythonで実行します。

```powershell
.\python_embeded\python.exe -m pip install "aiohttp>=3.11.8" PyYAML
```

ComfyUIを再起動し、画面を再読み込みしてください。ノード検索で **Jev Interpret**、**Jev Skill Choice**、**OpenRouter Text** が見つかれば導入完了です。

## APIキーの設定

### TypeSafe（デフォルト）

**Jev InterpretとJev Skill Choiceは、既定でTypeSafeを使います**（`provider = typesafe`）。[TypeSafe](https://typesafe.ai/)でAPIキーを発行し、ComfyUIを起動する環境に設定します。

macOS / Linux:

```sh
export TYPESAFE_API_KEY="your-typesafe-key"
```

Windows PowerShell:

```powershell
$env:TYPESAFE_API_KEY="your-typesafe-key"
```

設定したターミナルからComfyUIを起動してください。両ノードの`provider`は`typesafe`、`api_key`は空欄のまま使えます。起動環境に設定しにくい場合は、各ノードの`api_key`欄へTypeSafeのキーを直接入力しても使えます。

直接入力したキーが優先され、空欄の場合に環境変数を読みます。直接入力したキーはワークフロー・実行履歴にも保存されるため、共有前に削除してください。環境変数を変更した場合は、その環境からComfyUIを起動し直します。

### OpenRouterを使う場合

**OpenRouter Text**で文章を生成する場合は、OpenRouterのAPIキーも必要です。同じ起動環境で`OPENROUTER_API_KEY`を設定するか、このノードの`api_key`欄へ入力します。

```sh
export OPENROUTER_API_KEY="your-openrouter-key"
```

PowerShellでは`$env:OPENROUTER_API_KEY="your-openrouter-key"`です。

Jevの判定にもOpenRouterを使う場合は、Jev InterpretまたはJev Skill Choiceの`provider`を`openrouter`へ変更します。`api_key`が空欄なら`OPENROUTER_API_KEY`を読みます。直接入力する場合も、選択したproviderのキーを使ってください。

## まず試す

TypeSafeのキーを設定したら、文章が指定した条件に当てはまるかを判定してみます。

```text
Jev Interpret → Preview as Text
```

1. **Jev Interpret**を追加し、`task = boolean`、`provider = typesafe`、`model = jev-latest`にします。候補の接続は不要です。
2. `state`に`明日の打ち合わせは何時に始まりますか？`、`instructions`に`この文章は相手に回答を求める質問ですか？`と入力します。
3. `result`を標準の **Preview as Text** へ接続して実行します。判定結果が文字列の`true`または`false`で表示されます。

条件によって処理を分岐する場合は、標準の **Compare Text** と **Switch** などへつなげます。候補の選択や採点など、他の判定方法は[ノードの詳細](docs/nodes.ja.md)を参照してください。

## サンプルワークフロー

判定結果を利用する例として、プロンプトを選んで画像を生成するワークフローや、ローカルのSkillを選ぶワークフローを同梱しています。下の`.workflow.json`を保存し、ComfyUIへドラッグして読み込んでください。サンプル内の文章は英語です。

**同梱サンプルは`provider = openrouter`で保存されています。TypeSafeを使う場合は、読み込み後にJevノードの`provider`を`typesafe`へ変更してください。** OpenRouter Textの文章生成には引き続きOpenRouterのキーが必要です。

| サンプル | 内容 | JevのAPIキーに加えて必要なもの |
| --- | --- | --- |
| [02_manual_candidates.workflow.json](examples/02_manual_candidates.workflow.json) | 手持ちのプロンプトをJevで選び、画像を生成 | SD 1.5 / SDXL系チェックポイント |
| [01_generate_and_select.workflow.json](examples/01_generate_and_select.workflow.json) | 撮影プロンプトを4案生成し、Jevが選んだ案で画像を生成 | OpenRouterのキー、チェックポイント |
| [03_select_and_expand.workflow.json](examples/03_select_and_expand.workflow.json) | 撮影コンセプトを生成・選択し、詳細な画像プロンプトへ展開 | OpenRouterのキー、チェックポイント |
| [04_skill_choice.workflow.json](examples/04_skill_choice.workflow.json) | ローカルのSkillを選び、本文と適用度を表示 | インストール済みのSkill |

画像生成の3例では、Checkpoint Loaderの`YOUR_SD_OR_SDXL_CHECKPOINT.safetensors`を、CLIP・VAEを含むインストール済みのSD 1.5 / SDXL系チェックポイントに変更します。画像サイズ・seed・stepsは標準ノードで調整してください。

## 困ったとき

| 症状 | 確認すること |
| --- | --- |
| ノードが見つからない | ComfyUIのバージョンと再起動を確認し、起動ログの`import failed`を調べる |
| APIキーが見つからない・認証エラーになる | `provider`とキーの発行元が一致しているか、`api_key`欄に別のキーが残っていないか確認する |
| OpenRouterのモデル一覧が空 | `custom`でモデルIDを入力する。一覧の再取得にはComfyUIを再起動する |
| 候補生成がエラーになる | Structured Outputs対応モデルを使い、途中で切れている場合は`max_tokens`を増やす |
| 再実行しても結果が変わらない | ComfyUIのキャッシュを利用しているため、再問い合わせしたいノードの`refresh`を変更する |

画像生成側のseedやサイズだけを変えても、文章生成やJevの判定は再利用されます。詳しくは[実行とキャッシュ](docs/nodes.ja.md#実行とキャッシュ)を参照してください。

開発・保守を行う方向けのテスト方法とRegistry公開手順は、[開発ガイド](docs/development.ja.md)にまとめています。

## ライセンス

[MITライセンス](LICENSE)で公開しています。
