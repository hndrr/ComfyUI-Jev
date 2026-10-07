# Decisionsの入力

[English](modalities.md) | [ノード一覧](nodes.ja.md)

## 現在の状態

| OpenRouterのモデル | テキスト判定 | この拡張の画像入力 |
| --- | --- | --- |
| `openai/gpt-6-luna-decisions` | 実装済み・モックで契約を検証 | 明示的に有効化する実験用の送信形式。サーバー側の画像認識は未検証 |
| `cloudflare/clef-flash` | 実装済み・モックで契約を検証 | Routerでの画像変換形式は未確定 |
| `cloudflare/clef` | 実装済み・モックで契約を検証 | Routerでの画像変換形式は未確定 |

この変更では有料の推論を実行していません。画像入力は**マルチモーダル対応を実証したものではなく**、初期状態では無効です。モデル一覧の入力はtext/imageですが、それだけでは送信形式や音声・動画・文書ファイルのネイティブ対応を確認できません。

## 他のノードと組み合わせる

`Jev Interpret`と`Jev Skill Choice`の既存入出力は維持します。Lunaの画像を試す場合は、標準の`IMAGE`バッチを`images`に接続し、`provider=openrouter`と`openai/gpt-6-luna-decisions`を選び、詳細設定の`experimental_images`を有効にします。有効化せず画像を接続すると送信前にエラーになります。他モデルとの組み合わせも明示的なエラーになります。Clefの制限は変換形式が未確定なためであり、モデルに画像認識能力がないという意味ではありません。

バッチの全画像を順番どおりPNGにして、一つの判定文脈へ送ります。自動的な間引き・縮小・説明文生成・画像ごとの個別判定は行いません。画素を`[0, 1]`に収めて8bitへ変換し、グレースケール・RGB・RGBAを受け付けます。suggest/Skill選択の両段階へ同じ画像を渡します。`extract`は元のテキスト内の数値と位置を扱い、OCRは行いません。

動画のフレーム抽出、音声の文字起こし、文書のテキスト抽出・ページ画像化には既存ノードを使ってください。フレームバッチには順序はありますが、時刻や音声は含まれません。文字起こしや抽出テキストは`state`/`prompt`へ、ページ画像は画像の実験入力へ接続できます。ネイティブの`AUDIO`・`VIDEO`・ファイル入力はなく、テキスト中のURLも取得しません。Jevにデコーダーや文字起こしサービスを追加せず、前処理を組み替えられる構成です。

## 送信形式の根拠と未検証部分

[OpenRouter API](https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request)は`{model, state, questions}`を使います。[公式SDKの実装](https://github.com/OpenRouterTeam/ai-sdk-provider/blob/1b22b05352cb0f9243a6c3fdd326038dd3705544/src/evaluation/index.ts)は構造化した`state`をそのまま転送します。[OpenAIの公式ガイド](https://developers.openai.com/api/docs/guides/decisions)では、ユーザーメッセージに`input_text`と`input_image`を入れます。今回の候補は、このメッセージ配列をRouterの`state`に格納します。

これは明示的な推定です。クライアントが送れることは確認しましたが、Routerが画像として解釈・変換することを示す公開実装は確認できていません。OpenAIネイティブはトップレベルの`input`や異なる質問・回答形式を使うため、APIを同一視せずOpenRouter経由を維持します。画像がなければ従来の文字列`state`を変更しません。

[CloudflareのClef API](https://developers.cloudflare.com/workers-ai/models/clef/)には別の`images`フィールドがあります。この形式や画像制限がRouterにも適用されるとは限りません。確認済みのRouterの例か、制御した実測を得てから変換処理を追加します。画像処理は小さな[`media.py`](../media.py)に分離しています。

### OpenRouter共通のマルチモーダルガイド

追加された公式資料も2026-10-07に確認しました。

| 資料 | 記載された経路・形式 |
| --- | --- |
| [画像チュートリアル](https://openrouter.ai/blog/tutorials/send-image-to-llm/)・[画像理解](https://openrouter.ai/docs/guides/overview/multimodal/image-understanding) | Chat Completionsの`messages`に`text`、続いてURL/base64の`image_url` |
| [音声](https://openrouter.ai/docs/guides/overview/multimodal/audio) | 対応モデルのChat Completionsに、base64データと形式を持つ`input_audio` |
| [動画](https://openrouter.ai/docs/guides/overview/multimodal/videos) | Chat Completionsの`video_url`。対応モデルのResponses向け`input_video`処理も記載 |

共通のメディア形式は確認できましたが、いずれも`/api/alpha/decisions`での変換を説明した資料ではありません。音声・動画はモデル側の対応も必要です。これらのchat形式をDecisionsのstateへ入れた場合と、今回のOpenAIネイティブ形式が同じ扱いになる根拠も得られていません。そのため実験用という表示を維持し、別モデルやchatへの自動切り替えは追加しません。Decisionsの形式を確認できれば、ComfyUIの入力境界を保ったまま小さな変換関数を差し替えられます。音声・動画の直接入力には、モデルの能力とDecisions経由の形式の両方を確認する必要があります。

## 任意の実測

[`tools/check_luna_images.py`](../tools/check_luna_images.py)は、ランダムな色の円6個を描いた画像と6問の選択質問、画像なしの対照入力を用意します。正解の色はリクエストの文章に含めません。

```sh
python tools/check_luna_images.py
```

通常実行では通信も認証情報へのアクセスもありません。費用を明示的に許可する場合だけ、ComfyUIのPython環境で`OPENROUTER_API_KEY`を設定済みの状態で`python tools/check_luna_images.py --run`を実行します。最大**2回の有料リクエスト**、各6問、リトライなしです。キーの保存やComfyUI設定の変更はしません。

6問すべてが画像の色と一致して確率0.8以上になり、そのうち4問以上で画像なしの正解確率を0.5以上上回ると合格です。合格はその時点・モデル・入力に対する画像認識の根拠になりますが、HTTP成功だけでは確認になりません。不合格や拒否は結論保留とし、送信形式を見直してください。この変更ではオフラインの準備モードだけを実行しています。
