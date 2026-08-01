"""ルート解決ヘルパー。"""

from __future__ import annotations

from typing import Any


def resolve_destinations(
    routes: list[dict[str, Any]],
    from_guild_id: str,
    from_channel_id: str,
) -> list[dict[str, str]]:
    """
    from に一致する to 一覧を返す。
    実行チャンネル自身（同一 guild+channel）は除外する。
    """
    # 結果リスト
    destinations: list[dict[str, str]] = []
    # 全ルートを走査
    for route in routes:
        # from ブロック
        source = route.get("from") or {}
        # 一致しなければ次へ
        if str(source.get("guild_id", "")) != str(from_guild_id):
            continue
        if str(source.get("channel_id", "")) != str(from_channel_id):
            continue
        # to を積む
        for item in route.get("to") or []:
            if not isinstance(item, dict):
                continue
            guild_id = str(item.get("guild_id", ""))
            channel_id = str(item.get("channel_id", ""))
            # 自分自身へのループ送信は除外
            if guild_id == str(from_guild_id) and channel_id == str(from_channel_id):
                continue
            # 宛先を追加
            destinations.append(
                {
                    "guild_id": guild_id,
                    "channel_id": channel_id,
                    "added_by": str(item.get("added_by", "")),
                    "added_by_name": str(item.get("added_by_name", "")),
                }
            )
    return destinations
