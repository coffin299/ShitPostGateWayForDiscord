"""/help … Components V2 でコマンド案内を表示する。"""

from __future__ import annotations

import discord
from discord import app_commands, ui
from discord.ext import commands

# アクセント（紫系は避けて落ち着いたティール）
_ACCENT = discord.Color.from_rgb(38, 148, 140)


class HelpLayout(ui.LayoutView):
    """ヘルプ用 LayoutView（Components V2）。"""

    def __init__(self) -> None:
        # タイムアウト付き
        super().__init__(timeout=300)
        # 見出しコンテナ
        header = ui.Container(
            ui.TextDisplay("## ShitPostGateWayBOT"),
            ui.TextDisplay(
                "複数の界隈にいるのに、同じ投稿をサーバーごと貼るのが面倒な人向けのゲートウェイです。"
            ),
            accent_color=_ACCENT,
        )
        # コマンド説明コンテナ
        commands_box = ui.Container(
            ui.TextDisplay("### コマンド一覧"),
            ui.Separator(visible=True),
            ui.TextDisplay(
                "**`/shitpost`**\n"
                "URL を fixlink してこのチャンネルに投稿し、ルート先へ同時転送します。\n"
                "-# 応答（件数）は実行者のみに表示"
            ),
            ui.Separator(visible=True),
            ui.TextDisplay(
                "**`/shitposting_router`**\n"
                "単方向ルートを追加します。サーバー → チャンネルをプルダウンで選択。\n"
                "-# `count` でルート先の数を指定（省略時 1）"
            ),
            ui.Separator(visible=True),
            ui.TextDisplay(
                "**`/shitposting_router_mesh`**\n"
                "選んだチャンネル同士を双方向メッシュで一括接続します。\n"
                "-# `count` は双方向にする鯖（チャンネル）数（2 以上）"
            ),
            ui.Separator(visible=True),
            ui.TextDisplay(
                "**`/show_settings`**\n"
                "このチャンネルをルート元とする転送設定を、サーバー名・チャンネル名で表示します。\n"
                "-# 未設定ならその旨を表示"
            ),
            ui.Separator(visible=True),
            ui.TextDisplay(
                "**`/reload_config`**\n"
                "`config.yaml` と `routes.json` を再読み込みします。"
            ),
            ui.Separator(visible=True),
            ui.TextDisplay(
                "**`/help`**\n"
                "このヘルプを表示します。"
            ),
            accent_color=_ACCENT,
        )
        # 補足コンテナ
        tips = ui.Container(
            ui.TextDisplay("### 補足"),
            ui.TextDisplay(
                "- fixlink 対応: X / Twitter・pixiv・Instagram\n"
                "- NSFW チャンネルからの転送先は NSFW のみ\n"
                "- ルート設定のサーバー一覧は、あなたと Bot の共通サーバーのみ\n"
                "- 投稿には `-# ShitPostGateWayBot From <username>` が付きます"
            ),
            accent_color=_ACCENT,
        )
        # Layout に載せる
        self.add_item(header)
        self.add_item(commands_box)
        self.add_item(tips)


class HelpCog(commands.Cog):
    """ヘルプコマンド。"""

    def __init__(self, bot: commands.Bot) -> None:
        # Bot 参照
        self.bot = bot

    @app_commands.command(
        name="help",
        description="コマンド一覧と使い方を表示する",
    )
    async def help_command(self, interaction: discord.Interaction) -> None:
        """Components V2 のヘルプを ephemeral で返す。"""
        # LayoutView を構築
        view = HelpLayout()
        # 実行者のみに表示（content/embeds は V2 では使わない）
        await interaction.response.send_message(view=view, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    """cog 登録。"""
    await bot.add_cog(HelpCog(bot))
