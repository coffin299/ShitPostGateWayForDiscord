"""設定ファイルの読込・初回コピー・リロードを担当する。"""

from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import Any

import yaml

from bot.routes_store import RoutesStore

# リポジトリルート（bot/ の親）
ROOT_DIR = Path(__file__).resolve().parent.parent
# 実運用の YAML / テンプレ YAML
CONFIG_PATH = ROOT_DIR / "config.yaml"
CONFIG_DEFAULT_PATH = ROOT_DIR / "config.default.yaml"
# 文言 YAML / テンプレ
I18N_PATH = ROOT_DIR / "i18n.yaml"
I18N_DEFAULT_PATH = ROOT_DIR / "i18n.default.yaml"
# 実運用の routes JSON / テンプレ JSON
ROUTES_PATH = ROOT_DIR / "routes.json"
ROUTES_DEFAULT_PATH = ROOT_DIR / "routes.default.json"


class AppConfig:
    """config.yaml・i18n.yaml・routes.json をまとめて保持する。"""

    def __init__(self) -> None:
        # 並行アクセス用ロック
        self._lock = threading.RLock()
        # YAML 生データ
        self._data: dict[str, Any] = {}
        # 文言データ
        self._i18n: dict[str, Any] = {}
        # ルート永続化ストア
        self.routes_store = RoutesStore(ROUTES_PATH, ROUTES_DEFAULT_PATH)

    @property
    def i18n(self) -> dict[str, Any]:
        """文言ツリー（読み取り専用想定）。"""
        return self._i18n

    @property
    def token(self) -> str:
        # Bot トークンを文字列で返す
        return str(self._data.get("token", "")).strip()

    @property
    def guild_ids(self) -> list[int]:
        # スラッシュ同期用ギルド ID 一覧
        raw = self._data.get("guild_ids") or []
        # 数値化できるものだけ残す
        result: list[int] = []
        for item in raw:
            try:
                # snowflake を int に変換する
                result.append(int(item))
            except (TypeError, ValueError):
                # 不正値は無視する
                continue
        return result

    @property
    def fixlink_map(self) -> dict[str, str]:
        # ホスト置換表をコピーして返す
        raw = self._data.get("fixlink") or {}
        # キー・値とも文字列化する
        return {str(k).lower(): str(v) for k, v in raw.items()}

    @property
    def permissions(self) -> dict[str, list[int]]:
        # 権限ロール ID マップを返す
        raw = self._data.get("permissions") or {}
        out: dict[str, list[int]] = {}
        for key, values in raw.items():
            ids: list[int] = []
            for item in values or []:
                try:
                    # ロール ID を int 化
                    ids.append(int(item))
                except (TypeError, ValueError):
                    # 不正値はスキップ
                    continue
            out[str(key)] = ids
        return out

    def ensure_files(self) -> None:
        """初回起動時に default から実ファイルをコピーする。"""
        # config.yaml が無ければテンプレをコピー
        if not CONFIG_PATH.exists():
            # テンプレ必須
            if not CONFIG_DEFAULT_PATH.exists():
                raise FileNotFoundError(f"missing {CONFIG_DEFAULT_PATH}")
            # 実ファイルを生成
            shutil.copyfile(CONFIG_DEFAULT_PATH, CONFIG_PATH)
        # i18n.yaml も同様
        if not I18N_PATH.exists():
            if not I18N_DEFAULT_PATH.exists():
                raise FileNotFoundError(f"missing {I18N_DEFAULT_PATH}")
            shutil.copyfile(I18N_DEFAULT_PATH, I18N_PATH)
        # routes.json 側も同様
        self.routes_store.ensure_file()

    def load(self) -> None:
        """YAML / JSON をメモリへ読み込む。"""
        with self._lock:
            # YAML を開いてパースする
            with CONFIG_PATH.open("r", encoding="utf-8") as fp:
                loaded = yaml.safe_load(fp) or {}
            # dict 以外は拒否する
            if not isinstance(loaded, dict):
                raise ValueError("config.yaml must be a mapping")
            # メモリへ格納する
            self._data = loaded
            # 文言
            with I18N_PATH.open("r", encoding="utf-8") as fp:
                i18n_loaded = yaml.safe_load(fp) or {}
            if not isinstance(i18n_loaded, dict):
                raise ValueError("i18n.yaml must be a mapping")
            self._i18n = i18n_loaded
            # ルートも読み込む
            self.routes_store.load()

    def reload(self) -> None:
        """両方を再読込する。"""
        # load と同じ処理で上書きする
        self.load()

    def role_ids_for(self, key: str) -> list[int]:
        """permissions の指定キーのロール ID 一覧を返す。"""
        # 空なら制限なし扱い
        return list(self.permissions.get(key, []))

    def is_allowed(self, key: str, member_role_ids: set[int]) -> bool:
        """ロール制限が空なら全員可。指定があれば交差で判定。"""
        # 設定されたロール ID
        required = self.role_ids_for(key)
        # 空配列は制限なし
        if not required:
            return True
        # 1つでも所持していれば可
        return bool(member_role_ids.intersection(required))


# プロセス全体で共有する設定シングルトン
app_config = AppConfig()
