# 開発・公開ガイド

[利用者向けREADME](../README.ja.md) | [English](development.md)

ComfyUI-Jevのコードを変更する方と、Registryへリリースするメンテナー向けの手順です。

## テスト

テストにはComfyUI本体とその依存ライブラリが必要です。リポジトリを`ComfyUI/custom_nodes/ComfyUI-Jev`へ配置し、ComfyUIが使うPython環境で、リポジトリのルートから実行します。

```sh
python -m unittest discover -s tests -v
```

通常テストは有料APIを呼びません。モック応答から候補生成・選択・キャッシュの再利用を確認し、実際のComfyUI実行エンジンで標準ノードを通してSave Imageまで実行します。チェックポイント読込と学習済みモデルの計算もモックのため、画質や実APIの判断精度は検証しません。

`examples/`にはUIから読み込む`.workflow.json`と、同じ処理の`.api.json`を置いています。入力や接続を変更する際は両方を更新してください。ノードIDと既存ワークフローの互換性を保ちます。

## Comfy Registryへの公開

[`pyproject.toml`](../pyproject.toml)でRegistry ID `comfyui-jev`、表示名`ComfyUI-Jev`、Publisher ID `hndr`を指定しています。公開するバージョンは`[project].version`で管理します。

### 初回の設定

1. [Comfy Registry](https://registry.comfy.org)のPublisher `hndr`で、公開用APIキーを用意します。同じPublisherの既存キーを再利用できます。値を保存していない場合は新しいキーを作成し、保管してください。
2. [このリポジトリのActions Secrets](https://github.com/hndrr/ComfyUI-Jev/settings/secrets/actions)に、その値を`REGISTRY_ACCESS_TOKEN`として登録します。他のrepoと同じキーを使う場合も、Repository secretはrepoごとに登録が必要です。このキーはノードで使うTypeSafe・OpenRouterのキーとは別です。
3. 公開設定とリリース対象のファイルを`main`へ反映します。`pyproject.toml`の更新で[公開ワークフロー](../.github/workflows/publish.yml)が起動します。

### 次回以降のリリース

リリース対象の変更と同じpushで`[project].version`を新しい`X.Y.Z`へ更新します。手動実行する場合は **Actions → Publish to Comfy Registry → Run workflow** で`main`を選びます。

公開済みバージョンは上書きできません。失敗したワークフローを再実行する場合も、そのバージョンが未公開であることを確認してください。公開は`hndrr/ComfyUI-Jev`の`main`に限定し、forkでは実行しません。

[`.comfyignore`](../.comfyignore)でテストとGitHub設定を配布対象から除外しています。実行用モジュール、README、ドキュメント、サンプルワークフローは含まれます。

公開仕様: [公式の公開手順](https://docs.comfy.org/registry/publishing) / [メタデータ仕様](https://docs.comfy.org/registry/specifications)

## API仕様

- [TypeSafe](https://docs.typesafe.ai/introduction)
- [OpenRouter Chat Completions](https://openrouter.ai/docs/api/api-reference/chat/create-a-chat-completion)
- [Structured Outputs](https://openrouter.ai/docs/guides/features/structured-outputs)
