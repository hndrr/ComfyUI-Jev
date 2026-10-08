# 開発・公開ガイド

[利用者向けREADME](../README.ja.md) | [English](development.md)

ComfyUI-Jevのコードを変更する方と、Registryへリリースするメンテナー向けの手順です。

採番・リリース・再試行・履歴補完の運用方針は [MAINTAINERS.md](../MAINTAINERS.md) にまとめています。

## テスト

テストにはComfyUI本体とその依存ライブラリが必要です。リポジトリを`ComfyUI/custom_nodes/ComfyUI-Jev`へ配置し、ComfyUIが使うPython環境で、リポジトリのルートから実行します。

```sh
python -m unittest discover -s tests -v
```

通常テストは有料APIを呼びません。モック応答から候補生成・選択・キャッシュの再利用を確認し、実際のComfyUI実行エンジンで標準ノードを通してSave Imageまで実行します。チェックポイント読込と学習済みモデルの計算もモックのため、画質や実APIの判断精度は検証しません。

リリース用スクリプトのテストは、Python 3.11以降とGitだけで実行できます。

```sh
python3 -m unittest discover -s .github/tests -v
```

`examples/`にはUIから読み込む`.workflow.json`と、同じ処理の`.api.json`を置いています。入力や接続を変更する際は両方を更新してください。ノードIDと既存ワークフローの互換性を保ちます。

## Comfy Registryへの公開

[`pyproject.toml`](../pyproject.toml)でRegistry ID `comfyui-jev`、表示名`ComfyUI-Jev`、Publisher ID `hndr`を指定しています。公開するバージョンは`[project].version`で管理します。

### 初回の設定

1. [Comfy Registry](https://registry.comfy.org)のPublisher `hndr`で、公開用APIキーを用意します。同じPublisherの既存キーを再利用できます。値を保存していない場合は新しいキーを作成し、保管してください。
2. [このリポジトリのActions Secrets](https://github.com/hndrr/ComfyUI-Jev/settings/secrets/actions)に、その値を`REGISTRY_ACCESS_TOKEN`として登録します。他のrepoと同じキーを使う場合も、Repository secretはrepoごとに登録が必要です。このキーはノードで使うTypeSafe・OpenRouterのキーとは別です。
3. Actions設定とブランチ保護で、バージョン準備ジョブが`main`にpushできることを確認します。
4. 次の通常公開より先に、**Actions → Publish to Comfy registry → Run workflow** で`main`、`mode = sync-notes`を選び、Active・Pending・Flaggedの既存Registry版のGitHub Releasesを補完します。履歴と更新内容を整え、審査状態は維持します。削除済み・Bannedの版はスキップします。

### 次回以降のリリース

リリース対象の変更が`main`に入ると、[公開ワークフロー](../.github/workflows/publish.yml)が起動します。通常の修正はパッチ番号を自動更新します。新機能はマイナー番号、互換性を壊す変更はメジャー番号をPRで明示的に上げてください。`0.x.y`でも同じ基準を使います。版数は先頭のゼロや`v`を付けない`X.Y.Z`形式にします。README・保守/開発ガイド・テスト・GitHub設定だけの変更では公開しません。

準備コミットで公開対象を記録し、そのSHAをRegistryに公開して、同じコミットの`vX.Y.Z`タグとGitHub Releaseを作ります。PRタイトルと更新内容は英語にし、同じ本文をRegistryにも渡します。実行はキューで待ち、既に準備済みの変更はスキップします。公開は`hndrr/ComfyUI-Jev`の`main`に限定し、forkでは実行しません。

公開済みパッケージは上書きできません。手動の`mode = publish`は番号を上げずに現行版を公開するため、準備済みで未公開の版に使います。Registry公開後、対象版がActive・Pendingなら、失敗したReleaseジョブだけを再実行するか、`sync-notes`で履歴を補完してください。Release作成前にFlaggedになった場合も、審査状態を変えず、再公開せずに`sync-notes`で履歴・更新内容を補完できます。準備前に失敗した場合は[再試行の表](../MAINTAINERS.md#公開失敗時の再試行)を参照してください。

アップロード成功とRegistryの承認は別です。通常公開後のRelease自動作成はActive・Pendingを対象にします。手動の`sync-notes`はFlaggedも対象とし、[本文更新API](https://docs.comfy.org/registry/api-reference/registry/update-changelog-and-deprecation-status-of-a-node-version)で審査・非推奨状態を維持して同期します。削除済み・Bannedの版はスキップします。2026-10-08時点では`0.1.0`・`0.1.1`ともにFlaggedで、理由は`policy-v0.5: arbitrary-file-read`です。履歴補完によって承認されるわけではありません。

[`.comfyignore`](../.comfyignore)でテストとGitHub設定を配布対象から除外しています。実行用モジュール、README、保守手順、ドキュメント、サンプルワークフローは含まれます。

公開仕様: [公式の公開手順](https://docs.comfy.org/registry/publishing) / [メタデータ仕様](https://docs.comfy.org/registry/specifications)

## API仕様

- [TypeSafe](https://docs.typesafe.ai/introduction)
- [OpenRouter Chat Completions](https://openrouter.ai/docs/api/api-reference/chat/create-a-chat-completion)
- [Structured Outputs](https://openrouter.ai/docs/guides/features/structured-outputs)
