# hellocloud-hermes-agent-videogen

[Hermes Agent](https://hermes-agent.nousresearch.com/) 用の **HelloCloud 動画生成バックエンドプラグイン** です。
[HelloCloud](https://router.hello.ai) の OpenAI 互換動画生成 API (`/v1/videos`) 経由で
**MiniMax H3** (`minimax-h3`) を利用できます。

- テキスト → 動画 (text-to-video)
- 画像 → 動画 (image-to-video)
- 参照画像は合計 2 枚まで
- 2K MP4、5〜15 秒、音声生成 ON/OFF
- 非同期ジョブをポーリングし、MP4 を `$HERMES_HOME/cache/generated/videos/` に保存
- API キーは **環境変数 `HELLOCLOUD_API_KEY`** から取得

API 仕様: [HelloCloud 動画生成ドキュメント](https://router.hello.ai/docs/videos/)

## 仕組み

Hermes 内蔵の `video_generate` ツールに、プロバイダー `hellocloud` を追加します。
`POST /v1/videos` でジョブを作成し、10 秒ごとに `GET /v1/videos/{id}` で状態を確認、
完了後に `/content` から MP4 をダウンロードします。ポーリング期限は 30 分です。

依存は Hermes の公開プロバイダー API、Python 標準ライブラリ、Hermes 同梱の `openai` です。

## インストール

### 1. API キー

[HelloCloud コンソール](https://router.hello.ai/console/) で有料 API キー (`hello_...`) を発行し、
環境変数 `HELLOCLOUD_API_KEY` に設定します。無料トライアルキーは動画生成に使えません。

```bash
export HELLOCLOUD_API_KEY=hello_xxxxxxxxxxxx
```

持続させる場合は、Hermes の `.env` (`~/.hermes/.env`、またはアクティブプロファイルの `.env`) に
`HELLOCLOUD_API_KEY=hello_...` を追記してください。

### 2. プラグイン

```bash
hermes plugins install elderica/hellocloud-hermes-agent-videogen --enable
```

後から有効化する場合:

```bash
hermes plugins enable hellocloud-videogen
```

### 3. プロバイダー設定

```bash
hermes config set video_gen.provider hellocloud
hermes config set video_gen.model minimax-h3
```

`config.yaml` 相当:

```yaml
video_gen:
  provider: hellocloud
  model: minimax-h3
  hellocloud:
    # base_url: https://api.router.hello.ai/v1  # 任意 (既定)
```

設定変更は新規セッションで使用されます。CLI は再起動、ゲートウェイ / デスクトップは `/reset`
で新しいセッションを開始してください。

## 使い方

Hermes に動画生成を依頼します。

```text
「陸奥湾を泳ぐイルカを 5 秒の動画にして。波の音も入れて」
```

主なパラメータ:

| パラメータ | 仕様 |
|------------|------|
| `prompt` | 生成内容 (必須) |
| `duration` | 5〜15 秒。範囲外は 5〜15 に丸める。省略時は API の最短尺 |
| `aspect_ratio` | `21:9` / `16:9` / `4:3` / `1:1` / `3:4` / `9:16` |
| `audio` | `true` / `false`。省略時は ON。`false` でも無音の音声トラックは残ることがある |
| `image_url` | 動画化する画像。公開 HTTPS URL、ローカルパス、base64 data URL |
| `reference_image_urls` | 追加の参照画像。公開 HTTPS URL のみ |

`image_url` と `reference_image_urls` の合計は最大 2 枚です。ローカルパス / data URL の
`image_url` は、OpenAI SDK のファイルアップロードとして送信します。

解像度は **2K 固定**です。Hermes 共通の `resolution` 値は API に送信しません。
`seed`、`negative_prompt`、upscale、動画の編集・延長には対応していません。

## 課金と保存期限

`GET /v1/videos/models` の現在の単価は次のとおりです。

- 動画: **¥30 / 秒**
- 参照画像: **1 枚ごとに ¥10**
- 例: 5 秒・参照画像なしなら ¥150

投入時に見積り全額が予約され、完了時に実額で精算されます。失敗・期限切れのジョブは課金されません。
生成物には保持期限があるため、このプラグインは完了後すぐにローカルへ保存します。
レスポンスの `expires_at` で API 側の期限を確認できます。

## 設定項目

| 場所 | キー | 既定 |
|------|------|------|
| 環境変数 | `HELLOCLOUD_API_KEY` | (必須) |
| `config.yaml` | `video_gen.provider` | `hellocloud` を指定 |
| `config.yaml` | `video_gen.model` | `minimax-h3` |
| `config.yaml` | `video_gen.hellocloud.base_url` | `https://api.router.hello.ai/v1` |
| 環境変数 | `HELLOCLOUD_VIDEO_BASE_URL` | 同上 |

## テスト

Hermes のソースチェックアウトを `PYTHONPATH` に指定して実行します。

```bash
PYTHONPATH=/path/to/hermes-agent python -m unittest discover -s tests -v
hermes plugins doctor --ci .
```

## ライセンス

[MIT](./LICENSE)
