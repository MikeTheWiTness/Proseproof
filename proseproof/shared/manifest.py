"""v0.2.0 断点续传 Manifest —— 片段级校对状态追踪。

存储于 output/{doc}/.proofread_manifest.json，支持:
  - 记录每个片段的校对状态 (pending/in_progress/completed/failed)
  - MD5 校验检测内容变更
  - 跳过已完成且未变更的片段
"""
from __future__ import annotations
import json
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Literal

FragmentStatus = Literal["pending", "in_progress", "completed", "failed"]


class Manifest:
    """片段级校对状态清单。"""

    def __init__(self, fragments: dict[str, dict] | None = None):
        self.fragments: dict[str, dict] = fragments or {}

    def to_dict(self) -> dict:
        return {"fragments": self.fragments}

    @classmethod
    def from_dict(cls, data: dict) -> Manifest:
        return cls(fragments=data.get("fragments", {}))


def create_manifest(fragment_ids: list[str]) -> Manifest:
    """为给定的片段 ID 列表创建初始 manifest。"""
    fragments = {}
    for fid in fragment_ids:
        fragments[fid] = {"status": "pending"}
    return Manifest(fragments)


def save_manifest(manifest: Manifest, path: Path):
    """保存 manifest 到文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest.to_dict(), f, ensure_ascii=False, indent=2)


def load_manifest(path: Path) -> Manifest | None:
    """从文件加载 manifest。文件不存在时返回 None。"""
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return Manifest.from_dict(data)


def _compute_md5(content: str) -> str:
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def mark_completed(manifest: Manifest, fragment_id: str, content: str):
    """标记片段为已完成，记录 MD5。"""
    manifest.fragments[fragment_id] = {
        "status": "completed",
        "md5": _compute_md5(content),
        "timestamp": datetime.now().isoformat(),
    }


def mark_failed(manifest: Manifest, fragment_id: str, error: str):
    """标记片段为失败，记录错误信息。"""
    manifest.fragments[fragment_id] = {
        "status": "failed",
        "error": error,
        "timestamp": datetime.now().isoformat(),
    }


def mark_in_progress(manifest: Manifest, fragment_id: str):
    """标记片段为处理中。"""
    manifest.fragments[fragment_id] = {
        "status": "in_progress",
        "timestamp": datetime.now().isoformat(),
    }


def should_skip(manifest: Manifest, fragment_id: str, content: str) -> bool:
    """判断片段是否应跳过（已完成且内容未变更）。"""
    entry = manifest.fragments.get(fragment_id, {})
    if entry.get("status") != "completed":
        return False
    recorded_md5 = entry.get("md5", "")
    current_md5 = _compute_md5(content)
    return recorded_md5 == current_md5


def get_fragment_status(fragment_id: str, manifest_path: Path) -> FragmentStatus:
    """获取片段的校对状态。

    文件不存在或无记录均返回 pending。
    """
    manifest = load_manifest(manifest_path)
    if manifest is None:
        return "pending"
    entry = manifest.fragments.get(fragment_id, {})
    return entry.get("status", "pending")


def get_next_pending(manifest: Manifest) -> str | None:
    """获取第一个状态为 pending 的片段 ID。

    Returns:
        片段 ID，或 None（全部完成）。
    """
    for fid, entry in manifest.fragments.items():
        if entry.get("status") in ("pending", "failed"):
            return fid
    return None


def get_progress(manifest: Manifest) -> dict:
    """统计完成进度。

    Returns:
        {"total": int, "completed": int, "failed": int, "pending": int}
    """
    stats = {"total": 0, "completed": 0, "failed": 0, "pending": 0}
    for entry in manifest.fragments.values():
        stats["total"] += 1
        status = entry.get("status", "pending")
        if status == "completed":
            stats["completed"] += 1
        elif status == "failed":
            stats["failed"] += 1
        else:
            stats["pending"] += 1
    return stats
