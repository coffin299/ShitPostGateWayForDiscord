"""/shitposting_router と /reload_config（プルダウンウィザード）。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import discord
from discord import app_commands, ui
from discord.ext import commands

from bot.channel_util import channel_is_nsfw
from bot.config import app_config
from bot.i18n import lang_from_interaction, t, ti

# モジュールロガー
logger = logging.getLogger(__name__)
# ルート先上限
_MAX_COUNT = 10
# Select 1ページあたり最大件数（Discord 上限 25）
_PAGE_SIZE = 25
# ユーザー別ウィザード状態
_sessions: dict[int, "RouterSession"] = {}


def _member_role_ids(interaction: discord.Interaction) -> set[int]:
    """実行者ロール ID 集合。"""
    # Member 以外は空
    if not isinstance(interaction.user, discord.Member):
        return set()
    # ロール ID を集める
    return {role.id for role in interaction.user.roles}


def _truncate(text: str, limit: int = 100) -> str:
    """Select ラベル用に切り詰める。"""
    # 短いならそのまま
    if len(text) <= limit:
        return text
    # 末尾を省略
    return text[: limit - 1] + "…"


def _added_by_fields(user: discord.abc.User) -> tuple[str, str]:
    """ルート保存用の added_by / added_by_name を返す。"""
    # ユーザー ID
    user_id = str(user.id)
    # 追加時点のユーザー名（グローバル名）
    user_name = str(getattr(user, "name", "") or "")
    return user_id, user_name


async def _user_in_guild(guild: discord.Guild, user_id: int) -> bool:
    """ユーザーがそのサーバーに居るか確認する。"""
    # キャッシュにあれば即 True
    if guild.get_member(user_id) is not None:
        return True
    try:
        # API で個別取得（Members Intent 無しでも ID 指定取得は可能なことが多い）
        await guild.fetch_member(user_id)
        return True
    except discord.NotFound:
        # 未所属
        return False
    except discord.HTTPException:
        # 権限不足等は居ない扱い
        return False


async def _shared_guilds(bot: commands.Bot, user_id: int) -> list[discord.Guild]:
    """Bot と実行者の両方が参加しているサーバーを名前順で返す。"""
    # 結果リスト
    shared: list[discord.Guild] = []
    # Bot 参加サーバーを走査
    for guild in bot.guilds:
        # ユーザーも居るか
        if await _user_in_guild(guild, user_id):
            # 共通サーバーとして追加
            shared.append(guild)
    # 名前順
    return sorted(shared, key=lambda g: g.name.lower())


def _guilds_from_ids(bot: commands.Bot, guild_ids: list[str]) -> list[discord.Guild]:
    """キャッシュ済み ID から Guild オブジェクトを復元する。"""
    # 結果
    guilds: list[discord.Guild] = []
    for guild_id in guild_ids:
        # 数字以外は無視
        if not guild_id.isdigit():
            continue
        # キャッシュ取得
        guild = bot.get_guild(int(guild_id))
        # 抜けていたらスキップ
        if guild is None:
            continue
        guilds.append(guild)
    return guilds


def _sorted_text_channels(
    guild: discord.Guild,
    *,
    nsfw_only: bool = False,
) -> list[discord.TextChannel]:
    """テキスト系チャンネルを位置順で返す（必要なら NSFW のみ）。"""
    # TextChannel（News 含む）のみ
    channels = [c for c in guild.channels if isinstance(c, discord.TextChannel)]
    # NSFW 元からのルート／メッシュでは NSFW チャンネルだけ候補にする
    if nsfw_only:
        channels = [c for c in channels if channel_is_nsfw(c)]
    # カテゴリ位置・チャンネル位置でソート
    return sorted(channels, key=lambda c: (c.category.position if c.category else -1, c.position, c.id))


def _mark_nsfw_if_needed(session: "RouterSession", bot: commands.Bot, guild_id: str, channel_id: str) -> None:
    """選んだチャンネルが NSFW なら以降の候補を NSFW 限定にする。"""
    # 既に限定済みなら何もしない
    if session.require_nsfw:
        return
    # ギルド解決
    guild = bot.get_guild(int(guild_id)) if guild_id.isdigit() else None
    if guild is None:
        return
    # チャンネル解決
    channel = guild.get_channel(int(channel_id)) if channel_id.isdigit() else None
    if channel is None and channel_id.isdigit():
        channel = guild.get_thread(int(channel_id))
    # NSFW ならフラグを立てる
    if channel_is_nsfw(channel):
        session.require_nsfw = True


@dataclass
class RouterSession:
    """ウィザード途中状態。"""

    # 予定ルート先数（one_way）または端点数（mesh）
    count: int
    # 実行者
    user_id: int
    # one_way=単方向追加 / mesh=双方向メッシュ
    mode: str = "one_way"
    # Bot とユーザー共通のサーバー ID（起動時に確定）
    shared_guild_ids: list[str] = field(default_factory=list)
    # フェーズ: pick_from_guild / pick_from_channel / pick_to_guild / pick_to_channel
    phase: str = "pick_from_guild"
    # ページ番号（ギルド / チャンネル共用）
    page: int = 0
    # 選択中サーバー（チャンネル選択前）
    pending_guild_id: Optional[str] = None
    # 確定済み from（one_way）
    from_guild_id: Optional[str] = None
    from_channel_id: Optional[str] = None
    # 確定済み to（one_way）
    destinations: list[tuple[str, str]] = field(default_factory=list)
    # 確定済み端点（mesh / mesh_add の新規分）
    endpoints: list[tuple[str, str]] = field(default_factory=list)
    # 既存メッシュ構成員（mesh_add 用）
    existing_endpoints: list[tuple[str, str]] = field(default_factory=list)
    # UI 言語（ja / en）
    lang: str = "en"
    # NSFW 元なら候補を NSFW チャンネルに限定
    require_nsfw: bool = False
    # 次の番号（1-based）
    next_index: int = 1


def _guild_warning(bot: commands.Bot, guild_id: str, lang: str) -> Optional[str]:
    """Bot 未参加なら警告文。"""
    # 数字確認
    if not guild_id.isdigit():
        return t("msg.guild_warn", lang, guild_id=guild_id)
    # キャッシュ確認
    if bot.get_guild(int(guild_id)) is None:
        return t("msg.guild_warn", lang, guild_id=guild_id)
    return None


def _endpoint_label(bot: commands.Bot, guild_id: str, channel_id: str, lang: str = "en") -> str:
    """端点をサーバー名 / チャンネル名で返す。"""
    guild = bot.get_guild(int(guild_id)) if guild_id.isdigit() else None
    channel = guild.get_channel(int(channel_id)) if guild and channel_id.isdigit() else None
    unknown = t("msg.unknown_user", lang)
    return f"{guild.name if guild else unknown} / #{getattr(channel, 'name', unknown)}"


def _collect_mesh_members(guild_id: str, channel_id: str) -> list[tuple[str, str]]:
    """実行チャンネルを起点に、つながっているチャンネル集合を集める。"""
    # 重複排除用
    members: set[tuple[str, str]] = {(str(guild_id), str(channel_id))}
    # 最新を読む
    app_config.routes_store.load()
    # このチャンネルからの宛先
    outgoing = app_config.routes_store.get_destinations(guild_id, channel_id)
    for dest in outgoing:
        members.add((dest["guild_id"], dest["channel_id"]))
    # このチャンネルを to に含むルートも取り込む
    for route in app_config.routes_store.routes:
        source = route.get("from") or {}
        source_pair = (str(source.get("guild_id", "")), str(source.get("channel_id", "")))
        destinations = route.get("to") or []
        hit = False
        for item in destinations:
            if not isinstance(item, dict):
                continue
            if str(item.get("channel_id", "")) == str(channel_id) and str(
                item.get("guild_id", "")
            ) == str(guild_id):
                hit = True
                break
        if not hit:
            continue
        # from を追加
        if source_pair[0] and source_pair[1]:
            members.add(source_pair)
        # 同ルートの他 to も同じメッシュとみなす
        for item in destinations:
            if not isinstance(item, dict):
                continue
            members.add((str(item.get("guild_id", "")), str(item.get("channel_id", ""))))
    # 空 ID を除去
    cleaned = [(g, c) for g, c in members if g and c]
    return cleaned


async def _persist_mesh(interaction: discord.Interaction, session: RouterSession) -> None:
    """選んだチャンネル同士を全双方向で routes.json へ書く。"""
    lang = session.lang
    # 2 未満はメッシュ不可
    if len(session.endpoints) < 2:
        await interaction.followup.send(
            t("msg.mesh_need_two", lang),
            ephemeral=True,
        )
        return
    # 件数集計
    totals = {"added": 0, "appended": 0, "duplicate": 0}
    # 追加者 ID + 名前
    adder_id, adder_name = _added_by_fields(interaction.user)
    # 各チャンネルを from にして「自分以外の全員」へ（本当のメッシュ）
    for index, (from_guild, from_channel) in enumerate(session.endpoints):
        # 自分以外
        others = [
            endpoint
            for other_index, endpoint in enumerate(session.endpoints)
            if other_index != index
        ]
        # バッチ追記
        counts = app_config.routes_store.add_routes_batch(
            from_guild,
            from_channel,
            others,
            adder_id,
            adder_name,
        )
        # 合算
        for key in totals:
            totals[key] += counts.get(key, 0)
    # 再読込
    app_config.routes_store.load()
    bot: commands.Bot = interaction.client  # type: ignore[assignment]
    header = t(
        "msg.mesh_saved",
        lang,
        changed=totals["added"] + totals["appended"],
        duplicate=totals["duplicate"],
        count=len(session.endpoints),
    )
    lines = [header]
    for guild_id, channel_id in session.endpoints:
        lines.append(f"- {_endpoint_label(bot, guild_id, channel_id, lang)}")
    await interaction.followup.send("\n".join(lines), ephemeral=True)


async def _persist_mesh_add(interaction: discord.Interaction, session: RouterSession) -> None:
    """既存メッシュへ新規チャンネルを双方向で合流させる。"""
    lang = session.lang
    # 新規が無ければ何もしない
    if not session.endpoints:
        await interaction.followup.send(
            t("msg.mesh_add_empty", lang),
            ephemeral=True,
        )
        return
    existing = list(session.existing_endpoints)
    news = list(session.endpoints)
    # 既存が空なら異常
    if len(existing) < 1:
        await interaction.followup.send(
            t("msg.mesh_add_missing", lang),
            ephemeral=True,
        )
        return
    adder_id, adder_name = _added_by_fields(interaction.user)
    totals = {"added": 0, "appended": 0, "duplicate": 0}
    # 全体（既存 + 新規）
    combined = existing + news

    def _merge(counts: dict[str, int]) -> None:
        for key in totals:
            totals[key] += counts.get(key, 0)

    # 新規それぞれ → 自分以外の全員
    for new_endpoint in news:
        others = [endpoint for endpoint in combined if endpoint != new_endpoint]
        _merge(
            app_config.routes_store.add_routes_batch(
                new_endpoint[0],
                new_endpoint[1],
                others,
                adder_id,
                adder_name,
            )
        )
    # 既存それぞれ → 新規全員（既存同士は既にある想定）
    for old_endpoint in existing:
        _merge(
            app_config.routes_store.add_routes_batch(
                old_endpoint[0],
                old_endpoint[1],
                news,
                adder_id,
                adder_name,
            )
        )
    # 再読込
    app_config.routes_store.load()
    bot: commands.Bot = interaction.client  # type: ignore[assignment]
    header = t(
        "msg.mesh_add_saved",
        lang,
        changed=totals["added"] + totals["appended"],
        duplicate=totals["duplicate"],
        count=len(news),
    )
    lines = [header]
    for guild_id, channel_id in news:
        lines.append(f"- {_endpoint_label(bot, guild_id, channel_id, lang)}")
    lines.append(t("msg.mesh_members_label", lang))
    for guild_id, channel_id in combined:
        lines.append(f"- {_endpoint_label(bot, guild_id, channel_id, lang)}")
    await interaction.followup.send("\n".join(lines), ephemeral=True)


async def _persist_session(interaction: discord.Interaction, session: RouterSession) -> None:
    """モードに応じて保存する。"""
    # メッシュ新規
    if session.mode == "mesh":
        await _persist_mesh(interaction, session)
        return
    # メッシュへ追加
    if session.mode == "mesh_add":
        await _persist_mesh_add(interaction, session)
        return
    # from または to が無ければ追加なし
    if not session.from_guild_id or not session.from_channel_id or not session.destinations:
        await interaction.followup.send(
            t("msg.route_cancel_empty", session.lang),
            ephemeral=True,
        )
        return
    # まとめて追記
    adder_id, adder_name = _added_by_fields(interaction.user)
    counts = app_config.routes_store.add_routes_batch(
        session.from_guild_id,
        session.from_channel_id,
        session.destinations,
        adder_id,
        adder_name,
    )
    # 再読込
    app_config.routes_store.load()
    lang = session.lang
    # 警告収集
    warnings: list[str] = []
    warn = _guild_warning(interaction.client, session.from_guild_id, lang)  # type: ignore[arg-type]
    if warn:
        warnings.append(warn)
    for guild_id, _channel_id in session.destinations:
        warn = _guild_warning(interaction.client, guild_id, lang)  # type: ignore[arg-type]
        if warn and warn not in warnings:
            warnings.append(warn)
    # 表示用に名前解決
    bot: commands.Bot = interaction.client  # type: ignore[assignment]
    from_label = _endpoint_label(bot, session.from_guild_id, session.from_channel_id, lang)
    header = t(
        "msg.route_saved",
        lang,
        changed=counts.get("added", 0) + counts.get("appended", 0),
        duplicate=counts.get("duplicate", 0),
        from_label=from_label,
    )
    lines = [header]
    for guild_id, channel_id in session.destinations:
        lines.append(f"- {_endpoint_label(bot, guild_id, channel_id, lang)}")
    lines.extend(warnings)
    await interaction.followup.send("\n".join(lines), ephemeral=True)


def _phase_prompt(session: RouterSession) -> str:
    """現在フェーズの案内文。"""
    lang = session.lang
    # メッシュ新規
    if session.mode == "mesh":
        if session.phase in ("pick_to_guild", "pick_from_guild"):
            return t(
                "msg.prompt_mesh_guild",
                lang,
                index=session.next_index,
                total=session.count,
            )
        return t(
            "msg.prompt_mesh_channel",
            lang,
            index=session.next_index,
            total=session.count,
        )
    # メッシュ追加
    if session.mode == "mesh_add":
        if session.phase in ("pick_to_guild", "pick_from_guild"):
            return t(
                "msg.prompt_mesh_add_guild",
                lang,
                index=session.next_index,
                total=session.count,
            )
        return t(
            "msg.prompt_mesh_add_channel",
            lang,
            index=session.next_index,
            total=session.count,
        )
    # from サーバー選択
    if session.phase == "pick_from_guild":
        return t("msg.prompt_from_guild", lang)
    # from チャンネル選択
    if session.phase == "pick_from_channel":
        return t("msg.prompt_from_channel", lang)
    # to サーバー
    if session.phase == "pick_to_guild":
        return t(
            "msg.prompt_to_guild",
            lang,
            index=session.next_index,
            total=session.count,
        )
    # to チャンネル
    return t(
        "msg.prompt_to_channel",
        lang,
        index=session.next_index,
        total=session.count,
    )


class RouterWizardView(ui.View):
    """サーバー / チャンネルのプルダウン＋ページ送り＋キャンセル。"""

    def __init__(self, bot: commands.Bot, session: RouterSession) -> None:
        # 10 分タイムアウト
        super().__init__(timeout=600)
        # Bot 参照
        self.bot = bot
        # セッション
        self.session = session
        # 動的コンポーネントを組み立て
        self._rebuild_items()

    def _rebuild_items(self) -> None:
        """フェーズに応じて Select / ボタンを載せ替える。"""
        # 既存をクリア
        self.clear_items()
        # ギルド選択フェーズ
        if self.session.phase in ("pick_from_guild", "pick_to_guild"):
            # 共通サーバーのみ（セッションにキャッシュ済み）
            guilds = _guilds_from_ids(self.bot, self.session.shared_guild_ids)
            # ページ切片
            start = self.session.page * _PAGE_SIZE
            page_items = guilds[start : start + _PAGE_SIZE]
            # 選択肢が空なら何も出せない
            options = [
                discord.SelectOption(
                    label=_truncate(guild.name),
                    value=str(guild.id),
                    description=_truncate(f"ID {guild.id}", 100),
                )
                for guild in page_items
            ]
            # 1件以上あるときだけ Select
            if options:
                # Select を作る
                select = ui.Select(
                    placeholder=t("msg.select_guild_placeholder", self.session.lang),
                    min_values=1,
                    max_values=1,
                    options=options,
                )

                async def guild_callback(interaction: discord.Interaction, sel: ui.Select = select) -> None:
                    # 実行者チェック
                    if not await self._ensure_owner(interaction):
                        return
                    # 選択値
                    selected = sel.values[0]
                    # 保留ギルド
                    self.session.pending_guild_id = str(selected)
                    # チャンネル選択へ
                    if self.session.phase == "pick_from_guild":
                        self.session.phase = "pick_from_channel"
                    else:
                        self.session.phase = "pick_to_channel"
                    # ページリセット
                    self.session.page = 0
                    await self._refresh(interaction)

                # コールバック設定
                select.callback = guild_callback  # type: ignore[method-assign]
                self.add_item(select)
            # ページ送り
            total_pages = max(1, (len(guilds) + _PAGE_SIZE - 1) // _PAGE_SIZE)
            if total_pages > 1:
                prev_btn = ui.Button(
                    label=t("msg.btn_prev", self.session.lang),
                    disabled=self.session.page <= 0,
                )
                next_btn = ui.Button(
                    label=t("msg.btn_next", self.session.lang),
                    disabled=self.session.page >= total_pages - 1,
                )
                prev_btn.callback = self._on_prev_page  # type: ignore[method-assign]
                next_btn.callback = self._on_next_page  # type: ignore[method-assign]
                self.add_item(prev_btn)
                self.add_item(next_btn)
        else:
            # チャンネル選択フェーズ
            guild = None
            if self.session.pending_guild_id and self.session.pending_guild_id.isdigit():
                guild = self.bot.get_guild(int(self.session.pending_guild_id))
            channels = (
                _sorted_text_channels(guild, nsfw_only=self.session.require_nsfw)
                if guild
                else []
            )
            start = self.session.page * _PAGE_SIZE
            page_items = channels[start : start + _PAGE_SIZE]
            # NSFW 限定時はプレースホルダも分かりやすく
            channel_ph = (
                t("msg.select_channel_nsfw_placeholder", self.session.lang)
                if self.session.require_nsfw
                else t("msg.select_channel_placeholder", self.session.lang)
            )
            options = [
                discord.SelectOption(
                    label=_truncate(f"#{channel.name}"),
                    value=str(channel.id),
                    description=_truncate(
                        channel.category.name
                        if channel.category
                        else t("msg.no_category", self.session.lang),
                        100,
                    ),
                )
                for channel in page_items
            ]
            if options:
                # チャンネル Select
                select = ui.Select(
                    placeholder=channel_ph,
                    min_values=1,
                    max_values=1,
                    options=options,
                )

                async def channel_callback(interaction: discord.Interaction, sel: ui.Select = select) -> None:
                    # 実行者チェック
                    if not await self._ensure_owner(interaction):
                        return
                    lang = self.session.lang
                    # 未選択ガード
                    if not sel.values or not self.session.pending_guild_id:
                        await interaction.response.send_message(
                            t("msg.empty_selection", lang),
                            ephemeral=True,
                        )
                        return
                    guild_id = self.session.pending_guild_id
                    channel_id = str(sel.values[0])
                    # NSFW を選んだ／元が NSFW なら以降の候補を NSFW のみに
                    _mark_nsfw_if_needed(self.session, self.bot, guild_id, channel_id)
                    # メッシュ新規 / メッシュ追加: チャンネルを積む
                    if self.session.mode in ("mesh", "mesh_add"):
                        endpoint = (guild_id, channel_id)
                        # 新規選択内の重複
                        if endpoint in self.session.endpoints:
                            await interaction.response.send_message(
                                t("msg.duplicate_pick", lang),
                                ephemeral=True,
                            )
                            return
                        # 既存メッシュへの再追加を拒否
                        if endpoint in self.session.existing_endpoints:
                            await interaction.response.send_message(
                                t("msg.mesh_already_in", lang),
                                ephemeral=True,
                            )
                            return
                        self.session.endpoints.append(endpoint)
                        self.session.pending_guild_id = None
                        self.session.page = 0
                        # 予定数に達したら保存
                        if len(self.session.endpoints) >= self.session.count:
                            _sessions.pop(self.session.user_id, None)
                            self.stop()
                            await interaction.response.edit_message(
                                content=t("msg.saving", lang),
                                view=None,
                            )
                            await _persist_session(interaction, self.session)
                            return
                        # 次へ
                        self.session.next_index = len(self.session.endpoints) + 1
                        self.session.phase = "pick_to_guild"
                        await self._refresh(interaction)
                        return
                    # from 確定（one_way）
                    if self.session.phase == "pick_from_channel":
                        self.session.from_guild_id = guild_id
                        self.session.from_channel_id = channel_id
                        self.session.pending_guild_id = None
                        self.session.next_index = 1
                        self.session.page = 0
                        self.session.phase = "pick_to_guild"
                        await self._refresh(interaction)
                        return
                    # to 確定（one_way）
                    self.session.destinations.append((guild_id, channel_id))
                    self.session.pending_guild_id = None
                    self.session.page = 0
                    # 必要数に達したら保存
                    if len(self.session.destinations) >= self.session.count:
                        _sessions.pop(self.session.user_id, None)
                        self.stop()
                        await interaction.response.edit_message(
                            content=t("msg.saving", lang),
                            view=None,
                        )
                        await _persist_session(interaction, self.session)
                        return
                    # 次のルート先
                    self.session.next_index = len(self.session.destinations) + 1
                    self.session.phase = "pick_to_guild"
                    await self._refresh(interaction)

                select.callback = channel_callback  # type: ignore[method-assign]
                self.add_item(select)
            total_pages = max(1, (len(channels) + _PAGE_SIZE - 1) // _PAGE_SIZE)
            if total_pages > 1:
                prev_btn = ui.Button(
                    label=t("msg.btn_prev", self.session.lang),
                    disabled=self.session.page <= 0,
                )
                next_btn = ui.Button(
                    label=t("msg.btn_next", self.session.lang),
                    disabled=self.session.page >= total_pages - 1,
                )
                prev_btn.callback = self._on_prev_page  # type: ignore[method-assign]
                next_btn.callback = self._on_next_page  # type: ignore[method-assign]
                self.add_item(prev_btn)
                self.add_item(next_btn)
        # キャンセルは常時
        cancel = ui.Button(
            label=t("msg.btn_cancel_save", self.session.lang),
            style=discord.ButtonStyle.secondary,
        )
        cancel.callback = self._on_cancel  # type: ignore[method-assign]
        self.add_item(cancel)

    async def _ensure_owner(self, interaction: discord.Interaction) -> bool:
        """実行者以外は拒否。"""
        if interaction.user.id != self.session.user_id:
            await interaction.response.send_message(
                t("msg.owner_only_button", self.session.lang),
                ephemeral=True,
            )
            return False
        return True

    async def _refresh(self, interaction: discord.Interaction) -> None:
        """同じ ephemeral メッセージを更新する。"""
        # コンポーネント再構築
        self._rebuild_items()
        # 案内文
        content = _phase_prompt(self.session)
        # チャンネルが0件のとき追記
        if self.session.phase in ("pick_from_channel", "pick_to_channel"):
            guild = (
                self.bot.get_guild(int(self.session.pending_guild_id))
                if self.session.pending_guild_id and self.session.pending_guild_id.isdigit()
                else None
            )
            if guild is None:
                content += "\n" + t("msg.guild_missing", self.session.lang)
            elif not _sorted_text_channels(guild, nsfw_only=self.session.require_nsfw):
                if self.session.require_nsfw:
                    content += "\n" + t("msg.no_nsfw_text_channels", self.session.lang)
                else:
                    content += "\n" + t("msg.no_text_channels", self.session.lang)
        # 編集で差し替え
        await interaction.response.edit_message(content=content, view=self)

    async def _on_prev_page(self, interaction: discord.Interaction) -> None:
        """前ページ。"""
        if not await self._ensure_owner(interaction):
            return
        # ページを戻す
        self.session.page = max(0, self.session.page - 1)
        await self._refresh(interaction)

    async def _on_next_page(self, interaction: discord.Interaction) -> None:
        """次ページ。"""
        if not await self._ensure_owner(interaction):
            return
        # ページを進める
        self.session.page += 1
        await self._refresh(interaction)

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        """未確定を捨て、確定済みだけ保存。"""
        if not await self._ensure_owner(interaction):
            return
        # セッション除去
        _sessions.pop(self.session.user_id, None)
        self.stop()
        # メッセージ更新
        await interaction.response.edit_message(
            content=t("msg.cancel_processing", self.session.lang),
            view=None,
        )
        # 部分保存
        await _persist_session(interaction, self.session)

    async def on_timeout(self) -> None:
        """タイムアウトでセッション破棄。"""
        # メモリから消す
        _sessions.pop(self.session.user_id, None)


class AdminCog(commands.Cog):
    """管理・ルート設定コマンド。"""

    def __init__(self, bot: commands.Bot) -> None:
        # Bot 参照
        self.bot = bot

    @app_commands.command(
        name="reload_config",
        description="Reload config, i18n, and routes",
    )
    async def reload_config(self, interaction: discord.Interaction) -> None:
        """設定をリロードする。"""
        # 権限
        if not app_config.is_allowed("reload_role_ids", _member_role_ids(interaction)):
            await interaction.response.send_message(
                ti(interaction, "msg.no_permission"),
                ephemeral=True,
            )
            return
        try:
            # 再読込
            app_config.reload()
            # メモリ上のスラッシュ説明も更新
            from bot.i18n import setup_i18n

            await setup_i18n(self.bot.tree)
        except Exception as exc:
            logger.exception("reload failed")
            await interaction.response.send_message(
                ti(interaction, "msg.reload_fail_detail", error=str(exc)),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            ti(interaction, "msg.reload_ok"),
            ephemeral=True,
        )

    async def _start_router_wizard(
        self,
        interaction: discord.Interaction,
        *,
        count: int,
        mode: str,
        existing_endpoints: list[tuple[str, str]] | None = None,
    ) -> None:
        """共通のルート設定ウィザードを開始する。"""
        # 権限
        if not app_config.is_allowed("router_role_ids", _member_role_ids(interaction)):
            await interaction.response.send_message(
                ti(interaction, "msg.no_permission"),
                ephemeral=True,
            )
            return
        # Bot 未参加
        if not self.bot.guilds:
            await interaction.response.send_message(
                ti(interaction, "msg.no_bot_guilds"),
                ephemeral=True,
            )
            return
        # 共通サーバー判定のため defer
        await interaction.response.defer(ephemeral=True)
        # 共通サーバー列挙
        shared = await _shared_guilds(self.bot, interaction.user.id)
        if not shared:
            await interaction.followup.send(
                ti(interaction, "msg.no_shared_guilds"),
                ephemeral=True,
            )
            return
        # 初期フェーズ
        phase = "pick_to_guild" if mode in ("mesh", "mesh_add") else "pick_from_guild"
        # 実行チャンネル or 既存メッシュが NSFW なら候補を NSFW 限定
        require_nsfw = channel_is_nsfw(
            interaction.channel if isinstance(interaction.channel, discord.abc.GuildChannel) else None
        )
        if isinstance(interaction.channel, discord.Thread):
            require_nsfw = channel_is_nsfw(interaction.channel)
        # 既存メッシュに NSFW が居れば限定
        if not require_nsfw and existing_endpoints:
            for guild_id, channel_id in existing_endpoints:
                guild = self.bot.get_guild(int(guild_id)) if str(guild_id).isdigit() else None
                if guild is None:
                    continue
                channel = guild.get_channel(int(channel_id)) if str(channel_id).isdigit() else None
                if channel is None and str(channel_id).isdigit():
                    channel = guild.get_thread(int(channel_id))
                if channel_is_nsfw(channel):
                    require_nsfw = True
                    break
        # セッション
        session = RouterSession(
            count=int(count),
            user_id=interaction.user.id,
            mode=mode,
            shared_guild_ids=[str(guild.id) for guild in shared],
            phase=phase,
            next_index=1,
            existing_endpoints=list(existing_endpoints or []),
            lang=lang_from_interaction(interaction),
            require_nsfw=require_nsfw,
        )
        _sessions[interaction.user.id] = session
        # View 表示
        view = RouterWizardView(self.bot, session)
        await interaction.followup.send(
            _phase_prompt(session),
            view=view,
            ephemeral=True,
        )

    @app_commands.command(
        name="shitposting_router",
        description="Add a one-way forward route (pick server → channel)",
    )
    @app_commands.describe(count="Number of destinations to add (default 1)")
    async def shitposting_router(
        self,
        interaction: discord.Interaction,
        count: app_commands.Range[int, 1, _MAX_COUNT] = 1,
    ) -> None:
        """単方向（from → to）ルートを追加する。"""
        await self._start_router_wizard(interaction, count=int(count), mode="one_way")

    @app_commands.command(
        name="shitposting_router_mesh",
        description="Connect chosen channels into a bidirectional send/receive mesh",
    )
    @app_commands.describe(count="How many channels to include in the mesh (2+)")
    async def shitposting_router_mesh(
        self,
        interaction: discord.Interaction,
        count: app_commands.Range[int, 2, _MAX_COUNT],
    ) -> None:
        """選んだ N チャンネルを相互に双方向接続する。"""
        await self._start_router_wizard(interaction, count=int(count), mode="mesh")

    @app_commands.command(
        name="shitposting_router_mesh_add",
        description="Add channels to an existing send/receive mesh",
    )
    @app_commands.describe(count="How many channels to add (default 1)")
    async def shitposting_router_mesh_add(
        self,
        interaction: discord.Interaction,
        count: app_commands.Range[int, 1, _MAX_COUNT] = 1,
    ) -> None:
        """実行チャンネルのメッシュへ、新規チャンネルを双方向で合流させる。"""
        # ギルドチャンネル必須
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message(
                ti(interaction, "msg.guild_channel_only"),
                ephemeral=True,
            )
            return
        # 既存メンバー収集
        existing = _collect_mesh_members(
            str(interaction.guild.id),
            str(interaction.channel.id),
        )
        # 自分以外が居ないとメッシュ未参加扱い
        if len(existing) < 2:
            await interaction.response.send_message(
                ti(interaction, "msg.mesh_not_member"),
                ephemeral=True,
            )
            return
        await self._start_router_wizard(
            interaction,
            count=int(count),
            mode="mesh_add",
            existing_endpoints=existing,
        )

    @app_commands.command(
        name="shitposting_router_remove",
        description="Remove one mesh destination from this channel",
    )
    @app_commands.describe(channel_id="Destination channel ID to remove")
    async def shitposting_router_remove(
        self,
        interaction: discord.Interaction,
        channel_id: str,
    ) -> None:
        """実行チャンネルを from として、指定チャンネルへのルートを外す。"""
        # 権限
        if not app_config.is_allowed("router_role_ids", _member_role_ids(interaction)):
            await interaction.response.send_message(
                ti(interaction, "msg.no_permission"),
                ephemeral=True,
            )
            return
        # ギルドチャンネル必須
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message(
                ti(interaction, "msg.guild_channel_only"),
                ephemeral=True,
            )
            return
        # ID 正規化
        target_id = channel_id.strip()
        if not target_id.isdigit():
            await interaction.response.send_message(
                ti(interaction, "msg.channel_id_digits"),
                ephemeral=True,
            )
            return
        # 解除実行
        removed = app_config.routes_store.remove_destination(
            str(interaction.guild.id),
            str(interaction.channel.id),
            target_id,
        )
        # 再読込
        app_config.routes_store.load()
        if not removed:
            await interaction.response.send_message(
                ti(interaction, "msg.remove_failed"),
                ephemeral=True,
            )
            return
        # 名前解決
        lang = lang_from_interaction(interaction)
        label = target_id
        for guild in self.bot.guilds:
            channel = guild.get_channel(int(target_id))
            if channel is not None:
                label = _endpoint_label(self.bot, str(guild.id), target_id, lang)
                break
        await interaction.response.send_message(
            ti(interaction, "msg.remove_ok", label=label),
            ephemeral=True,
        )

    @app_commands.command(
        name="shitposting_router_remove_all",
        description="Delete send/receive mesh routes in bulk",
    )
    @app_commands.describe(scope="What to delete")
    @app_commands.choices(
        scope=[
            app_commands.Choice(
                name="Delete all outbound mesh routes from this channel",
                value="source",
            ),
            app_commands.Choice(
                name="Remove this channel from the mesh (send + receive)",
                value="purge",
            ),
            app_commands.Choice(
                name="Delete all send/receive mesh routes",
                value="all",
            ),
        ]
    )
    async def shitposting_router_remove_all(
        self,
        interaction: discord.Interaction,
        scope: app_commands.Choice[str],
    ) -> None:
        """送受信メッシュ一括削除（all は確認ボタン付き）。"""
        # 権限
        if not app_config.is_allowed("router_role_ids", _member_role_ids(interaction)):
            await interaction.response.send_message(
                ti(interaction, "msg.no_permission"),
                ephemeral=True,
            )
            return
        scope_value = scope.value
        # all は確認 UI
        if scope_value == "all":
            view = ClearAllConfirmView(
                interaction.user.id,
                lang_from_interaction(interaction),
            )
            await interaction.response.send_message(
                ti(interaction, "msg.clear_all_confirm"),
                view=view,
                ephemeral=True,
            )
            return
        # ギルドチャンネル必須
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message(
                ti(interaction, "msg.guild_channel_only"),
                ephemeral=True,
            )
            return
        guild_id = str(interaction.guild.id)
        channel_id = str(interaction.channel.id)
        if scope_value == "source":
            removed = app_config.routes_store.clear_from_route(guild_id, channel_id)
            app_config.routes_store.load()
            if removed == 0:
                await interaction.response.send_message(
                    ti(interaction, "msg.clear_source_empty"),
                    ephemeral=True,
                )
                return
            await interaction.response.send_message(
                ti(interaction, "msg.clear_source_ok", removed=removed),
                ephemeral=True,
            )
            return
        stats = app_config.routes_store.purge_channel(guild_id, channel_id)
        app_config.routes_store.load()
        await interaction.response.send_message(
            ti(
                interaction,
                "msg.clear_purge_ok",
                from_removed=stats.get("from_removed", 0),
                to_removed=stats.get("to_removed", 0),
            ),
            ephemeral=True,
        )


class ClearAllConfirmView(ui.View):
    """全ルート削除の確認。"""

    def __init__(self, user_id: int, lang: str) -> None:
        # 短めタイムアウト
        super().__init__(timeout=60)
        # 実行者
        self.user_id = user_id
        # 言語
        self.lang = lang
        # ボタン
        confirm = ui.Button(
            label=t("msg.btn_confirm_delete", lang),
            style=discord.ButtonStyle.danger,
        )
        abort = ui.Button(
            label=t("msg.btn_abort", lang),
            style=discord.ButtonStyle.secondary,
        )
        confirm.callback = self._confirm  # type: ignore[method-assign]
        abort.callback = self._abort  # type: ignore[method-assign]
        self.add_item(confirm)
        self.add_item(abort)

    async def _confirm(self, interaction: discord.Interaction) -> None:
        """全削除を実行する。"""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                t("msg.owner_only_button", self.lang),
                ephemeral=True,
            )
            return
        count = app_config.routes_store.clear_all()
        app_config.routes_store.load()
        self.stop()
        await interaction.response.edit_message(
            content=t("msg.clear_all_ok", self.lang, count=count),
            view=None,
        )

    async def _abort(self, interaction: discord.Interaction) -> None:
        """キャンセル。"""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                t("msg.owner_only_button", self.lang),
                ephemeral=True,
            )
            return
        self.stop()
        await interaction.response.edit_message(
            content=t("msg.clear_all_cancelled", self.lang),
            view=None,
        )


async def setup(bot: commands.Bot) -> None:
    """cog 登録。"""
    await bot.add_cog(AdminCog(bot))
