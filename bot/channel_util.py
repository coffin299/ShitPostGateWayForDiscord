"""チャンネル属性ヘルパー。"""

from __future__ import annotations

import discord


def channel_is_nsfw(channel: discord.abc.GuildChannel | discord.Thread | None) -> bool:
    """チャンネル（またはスレッド親）が NSFW か判定する。"""
    # 無しは非 NSFW
    if channel is None:
        return False
    # スレッドは親の NSFW を見る
    if isinstance(channel, discord.Thread) and channel.parent is not None:
        channel = channel.parent
    # is_nsfw メソッド / 属性
    is_nsfw = getattr(channel, "is_nsfw", None)
    # 呼び出し可能なら実行
    if callable(is_nsfw):
        return bool(is_nsfw())
    # 属性 bool の場合
    if isinstance(is_nsfw, bool):
        return is_nsfw
    # 不明は非 NSFW 扱い
    return False
