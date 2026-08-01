# ShitPostGateWayBOT

複数の界隈・Discord サーバーに同時に所属しているのに、コミュニティ同士が分断されていて、同じツイートや pixiv・Instagram をサーバーごとにいちいち個別貼りするのが面倒な人向けのゲートウェイ Bot です。

`/shitpost` 一発で fixlink 付き投稿をし、設定したチャンネル群へ同時転送します。

> English README: [README.en.md](./README.en.md)

## できること

- `/shitpost` … URL を fixlink（X / pixiv / Instagram）して投稿し、送受信メッシュ先へ転送（`silent` オプションあり）
- `/shitposting_router` … 単方向ルートをプルダウンで追加
- `/shitposting_router_mesh` … 複数チャンネルを双方向の送受信メッシュで一括接続
- `/shitposting_router_mesh_add` … 既存の送受信メッシュにチャンネルを追加
- `/shitposting_router_remove` … 送受信メッシュの転送先を1件解除（チャンネル ID）
- `/shitposting_router_remove_all` … 送受信メッシュ一括削除
- `/show_settings` … このサーバーにおける自分の送受信メッシュ一覧
- `/show_settings_admin` … 【管理者】全ユーザーの送受信メッシュ一覧（V2・ページング）
- `/reload_config` … `config.yaml` と `routes.json` を再読み込み
- `/help` … Components V2 でコマンド案内を表示

投稿本文の例:

```text
-# ShitPostGateWayBot From [username](<https://discord.com/users/123456789012345678>)
https://fxtwitter.com/...
```

ユーザー名はメンションではなくプロフィール URL のリンクです（クリックでプロフィール表示・通知なし）。プロフィール URL は `<>` で embed 抑制し、fixlink のプレビューは残します。投稿・転送の通知は `/shitpost` の `silent` で選べます（省略時は通常通知）。

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
| `i18n.default.yaml` | コマンド説明・UI 文言（日英・リポジトリ同梱） |
| `i18n.yaml` | 文言の実行用コピー（起動時に自動生成・gitignore） |
| `routes.default.json` | ルート空テンプレ |
| `routes.json` | 転送ルート（コマンドで追記・gitignore） |

`config.yaml` 例の要点:

- `token` … Bot トークン
- `guild_ids` … スラッシュ即時同期したいサーバー ID（空ならグローバル同期）
- `fixlink` … ドメイン置換表
- `permissions.*_role_ids` … 空なら誰でも実行可

### 言語（i18n）

- スラッシュコマンドの description は `i18n.yaml` から日英を Discord に登録（**既定は日本語**、英語クライアント向けに `en-US` / `en-GB` ローカライズ）
- 実行時の応答・ヘルプ・ウィザードは `interaction.locale` を見て **日本語なら日本語、それ以外は英語**
- discord.py 2.x では `Translator` 経由で同期します（古い `description_localizations` 直接代入は無効）
- 文言を変えたら `/reload_config` のあと **Bot 再起動でスラッシュ再同期**すると確実です

ルーティングはエッジ方式です。双方向にしたい場合は相互にルートを追加してください。同一サーバー内の別チャンネル同士も設定できます。各転送先には追加者の `added_by`（ユーザー ID）と `added_by_name`（ユーザー名）が保存されます。スラッシュコマンド実行時に、実行者の名前が変わっていれば同 ID の `added_by_name` を `routes.json` へ自動更新します。

## コマンド詳細

### `/shitpost url: [silent:]`

実行チャンネルに投稿したうえで、そのチャンネルをルート元とする先へ転送します。応答（件数）は実行者のみに見えます。

- `silent` … `true` は通知なし、`false`（省略時）は通常通知

- Bot 未参加サーバー / 取得できないチャンネルはスキップ
- **NSFW チャンネルで実行した場合、転送先も NSFW のみ**（非 NSFW 先はスキップ）
- ルート／メッシュ作成時も、実行チャンネルや選んだ元が NSFW なら候補リストは NSFW チャンネルのみ

### `/shitposting_router count:`

Modal の ID 手入力はやめ、**Bot が参加しているサーバー / テキストチャンネルをプルダウンで選ぶ**方式です（モーダル中はコピペできないため）。

- `count` 省略または `1` … ルート元 → ルート先を1組
- `count` が 2 以上 … ルート先を複数回選択
- 流れ: サーバー選択 → チャンネル選択（25件超はページ送り）
- サーバー一覧は **実行者と Bot の両方に参加があるサーバーのみ**
- 「キャンセル（ここまでで設定）」で未確定分を捨て、確定済みだけ `routes.json` に保存

### `/shitposting_router_mesh count:`

選んだ N 個のチャンネルを **相互双方向の送受信メッシュ** でつなぎます。`count` は送信先チャンネル数（同じ数だけ選ぶ）です。

- `count` は送信先チャンネル数（2 以上）
- 例: A/B/C を選ぶと A↔B、A↔C、B↔C のように、選んだチャンネル同士が相互に転送し合う
- 途中キャンセル時は、確定済みが 2 チャンネル以上ならその範囲だけでメッシュ化
- サーバー一覧は実行者と Bot の共通サーバーのみ

### `/shitposting_router_mesh_add count:`

**既に送受信メッシュに入っているチャンネル上で実行**し、追加するチャンネルを選びます。新規分は既存メンバー全員と双方向接続されます。

- `count` は追加する送信先チャンネル数（省略時 1）

### `/shitposting_router_remove channel_id:`

**ルート元チャンネルで実行**し、送受信メッシュの転送先チャンネル ID を指定して1件解除します。

### `/shitposting_router_remove_all scope:`

送受信メッシュの一括削除です。

| scope | 動作 |
| :--- | :--- |
| このチャンネルからの送信メッシュを全削除 | 実行チャンネルを from とするエントリを削除 |
| このチャンネルを送受信メッシュから除外 | 送信元としても受信先としても消す |
| すべての送受信メッシュを削除 | `routes.json` を空にする（確認ボタンあり） |

### `/show_settings`

サーバー内のどのチャンネルでも実行できます。**あなたが追加した送受信メッシュだけ**（`added_by` = ユーザー ID）を表示します。

- **送信** … この鯖のどのチャンネルから、どこへ送るか
- **受信** … この鯖のどのチャンネルが、どこから受け取るか
- いま実行したチャンネルが送受信のどれに当たるか

他人の送受信メッシュは見えません。追加者は **名前 + ユーザー ID** で表示します。

### `/show_settings_admin`

**サーバーオーナー**または **Administrator** 権限向けです（Discord 上も管理者向けに表示）。このサーバーに関係する **全ユーザー** の送受信メッシュを、追加者ごと（**名前 + ユーザー ID**）に Components V2 で表示します（名前は鯖内メンバー優先、いなければ保存済み `added_by_name`）。長い場合は「前へ / 次へ」でページングします（実行者のみ・ephemeral）。

### `/help`

Components V2（LayoutView / Container）でコマンド一覧と補足を表示します。実行者のみに見えます。

### `/reload_config`

`config.yaml`・`i18n.yaml`・`routes.json` を再読み込みします。

## fixlink 対応（デフォルト）

| 元 | 置換 |
| :--- | :--- |
| twitter.com | fxtwitter.com |
| x.com | fixupx.com |
| pixiv.net | phixiv.net |
| instagram.com | ddinstagram.com |

`config.yaml` の `fixlink` で変更できます。

## 注意

- `config.yaml` / `i18n.yaml` / `routes.json` / `.venv` は git 管理しません
- コマンド説明・UI 文言は `i18n.yaml`（日英）。`start.bat` 内メッセージは英語（文字化け対策）です
