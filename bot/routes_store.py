"""routes.json の読込・追記・保存を担当する。"""

from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path
from typing import Any


def added_by_id(item: dict[str, Any]) -> str:
    """to エントリから追加者ユーザー ID を取り出す（旧形式互換）。"""
    # 文字列 ID を返す
    return str(item.get("added_by", "") or "")


def added_by_name(item: dict[str, Any]) -> str:
    """to エントリから追加時点のユーザー名を取り出す（無ければ空）。"""
    # 保存時の表示名
    return str(item.get("added_by_name", "") or "")


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
        """ルート一覧のディープコピーを返す。"""
        with self._lock:
            # ネストまでコピーして外部改変を防ぐ
            return json.loads(json.dumps(self._routes))

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
                        "added_by": added_by_id(item),
                        "added_by_name": added_by_name(item),
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
        adder_name: str = "",
    ) -> str:
        """
        ルートを追記して保存する。

        Returns:
            "added" | "appended" | "duplicate"
        """
        with self._lock:
            # 追加する to エントリ（ID + 追加時点のユーザー名）
            destination = {
                "guild_id": str(to_guild_id),
                "channel_id": str(to_channel_id),
                "added_by": str(added_by),
                "added_by_name": str(adder_name),
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
        adder_name: str = "",
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
                adder_name,
            )
            # カウンタ更新（added は初回のみ意味があるが件数として数える）
            counts[status] = counts.get(status, 0) + 1
        return counts

    def remove_destination(
        self,
        from_guild_id: str,
        from_channel_id: str,
        to_channel_id: str,
    ) -> bool:
        """
        指定 from から to_channel_id の宛先を1件外す。
        to が空になったら from エントリごと削除する。
        """
        with self._lock:
            # from を探す
            index = self.find_route_index(from_guild_id, from_channel_id)
            # 無ければ失敗
            if index < 0:
                return False
            # to 一覧
            destinations = self._routes[index].get("to") or []
            # 残すもの
            kept: list[Any] = []
            # 削除したか
            removed = False
            for item in destinations:
                # dict 以外は維持
                if not isinstance(item, dict):
                    kept.append(item)
                    continue
                # 対象チャンネルなら落とす
                if str(item.get("channel_id", "")) == str(to_channel_id):
                    removed = True
                    continue
                # それ以外は残す
                kept.append(item)
            # 何も消えていなければ失敗
            if not removed:
                return False
            # to が空ならエントリ削除
            if not kept:
                self._routes.pop(index)
            else:
                # to を更新
                self._routes[index]["to"] = kept
            # 保存
            self.save()
            return True

    def clear_from_route(self, from_guild_id: str, from_channel_id: str) -> int:
        """from エントリを丸ごと削除し、消した宛先数を返す。"""
        with self._lock:
            # インデックス
            index = self.find_route_index(from_guild_id, from_channel_id)
            # 無ければ 0
            if index < 0:
                return 0
            # 宛先数
            destinations = self._routes[index].get("to") or []
            count = len([item for item in destinations if isinstance(item, dict)])
            # エントリ削除
            self._routes.pop(index)
            # 保存
            self.save()
            return count

    def purge_channel(self, guild_id: str, channel_id: str) -> dict[str, int]:
        """チャンネルを from / to の両方から除外する。"""
        with self._lock:
            # 結果
            result = {"from_removed": 0, "to_removed": 0}
            # 新しい routes
            new_routes: list[dict[str, Any]] = []
            for route in self._routes:
                source = route.get("from") or {}
                # from が対象ならエントリごと捨てる
                if (
                    str(source.get("guild_id", "")) == str(guild_id)
                    and str(source.get("channel_id", "")) == str(channel_id)
                ):
                    result["from_removed"] = 1
                    continue
                # to から対象を除去
                destinations = route.get("to") or []
                kept: list[Any] = []
                for item in destinations:
                    if not isinstance(item, dict):
                        kept.append(item)
                        continue
                    if (
                        str(item.get("guild_id", "")) == str(guild_id)
                        and str(item.get("channel_id", "")) == str(channel_id)
                    ):
                        result["to_removed"] += 1
                        continue
                    kept.append(item)
                # to が空なら from エントリも捨てる
                if not kept:
                    continue
                # コピーして残す
                updated = dict(route)
                updated["to"] = kept
                new_routes.append(updated)
            # 差し替え
            self._routes = new_routes
            # 保存
            self.save()
            return result

    def clear_all(self) -> int:
        """全ルートを削除し、削除した from エントリ数を返す。"""
        with self._lock:
            # 件数
            count = len(self._routes)
            # 空にする
            self._routes = []
            # 保存
            self.save()
            return count
