"""/shitposting_router と /reload_config。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

import discord
from discord import app_commands, ui
from discord.ext import commands

from bot.config import app_config

# モジュールロガー
logger = logging.getLogger(__name__)
# snowflake 簡易チェック
_SNOWFLAKE_RE = re.compile(r"^\d{17,20}$")
# ウィザード上限
_MAX_COUNT = 10
# ユーザー別ウィザード状態
_sessions: dict[int, "RouterSession"] = {}


def _member_role_ids(interaction: discord.Interaction) -> set[int]:
    """実行者ロール ID 集合。"""
    # Member 以外は空
    if not isinstance(interaction.user, discord.Member):
        return set()
    # ロール ID を集める
    return {role.id for role in interaction.user.roles}


def _normalize_id(value: str) -> Optional[str]:
    """ID 文字列を正規化する。不正なら None。"""
    # 空白除去
    text = (value or "").strip()
    # 数字のみ許可
    if not _SNOWFLAKE_RE.match(text):
        return None
    return text


@dataclass
class RouterSession:
    """ウィザード途中状態。"""

    # 予定ルート先数
    count: int
    # 実行者 ID
    user_id: int
    # 送信元（確定後に入る）
    from_guild_id: Optional[str] = None
    from_channel_id: Optional[str] = None
    # 確定済みルート先
    destinations: list[tuple[str, str]] = field(default_factory=list)
    # 次に入力するルート先番号（1-based）
    next_index: int = 1


def _guild_warning(bot: commands.Bot, guild_id: str) -> Optional[str]:
    """Bot 未参加なら警告文を返す。"""
    # 数字でなければ警告
    if not guild_id.isdigit():
        return f"警告: サーバー ID `{guild_id}` が不正です"
    # キャッシュに無ければ未参加扱い
    if bot.get_guild(int(guild_id)) is None:
        return f"警告: Bot がサーバー `{guild_id}` に未参加です（投稿時はスキップされます）"
    return None


async def _persist_session(
    interaction: discord.Interaction,
    session: RouterSession,
) -> None:
    """確定済み内容だけ routes.json へ書き込む。"""
    # from 未確定 or 先が空なら追加なし
    if not session.from_guild_id or not session.from_channel_id or not session.destinations:
        await interaction.followup.send(
            "キャンセルしました（ルートは追加されていません）。",
            ephemeral=True,
        )
        return
    # バッチ追記
    counts = app_config.routes_store.add_routes_batch(
        session.from_guild_id,
        session.from_channel_id,
        session.destinations,
        str(interaction.user.id),
    )
    # メモリ再読込（他プロセス編集は想定しないが整合のため）
    app_config.routes_store.load()
    # 警告を集める
    warnings: list[str] = []
    # from 側警告
    warn = _guild_warning(interaction.client, session.from_guild_id)  # type: ignore[arg-type]
    if warn:
        warnings.append(warn)
    # to 側警告
    for guild_id, _channel_id in session.destinations:
        warn = _guild_warning(interaction.client, guild_id)  # type: ignore[arg-type]
        if warn and warn not in warnings:
            warnings.append(warn)
    # 件数メッセージ
    lines = [
        "ルートを保存しました。",
        f"追加/追記: {counts.get('added', 0) + counts.get('appended', 0)} 件 / 重複スキップ: {counts.get('duplicate', 0)} 件",
        f"ルート元: `{session.from_guild_id}` / `{session.from_channel_id}`",
        "ルート先:",
    ]
    # 先を列挙
    for guild_id, channel_id in session.destinations:
        lines.append(f"- `{guild_id}` / `{channel_id}`")
    # 警告追記
    lines.extend(warnings)
    # ephemeral 応答
    await interaction.followup.send("\n".join(lines), ephemeral=True)


class WizardActionView(ui.View):
    """次へ / キャンセルボタン。"""

    def __init__(self, session: RouterSession) -> None:
        # 5 分でタイムアウト
        super().__init__(timeout=300)
        # セッション参照
        self.session = session

    @ui.button(label="次のルート先を入力", style=discord.ButtonStyle.primary)
    async def continue_button(
        self,
        interaction: discord.Interaction,
        button: ui.Button,
    ) -> None:
        """次のルート先 Modal を開く。"""
        # 実行者以外は拒否
        if interaction.user.id != self.session.user_id:
            await interaction.response.send_message("この操作は実行者のみ可能です。", ephemeral=True)
            return
        # 次 Modal
        modal = DestinationModal(self.session)
        # Modal 表示
        await interaction.response.send_modal(modal)
        # この View は用済み
        self.stop()

    @ui.button(label="キャンセル（ここまでで設定）", style=discord.ButtonStyle.secondary)
    async def cancel_button(
        self,
        interaction: discord.Interaction,
        button: ui.Button,
    ) -> None:
        """未確定を捨て、確定済みだけ保存して終了。"""
        # 実行者以外は拒否
        if interaction.user.id != self.session.user_id:
            await interaction.response.send_message("この操作は実行者のみ可能です。", ephemeral=True)
            return
        # 応答遅延
        await interaction.response.defer(ephemeral=True)
        # セッション破棄
        _sessions.pop(self.session.user_id, None)
        # 保存処理
        await _persist_session(interaction, self.session)
        # View 停止
        self.stop()


class SingleRouteModal(ui.Modal, title="ルート追加"):
    """count=1 用の一枚 Modal。"""

    # 送信元サーバー
    from_guild = ui.Label(
        text="送信元サーバー ID",
        description="ルート元のサーバー snowflake",
        component=ui.TextInput(
            style=discord.TextStyle.short,
            required=True,
            max_length=20,
            placeholder="123456789012345678",
        ),
    )
    # 送信元チャンネル
    from_channel = ui.Label(
        text="送信元チャンネル ID",
        description="ルート元のテキストチャンネル snowflake",
        component=ui.TextInput(
            style=discord.TextStyle.short,
            required=True,
            max_length=20,
            placeholder="123456789012345678",
        ),
    )
    # 送信先サーバー
    to_guild = ui.Label(
        text="送信先サーバー ID",
        description="ルート先のサーバー snowflake",
        component=ui.TextInput(
            style=discord.TextStyle.short,
            required=True,
            max_length=20,
            placeholder="123456789012345678",
        ),
    )
    # 送信先チャンネル
    to_channel = ui.Label(
        text="送信先チャンネル ID",
        description="ルート先のテキストチャンネル snowflake",
        component=ui.TextInput(
            style=discord.TextStyle.short,
            required=True,
            max_length=20,
            placeholder="123456789012345678",
        ),
    )

    def __init__(self, bot: commands.Bot) -> None:
        # Modal 初期化
        super().__init__()
        # Bot 参照（警告用）
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """1 件ルートを保存する。"""
        # 入力取得
        from_guild = _normalize_id(self.from_guild.component.value)  # type: ignore[union-attr]
        from_channel = _normalize_id(self.from_channel.component.value)  # type: ignore[union-attr]
        to_guild = _normalize_id(self.to_guild.component.value)  # type: ignore[union-attr]
        to_channel = _normalize_id(self.to_channel.component.value)  # type: ignore[union-attr]
        # 不正チェック
        if not all([from_guild, from_channel, to_guild, to_channel]):
            await interaction.response.send_message(
                "ID は 17〜20 桁の数字で入力してください。",
                ephemeral=True,
            )
            return
        # 型ガード後の値
        assert from_guild and from_channel and to_guild and to_channel
        # 追記
        status = app_config.routes_store.add_route(
            from_guild,
            from_channel,
            to_guild,
            to_channel,
            str(interaction.user.id),
        )
        # 再読込
        app_config.routes_store.load()
        # メッセージ分岐
        if status == "duplicate":
            msg = "既に同じルートが登録されています。"
        else:
            msg = "ルートを保存しました。"
        # 警告
        warnings = []
        for gid in (from_guild, to_guild):
            warn = _guild_warning(self.bot, gid)
            if warn and warn not in warnings:
                warnings.append(warn)
        # 結合
        body = "\n".join([msg, f"`{from_guild}`/`{from_channel}` → `{to_guild}`/`{to_channel}`", *warnings])
        # ephemeral
        await interaction.response.send_message(body, ephemeral=True)


class SourceModal(ui.Modal, title="ルート元"):
    """ウィザード: 送信元入力。"""

    from_guild = ui.Label(
        text="送信元サーバー ID",
        component=ui.TextInput(style=discord.TextStyle.short, required=True, max_length=20),
    )
    from_channel = ui.Label(
        text="送信元チャンネル ID",
        component=ui.TextInput(style=discord.TextStyle.short, required=True, max_length=20),
    )

    def __init__(self, session: RouterSession) -> None:
        # Modal 初期化
        super().__init__()
        # セッション
        self.session = session

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """送信元をバッファへ入れ、次アクションを出す。"""
        # 正規化
        from_guild = _normalize_id(self.from_guild.component.value)  # type: ignore[union-attr]
        from_channel = _normalize_id(self.from_channel.component.value)  # type: ignore[union-attr]
        # 不正
        if not from_guild or not from_channel:
            await interaction.response.send_message(
                "ID は 17〜20 桁の数字で入力してください。",
                ephemeral=True,
            )
            return
        # セッション更新
        self.session.from_guild_id = from_guild
        self.session.from_channel_id = from_channel
        self.session.next_index = 1
        # 続けて最初のルート先 Modal を開く（Modal 送信への応答として可）
        await interaction.response.send_modal(DestinationModal(self.session))


class DestinationModal(ui.Modal, title="ルート先"):
    """ウィザード: 送信先入力。"""

    to_guild = ui.Label(
        text="送信先サーバー ID",
        component=ui.TextInput(style=discord.TextStyle.short, required=True, max_length=20),
    )
    to_channel = ui.Label(
        text="送信先チャンネル ID",
        component=ui.TextInput(style=discord.TextStyle.short, required=True, max_length=20),
    )

    def __init__(self, session: RouterSession) -> None:
        # タイトルに進捗を入れる
        super().__init__(title=f"ルート先 {session.next_index}/{session.count}")
        # セッション
        self.session = session

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """ルート先を確定バッファへ追加する。"""
        # 正規化
        to_guild = _normalize_id(self.to_guild.component.value)  # type: ignore[union-attr]
        to_channel = _normalize_id(self.to_channel.component.value)  # type: ignore[union-attr]
        # 不正
        if not to_guild or not to_channel:
            await interaction.response.send_message(
                "ID は 17〜20 桁の数字で入力してください。",
                ephemeral=True,
            )
            return
        # バッファへ追加
        self.session.destinations.append((to_guild, to_channel))
        # 完了か
        if len(self.session.destinations) >= self.session.count:
            # セッション除去
            _sessions.pop(self.session.user_id, None)
            # defer して保存
            await interaction.response.defer(ephemeral=True)
            await _persist_session(interaction, self.session)
            return
        # 次の番号
        self.session.next_index = len(self.session.destinations) + 1
        # 続行 / キャンセル
        view = WizardActionView(self.session)
        await interaction.response.send_message(
            f"ルート先 {len(self.session.destinations)}/{self.session.count} を受け付けました。",
            view=view,
            ephemeral=True,
        )


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
            # 失敗
            logger.exception("reload failed")
            await interaction.response.send_message(
                f"リロードに失敗しました: {exc}",
                ephemeral=True,
            )
            return
        # 成功
        await interaction.response.send_message(
            "config.yaml と routes.json を再読み込みしました。",
            ephemeral=True,
        )

    @app_commands.command(
        name="shitposting_router",
        description="転送ルート追加用の入力フォームを開く",
    )
    @app_commands.describe(count="まとめて設定するルート先のサーバー数（省略時は1）")
    async def shitposting_router(
        self,
        interaction: discord.Interaction,
        count: app_commands.Range[int, 1, _MAX_COUNT] = 1,
    ) -> None:
        """Modal / ウィザードでルートを追加する。"""
        # 権限
        if not app_config.is_allowed("router_role_ids", _member_role_ids(interaction)):
            await interaction.response.send_message(
                "このコマンドを実行する権限がありません。",
                ephemeral=True,
            )
            return
        # 単発
        if count == 1:
            # 一枚 Modal
            await interaction.response.send_modal(SingleRouteModal(self.bot))
            return
        # ウィザード用セッション
        session = RouterSession(count=count, user_id=interaction.user.id)
        # 上書き保存
        _sessions[interaction.user.id] = session
        # ルート元 Modal
        await interaction.response.send_modal(SourceModal(session))


async def setup(bot: commands.Bot) -> None:
    """cog 登録。"""
    # AdminCog を追加
    await bot.add_cog(AdminCog(bot))
