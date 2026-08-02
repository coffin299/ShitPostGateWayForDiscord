"""/help … Components V2 でコマンド案内を表示する。"""

from __future__ import annotations

import discord
from discord import app_commands, ui
from discord.ext import commands

from bot.i18n import lang_from_interaction, t

# アクセント（紫系は避けて落ち着いたティール）
_ACCENT = discord.Color.from_rgb(38, 148, 140)
# ヘルプに載せるコマンド順
_HELP_BLOCK_KEYS = (
    "shitpost",
    "fixlink",
    "shitposting_router",
    "shitposting_router_mesh",
    "shitposting_router_mesh_add",
    "shitposting_router_remove",
    "shitposting_router_remove_all",
    "show_settings",
    "show_settings_admin",
    "reload_config",
    "help",
)


class HelpLayout(ui.LayoutView):
    """ヘルプ用 LayoutView（Components V2）。"""

    def __init__(self, lang: str) -> None:
        # タイムアウト付き
        super().__init__(timeout=300)
        # 見出しコンテナ
        header = ui.Container(
            ui.TextDisplay(t("help.title", lang)),
            ui.TextDisplay(t("help.intro", lang)),
            accent_color=_ACCENT,
        )
        # コマンド説明を組み立て
        command_children: list = [
            ui.TextDisplay(t("help.commands_heading", lang)),
        ]
        for key in _HELP_BLOCK_KEYS:
            # 区切り
            command_children.append(ui.Separator(visible=True))
            # 本文
            command_children.append(ui.TextDisplay(t(f"help.blocks.{key}", lang)))
        commands_box = ui.Container(
            *command_children,
            accent_color=_ACCENT,
        )
        # 補足コンテナ
        tips = ui.Container(
            ui.TextDisplay(t("help.tips_heading", lang)),
            ui.TextDisplay(t("help.tips_body", lang)),
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
        description="Show commands and how to use them",
    )
    async def help_command(self, interaction: discord.Interaction) -> None:
        """Components V2 のヘルプを ephemeral で返す。"""
        # ユーザー locale
        lang = lang_from_interaction(interaction)
        # LayoutView を構築
        view = HelpLayout(lang)
        # 実行者のみに表示
        await interaction.response.send_message(view=view, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    """cog 登録。"""
    await bot.add_cog(HelpCog(bot))
