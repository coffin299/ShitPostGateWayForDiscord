"""Bot 起動・スラッシュ同期・cog 読込。"""

from __future__ import annotations

import logging
import sys

import discord
from discord.ext import commands

from bot.config import app_config

# ログ基本設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
# このモジュール用ロガー
logger = logging.getLogger("shitpostgateway")


class ShitPostGateWayBot(commands.Bot):
    """ShitPostGateWayBOT 本体。"""

    def __init__(self) -> None:
        # スラッシュのみなので intents は標準で足りる
        intents = discord.Intents.default()
        # Bot 基底を初期化
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        # gateway / admin / help cog を読み込む
        await self.load_extension("bot.cogs.gateway")
        # ルーター・リロード cog
        await self.load_extension("bot.cogs.admin")
        # ヘルプ cog
        await self.load_extension("bot.cogs.help")
        # i18n Translator + スラッシュ文言（discord.py 2.x 正式経路）
        from bot.i18n import setup_i18n

        await setup_i18n(self.tree)

    async def on_ready(self) -> None:
        # 起動ログ
        logger.info("Logged in as %s (%s)", self.user, self.user and self.user.id)
        # スラッシュ同期対象ギルド
        guild_ids = app_config.guild_ids
        try:
            # 同期直前にも再適用
            from bot.i18n import setup_i18n

            await setup_i18n(self.tree)
            if guild_ids:
                # ギルド単位で即時同期
                for guild_id in guild_ids:
                    # オブジェクト化
                    guild = discord.Object(id=guild_id)
                    # グローバルコマンドをギルドへコピー
                    self.tree.copy_global_to(guild=guild)
                    # 同期する
                    await self.tree.sync(guild=guild)
                    # ログ
                    logger.info("Synced slash commands to guild %s", guild_id)
            else:
                # グローバル同期（反映に時間がかかることがある）
                synced = await self.tree.sync()
                # 件数ログ
                logger.info("Synced %s global slash commands", len(synced))
        except Exception:
            # 同期失敗は起動継続しつつログ
            logger.exception("Failed to sync slash commands")


def main() -> None:
    """エントリから呼ばれる起動処理。"""
    try:
        # 初回ファイル生成
        app_config.ensure_files()
        # 設定読込
        app_config.load()
    except Exception as exc:
        # 設定失敗は即終了
        print(f"Failed to load config: {exc}", file=sys.stderr)
        sys.exit(1)
    # トークン確認
    token = app_config.token
    # プレースホルダや空は拒否
    if not token or token == "YOUR_DISCORD_BOT_TOKEN":
        # 案内を出して終了
        print(
            "Edit config.yaml and set a valid Discord bot token, then restart.",
            file=sys.stderr,
        )
        sys.exit(1)
    # Bot インスタンス
    bot = ShitPostGateWayBot()
    # 起動する
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    # 直接実行時も main を呼ぶ
    main()
