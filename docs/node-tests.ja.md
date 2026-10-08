# ノードのテスト

[English](node-tests.md) · [開発ガイド](development.ja.md)

テストにはComfyUI本体とその依存ライブラリが必要です。リポジトリを`ComfyUI/custom_nodes/ComfyUI-Jev`へ配置し、ComfyUIが使うPython環境で、ComfyUIのルートから実行します。

```sh
python custom_nodes/ComfyUI-Jev/.github/scripts/run_node_tests.py --comfy-dir .
```

通常テストは有料APIを呼びません。モック応答から候補生成・選択・キャッシュの再利用を確認し、実際のComfyUI実行エンジンで標準ノードを通してSave Imageまで実行します。チェックポイント読込と学習済みモデルの計算もモックのため、画質や実APIの判断精度は検証しません。

[Test nodes workflow](../.github/workflows/tests.yml)は全PRで自動実行され、手動実行にも対応します。GitHubの一時runnerでPython 3.12、コミットを固定したComfyUI 0.39.0、CPU版PyTorchを使います。依存のインストールはCI内だけで行い、API secretやモデルの重みは不要です。実行スクリプトはimport時から実ネットワーク接続を遮断し、全7テストモジュール・現状68件以上の検出を必須にして、skipや検出不足を失敗と扱います。`test_execution.py`と`test_skill_workflow.py`の実ComfyUIによる検証・実行、マルチモーダルのアダプターテストも対象です。テスト削除・統合時には最低件数も意図的に更新してください。リリース用workflowは独立しています。
