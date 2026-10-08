# メンテナンス運用

[開発ガイド](docs/development.ja.md) | [Development guide](docs/development.md)

## バージョンの決め方

公開する版数は `pyproject.toml` の `[project].version` で管理します。Comfy Registry の[公式仕様](https://docs.comfy.org/registry/specifications)に合わせ、先頭に `v` を付けない `MAJOR.MINOR.PATCH` 形式にします。GitHub のタグだけに `v` を付けます。

| 変更 | バージョンの指定 | 現行版からの例 |
| --- | --- | --- |
| 互換性を保つ修正 | 通常はパッチ番号を自動更新 | `0.1.1 → 0.1.2` |
| 新機能の追加 | PR でマイナー番号を明示的に上げる | `0.1.1 → 0.2.0` |
| 互換性を壊す変更 | PR でメジャー番号を明示的に上げる | `0.1.1 → 1.0.0` |

ノードID、入力名・型、必須入力、出力名・型、保存済みワークフローの意味が変わる変更は、破壊的変更として扱います。既存の接続や設定で同じ処理を続けられるかを確認してください。開発中の `0.x.y` でも、このリポジトリでは同じ判断基準を使います。

明示的に上げた版数はそのまま公開します。版数の引き下げ、不正な形式、公開済みパッケージの上書きは認めません。Registry ID `comfyui-jev` と Publisher ID `hndr` は維持します。

## 自動公開

設定は [publish.yml](.github/workflows/publish.yml) にあります。`hndrr/ComfyUI-Jev` の `main` への push・PR マージで起動し、fork では公開しません。変更が `.github/**`、`README*.md`、`MAINTAINERS.md`、`docs/development*.md`、`tests/**` だけの場合は、自動公開も採番も起動しません。

1. リリース用テストを実行します。
2. 最新の `main` を取得し、必要ならパッチ番号を上げます。
3. `Prepare registry version X.Y.Z` コミットを `main` に push します。明示的な版数更新でも、対象の変更を記録するために空コミットを作ります。
4. 確定したコミットSHAを checkout し、Registryへ公開します。
5. 公開したコミットを指す `vX.Y.Z` タグと GitHub Release を作り、同じ更新内容をRegistryにも保存します。

公開処理はキューで1件ずつ実行します。短時間に複数のマージがあると、最新の `main` を使った1つのリリースにまとまる場合があります。準備コミットの `Registry-Source: <40桁のコミットSHA>` で対象を記録し、既に含まれる変更の後続実行はスキップします。Actions の `GITHUB_TOKEN` で作る準備コミットは、次の公開処理を起動しません。

更新内容と自動生成に使うPRタイトルは英語にします。利用者に影響する機能追加・修正を短くまとめ、公開操作の説明はここに記載します。手動の `publish` 実行では、`release_notes` で本文を指定できます。

## 初回の設定

- Publisher `hndr` の公開用APIキーを、リポジトリの Actions secret `REGISTRY_ACCESS_TOKEN` に登録します。TypeSafe・OpenRouterのキーとは別です。
- バージョン準備ジョブが `main` に push できるよう、リポジトリのActions設定・ブランチ保護を確認します。`contents: write` を指定していても、ブランチ保護によって拒否される場合があります。
- 既に公開した版が `Active`・`Pending`・`Flagged` で、GitHub Releasesがなければ、次の通常公開より先に `sync-notes` を実行します。過去版の履歴を確定し、次の自動生成本文に過去のPR全体が含まれるのを防ぎます。審査状態は変更しません。

## 公開失敗時の再試行

対象の `Prepare registry version ...` コミットが `main` に入っているかを確認します。

| 失敗した段階 | 再試行の方法 |
| --- | --- |
| 準備コミットの push 前 | 失敗した実行の **Re-run** で採番からやり直す |
| 準備コミットの push 後、Registry公開前 | **Run workflow** の `mode = publish`、ブランチ `main` で現行版を公開する。公開ジョブだけが失敗した場合は **Re-run failed jobs** も使える |
| Registry公開後、対象版がActive・PendingでGitHub Release作成・本文同期に失敗 | **Re-run failed jobs**、または `mode = sync-notes` を使う |
| Registry公開後、対象版がFlaggedになりRelease作成に失敗 | `mode = sync-notes` で履歴・本文を補完する。審査状態、版数、パッケージは変更しない |

手動の `publish` は番号を上げません。準備コミットが入る前に使うと、既存の版数で公開しようとします。準備コミットが入った後に **Re-run all jobs** を使うと、既に準備済みと判定され、公開をスキップしたまま成功扱いになる場合があります。

Registry公開済みの版に `publish` を再実行してはいけません。パッケージを修正する場合は、新しい版数で公開します。

## 公開済み版の履歴補完・本文の同期

Actions の **Publish to Comfy registry → Run workflow** で、ブランチ `main`、`mode = sync-notes` を選びます。このモードは、版数の更新やパッケージの再公開を行いません。

[release-history.json](.github/release-history.json) に記録したコミット・更新内容を使って、既存Registry版のGitHub Releasesを補完します。`0.1.0` と `0.1.1` のコミットは、Registryの配布ZIP内の全ファイルと照合済みです。それ以降は準備コミットから公開したSHAを特定します。

既存のGitHub Releaseがある場合は、その本文をRegistryの `changelog` に反映し、`deprecated` と審査状態を維持します。本文の変更は先に記録ファイルをPRで確認し、GitHub Releaseの本文も同じ文言に更新してから同期してください。記録ファイルだけの変更では、既存Releaseの本文を上書きしません。

手動の `sync-notes` は `Active`・`Pending`・`Flagged` の既存版を対象にします。[本文更新API](https://docs.comfy.org/registry/api-reference/registry/update-changelog-and-deprecation-status-of-a-node-version)で更新内容だけを同期し、パッケージは再公開しません。削除済み・`Banned` の版はスキップします。

別のコミットを指す既存タグや、下書きのReleaseがある場合は停止します。既存のタグを付け替えたり、下書きを公開したりしません。

## Registryでの審査状況

Registryへのアップロード成功と、審査・インストール可否は別です。通常公開後のRelease自動作成は `Active`・`Pending` を対象にします。手動の履歴補完は `Flagged` も対象にしますが、GitHub Releaseの存在はRegistryでの承認を意味しません。`Pending` は審査中で、インストール可能と確認された状態ではありません。

2026-10-08の確認では、`0.1.0` と `0.1.1` はどちらも `NodeVersionStatusFlagged`、理由は `policy-v0.5: arbitrary-file-read` でした。履歴・更新内容の同期後も、この審査状態は維持します。新しい版数の公開だけでは審査の問題は解消しません。

## 公開設定の管理

- バージョン準備とRelease作成はそれぞれ `contents: write` ジョブ、Registry公開は別の `contents: read` ジョブで行います。全checkoutで `persist-credentials: false` を指定し、書き込み認証は準備コミットのpushコマンドだけに渡します。
- 公開Actionは `Comfy-Org/publish-node-action@d2366e7abb6ab16f3bb03e3520ae25c8cf749bc9` に固定しています。更新先の内容を確認してからSHAを書き換えます。
- `skip_checkout: 'true'` で、準備した版のcheckoutを維持します。`PIP_CONSTRAINT` で `comfy-cli==1.22.0` に固定し、`COMFY_NODE_CHANGELOG` で共有する更新内容を渡します。
- [release-checks.yml](.github/workflows/release-checks.yml) で、公開処理を実行せずにリリース用スクリプトを検証します。ローカルでも `python3 -m unittest discover -s .github/tests -v` で実行できます（Python 3.11以降）。

参考: [SceneDetectの運用方針](https://github.com/hndrr/Comfyui-SceneDetect/blob/master/MAINTAINERS.md)、[Comfy Registry公開手順](https://docs.comfy.org/registry/publishing)、[メタデータ仕様](https://docs.comfy.org/registry/specifications)。
