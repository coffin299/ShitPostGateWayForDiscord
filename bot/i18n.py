"""文言の読込・言語判定・スラッシュ説明への適用（discord.py Translator）。"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional, Union

import discord
from discord import Locale, app_commands
from discord.app_commands import (
    TranslationContextLocation,
    TranslationContextTypes,
    Translator,
    locale_str,
)

from bot.config import app_config

# モジュールロガー
logger = logging.getLogger(__name__)


def lang_from_locale(locale: Optional[Union[discord.Locale, str]]) -> str:
    """Discord locale → ja / en（日本語以外は英語）。"""
    # 無しは英語
    if locale is None:
        return "en"
    # Enum なら .value（'ja' 等）。str(Locale.x) 依存は避ける
    if isinstance(locale, Locale):
        raw = str(locale.value)
    else:
        raw = str(getattr(locale, "value", locale) or "")
    # 正規化
    raw = raw.lower().replace("_", "-")
    # ja / ja-JP など
    if raw == "ja" or raw.startswith("ja-"):
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
        text = node.get(lang) or node.get("ja") or node.get("en")
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


def _ls(key: str, *, limit: int = 100) -> locale_str:
    """i18n キーから locale_str を作る（Discord 既定文言は日本語）。"""
    # 日本語を API のデフォルト description にする
    message = t(key, "ja")[:limit]
    # Translator が extras のキーで他言語を返す
    return locale_str(message, i18n_key=key)


def _key_from_context(context: TranslationContextTypes) -> Optional[str]:
    """コンテキストから i18n キーを推定する（extras が無い場合の保険）。"""
    location = context.location
    data = context.data
    # コマンド説明
    if location is TranslationContextLocation.command_description:
        name = getattr(data, "name", None)
        if name:
            return f"commands.{name}.description"
    # オプション説明
    if location is TranslationContextLocation.parameter_description:
        # data は Parameter ラッパのことがある
        cmd = getattr(data, "command", None) or getattr(data, "parent", None)
        opt = getattr(data, "name", None)
        cmd_name = getattr(cmd, "name", None)
        if cmd_name and opt:
            return f"commands.{cmd_name}.options.{opt}"
    # Choice 名
    if location is TranslationContextLocation.choice_name:
        value = getattr(data, "value", None)
        if value is not None:
            return f"choices.remove_all_scope.{value}"
    return None


class I18nTranslator(Translator):
    """i18n.yaml を使う discord.py Translator。"""

    async def translate(
        self,
        string: locale_str,
        locale: Locale,
        context: TranslationContextTypes,
    ) -> Optional[str]:
        """同期時に各 locale 向け文言を返す。"""
        # extras 優先
        key = string.extras.get("i18n_key")
        # 無ければコンテキストから
        if not key:
            key = _key_from_context(context)
        # キー不明なら翻訳なし
        if not key:
            return None
        # 言語
        lang = lang_from_locale(locale)
        # 文言
        text = t(str(key), lang)[:100]
        # 既定メッセージと同じなら None（Discord 推奨）
        if text == string.message:
            return None
        return text


def apply_slash_localizations(tree: app_commands.CommandTree) -> None:
    """
    登録済みスラッシュへ i18n の locale_str を載せる。
    discord.py 2.x は Translator + locale_str が正式経路
    （description_localizations の直接代入は効かない）。
    """
    # コマンド定義
    commands_meta = app_config.i18n.get("commands") or {}
    # 選択肢定義
    choices_meta = app_config.i18n.get("choices") or {}
    # ツリー上のコマンド
    for cmd in tree.get_commands():
        meta = commands_meta.get(cmd.name)
        if not isinstance(meta, Mapping):
            continue
        # コマンド説明（既定は日本語）
        desc_key = f"commands.{cmd.name}.description"
        if isinstance(meta.get("description"), Mapping):
            ls = _ls(desc_key)
            cmd.description = ls.message
            cmd._locale_description = ls  # type: ignore[attr-defined]
        # オプション
        options = meta.get("options") or {}
        if not isinstance(options, Mapping):
            continue
        params = getattr(cmd, "_params", None) or {}
        for opt_name, param in params.items():
            opt_meta = options.get(opt_name)
            if not isinstance(opt_meta, Mapping):
                continue
            opt_key = f"commands.{cmd.name}.options.{opt_name}"
            param.description = _ls(opt_key)
            # remove_all の scope choices
            if opt_name == "scope" and getattr(param, "choices", None):
                scope_map = choices_meta.get("remove_all_scope") or {}
                for choice in param.choices:
                    choice_pair = scope_map.get(choice.value)
                    if not isinstance(choice_pair, Mapping):
                        continue
                    choice_key = f"choices.remove_all_scope.{choice.value}"
                    choice_ls = _ls(choice_key)
                    choice.name = choice_ls.message
                    choice._locale_name = choice_ls


async def setup_i18n(tree: app_commands.CommandTree) -> None:
    """Translator 登録 + スラッシュ文言適用。"""
    # Translator を載せる（同期時に翻訳が走る）
    await tree.set_translator(I18nTranslator())
    # locale_str / 日本語デフォルトを適用
    apply_slash_localizations(tree)
