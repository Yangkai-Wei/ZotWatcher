from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set

import requests

from .models import ZoteroItem
from .settings import Settings
from .storage import ProfileStorage
from .utils import hash_content

logger = logging.getLogger(__name__)

API_BASE = "https://api.zotero.org"


@dataclass
class ZoteroCollection:
    """Zotero 分类信息"""
    key: str
    name: str
    parent_key: Optional[str] = None
    children: List["ZoteroCollection"] = field(default_factory=list)

    def full_path(self, all_collections: Dict[str, "ZoteroCollection"]) -> str:
        """获取分类的完整路径，如 '生物信息/单细胞/scRNA-seq'"""
        parts = [self.name]
        current = self
        while current.parent_key and current.parent_key in all_collections:
            current = all_collections[current.parent_key]
            parts.insert(0, current.name)
        return "/".join(parts)


@dataclass
class IngestStats:
    fetched: int = 0
    updated: int = 0
    removed: int = 0
    filtered: int = 0  # 被分类过滤掉的数量
    last_modified_version: Optional[int] = None


class ZoteroClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.session = requests.Session()
        api_key = settings.zotero.api.api_key()
        self.session.headers.update(
            {
                "Zotero-API-Version": "3",
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "ZotWatcher/0.1",
            }
        )
        self.base_user_url = f"{API_BASE}/users/{settings.zotero.api.user_id}"
        self.base_items_url = f"{self.base_user_url}/items"
        self.polite_delay = settings.zotero.api.polite_delay_ms / 1000

    def iter_items(self, since_version: Optional[int] = None) -> Iterable[requests.Response]:
        params = {
            "limit": self.settings.zotero.api.page_size,
            "sort": "dateAdded",
            "direction": "asc",
        }
        headers = {}
        if since_version is not None:
            headers["If-Modified-Since-Version"] = str(since_version)

        next_url = self.base_items_url
        while next_url:
            logger.debug("Fetching Zotero page: %s", next_url)
            resp = self.session.get(next_url, params=params if next_url == self.base_items_url else None, headers=headers)
            if resp.status_code == 304:
                logger.info("Zotero API indicated no changes since version %s", since_version)
                return
            resp.raise_for_status()
            yield resp
            next_url = _parse_next_link(resp.headers.get("Link"))
            headers = {}
            params = {}
            time.sleep(self.polite_delay)

    def fetch_deleted(self, since_version: Optional[int]) -> List[str]:
        if since_version is None:
            return []
        url = f"{self.base_user_url}/deleted"
        resp = self.session.get(url, params={"since": since_version})
        resp.raise_for_status()
        payload = resp.json() or {}
        deleted_items = payload.get("items", [])
        logger.info("Fetched %d deleted item tombstones", len(deleted_items))
        return deleted_items

    def fetch_collections(self) -> Dict[str, ZoteroCollection]:
        """获取所有分类，返回 {key: ZoteroCollection} 字典"""
        url = f"{self.base_user_url}/collections"
        collections: Dict[str, ZoteroCollection] = {}

        next_url = url
        while next_url:
            logger.debug("Fetching collections page: %s", next_url)
            resp = self.session.get(next_url)
            resp.raise_for_status()

            for item in resp.json():
                data = item.get("data", {})
                key = data.get("key")
                if key:
                    collections[key] = ZoteroCollection(
                        key=key,
                        name=data.get("name", ""),
                        parent_key=data.get("parentCollection") or None,
                    )

            next_url = _parse_next_link(resp.headers.get("Link"))
            time.sleep(self.polite_delay)

        # 构建父子关系
        for coll in collections.values():
            if coll.parent_key and coll.parent_key in collections:
                collections[coll.parent_key].children.append(coll)

        logger.info("Fetched %d collections from Zotero", len(collections))
        return collections


def _parse_next_link(link_header: Optional[str]) -> Optional[str]:
    if not link_header:
        return None
    parts = [part.strip() for part in link_header.split(",")]
    for part in parts:
        if "rel=\"next\"" in part:
            url_part = part.split(";")[0].strip()
            if url_part.startswith("<") and url_part.endswith(">"):
                return url_part[1:-1]
    return None


def _get_all_descendant_ids(collection: ZoteroCollection) -> Set[str]:
    """递归获取分类及其所有子分类的 ID"""
    result = {collection.key}
    for child in collection.children:
        result.update(_get_all_descendant_ids(child))
    return result


class CollectionFilter:
    """分类过滤器，支持多级分类"""

    def __init__(self, settings: Settings, collections: Dict[str, ZoteroCollection]):
        self.settings = settings
        self.collections = collections
        self.filter_config = settings.zotero.collections
        self._allowed_ids: Optional[Set[str]] = None

    def _resolve_allowed_ids(self) -> Set[str]:
        """解析配置，返回所有允许的分类 ID 集合"""
        if self._allowed_ids is not None:
            return self._allowed_ids

        if self.filter_config.is_empty():
            # 没有过滤条件，返回所有分类
            self._allowed_ids = set(self.collections.keys())
            return self._allowed_ids

        allowed = set()

        # 处理按 ID 配置的分类
        for coll_id in self.filter_config.ids:
            if coll_id in self.collections:
                coll = self.collections[coll_id]
                if self.filter_config.include_children:
                    allowed.update(_get_all_descendant_ids(coll))
                else:
                    allowed.add(coll_id)
            else:
                logger.warning("Collection ID '%s' not found in Zotero library", coll_id)

        # 处理按名称配置的分类（支持路径格式如 "父分类/子分类"）
        for name_path in self.filter_config.names:
            matched = self._find_collection_by_path(name_path)
            if matched:
                if self.filter_config.include_children:
                    allowed.update(_get_all_descendant_ids(matched))
                else:
                    allowed.add(matched.key)
            else:
                logger.warning("Collection path '%s' not found in Zotero library", name_path)

        self._allowed_ids = allowed
        logger.info("Collection filter resolved %d allowed collection IDs", len(allowed))
        return allowed

    def _find_collection_by_path(self, path: str) -> Optional[ZoteroCollection]:
        """按路径查找分类，如 '生物信息/单细胞'"""
        parts = [p.strip() for p in path.split("/") if p.strip()]
        if not parts:
            return None

        # 查找所有匹配最后一个名称的分类
        target_name = parts[-1]
        candidates = [c for c in self.collections.values() if c.name == target_name]

        if len(parts) == 1:
            # 只指定了名称，返回第一个匹配的（如果有多个同名分类，可能需要改进）
            if candidates:
                if len(candidates) > 1:
                    logger.warning(
                        "Multiple collections named '%s' found, using first match. "
                        "Consider using full path like 'Parent/Child' to be specific.",
                        target_name
                    )
                return candidates[0]
            return None

        # 验证完整路径
        for coll in candidates:
            full_path = coll.full_path(self.collections)
            if full_path == path or full_path.endswith("/" + path):
                return coll

        return None

    def should_include_item(self, item: ZoteroItem) -> bool:
        """判断条目是否应该被包含（基于分类过滤）"""
        if self.filter_config.is_empty():
            return True

        allowed = self._resolve_allowed_ids()

        # 检查条目的分类是否有任何一个在允许列表中
        for coll_id in item.collections:
            if coll_id in allowed:
                return True

        return False


class ZoteroIngestor:
    def __init__(self, storage: ProfileStorage, settings: Settings):
        self.storage = storage
        self.settings = settings
        self.client = ZoteroClient(settings)

    def run(self, *, full: bool = False) -> IngestStats:
        stats = IngestStats()
        self.storage.initialize()
        since_version = None if full else self.storage.last_modified_version()
        logger.info("Starting Zotero ingest (full=%s, since_version=%s)", full, since_version)
        max_version = since_version or 0

        # 获取分类信息并创建过滤器
        all_collections = self.client.fetch_collections()
        coll_filter = CollectionFilter(self.settings, all_collections)

        # 保存分类信息到 storage（供后续查询）
        self.storage.save_collections(all_collections)

        # 如果是全量同步且有分类过滤，先清空数据库
        if full and not self.settings.zotero.collections.is_empty():
            logger.info("Full sync with collection filter: clearing existing items")
            self.storage.clear_all_items()

        for response in self.client.iter_items(since_version=since_version):
            items = response.json()
            response_version = int(response.headers.get("Last-Modified-Version", 0))
            max_version = max(max_version, response_version)
            for raw_item in items:
                zot_item = ZoteroItem.from_zotero_api(raw_item)

                # 应用分类过滤
                if not coll_filter.should_include_item(zot_item):
                    stats.filtered += 1
                    continue

                content_hash = hash_content(
                    zot_item.title,
                    zot_item.abstract or "",
                    ",".join(zot_item.creators),
                    ",".join(zot_item.tags),
                )
                self.storage.upsert_item(zot_item, content_hash=content_hash)
                stats.fetched += 1
                stats.updated += 1

        deleted_keys = self.client.fetch_deleted(since_version=max_version if not full else None)
        self.storage.remove_items(deleted_keys)
        stats.removed = len(deleted_keys)

        if stats.fetched or full:
            stats.last_modified_version = max_version
            if max_version:
                self.storage.set_last_modified_version(max_version)
                logger.info("Updated last modified version to %s", max_version)

        if stats.filtered:
            logger.info("Filtered out %d items not in target collections", stats.filtered)

        return stats

    def list_collections(self) -> Dict[str, ZoteroCollection]:
        """获取所有分类（用于 CLI 命令）"""
        return self.client.fetch_collections()


__all__ = ["ZoteroIngestor", "IngestStats", "ZoteroCollection", "CollectionFilter"]
