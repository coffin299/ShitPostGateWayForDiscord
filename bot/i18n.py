"""文言の読込・言語判定・スラッシュ説明への適用。"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional, Union

import discord
from discord import app_commands

from bot.config import app_config

# モジュールロガー
logger = logging.getLogger(__name__)


def lang_from_locale(locale: Optional[Union[discord.Locale, str]]) -> str:
    """Discord locale → ja / en（日本語以外は英語）。"""
    # 文字列化
    raw = str(locale or "").lower()
    # ja / ja-JP など
    if raw.startswith("ja"):
        return "ja"
    return "en"


def lang_from_interaction(interaction: discord.Interaction) -> str:
    """Interaction のユーザー locale から言語を決める。"""
    return lang_from_locale(interaction.locale)


def _dig(data: Mapping[str, Any], dotted: str) -> Any:
    """ドット区切りキーでネストを辿る。"""
    # 現在ノード
    node: Any = data
    # セグメントごと
    for part in dotted.split("."):
        # dict 以外は打ち切り
        if not isinstance(node, Mapping):
            return None
        # 次へ
        node = node.get(part)
    return node


def t(key: str, lang: str, **kwargs: Any) -> str:
    """
    i18n キーから文言を取る。
    ノードが {ja, en} なら言語別、文字列ならそのまま。
    """
    # ルート辞書
    root = app_config.i18n
    # キー解決
    node = _dig(root, key)
    # 未定義
    if node is None:
        logger.warning("Missing i18n key: %s", key)
        return key
    # 言語マップ
    if isinstance(node, Mapping):
        text = node.get(lang) or node.get("en") or node.get("ja")
        if text is None:
            return key
        text = str(text)
    else:
        text = str(node)
    # 末尾改行を YAML | から削る
    text = text.rstrip("\n")
    # format 引数
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, ValueError):
            return text
    return text


def ti(interaction: discord.Interaction, key: str, **kwargs: Any) -> str:
    """Interaction locale 付きの t。"""
    return t(key, lang_from_interaction(interaction), **kwargs)


def _loc_map(pair: Mapping[str, Any]) -> dict[str, str]:
    """ja/en ペアから Discord localization dict を作る。"""
    out: dict[str, str] = {}
    # 日本語
    if pair.get("ja"):
        out["ja"] = str(pair["ja"])[:100]
    # 英語（米・英）
    if pair.get("en"):
        en = str(pair["en"])[:100]
        out["en-US"] = en
        out["en-GB"] = en
    return out


def apply_slash_localizations(tree: app_commands.CommandTree) -> None:
    """登録済みスラッシュへ description / option / choice の多言語を適用する。"""
    # コマンド定義
    commands_meta = app_config.i18n.get("commands") or {}
    # 選択肢定義
    choices_meta = app_config.i18n.get("choices") or {}
    # ツリー上のコマンド
    for cmd in tree.get_commands():
        # グループは今回なし想定だが名前で引く
        meta = commands_meta.get(cmd.name)
        if not isinstance(meta, Mapping):
            continue
        # 説明
        desc = meta.get("description") or {}
        if isinstance(desc, Mapping):
            # デフォルトは英語（非日本語クライアント向け）
            if desc.get("en"):
                cmd.description = str(desc["en"])[:100]
            locs = _loc_map(desc)
            if locs:
                cmd.description_localizations = locs  # type: ignore[assignment]
        # オプション
        options = meta.get("options") or {}
        if not isinstance(options, Mapping):
            continue
        params = getattr(cmd, "_params", None) or {}
        for opt_name, param in params.items():
            opt_meta = options.get(opt_name)
            if not isinstance(opt_meta, Mapping):
                continue
            if opt_meta.get("en"):
                param.description = str(opt_meta["en"])[:100]
            opt_locs = _loc_map(opt_meta)
            if opt_locs:
                param.description_localizations = opt_locs  # type: ignore[assignment]
            # remove_all の scope choices
            if opt_name == "scope" and getattr(param, "choices", None):
                scope_map = choices_meta.get("remove_all_scope") or {}
                for choice in param.choices:
                    choice_pair = scope_map.get(choice.value)
                    if not isinstance(choice_pair, Mapping):
                        continue
                    if choice_pair.get("en"):
                        choice.name = str(choice_pair["en"])[:100]
                    choice_locs = _loc_map(choice_pair)
                    if choice_locs:
                        choice.name_localizations = choice_locs  # type: ignore[assignment]
