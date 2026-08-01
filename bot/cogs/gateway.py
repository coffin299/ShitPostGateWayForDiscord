"""/shitpost・/show_settings・/show_settings_admin。"""

from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands, ui
from discord.ext import commands

from bot.channel_util import channel_is_nsfw
from bot.config import app_config
from bot.fixlink import apply_fixlink
from bot.i18n import lang_from_interaction, t, ti
from bot.router import resolve_destinations
from bot.routes_store import added_by_id, added_by_name

# モジュールロガー
logger = logging.getLogger(__name__)
# 管理者ビュー用アクセント
_ADMIN_ACCENT = discord.Color.from_rgb(200, 120, 40)
# 1ページあたりの本文上限（TextDisplay 合計制限に余裕を残す）
_ADMIN_PAGE_CHARS = 1600


def _is_guild_high_priv(member: discord.abc.User, guild: discord.Guild) -> bool:
    """サーバーオーナーまたは Administrator 権限か。"""
    # オーナー
    if member.id == guild.owner_id:
        return True
    # Member で管理者権限
    if isinstance(member, discord.Member) and member.guild_permissions.administrator:
        return True
    return False


def _build_admin_route_pages(
    bot: commands.Bot,
    guild: discord.Guild,
    routes: list,
    lang: str,
) -> list[str]:
    """この鯖に関係する全ユーザーのルートをページ文字列へ分割する。"""
    guild_id = str(guild.id)
    # added_by -> 送信/受信テキスト断片 + 保存名
    by_user: dict[str, dict[str, list[str]]] = {}

    def ensure_user(user_key: str) -> dict[str, list[str]]:
        return by_user.setdefault(user_key, {"send": [], "recv": [], "names": []})

    def ch_name(channel_id: str) -> str:
        channel = guild.get_channel(int(channel_id)) if channel_id.isdigit() else None
        if channel is None and channel_id.isdigit():
            channel = guild.get_thread(int(channel_id))
        name = getattr(channel, "name", None)
        return f"#{name}" if name else f"#不明({channel_id})"

    def remote_label(remote_guild_id: str, remote_channel_id: str) -> str:
        remote_guild = bot.get_guild(int(remote_guild_id)) if remote_guild_id.isdigit() else None
        remote_channel = None
        if remote_guild is not None and remote_channel_id.isdigit():
            remote_channel = remote_guild.get_channel(int(remote_channel_id))
            if remote_channel is None:
                remote_channel = remote_guild.get_thread(int(remote_channel_id))
        return _format_place(remote_guild, remote_channel)  # type: ignore[arg-type]

    for route in routes:
        source = route.get("from") or {}
        from_guild = str(source.get("guild_id", ""))
        from_channel = str(source.get("channel_id", ""))
        for item in route.get("to") or []:
            if not isinstance(item, dict):
                continue
            owner = added_by_id(item) or "不明"
            stored_name = added_by_name(item)
            to_guild = str(item.get("guild_id", ""))
            to_channel = str(item.get("channel_id", ""))
            bucket = ensure_user(owner)
            # 保存済みユーザー名を控える
            if stored_name:
                bucket["names"].append(stored_name)
            # この鯖からの送信
            if from_guild == guild_id and from_channel:
                bucket["send"].append(
                    f"- {ch_name(from_channel)} → {remote_label(to_guild, to_channel)}"
                )
            # この鯖への受信
            if to_guild == guild_id and to_channel:
                bucket["recv"].append(
                    f"- {ch_name(to_channel)} ← {remote_label(from_guild, from_channel)}"
                )

    # ユーザー単位のブロックを作る
    blocks: list[str] = []
    for owner_id, data in sorted(by_user.items(), key=lambda x: x[0]):
        # 保存済み名（重複除去の先頭）
        stored = next(iter(dict.fromkeys(data["names"])), "")
        # 名前 + ID を併記
        who = _format_user_label(owner_id, guild=guild, stored_name=stored, lang=lang)
        parts = [f"### {who}"]
        # 重複行を除去して順序維持
        send_lines = list(dict.fromkeys(data["send"]))
        recv_lines = list(dict.fromkeys(data["recv"]))
        parts.append(t("msg.label_send", lang))
        parts.extend(send_lines or [t("msg.settings_none_item", lang)])
        parts.append(t("msg.label_recv", lang))
        parts.extend(recv_lines or [t("msg.settings_none_item", lang)])
        blocks.append("\n".join(parts))

    if not blocks:
        return [t("msg.admin_no_routes", lang)]

    # 文字数でページ分割
    pages: list[str] = []
    current = ""
    for block in blocks:
        candidate = f"{current}\n\n{block}".strip() if current else block
        if len(candidate) <= _ADMIN_PAGE_CHARS:
            current = candidate
            continue
        if current:
            pages.append(current)
        # 1ブロックが大きすぎる場合は切る
        if len(block) <= _ADMIN_PAGE_CHARS:
            current = block
        else:
            start = 0
            while start < len(block):
                pages.append(block[start : start + _ADMIN_PAGE_CHARS])
                start += _ADMIN_PAGE_CHARS
            current = ""
    if current:
        pages.append(current)
    return pages or [t("msg.admin_empty_page", lang)]


class AdminSettingsLayout(ui.LayoutView):
    """管理者向けルート一覧（Components V2 + ページング）。"""

    def __init__(
        self,
        pages: list[str],
        owner_id: int,
        guild_name: str,
        lang: str,
    ) -> None:
        # タイムアウト
        super().__init__(timeout=300)
        # ページ本文
        self.pages = pages
        # ページ番号
        self.index = 0
        # 実行者のみ操作可
        self.owner_id = owner_id
        # 鯖名
        self.guild_name = guild_name
        # 言語
        self.lang = lang
        # 初回描画
        self._rebuild()

    def _rebuild(self) -> None:
        """現在ページでコンポーネントを組み立て直す。"""
        # クリア
        self.clear_items()
        total = len(self.pages)
        # ヘッダー
        header = ui.Container(
            ui.TextDisplay(t("msg.admin_header", self.lang, guild=self.guild_name)),
            ui.TextDisplay(
                t(
                    "msg.admin_page",
                    self.lang,
                    page=self.index + 1,
                    total=total,
                )
            ),
            accent_color=_ADMIN_ACCENT,
        )
        # 本文
        body = ui.Container(
            ui.TextDisplay(self.pages[self.index]),
            accent_color=_ADMIN_ACCENT,
        )
        self.add_item(header)
        self.add_item(body)
        # ページが2以上ならボタン
        if total > 1:
            row = ui.ActionRow()
            prev_btn = ui.Button(
                label=t("msg.btn_prev", self.lang),
                style=discord.ButtonStyle.secondary,
                disabled=self.index <= 0,
            )
            next_btn = ui.Button(
                label=t("msg.btn_next", self.lang),
                style=discord.ButtonStyle.primary,
                disabled=self.index >= total - 1,
            )

            async def go_prev(interaction: discord.Interaction) -> None:
                if interaction.user.id != self.owner_id:
                    await interaction.response.send_message(
                        t("msg.owner_only_button", self.lang),
                        ephemeral=True,
                    )
                    return
                self.index = max(0, self.index - 1)
                self._rebuild()
                await interaction.response.edit_message(view=self)

            async def go_next(interaction: discord.Interaction) -> None:
                if interaction.user.id != self.owner_id:
                    await interaction.response.send_message(
                        t("msg.owner_only_button", self.lang),
                        ephemeral=True,
                    )
                    return
                self.index = min(len(self.pages) - 1, self.index + 1)
                self._rebuild()
                await interaction.response.edit_message(view=self)

            prev_btn.callback = go_prev  # type: ignore[method-assign]
            next_btn.callback = go_next  # type: ignore[method-assign]
            row.add_item(prev_btn)
            row.add_item(next_btn)
            self.add_item(row)


def _member_role_ids(interaction: discord.Interaction) -> set[int]:
    """実行者のロール ID 集合を返す。"""
    # DM 等で member が無い場合は空
    if not isinstance(interaction.user, discord.Member):
        return set()
    # ロール ID を集める
    return {role.id for role in interaction.user.roles}


def _build_post_content(username: str, user_id: int, fixed_url: str) -> str:
    """投稿本文（サブテキスト + URL）を組み立てる。"""
    # メンション (<@id>) は通知になるため、プロフィール URL の Markdown リンクにする
    profile = f"https://discord.com/users/{user_id}"
    # クリックでユーザープロフィールを開ける
    name_link = f"[{username}]({profile})"
    # Discord の -# サブテキスト行 + fixlink URL
    return f"-# ShitPostGateWayBot From {name_link}\n{fixed_url}"


def _channel_is_nsfw(channel: discord.abc.GuildChannel) -> bool:
    """互換ラッパー（channel_util へ委譲）。"""
    return channel_is_nsfw(channel)


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


def _format_user_label(
    user_id: str,
    *,
    guild: Optional[discord.Guild] = None,
    stored_name: str = "",
    live_user: Optional[discord.abc.User] = None,
    lang: str = "en",
) -> str:
    """ユーザー名と ID を併記した表示文字列を作る。"""
    # ライブユーザー名（実行者など）
    live_name = ""
    if live_user is not None:
        live_name = str(getattr(live_user, "name", "") or "")
    # 鯖内メンバー名
    member_name = ""
    if guild is not None and user_id.isdigit():
        member = guild.get_member(int(user_id))
        if member is not None:
            member_name = str(member.name)
    # 優先: ライブ → メンバー → 保存名 → 不明
    name = live_name or member_name or stored_name or t("msg.unknown_user", lang)
    return f"{name} (`{user_id}`)"


class GatewayCog(commands.Cog):
    """投稿ゲートウェイ系コマンド。"""

    def __init__(self, bot: commands.Bot) -> None:
        # Bot 参照を保持
        self.bot = bot

    @app_commands.command(
        name="shitpost",
        description="Fixlink a URL, post it here, and forward to your mesh",
    )
    @app_commands.describe(url="Post URL (X / Twitter, pixiv, Instagram, …)")
    async def shitpost(self, interaction: discord.Interaction, url: str) -> None:
        """URL を fixlink して実行チャンネル＋ルート先へ送る。"""
        # 権限チェック（空なら全員可）
        if not app_config.is_allowed("shitpost_role_ids", _member_role_ids(interaction)):
            # 拒否を ephemeral で返す
            await interaction.response.send_message(
                ti(interaction, "msg.no_permission"),
                ephemeral=True,
            )
            return
        # ギルドテキストチャンネル以外は不可
        if interaction.guild is None or not isinstance(
            interaction.channel, (discord.TextChannel, discord.Thread)
        ):
            # 案内
            await interaction.response.send_message(
                ti(interaction, "msg.guild_text_only"),
                ephemeral=True,
            )
            return
        # 処理開始を遅延応答（後で followup / edit）
        await interaction.response.defer(ephemeral=True)
        lang = lang_from_interaction(interaction)
        # ユーザーネーム（nick ではなく name）
        username = interaction.user.name
        # fixlink 変換
        fixed_url = apply_fixlink(url, app_config.fixlink_map)
        # 投稿本文（プロフィールリンク付き・メンションしない）
        content = _build_post_content(username, interaction.user.id, fixed_url)
        # 実行チャンネル
        origin = interaction.channel
        assert origin is not None
        try:
            # 実行チャンネルへ先に投稿（通知なし）
            await origin.send(content, silent=True)
        except discord.HTTPException as exc:
            # 投稿失敗
            logger.warning("Failed to post in origin channel: %s", exc)
            await interaction.followup.send(
                t("msg.post_origin_fail", lang),
                ephemeral=True,
            )
            return
        # 最新の routes.json を読んでから解決する
        try:
            app_config.routes_store.load()
        except Exception:
            # 読込失敗時はメモリ上のまま続行
            logger.exception("Failed to reload routes before shitpost")
        # ルート解決（スレッドなら親チャンネル ID も試す）
        origin_channel_id = str(origin.id)
        parent_channel_id = None
        if isinstance(origin, discord.Thread) and origin.parent_id is not None:
            parent_channel_id = str(origin.parent_id)
        destinations = resolve_destinations(
            app_config.routes_store.routes,
            str(interaction.guild.id),
            origin_channel_id,
        )
        # スレッド実行でヒットしなければ親チャンネルで再解決
        if not destinations and parent_channel_id is not None:
            destinations = resolve_destinations(
                app_config.routes_store.routes,
                str(interaction.guild.id),
                parent_channel_id,
            )
        # ルート未定義
        if not destinations:
            # ルート全体の有無でメッセージを分ける
            total_routes = len(app_config.routes_store.routes)
            if total_routes == 0:
                tip = t("msg.shitpost_no_routes_empty", lang)
            else:
                tip = t("msg.shitpost_no_routes_here", lang)
            await interaction.followup.send(
                t("msg.shitpost_no_dest", lang, tip=tip),
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
                # 転送投稿（通知なし）
                await channel.send(content, silent=True)
                # 成功
                sent += 1
            except discord.HTTPException as exc:
                # 権限不足等はスキップ
                logger.warning("Failed to send to %s/%s: %s", dest["guild_id"], dest["channel_id"], exc)
                skipped += 1
        # 結果を ephemeral で返す
        if sent == 0 and skipped > 0:
            await interaction.followup.send(
                t("msg.shitpost_all_failed", lang, ok=sent, skip=skipped),
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            t("msg.shitpost_done", lang, ok=sent, skip=skipped),
            ephemeral=True,
        )

    @app_commands.command(
        name="show_settings",
        description="Show your send/receive mesh for this server",
    )
    async def show_settings(self, interaction: discord.Interaction) -> None:
        """実行サーバー内のチャンネルが、どこへ送り／どこから受けているかを表示する。"""
        lang = lang_from_interaction(interaction)
        # ギルド必須（どのチャンネルからでも可）
        if interaction.guild is None:
            await interaction.response.send_message(
                ti(interaction, "msg.guild_only"),
                ephemeral=True,
            )
            return
        # 最新ルートを読む
        try:
            app_config.routes_store.load()
        except Exception:
            logger.exception("Failed to reload routes for show_settings")
        guild = interaction.guild
        guild_id = str(guild.id)
        # 自分のルートだけ（added_by で判別）
        my_id = str(interaction.user.id)
        routes = app_config.routes_store.routes
        # この鯖がルート元の一覧: channel_id -> destinations
        send_map: dict[str, list[dict[str, str]]] = {}
        # この鯖がルート先の一覧: channel_id -> list of sources
        recv_map: dict[str, list[tuple[str, str]]] = {}
        for route in routes:
            source = route.get("from") or {}
            from_guild = str(source.get("guild_id", ""))
            from_channel = str(source.get("channel_id", ""))
            destinations = route.get("to") or []
            # 送信側がこの鯖
            if from_guild == guild_id and from_channel:
                for item in destinations:
                    if not isinstance(item, dict):
                        continue
                    # 自分が追加した宛先だけ
                    if added_by_id(item) != my_id:
                        continue
                    send_map.setdefault(from_channel, []).append(
                        {
                            "guild_id": str(item.get("guild_id", "")),
                            "channel_id": str(item.get("channel_id", "")),
                        }
                    )
            # 受信側にこの鯖のチャンネルがあるか
            for item in destinations:
                if not isinstance(item, dict):
                    continue
                # 自分が追加したエッジだけ
                if added_by_id(item) != my_id:
                    continue
                to_guild = str(item.get("guild_id", ""))
                to_channel = str(item.get("channel_id", ""))
                if to_guild != guild_id or not to_channel:
                    continue
                recv_map.setdefault(to_channel, []).append((from_guild, from_channel))
        # 何も無ければ
        if not send_map and not recv_map:
            await interaction.response.send_message(
                ti(interaction, "msg.settings_none", guild=guild.name),
                ephemeral=True,
            )
            return

        def _ch_name(channel_id: str) -> str:
            # この鯖内のチャンネル名
            channel = guild.get_channel(int(channel_id)) if channel_id.isdigit() else None
            if channel is None and channel_id.isdigit():
                channel = guild.get_thread(int(channel_id))
            name = getattr(channel, "name", None)
            unknown = t("msg.unknown_user", lang)
            return f"#{name}" if name else f"#{unknown}({channel_id})"

        def _remote_label(remote_guild_id: str, remote_channel_id: str) -> str:
            # 他鯖含む表示
            remote_guild = (
                self.bot.get_guild(int(remote_guild_id)) if remote_guild_id.isdigit() else None
            )
            remote_channel = None
            if remote_guild is not None and remote_channel_id.isdigit():
                remote_channel = remote_guild.get_channel(int(remote_channel_id))
                if remote_channel is None:
                    remote_channel = remote_guild.get_thread(int(remote_channel_id))
            return _format_place(remote_guild, remote_channel)  # type: ignore[arg-type]

        user_label = _format_user_label(
            my_id,
            guild=guild,
            live_user=interaction.user,
            lang=lang,
        )
        lines = [
            t("msg.settings_header", lang, guild=guild.name, user=user_label),
        ]
        # 送信
        lines.append("\n" + t("msg.settings_send", lang))
        if not send_map:
            lines.append(t("msg.settings_none_item", lang))
        else:
            for channel_id, destinations in sorted(send_map.items(), key=lambda x: _ch_name(x[0])):
                lines.append(f"**{_ch_name(channel_id)}** →")
                for dest in destinations:
                    lines.append(f"- {_remote_label(dest['guild_id'], dest['channel_id'])}")
        # 受信
        lines.append("\n" + t("msg.settings_recv", lang))
        if not recv_map:
            lines.append(t("msg.settings_none_item", lang))
        else:
            for channel_id, sources in sorted(recv_map.items(), key=lambda x: _ch_name(x[0])):
                lines.append(f"**{_ch_name(channel_id)}** ←")
                for from_guild, from_channel in sources:
                    lines.append(f"- {_remote_label(from_guild, from_channel)}")
        # 実行チャンネルの位置づけ
        if interaction.channel is not None:
            here_id = str(interaction.channel.id)
            roles: list[str] = []
            if here_id in send_map:
                roles.append(t("msg.settings_role_send", lang))
            if here_id in recv_map:
                roles.append(t("msg.settings_role_recv", lang))
            if roles:
                sep = "・" if lang == "ja" else " / "
                lines.append(
                    "\n" + t("msg.settings_here_roles", lang, roles=sep.join(roles))
                )
            else:
                lines.append("\n" + t("msg.settings_here_none", lang))
        # Discord 制限に合わせて切り詰め
        text = "\n".join(lines)
        if len(text) > 1900:
            text = text[:1900] + "\n…"
        await interaction.response.send_message(text, ephemeral=True)

    @app_commands.command(
        name="show_settings_admin",
        description="[Admin] Show every user's send/receive mesh on this server",
    )
    @app_commands.default_permissions(administrator=True)
    async def show_settings_admin(self, interaction: discord.Interaction) -> None:
        """オーナー / Administrator 向けに全 added_by のルートを Components V2 で表示する。"""
        lang = lang_from_interaction(interaction)
        # ギルド必須
        if interaction.guild is None:
            await interaction.response.send_message(
                ti(interaction, "msg.guild_only"),
                ephemeral=True,
            )
            return
        # 権限チェック
        if not _is_guild_high_priv(interaction.user, interaction.guild):
            await interaction.response.send_message(
                ti(interaction, "msg.admin_denied"),
                ephemeral=True,
            )
            return
        # 最新ルート
        try:
            app_config.routes_store.load()
        except Exception:
            logger.exception("Failed to reload routes for show_settings_admin")
        # ページ生成
        pages = _build_admin_route_pages(
            self.bot,
            interaction.guild,
            app_config.routes_store.routes,
            lang,
        )
        # Components V2 ビュー
        view = AdminSettingsLayout(
            pages,
            interaction.user.id,
            interaction.guild.name,
            lang,
        )
        await interaction.response.send_message(view=view, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    """cog 登録。"""
    # GatewayCog を追加
    await bot.add_cog(GatewayCog(bot))
