"""SNS URL を Discord embed 用 fixlink ドメインへ変換する。"""

from __future__ import annotations

from urllib.parse import urlparse, urlunparse


def apply_fixlink(url: str, host_map: dict[str, str]) -> str:
    """
    対応ホストなら置換し、それ以外はそのまま返す。

    Args:
        url: 入力 URL
        host_map: 元ホスト -> 置換ホスト（小文字キー想定）
    """
    # 前後空白を除去する
    raw = (url or "").strip()
    # 空ならそのまま
    if not raw:
        return raw
    # スキーム無しの場合に備えて仮付与でパースする
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    # ホストを小文字化（www 含む）
    host = (parsed.hostname or "").lower()
    # マップに無ければ無変換
    if host not in host_map:
        return raw if "://" in raw else urlunparse(parsed)
    # 置換後ホスト
    new_host = host_map[host]
    # netloc の userinfo / port は使わない単純置換
    # パス・クエリ・フラグメントは維持する
    replaced = parsed._replace(netloc=new_host, scheme=parsed.scheme or "https")
    # URL 文字列へ戻す
    return urlunparse(replaced)
