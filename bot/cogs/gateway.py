"""/shitpost と /show_settings。"""

from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import app_config
from bot.fixlink import apply_fixlink
from bot.router import resolve_destinations

# モジュールロガー
logger = logging.getLogger(__name__)


def _member_role_ids(interaction: discord.Interaction) -> set[int]:
    """実行者のロール ID 集合を返す。"""
    # DM 等で member が無い場合は空
    if not isinstance(interaction.user, discord.Member):
        return set()
    # ロール ID を集める
    return {role.id for role in interaction.user.roles}


def _build_post_content(username: str, fixed_url: str) -> str:
    """投稿本文（サブテキスト + URL）を組み立てる。"""
    # Discord の -# サブテキスト行
    return f"-# ShitPostGateWayBot From {username}\n{fixed_url}"


def _channel_is_nsfw(channel: discord.abc.GuildChannel) -> bool:
    """チャンネルが NSFW か判定する。"""
    # TextChannel / Thread 等の is_nsfw
    is_nsfw = getattr(channel, "is_nsfw", None)
    # 呼び出し可能なら実行
    if callable(is_nsfw):
        return bool(is_nsfw())
    # 属性 bool の場合
    if isinstance(is_nsfw, bool):
        return is_nsfw
    # 不明は非 NSFW 扱い
    return False


def _format_place(guild: Optional[discord.Guild], channel: Optional[discord.abc.GuildChannel]) -> str:
    """サーバー名 / チャンネル名表示を作る。"""
    # サーバー名
    guild_name = guild.name if guild is not None else "不明なサーバー"
    # チャンネル名
    if channel is None:
        channel_name = "不明なチャンネル"
    else:
        channel_name = getattr(channel, "name", "不明なチャンネル")
    # 表示用に # を付ける
    return f"{guild_name} / #{channel_name}"


class GatewayCog(commands.Cog):
    """投稿ゲートウェイ系コマンド。"""

    def __init__(self, bot: commands.Bot) -> None:
        # Bot 参照を保持
        self.bot = bot

    @app_commands.command(
        name="shitpost",
        description="URL を fixlink して投稿し、設定ルートへ転送する",
    )
    @app_commands.describe(url="Twitter / Pixiv / Instagram などの投稿 URL")
    async def shitpost(self, interaction: discord.Interaction, url: str) -> None:
        """URL を fixlink して実行チャンネル＋ルート先へ送る。"""
        # 権限チェック（空なら全員可）
        if not app_config.is_allowed("shitpost_role_ids", _member_role_ids(interaction)):
            # 拒否を ephemeral で返す
            await interaction.response.send_message(
                "このコマンドを実行する権限がありません。",
                ephemeral=True,
            )
            return
        # ギルドテキストチャンネル以外は不可
        if interaction.guild is None or not isinstance(
            interaction.channel, (discord.TextChannel, discord.Thread)
        ):
            # 案内
            await interaction.response.send_message(
                "サーバーのテキストチャンネルで実行してください。",
                ephemeral=True,
            )
            return
        # 処理開始を遅延応答（後で followup / edit）
        await interaction.response.defer(ephemeral=True)
        # ユーザーネーム（nick ではなく name）
        username = interaction.user.name
        # fixlink 変換
        fixed_url = apply_fixlink(url, app_config.fixlink_map)
        # 投稿本文
        content = _build_post_content(username, fixed_url)
        # 実行チャンネル
        origin = interaction.channel
        assert origin is not None
        try:
            # 実行チャンネルへ先に投稿（embed 用）
            await origin.send(content)
        except discord.HTTPException as exc:
            # 投稿失敗
            logger.warning("Failed to post in origin channel: %s", exc)
            await interaction.followup.send(
                "このチャンネルへの投稿に失敗しました。",
                ephemeral=True,
            )
            return
        # ルート解決
        destinations = resolve_destinations(
            app_config.routes_store.routes,
            str(interaction.guild.id),
            str(origin.id),
        )
        # ルート未定義
        if not destinations:
            # 件数 0 を報告
            await interaction.followup.send(
                "このチャンネルをルート元とする転送先がありません。投稿のみ完了しました。",
                ephemeral=True,
            )
            return
        # NSFW 実行か
        source_nsfw = _channel_is_nsfw(origin) if isinstance(origin, discord.abc.GuildChannel) else False
        # スレッドの場合は親の NSFW を見る
        if isinstance(origin, discord.Thread) and origin.parent is not None:
            source_nsfw = _channel_is_nsfw(origin.parent)
        # 成功 / スキップカウンタ
        sent = 0
        skipped = 0
        # 各宛先へ送信
        for dest in destinations:
            # ギルド取得
            guild = self.bot.get_guild(int(dest["guild_id"])) if dest["guild_id"].isdigit() else None
            # 未参加はスキップ
            if guild is None:
                skipped += 1
                continue
            # チャンネル取得
            channel = guild.get_channel(int(dest["channel_id"])) if dest["channel_id"].isdigit() else None
            # スレッドも探す
            if channel is None and dest["channel_id"].isdigit():
                channel = guild.get_thread(int(dest["channel_id"]))
            # テキスト系以外はスキップ
            if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                skipped += 1
                continue
            # NSFW 安全: 実行元が NSFW なら先も NSFW 必須
            target_nsfw = _channel_is_nsfw(channel)
            if isinstance(channel, discord.Thread) and channel.parent is not None:
                target_nsfw = _channel_is_nsfw(channel.parent)
            if source_nsfw and not target_nsfw:
                skipped += 1
                continue
            try:
                # 転送投稿
                await channel.send(content)
                # 成功
                sent += 1
            except discord.HTTPException as exc:
                # 権限不足等はスキップ
                logger.warning("Failed to send to %s/%s: %s", dest["guild_id"], dest["channel_id"], exc)
                skipped += 1
        # 結果を ephemeral で返す
        await interaction.followup.send(
            f"送信 {sent} 件 / スキップ {skipped} 件",
            ephemeral=True,
        )

    @app_commands.command(
        name="show_settings",
        description="このチャンネルをルート元とする転送設定を表示する",
    )
    async def show_settings(self, interaction: discord.Interaction) -> None:
        """実行チャンネルを from とするルートを名前で表示する。"""
        # ギルドチャンネル必須
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message(
                "サーバーのチャンネルで実行してください。",
                ephemeral=True,
            )
            return
        # ルート先一覧
        destinations = resolve_destinations(
            app_config.routes_store.routes,
            str(interaction.guild.id),
            str(interaction.channel.id),
        )
        # 未設定
        if not destinations:
            await interaction.response.send_message(
                "このチャンネルではルーティングが設定されていません！",
                ephemeral=True,
            )
            return
        # ルート元表示
        origin_line = _format_place(interaction.guild, interaction.channel)  # type: ignore[arg-type]
        # 先の行を組み立て
        lines = [f"ルート元: {origin_line}", "ルート先:"]
        for dest in destinations:
            # ギルド解決
            guild = self.bot.get_guild(int(dest["guild_id"])) if dest["guild_id"].isdigit() else None
            # チャンネル解決
            channel = None
            if guild is not None and dest["channel_id"].isdigit():
                channel = guild.get_channel(int(dest["channel_id"]))
                if channel is None:
                    channel = guild.get_thread(int(dest["channel_id"]))
            # 名前行を追加
            lines.append(f"- {_format_place(guild, channel)}")  # type: ignore[arg-type]
        # ephemeral で返す
        await interaction.response.send_message("\n".join(lines), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    """cog 登録。"""
    # GatewayCog を追加
    await bot.add_cog(GatewayCog(bot))
