from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path
from typing import List, Set

import numpy as np

from .faiss_store import FaissIndex
from .models import ProfileArtifacts, ZoteroItem
from .settings import Settings
from .storage import ProfileStorage
from .utils import json_dumps, utc_now
from .vectorizer import TextVectorizer

logger = logging.getLogger(__name__)


def _get_allowed_collection_ids(settings: Settings, storage: ProfileStorage) -> Set[str]:
    """根据配置获取允许的分类 ID 集合"""
    filter_config = settings.zotero.collections
    if filter_config.is_empty():
        return set()  # 空集表示不过滤

    # 从数据库加载分类信息
    collections_data = storage.load_collections()
    if not collections_data:
        logger.warning("No collection data found in database, skipping filter")
        return set()

    allowed = set()

    # 按 ID 过滤
    for coll_id in filter_config.ids:
        if coll_id in collections_data:
            allowed.add(coll_id)
            if filter_config.include_children:
                allowed.update(_get_children_ids(coll_id, collections_data))

    # 按名称过滤
    for name_path in filter_config.names:
        coll_id = _find_collection_id_by_path(name_path, collections_data)
        if coll_id:
            allowed.add(coll_id)
            if filter_config.include_children:
                allowed.update(_get_children_ids(coll_id, collections_data))

    return allowed


def _get_children_ids(parent_id: str, collections_data: dict) -> Set[str]:
    """递归获取所有子分类 ID"""
    children = set()
    for coll_id, coll in collections_data.items():
        if coll.get("parent_key") == parent_id:
            children.add(coll_id)
            children.update(_get_children_ids(coll_id, collections_data))
    return children


def _find_collection_id_by_path(path: str, collections_data: dict) -> str | None:
    """按路径查找分类 ID"""
    parts = [p.strip() for p in path.split("/") if p.strip()]
    if not parts:
        return None

    target_name = parts[-1]

    # 构建 ID -> 完整路径的映射
    def get_full_path(coll_id: str) -> str:
        names = []
        current_id = coll_id
        while current_id and current_id in collections_data:
            coll = collections_data[current_id]
            names.insert(0, coll["name"])
            current_id = coll.get("parent_key")
        return "/".join(names)

    # 查找匹配的分类
    for coll_id, coll in collections_data.items():
        if coll["name"] == target_name:
            full_path = get_full_path(coll_id)
            if len(parts) == 1 or full_path == path or full_path.endswith("/" + path):
                return coll_id

    return None


class ProfileBuilder:
    def __init__(
        self,
        base_dir: Path | str,
        storage: ProfileStorage,
        settings: Settings,
        vectorizer: TextVectorizer | None = None,
    ):
        self.base_dir = Path(base_dir)
        self.storage = storage
        self.settings = settings
        self.vectorizer = vectorizer or TextVectorizer()
        self.artifacts = ProfileArtifacts(
            sqlite_path=str(self.base_dir / "data" / "profile.sqlite"),
            faiss_path=str(self.base_dir / "data" / "faiss.index"),
            profile_json_path=str(self.base_dir / "data" / "profile.json"),
        )

    def run(self) -> ProfileArtifacts:
        # 获取允许的分类 ID
        allowed_ids = _get_allowed_collection_ids(self.settings, self.storage)

        # 获取条目并应用分类过滤
        all_items = list(self.storage.iter_items())
        if allowed_ids:
            items = [
                item for item in all_items
                if any(cid in allowed_ids for cid in item.collections)
            ]
            logger.info(
                "Collection filter applied: %d/%d items match target collections",
                len(items), len(all_items)
            )
        else:
            items = all_items

        if not items:
            raise RuntimeError("No items found in storage; run ingest before building profile.")

        logger.info("Vectorizing %d library items", len(items))
        texts = [item.content_for_embedding() for item in items]
        vectors = self.vectorizer.encode(texts)

        for item, vector in zip(items, vectors):
            self.storage.set_embedding(item.key, vector.tobytes())

        logger.info("Building FAISS index")
        index, order = FaissIndex.from_vectors(vectors)
        index.save(self.artifacts.faiss_path)

        profile_summary = self._summarize(items, vectors)
        json_path = Path(self.artifacts.profile_json_path)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json_dumps(profile_summary, indent=2), encoding="utf-8")
        logger.info("Wrote profile summary to %s", json_path)
        return self.artifacts

    def _summarize(self, items: List[ZoteroItem], vectors: np.ndarray) -> dict:
        authors = Counter()
        venues = Counter()
        for item in items:
            authors.update(item.creators)
            venue = item.raw.get("data", {}).get("publicationTitle")
            if venue:
                venues.update([venue])

        centroid = np.mean(vectors, axis=0)
        centroid = centroid / (np.linalg.norm(centroid) + 1e-12)

        top_authors = [{"author": k, "count": v} for k, v in authors.most_common(20)]
        top_venues = [{"venue": k, "count": v} for k, v in venues.most_common(20)]

        return {
            "generated_at": utc_now().isoformat(),
            "item_count": len(items),
            "model": self.vectorizer.model_name,
            "centroid": centroid.tolist(),
            "top_authors": top_authors,
            "top_venues": top_venues,
        }


__all__ = ["ProfileBuilder"]
