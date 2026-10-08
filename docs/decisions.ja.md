# 画像の判定

[English](decisions.md) · [ノードの詳細](nodes.ja.md)

Jev InterpretまたはJev Skill Choiceで`provider = openrouter`にし、`openai/gpt-6-luna-decisions`・`cloudflare/clef`・`cloudflare/clef-flash`を選びます。通常の任意入力`images`にComfyUIのIMAGEバッチを接続します。既存の文章ワークフロー、既定値、出力型、認証方法は維持します。

最小構成は **Load Image → Jev Interpret.images** です。`task = boolean`、`instructions = 画像に赤い物体があるか`とし、`result`をPreview as Textへ接続します。チェックポイントや文章生成ノードは不要です。

## 文脈の組み立て

順序は元の`state`/`prompt`、`content_json`の配列順、IMAGEバッチの全画像の順です。ランキングと候補の全文検証で同じ文脈を使います。画像はPNG化し、自動リサイズや間引きは行いません。必要なら既存の画像リサイズ・バッチ選択ノードを手前に接続します。

`content_json`は、次のようなJSON配列を渡す任意のSTRING接続です。

```json
[
  {"type":"text","text":"パッケージの正面です。"},
  {"type":"image_url","image_url":{"url":"data:image/png;base64,<BASE64>"}}
]
```

APIアダプターがDecisions用の`state`配列へ変換します。画像はPNG/JPEG/WebPのdata URLのみです。リモートURL、入れ子の画像メッセージ、`input_image`形式は使えません。画像上限はLunaが128枚、Clef系が4枚です。`detail`はauto/low/highを受け付けますが、Clef系は無視します。[専用ガイド](https://openrouter.ai/docs/guides/community/multimodal-decisions)に基づく制約です。

音声・動画・ファイルのpartsは送信前に拒否します。既存ノードで音声を文字起こしし、動画からフレームを選び、文書からテキストやページ画像を取り出してください。文章・時刻情報はテキストへ、フレームやページはIMAGEへ接続できます。元の音声や動画の時間情報を自動保持する機能ではありません。数値抽出の対象・位置は元のstateだけを基準とします。

## 既存の前処理ノードとの接続

| 前段の出力 | 接続先 | 送る内容 |
| --- | --- | --- |
| 文章／音声文字起こしノードのSTRING | `state`、Skill Choiceでは`prompt` | 話者名・時刻を含む元の文章 |
| 動画から選んだIMAGEフレーム | `images` | 選択済みバッチの全フレームを順序どおり |
| 動画の時刻・字幕 | `state`または`content_json`のtext | バッチと対応するフレーム番号を明記した補足情報 |
| 文書・PDFから抽出した文章 | `state` | 必要に応じてページ番号を付けた抽出テキスト |
| 文書・PDFのページIMAGE | `images` | 画像枚数上限内で選んだページ |
| STRING化したJSON解析結果 | `state` | 入れ子のフィールドも含め、変更しないJSON文字列 |

音声なら文字起こしノードのSTRING出力を`state`へ直接つなぎ、「話者は提案に同意しているか」と指示できます。動画ならフレームを`images`へ、`{"frames":[{"index":0,"seconds":0},{"index":1,"seconds":1.5}],"subtitle":"両方のフレームを確認"}`を`state`へ渡します。文書なら抽出テキストや`{"page":2,"status":"approved","notes":["signed"]}`のようなJSON文字列を渡します。

追加のASR・動画デコーダー・要約・変換サービスは作りません。DICT出力は前段でSTRINGへ直列化します。音声・動画・PDFのネイティブアップロード対応とは区別してください。実ComfyUIの実行テストでは、文字起こし・文書・入れ子のJSONのSTRING接続と、IMAGEフレームバッチ＋時刻／補足テキストの接続を確認しています。前段の文字起こし・デコード・文書抽出そのものの品質は検証対象外です。長い文字起こしには以下の提供元制約が残ります。

## 制約と提供元

[OpenRouterのDecisions専用ガイド](https://openrouter.ai/docs/guides/community/multimodal-decisions#limits)では、Cloudflare経路はstateの文章を約2,000トークンまで読み、画像の符号化サイズから65,536トークンの上限に対する推定を行うと説明しています。この拡張は文章が2,000 UTF-8バイトを超えると、Jev Interpret（`suggest`を含む）とJev Skill Choiceの各実行結果の`details.warnings`へ警告を付けます。これは早めに知らせるための保守的なバイト数の基準で、トークン数でも、実際に切り詰められた証拠でもありません。繰り返し実行した結果にも残ります。

ClefとClef Flashでは、非ASCII文字のエスケープ、質問、画像、提供元指定をすべて含むJSON本文へ、ローカルの保守的な256 KiB制限を適用します。検査する符号化と実際のHTTP送信本文は一致します。このローカル制限は、上流APIの正確なサイズ上限を表すものではありません。拡張側で文章を削ったり画像を縮めたりはしません。必要に応じて文脈を短くするか、別のモデルを使ってください。

質問数はLunaで200、Clef系で64まで検証します。Clef系はchoiceが2〜255候補、scoreが2〜10段階です。候補の展開やSkillの絞り込みで複数の質問が生成される点にも注意してください。候補が1件だけなら比較質問を省き、必要性・適合性・任意の適用度の判定は維持します。根拠：[OpenAI Decisions](https://developers.openai.com/api/docs/guides/decisions)、[Clef](https://developers.cloudflare.com/workers-ai/models/clef/)、[Clef Flash](https://developers.cloudflare.com/workers-ai/models/clef-flash/)。

画像リクエストはOpenRouter内でLunaをOpenAI、Clef系をCloudflareへ明示的に送り、他の提供元への自動代替を止めます。第三者提供元のClefは今回の画像実験に合格しませんでした。画像経路の料金は最安の提供元より高くなる場合があります。既存の文章のみの経路は維持します。

画像・追加テキストもキャッシュ更新の対象です。同じ入力を再判定する場合は`refresh`を変更します。新しい接続は任意で、既存ウィジェットの位置は変わらないため、古い保存済みワークフローの移行は不要です。

## 2026-10-08の実測

64×64の合成PNGにランダムな色の円を6個配置し、正解を文章に含めず、許可された合計10回で検証しました。合格条件は全6問正解・確率0.8以上、同一モデル／提供元の画像なし対照から4問以上で0.5以上改善することです。

追加4回は、Cloudflare Clefの対照・画像、OpenAI Lunaの対照・画像の順に事前固定しました。実際の`media.prepare → api.evaluate → _post_json`を使い、IMAGEテンソルのPNG化から送信まで通しています。その呼び出し時のPython実行コードは、公開済みの[コミット`8a598354`](https://github.com/hndrr/ComfyUI-Jev/commit/8a598354def6b1d34ddf2381515996ae87fa570c)とバイト単位で一致します。実測後に更新したドキュメントはこの同一性の対象外です。その後のレビュー修正はオフライン回帰テストで検証しており、追加の実API呼び出しでは検証していません。検証側だけで対照の提供元を画像側と揃え、単価フィルターと送信前の再試行禁止を加えました。製品コードは変更していません。元の312 bytesのPNGは、画素が同一の455 bytesのPNGへ再符号化されました。

| モデル／実際の提供元 | 結果 |
| --- | --- |
| Clef／Cloudflare | 追加の対照・画像とも成功。6/6正解、正解確率0.9435〜0.9853。全6問で改善条件も満たし合格。 |
| Clef Flash／Cloudflare | 先の対照・画像とも成功。6/6正解、正解確率0.9599〜0.9724。全6問で改善条件も満たし合格。 |
| Luna Decisions／OpenAI | 正式形式の画像は2回とも基準を満たす正解4/6。右上の黄色と右下の紫を誤判定。追加の正式形式の対照も502で、理由は`OpenAI refused to answer question "top_left"`。有効な対照がなく、画像試験全体は未合格。 |
| Clef／PrimeIntellect | 先の対照・画像は応答成功だが画像は0/6で不合格。画像経路はCloudflareへ固定したままです。 |

成功8回の料金は合計USD 0.000970606。Lunaの対照失敗2回の実課金は不明で、それぞれUSD 0.0032768を留保し、計上額はUSD 0.007524206（承認上限USD 0.10）です。許可された10回を消費済みで、再試行や追加送信はしていません。最初の失敗した対照は旧形式の仮説を使い、エラー本文が未保存のため、今回と同じ失敗原因だったとは断定できません。

公式API契約、オフラインテスト、実際の認識結果は区別しています。ClefとFlashの合格はこの小さな単一PNGに限ります。Lunaは画像リクエストを受理していますが認識の合格基準は未達です。大きなバッチ、JPEG/WebPの認識、一般的な精度は未検証です。初期無効の実験スイッチは設けていません。

## 保守

`media.py`はComfyUI入力の正規化、`decisions.py`は送信形式・モデル制約・提供元選択、`api.py`は通信、`nodes.py`は接続入力を担当します。新しいモデルへの変更箇所を小さなモデル表と契約テストにまとめています。
