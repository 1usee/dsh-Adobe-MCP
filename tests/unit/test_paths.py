"""路径规范化与白名单（说明书 §8.6）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from dsh_adobe_mcp.safety.paths import PathNotAllowed, PathPolicy, is_within, looks_like_path_param


@pytest.fixture
def policy(tmp_path: Path) -> PathPolicy:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (tmp_path / "outside").mkdir()
    return PathPolicy([allowed])


def test_path_inside_root_is_allowed(policy: PathPolicy, tmp_path: Path):
    target = tmp_path / "allowed" / "clip.mp4"
    assert policy.check(str(target)) == target.resolve()


def test_relative_escape_via_dotdot_is_rejected(policy: PathPolicy, tmp_path: Path):
    sneaky = tmp_path / "allowed" / ".." / "outside" / "clip.mp4"
    with pytest.raises(PathNotAllowed) as excinfo:
        policy.check(str(sneaky))
    assert excinfo.value.detail()["reason"] == "规范化后不在任何允许根目录之内"
    assert "allowed_roots" in excinfo.value.detail()


def test_absolute_outside_path_is_rejected(policy: PathPolicy):
    with pytest.raises(PathNotAllowed):
        policy.check(r"C:\Windows\System32\evil.dll")


def test_user_home_is_allowed_by_default():
    policy = PathPolicy([Path.home()])
    assert policy.check(str(Path.home() / "media" / "a.mp4"))


def test_new_output_file_is_allowed_when_parent_is_inside(policy: PathPolicy, tmp_path: Path):
    target = tmp_path / "allowed" / "renders" / "out.mp4"
    resolved = policy.check(str(target))
    assert resolved == target.resolve()
    assert not resolved.exists()


def test_must_exist_rejects_missing_file(policy: PathPolicy, tmp_path: Path):
    with pytest.raises(PathNotAllowed) as excinfo:
        policy.check(str(tmp_path / "allowed" / "nope.mp4"), must_exist=True)
    assert excinfo.value.detail()["reason"] == "路径不存在"


def test_roots_can_be_extended_at_runtime(policy: PathPolicy, tmp_path: Path):
    (tmp_path / "project").mkdir()
    with pytest.raises(PathNotAllowed):
        policy.check(str(tmp_path / "project" / "p.prproj"))
    policy.add_root(tmp_path / "project")
    assert policy.check(str(tmp_path / "project" / "p.prproj"))


def test_empty_policy_rejects_everything(tmp_path: Path):
    with pytest.raises(PathNotAllowed):
        PathPolicy().check(str(tmp_path / "x"))


def test_path_case_insensitivity_on_windows(policy: PathPolicy, tmp_path: Path):
    upper = str(tmp_path / "allowed" / "CLIP.MP4").upper()
    assert policy.check(upper)


def test_is_within_uses_common_path():
    root = Path(r"C:\data")
    assert is_within(Path(r"C:\data\a\b"), root)
    assert not is_within(Path(r"C:\data2\a"), root)
    assert not is_within(Path(r"D:\data\a"), root)


def test_path_param_detection():
    assert looks_like_path_param("path")
    assert looks_like_path_param("paths")
    assert looks_like_path_param("output_path")
    assert looks_like_path_param("preset_path")
    assert looks_like_path_param("new_path")
    # 素材箱路径是**工程内路径**，不是文件系统路径，不应被白名单校验
    assert not looks_like_path_param("bin")
    assert not looks_like_path_param("target_bin")
    assert not looks_like_path_param("item")
    assert not looks_like_path_param("parent")
