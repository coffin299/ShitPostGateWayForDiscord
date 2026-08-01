"""/shitposting_router と /reload_config（プルダウンウィザード）。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import discord
from discord import app_commands, ui
from discord.ext import commands

from bot.config import app_config

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


def _sorted_text_channels(guild: discord.Guild) -> list[discord.TextChannel]:
    """テキスト系チャンネルを位置順で返す。"""
    # TextChannel（News 含む）のみ
    channels = [c for c in guild.channels if isinstance(c, discord.TextChannel)]
    # カテゴリ位置・チャンネル位置でソート
    return sorted(channels, key=lambda c: (c.category.position if c.category else -1, c.position, c.id))


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
    # 確定済み端点（mesh）
    endpoints: list[tuple[str, str]] = field(default_factory=list)
    # 次の番号（1-based）
    next_index: int = 1


def _guild_warning(bot: commands.Bot, guild_id: str) -> Optional[str]:
    """Bot 未参加なら警告文。"""
    # 数字確認
    if not guild_id.isdigit():
        return f"警告: サーバー ID `{guild_id}` が不正です"
    # キャッシュ確認
    if bot.get_guild(int(guild_id)) is None:
        return f"警告: Bot がサーバー `{guild_id}` に未参加です（投稿時はスキップされます）"
    return None


def _endpoint_label(bot: commands.Bot, guild_id: str, channel_id: str) -> str:
    """端点をサーバー名 / チャンネル名で返す。"""
    guild = bot.get_guild(int(guild_id)) if guild_id.isdigit() else None
    channel = guild.get_channel(int(channel_id)) if guild and channel_id.isdigit() else None
    return f"{guild.name if guild else '不明'} / #{getattr(channel, 'name', '不明')}"


async def _persist_mesh(interaction: discord.Interaction, session: RouterSession) -> None:
    """端点同士を全双方向で routes.json へ書く。"""
    # 2 未満はメッシュ不可
    if len(session.endpoints) < 2:
        await interaction.followup.send(
            "キャンセルしました（双方向設定には2箇所以上必要です）。",
            ephemeral=True,
        )
        return
    # 件数集計
    totals = {"added": 0, "appended": 0, "duplicate": 0}
    added_by = str(interaction.user.id)
    # 各端点を from にして他全員へ
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
            added_by,
        )
        # 合算
        for key in totals:
            totals[key] += counts.get(key, 0)
    # 再読込
    app_config.routes_store.load()
    bot: commands.Bot = interaction.client  # type: ignore[assignment]
    lines = [
        "双方向メッシュを保存しました。",
        f"追加/追記: {totals['added'] + totals['appended']} 件 / 重複スキップ: {totals['duplicate']} 件",
        f"端点数: {len(session.endpoints)}",
        "参加チャンネル:",
    ]
    for guild_id, channel_id in session.endpoints:
        lines.append(f"- {_endpoint_label(bot, guild_id, channel_id)}")
    await interaction.followup.send("\n".join(lines), ephemeral=True)


async def _persist_session(interaction: discord.Interaction, session: RouterSession) -> None:
    """モードに応じて保存する。"""
    # メッシュモード
    if session.mode == "mesh":
        await _persist_mesh(interaction, session)
        return
    # from または to が無ければ追加なし
    if not session.from_guild_id or not session.from_channel_id or not session.destinations:
        await interaction.followup.send(
            "キャンセルしました（ルートは追加されていません）。",
            ephemeral=True,
        )
        return
    # まとめて追記
    counts = app_config.routes_store.add_routes_batch(
        session.from_guild_id,
        session.from_channel_id,
        session.destinations,
        str(interaction.user.id),
    )
    # 再読込
    app_config.routes_store.load()
    # 警告収集
    warnings: list[str] = []
    warn = _guild_warning(interaction.client, session.from_guild_id)  # type: ignore[arg-type]
    if warn:
        warnings.append(warn)
    for guild_id, _channel_id in session.destinations:
        warn = _guild_warning(interaction.client, guild_id)  # type: ignore[arg-type]
        if warn and warn not in warnings:
            warnings.append(warn)
    # 表示用に名前解決
    bot: commands.Bot = interaction.client  # type: ignore[assignment]
    from_label = _endpoint_label(bot, session.from_guild_id, session.from_channel_id)
    lines = [
        "ルートを保存しました。",
        f"追加/追記: {counts.get('added', 0) + counts.get('appended', 0)} 件 / 重複スキップ: {counts.get('duplicate', 0)} 件",
        f"ルート元: {from_label}",
        "ルート先:",
    ]
    for guild_id, channel_id in session.destinations:
        lines.append(f"- {_endpoint_label(bot, guild_id, channel_id)}")
    lines.extend(warnings)
    await interaction.followup.send("\n".join(lines), ephemeral=True)


def _phase_prompt(session: RouterSession) -> str:
    """現在フェーズの案内文。"""
    # メッシュ: 端点選択
    if session.mode == "mesh":
        if session.phase in ("pick_to_guild", "pick_from_guild"):
            return (
                f"双方向メッシュ {session.next_index}/{session.count} の "
                "**サーバー** を選んでください。"
            )
        return (
            f"双方向メッシュ {session.next_index}/{session.count} の "
            "**チャンネル** を選んでください。"
        )
    # from サーバー選択
    if session.phase == "pick_from_guild":
        return "ルート元の **サーバー** を選んでください。"
    # from チャンネル選択
    if session.phase == "pick_from_channel":
        return "ルート元の **チャンネル** を選んでください。"
    # to サーバー
    if session.phase == "pick_to_guild":
        return f"ルート先 {session.next_index}/{session.count} の **サーバー** を選んでください。"
    # to チャンネル
    return f"ルート先 {session.next_index}/{session.count} の **チャンネル** を選んでください。"


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
                    placeholder="サーバーを選択",
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
                prev_btn = ui.Button(label="前のサーバー一覧", disabled=self.session.page <= 0)
                next_btn = ui.Button(
                    label="次のサーバー一覧",
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
            channels = _sorted_text_channels(guild) if guild else []
            start = self.session.page * _PAGE_SIZE
            page_items = channels[start : start + _PAGE_SIZE]
            options = [
                discord.SelectOption(
                    label=_truncate(f"#{channel.name}"),
                    value=str(channel.id),
                    description=_truncate(
                        channel.category.name if channel.category else "カテゴリなし",
                        100,
                    ),
                )
                for channel in page_items
            ]
            if options:
                # チャンネル Select
                select = ui.Select(
                    placeholder="チャンネルを選択",
                    min_values=1,
                    max_values=1,
                    options=options,
                )

                async def channel_callback(interaction: discord.Interaction, sel: ui.Select = select) -> None:
                    # 実行者チェック
                    if not await self._ensure_owner(interaction):
                        return
                    # 未選択ガード
                    if not sel.values or not self.session.pending_guild_id:
                        await interaction.response.send_message("選択が空です。", ephemeral=True)
                        return
                    guild_id = self.session.pending_guild_id
                    channel_id = str(sel.values[0])
                    # メッシュ: 端点を積む
                    if self.session.mode == "mesh":
                        endpoint = (guild_id, channel_id)
                        # 同一チャンネルの二重選択を拒否
                        if endpoint in self.session.endpoints:
                            await interaction.response.send_message(
                                "同じチャンネルは既に選ばれています。別のチャンネルを選んでください。",
                                ephemeral=True,
                            )
                            return
                        self.session.endpoints.append(endpoint)
                        self.session.pending_guild_id = None
                        self.session.page = 0
                        # 予定数に達したら全双方向保存
                        if len(self.session.endpoints) >= self.session.count:
                            _sessions.pop(self.session.user_id, None)
                            self.stop()
                            await interaction.response.edit_message(
                                content="双方向メッシュを保存しています…",
                                view=None,
                            )
                            await _persist_session(interaction, self.session)
                            return
                        # 次の端点へ
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
                            content="ルートを保存しています…",
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
                prev_btn = ui.Button(label="前のチャンネル一覧", disabled=self.session.page <= 0)
                next_btn = ui.Button(
                    label="次のチャンネル一覧",
                    disabled=self.session.page >= total_pages - 1,
                )
                prev_btn.callback = self._on_prev_page  # type: ignore[method-assign]
                next_btn.callback = self._on_next_page  # type: ignore[method-assign]
                self.add_item(prev_btn)
                self.add_item(next_btn)
        # キャンセルは常時
        cancel = ui.Button(label="キャンセル（ここまでで設定）", style=discord.ButtonStyle.secondary)
        cancel.callback = self._on_cancel  # type: ignore[method-assign]
        self.add_item(cancel)

    async def _ensure_owner(self, interaction: discord.Interaction) -> bool:
        """実行者以外は拒否。"""
        if interaction.user.id != self.session.user_id:
            await interaction.response.send_message("この操作は実行者のみ可能です。", ephemeral=True)
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
                content += "\n（サーバーが見つかりません）"
            elif not _sorted_text_channels(guild):
                content += "\n（このサーバーにテキストチャンネルがありません）"
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
            content="キャンセル処理中…",
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
        description="config.yaml と routes.json を再読み込みする",
    )
    async def reload_config(self, interaction: discord.Interaction) -> None:
        """設定をリロードする。"""
        # 権限
        if not app_config.is_allowed("reload_role_ids", _member_role_ids(interaction)):
            await interaction.response.send_message(
                "このコマンドを実行する権限がありません。",
                ephemeral=True,
            )
            return
        try:
            # 再読込
            app_config.reload()
        except Exception as exc:
            logger.exception("reload failed")
            await interaction.response.send_message(
                f"リロードに失敗しました: {exc}",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            "config.yaml と routes.json を再読み込みしました。",
            ephemeral=True,
        )

    async def _start_router_wizard(
        self,
        interaction: discord.Interaction,
        *,
        count: int,
        mode: str,
    ) -> None:
        """共通のルート設定ウィザードを開始する。"""
        # 権限
        if not app_config.is_allowed("router_role_ids", _member_role_ids(interaction)):
            await interaction.response.send_message(
                "このコマンドを実行する権限がありません。",
                ephemeral=True,
            )
            return
        # Bot 未参加
        if not self.bot.guilds:
            await interaction.response.send_message(
                "Bot が参加しているサーバーがありません。",
                ephemeral=True,
            )
            return
        # 共通サーバー判定のため defer
        await interaction.response.defer(ephemeral=True)
        # 共通サーバー列挙
        shared = await _shared_guilds(self.bot, interaction.user.id)
        if not shared:
            await interaction.followup.send(
                "あなたと Bot の両方が参加しているサーバーがありません。",
                ephemeral=True,
            )
            return
        # 初期フェーズ（mesh は端点選択から）
        phase = "pick_to_guild" if mode == "mesh" else "pick_from_guild"
        # セッション
        session = RouterSession(
            count=int(count),
            user_id=interaction.user.id,
            mode=mode,
            shared_guild_ids=[str(guild.id) for guild in shared],
            phase=phase,
            next_index=1,
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
        description="転送ルート追加用の選択パネルを開く",
    )
    @app_commands.describe(count="まとめて設定するルート先のサーバー数（省略時は1）")
    async def shitposting_router(
        self,
        interaction: discord.Interaction,
        count: app_commands.Range[int, 1, _MAX_COUNT] = 1,
    ) -> None:
        """単方向（from → to）ルートを追加する。"""
        await self._start_router_wizard(interaction, count=int(count), mode="one_way")

    @app_commands.command(
        name="shitposting_router_mesh",
        description="複数チャンネルを双方向メッシュで一括接続する",
    )
    @app_commands.describe(count="双方向にする鯖（チャンネル）数。同じ数だけ選択します")
    async def shitposting_router_mesh(
        self,
        interaction: discord.Interaction,
        count: app_commands.Range[int, 2, _MAX_COUNT],
    ) -> None:
        """選んだ N チャンネルを相互に双方向接続する。"""
        await self._start_router_wizard(interaction, count=int(count), mode="mesh")


async def setup(bot: commands.Bot) -> None:
    """cog 登録。"""
    await bot.add_cog(AdminCog(bot))
