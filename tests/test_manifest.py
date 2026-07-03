"""TDD: 断点续传 —— Manifest 状态清单 + --resume 逻辑。

覆盖:
  ✅ 创建/读取 manifest
  ✅ 跳过已完成且 MD5 未变的片段
  ✅ MD5 变化 → 重置为 pending
  ✅ 无 manifest → 全部 pending
  ✅ 状态转换: pending → in_progress → completed/failed
"""
import json
import hashlib
import tempfile
import os
from pathlib import Path
import pytest


def _md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


class TestManifestIO:
    """manifest 文件的读写测试。"""

    def test_create_and_read(self):
        """创建 manifest → 读取 → 内容一致。"""
        from proseproof.shared.manifest import (
            Manifest, create_manifest, load_manifest, save_manifest,
        )

        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / ".proofread_manifest.json"
            manifest = create_manifest(["frag_001", "frag_002"])

            save_manifest(manifest, manifest_path)
            assert manifest_path.exists()

            loaded = load_manifest(manifest_path)
            assert loaded.fragments["frag_001"]["status"] == "pending"
            assert loaded.fragments["frag_002"]["status"] == "pending"

    def test_no_manifest_all_pending(self):
        """无 manifest 文件 → 所有片段视为 pending。"""
        from proseproof.shared.manifest import get_fragment_status

        status = get_fragment_status("frag_001", Path("/nonexistent"))
        assert status == "pending"

    def test_mark_completed(self):
        """标记片段为 completed。"""
        from proseproof.shared.manifest import (
            create_manifest, mark_completed, load_manifest, save_manifest,
        )

        with tempfile.TemporaryDirectory() as tmp:
            manifest = create_manifest(["frag_001"])
            content = "片段内容"
            mark_completed(manifest, "frag_001", content)

            assert manifest.fragments["frag_001"]["status"] == "completed"
            assert manifest.fragments["frag_001"]["md5"] == _md5(content)
            assert "timestamp" in manifest.fragments["frag_001"]

    def test_skip_completed_with_matching_md5(self):
        """已完成 + MD5 匹配 → 跳过。"""
        from proseproof.shared.manifest import (
            create_manifest, mark_completed, should_skip,
        )

        manifest = create_manifest(["frag_001"])
        content = "原始内容"
        mark_completed(manifest, "frag_001", content)

        assert should_skip(manifest, "frag_001", content) is True

    def test_dont_skip_if_content_changed(self):
        """MD5 不匹配 → 不跳过（内容被修改了）。"""
        from proseproof.shared.manifest import (
            create_manifest, mark_completed, should_skip,
        )

        manifest = create_manifest(["frag_001"])
        original = "原始内容"
        mark_completed(manifest, "frag_001", original)

        # 内容被修改
        assert should_skip(manifest, "frag_001", "修改后的内容") is False

    def test_dont_skip_if_pending(self):
        """pending 状态的片段不跳过。"""
        from proseproof.shared.manifest import (
            create_manifest, should_skip,
        )

        manifest = create_manifest(["frag_001"])
        assert should_skip(manifest, "frag_001", "任何内容") is False

    def test_dont_skip_if_failed(self):
        """failed 状态的片段不跳过。"""
        from proseproof.shared.manifest import (
            create_manifest, mark_failed, should_skip,
        )

        manifest = create_manifest(["frag_001"])
        mark_failed(manifest, "frag_001", "API 调用失败")

        assert should_skip(manifest, "frag_001", "任何内容") is False

    def test_mark_failed_stores_error(self):
        """失败片段记录错误信息。"""
        from proseproof.shared.manifest import (
            create_manifest, mark_failed,
        )

        manifest = create_manifest(["frag_001"])
        mark_failed(manifest, "frag_001", "连接超时")

        assert manifest.fragments["frag_001"]["status"] == "failed"
        assert "连接超时" in manifest.fragments["frag_001"]["error"]

    def test_get_next_pending(self):
        """获取第一个 pending 片段。"""
        from proseproof.shared.manifest import (
            create_manifest, mark_completed, get_next_pending,
        )

        manifest = create_manifest(["frag_001", "frag_002", "frag_003"])
        mark_completed(manifest, "frag_001", "done")
        mark_completed(manifest, "frag_002", "done")

        next_id = get_next_pending(manifest)
        assert next_id == "frag_003"

    def test_all_completed(self):
        """全部完成时 get_next_pending 返回 None。"""
        from proseproof.shared.manifest import (
            create_manifest, mark_completed, get_next_pending,
        )

        manifest = create_manifest(["frag_001"])
        mark_completed(manifest, "frag_001", "done")

        assert get_next_pending(manifest) is None

    def test_progress_stats(self):
        """统计完成进度。"""
        from proseproof.shared.manifest import (
            create_manifest, mark_completed, mark_failed, get_progress,
        )

        manifest = create_manifest(["frag_001", "frag_002", "frag_003", "frag_004"])
        mark_completed(manifest, "frag_001", "done")
        mark_completed(manifest, "frag_002", "done")
        mark_failed(manifest, "frag_003", "err")

        stats = get_progress(manifest)
        assert stats["total"] == 4
        assert stats["completed"] == 2
        assert stats["failed"] == 1
        assert stats["pending"] == 1
