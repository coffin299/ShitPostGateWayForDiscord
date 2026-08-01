# ShitPostGateWayBOT

複数の界隈・Discord サーバーに同時に所属しているのに、コミュニティ同士が分断されていて、同じツイートや pixiv・Instagram をサーバーごとにいちいち個別貼りするのが面倒な人向けのゲートウェイ Bot です。

`/shitpost` 一発で fixlink 付き投稿をし、設定したチャンネル群へ同時転送します。

## できること

- `/shitpost` … URL を fixlink（X / pixiv / Instagram）して投稿し、ルート先へ転送
- `/shitposting_router` … 単方向ルートをプルダウンで追加
- `/shitposting_router_mesh` … 複数チャンネルを双方向メッシュで一括接続
- `/show_settings` … このチャンネルをルート元とする設定を表示
- `/reload_config` … `config.yaml` と `routes.json` を再読み込み
- `/help` … Components V2 でコマンド案内を表示

投稿本文の例:

```text
-# ShitPostGateWayBot From username
https://fxtwitter.com/...
```

## セットアップ

1. [Discord Developer Portal](https://discord.com/developers/applications) で Bot を作成し、トークンを控える
2. Bot を転送したいサーバーへ招待する（メッセージ送信権限が必要）
3. このリポジトリで `start.bat` を実行する（初回は `.venv` 作成と依存導入）
4. 生成された `config.yaml` の `token` を編集して保存
5. もう一度 `start.bat` で起動

手動起動する場合:

```powershell
$env:PYTHONDONTWRITEBYTECODE=1
.\.venv\Scripts\python.exe -m bot
```

## 設定ファイル

| ファイル | 役割 |
| :--- | :--- |
| `config.default.yaml` | テンプレ（リポジトリ同梱） |
| `config.yaml` | トークン・fixlink・権限（起動時に自動生成・gitignore） |
| `routes.default.json` | ルート空テンプレ |
| `routes.json` | 転送ルート（コマンドで追記・gitignore） |

`config.yaml` 例の要点:

- `token` … Bot トークン
- `guild_ids` … スラッシュ即時同期したいサーバー ID（空ならグローバル同期）
- `fixlink` … ドメイン置換表
- `permissions.*_role_ids` … 空なら誰でも実行可

ルーティングはエッジ方式です。双方向にしたい場合は相互にルートを追加してください。同一サーバー内の別チャンネル同士も設定できます。

## コマンド詳細

### `/shitpost url:`

実行チャンネルに投稿したうえで、そのチャンネルをルート元とする先へ転送します。応答（件数）は実行者のみに見えます。

- Bot 未参加サーバー / 取得できないチャンネルはスキップ
- **NSFW チャンネルで実行した場合、転送先も NSFW のみ**（非 NSFW 先はスキップ）

### `/shitposting_router count:`

Modal の ID 手入力はやめ、**Bot が参加しているサーバー / テキストチャンネルをプルダウンで選ぶ**方式です（モーダル中はコピペできないため）。

- `count` 省略または `1` … ルート元 → ルート先を1組
- `count` が 2 以上 … ルート先を複数回選択
- 流れ: サーバー選択 → チャンネル選択（25件超はページ送り）
- サーバー一覧は **実行者と Bot の両方に参加があるサーバーのみ**
- 「キャンセル（ここまでで設定）」で未確定分を捨て、確定済みだけ `routes.json` に保存

### `/shitposting_router_mesh count:`

選んだ N 個のチャンネルを **相互双方向（メッシュ）** でつなぎます。送信チャンネル数＝鯖数の想定で、同じ数だけサーバー→チャンネルを選びます。

- `count` は 2 以上（双方向には最低 2 箇所）
- 例: A/B/C を選ぶと A↔B、A↔C、B↔C 相当のエッジを一括追加
- 途中キャンセル時は、確定済みが 2 箇所以上ならその範囲だけでメッシュ化
- サーバー一覧は実行者と Bot の共通サーバーのみ

### `/show_settings`

ルート元チャンネルで実行すると、サーバー名・チャンネル名だけで設定を表示します（ID は出しません）。未設定ならその旨を表示します。

### `/help`

Components V2（LayoutView / Container）でコマンド一覧と補足を表示します。実行者のみに見えます。

### `/reload_config`

`config.yaml` と `routes.json` を再読み込みします。

## fixlink 対応（デフォルト）

| 元 | 置換 |
| :--- | :--- |
| twitter.com | fxtwitter.com |
| x.com | fixupx.com |
| pixiv.net | phixiv.net |
| instagram.com | ddinstagram.com |

`config.yaml` の `fixlink` で変更できます。

## 注意

- `config.yaml` / `routes.json` / `.venv` は git 管理しません
- コマンドの説明文は日本語、`start.bat` 内メッセージは英語（文字化け対策）です
