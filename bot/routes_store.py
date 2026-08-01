"""routes.json の読込・追記・保存を担当する。"""

from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path
from typing import Any


class RoutesStore:
    """エッジ方式のルートを JSON で永続化する。"""

    def __init__(self, path: Path, default_path: Path) -> None:
        # 実ファイルパス
        self.path = path
        # テンプレパス
        self.default_path = default_path
        # 並行書き込み防止
        self._lock = threading.RLock()
        # メモリ上のルート配列
        self._routes: list[dict[str, Any]] = []

    def ensure_file(self) -> None:
        """routes.json が無ければ default からコピーする。"""
        # 既存なら何もしない
        if self.path.exists():
            return
        # テンプレ必須
        if not self.default_path.exists():
            raise FileNotFoundError(f"missing {self.default_path}")
        # コピーして実ファイルを作る
        shutil.copyfile(self.default_path, self.path)

    def load(self) -> None:
        """JSON をメモリへ読み込む。"""
        with self._lock:
            # ファイルを開く
            with self.path.open("r", encoding="utf-8") as fp:
                data = json.load(fp)
            # ルート配列を取り出す
            routes = data.get("routes", []) if isinstance(data, dict) else []
            # 型を正規化する
            self._routes = list(routes) if isinstance(routes, list) else []

    def save(self) -> None:
        """メモリ内容を JSON へ書き戻す。"""
        with self._lock:
            # 整形して保存する
            payload = {"routes": self._routes}
            # UTF-8 / インデント付き
            with self.path.open("w", encoding="utf-8") as fp:
                json.dump(payload, fp, ensure_ascii=False, indent=2)
                # 末尾改行を付ける
                fp.write("\n")

    @property
    def routes(self) -> list[dict[str, Any]]:
        """ルート一覧のコピーを返す。"""
        with self._lock:
            # 外部改変を避けるためコピー
            return [dict(route) for route in self._routes]

    def find_route_index(self, guild_id: str, channel_id: str) -> int:
        """同一 from のインデックスを返す。無ければ -1。"""
        with self._lock:
            # 全ルートを走査する
            for index, route in enumerate(self._routes):
                # from ブロックを取る
                source = route.get("from") or {}
                # guild / channel 両方が一致するか
                if (
                    str(source.get("guild_id", "")) == str(guild_id)
                    and str(source.get("channel_id", "")) == str(channel_id)
                ):
                    # 見つかった位置を返す
                    return index
            # 未登録
            return -1

    def get_destinations(self, guild_id: str, channel_id: str) -> list[dict[str, str]]:
        """指定 from の to 一覧を返す。"""
        with self._lock:
            # インデックス検索
            index = self.find_route_index(guild_id, channel_id)
            # 無ければ空
            if index < 0:
                return []
            # to 配列を正規化して返す
            destinations = self._routes[index].get("to") or []
            result: list[dict[str, str]] = []
            for item in destinations:
                # dict 以外は無視
                if not isinstance(item, dict):
                    continue
                # 文字列化したエントリを積む
                result.append(
                    {
                        "guild_id": str(item.get("guild_id", "")),
                        "channel_id": str(item.get("channel_id", "")),
                        "added_by": str(item.get("added_by", "")),
                    }
                )
            return result

    def add_route(
        self,
        from_guild_id: str,
        from_channel_id: str,
        to_guild_id: str,
        to_channel_id: str,
        added_by: str,
    ) -> str:
        """
        ルートを追記して保存する。

        Returns:
            "added" | "appended" | "duplicate"
        """
        with self._lock:
            # 追加する to エントリ
            destination = {
                "guild_id": str(to_guild_id),
                "channel_id": str(to_channel_id),
                "added_by": str(added_by),
            }
            # 既存 from を探す
            index = self.find_route_index(from_guild_id, from_channel_id)
            # 新規ルートの場合
            if index < 0:
                # from + to 1件で追加
                self._routes.append(
                    {
                        "from": {
                            "guild_id": str(from_guild_id),
                            "channel_id": str(from_channel_id),
                        },
                        "to": [destination],
                    }
                )
                # ディスクへ保存
                self.save()
                return "added"
            # 既存 to を取得
            destinations = self._routes[index].setdefault("to", [])
            # 同一 to が既にあるか確認
            for item in destinations:
                if not isinstance(item, dict):
                    continue
                if (
                    str(item.get("guild_id", "")) == str(to_guild_id)
                    and str(item.get("channel_id", "")) == str(to_channel_id)
                ):
                    # 二重登録はしない
                    return "duplicate"
            # 末尾へ追記
            destinations.append(destination)
            # 保存する
            self.save()
            return "appended"

    def add_routes_batch(
        self,
        from_guild_id: str,
        from_channel_id: str,
        destinations: list[tuple[str, str]],
        added_by: str,
    ) -> dict[str, int]:
        """複数 to をまとめて追記する。"""
        # 結果カウント
        counts = {"added": 0, "appended": 0, "duplicate": 0}
        # 1件ずつ処理（同一 from への連続追記）
        for to_guild_id, to_channel_id in destinations:
            # 1エッジ追記
            status = self.add_route(
                from_guild_id,
                from_channel_id,
                to_guild_id,
                to_channel_id,
                added_by,
            )
            # カウンタ更新（added は初回のみ意味があるが件数として数える）
            counts[status] = counts.get(status, 0) + 1
        return counts
