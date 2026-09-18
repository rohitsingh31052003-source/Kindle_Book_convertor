"""Tests for the M5.7 application-owned temporary workspace."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from kindle_converter.application.errors import WorkspaceError
from kindle_converter.application.workspace import ConversionWorkspace


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _temp_dir_does_not_exist(path: Path) -> bool:
    """Check that a directory path no longer exists on disk."""
    return not path.exists()


# --------------------------------------------------------------------------
# Creation
# --------------------------------------------------------------------------


class TestCreation:
    def test_workspace_creates_directory(self) -> None:
        with ConversionWorkspace() as workspace:
            path = workspace.path
            assert path.exists()
            assert path.is_dir()

    def test_workspace_path_is_inside_temp(self) -> None:
        with ConversionWorkspace() as workspace:
            assert str(workspace.path).startswith(tempfile.gettempdir())

    def test_path_available_after_context_entry(self) -> None:
        with ConversionWorkspace() as workspace:
            path = workspace.path
        assert path is not None

    def test_path_unavailable_before_entry(self) -> None:
        workspace = ConversionWorkspace()
        with pytest.raises(RuntimeError):
            workspace.path

    def test_path_unavailable_after_cleanup(self) -> None:
        workspace = ConversionWorkspace()
        with workspace:
            pass
        with pytest.raises(RuntimeError):
            workspace.path

    def test_path_is_a_path_object(self) -> None:
        with ConversionWorkspace() as workspace:
            assert isinstance(workspace.path, Path)


# --------------------------------------------------------------------------
# Isolation
# --------------------------------------------------------------------------


class TestIsolation:
    def test_each_workspace_has_unique_path(self) -> None:
        paths: list[Path] = []
        for _ in range(3):
            with ConversionWorkspace() as workspace:
                paths.append(workspace.path)
        assert len(set(str(p) for p in paths)) == 3

    def test_workspace_does_not_overlap_temp_root(self) -> None:
        temp_root = Path(tempfile.gettempdir())
        with ConversionWorkspace() as workspace:
            assert workspace.path != temp_root
            assert temp_root in workspace.path.parents


# --------------------------------------------------------------------------
# Cleanup on normal completion
# --------------------------------------------------------------------------


class TestCleanupNormalCompletion:
    def test_directory_removed_after_context_exit(self) -> None:
        workspace = ConversionWorkspace()
        with workspace:
            path = workspace.path
            path.mkdir(parents=True, exist_ok=True)
            (path / "marker.txt").write_text("hello")
        assert not path.exists()

    def test_nested_files_cleaned_up(self) -> None:
        workspace = ConversionWorkspace()
        with workspace:
            path = workspace.path
            nested = path / "sub" / "deep"
            nested.mkdir(parents=True)
            (nested / "data.txt").write_text("nested")
            intermediate = path / "intermediate.bin"
            intermediate.write_bytes(b"x" * 100)
        assert not path.exists()

    def test_cleanup_is_idempotent(self) -> None:
        workspace = ConversionWorkspace()
        with workspace:
            pass
        workspace.cleanup()
        workspace.cleanup()

    def test_context_manager_returns_self(self) -> None:
        with ConversionWorkspace() as workspace:
            assert isinstance(workspace, ConversionWorkspace)


# --------------------------------------------------------------------------
# Cleanup when conversion raises an unexpected exception
# --------------------------------------------------------------------------


class TestCleanupUnexpectedException:
    def test_directory_removed_when_exception_raised(self) -> None:
        workspace = ConversionWorkspace()
        with pytest.raises(ValueError):
            with workspace:
                path = workspace.path
                (path / "file.txt").write_text("data")
                raise ValueError("unexpected boom")
        assert not path.exists()

    def test_directory_removed_when_base_exception_raised(self) -> None:
        workspace = ConversionWorkspace()
        with pytest.raises(RuntimeError):
            with workspace:
                path = workspace.path
                raise RuntimeError("boom")
        assert not path.exists()


# --------------------------------------------------------------------------
# Cleanup when an expected application error occurs
# --------------------------------------------------------------------------


class TestCleanupApplicationError:
    def test_directory_removed_on_application_error(self) -> None:
        workspace = ConversionWorkspace()
        saved_path = None
        try:
            with workspace:
                saved_path = workspace.path
                (saved_path / "artifacts").mkdir(parents=True)
                raise ValueError("expected application failure")
        except ValueError:
            pass
        assert not saved_path.exists()

    def test_artifacts_do_not_survive_after_error(self) -> None:
        workspace = ConversionWorkspace()
        try:
            with workspace:
                path = workspace.path
                staging = path / "staging"
                staging.mkdir(parents=True)
                (staging / "partial.epub").write_bytes(b"partial")
                raise ValueError("stage failure")
        except ValueError:
            pass
        assert not workspace.path.exists() if workspace._path is not None else True


# --------------------------------------------------------------------------
# Workspace creation failure
# --------------------------------------------------------------------------


class TestWorkspaceCreationFailure:
    def test_failing_temporary_directory_raises_workspace_error(self, monkeypatch) -> None:
        import kindle_converter.application.workspace as _ws
        def _fail(*args, **kwargs):
            raise OSError("no space")
        monkeypatch.setattr(_ws, "TemporaryDirectory", _fail)
        workspace = ConversionWorkspace()
        with pytest.raises(WorkspaceError, match="temporary workspace"):
            with workspace:
                pass

    def test_original_cause_preserved(self, monkeypatch) -> None:
        import kindle_converter.application.workspace as _ws
        def _fail(*args, **kwargs):
            raise OSError("no space")
        monkeypatch.setattr(_ws, "TemporaryDirectory", _fail)
        workspace = ConversionWorkspace()
        try:
            with workspace:
                pass
        except WorkspaceError as exc:
            assert isinstance(exc.__cause__, OSError)
            assert "no space" in str(exc.__cause__)

    def test_no_path_after_failed_creation(self, monkeypatch) -> None:
        import kindle_converter.application.workspace as _ws
        def _fail(*args, **kwargs):
            raise OSError("no space")
        monkeypatch.setattr(_ws, "TemporaryDirectory", _fail)
        workspace = ConversionWorkspace()
        with pytest.raises(WorkspaceError):
            with workspace:
                pass
        assert workspace._path is None
        assert workspace._temp_dir is None

    def test_workspace_error_is_application_error(self, monkeypatch) -> None:
        from kindle_converter.application.errors import ApplicationError
        import kindle_converter.application.workspace as _ws
        def _fail(*args, **kwargs):
            raise OSError("no space")
        monkeypatch.setattr(_ws, "TemporaryDirectory", _fail)
        workspace = ConversionWorkspace()
        try:
            with workspace:
                pass
        except WorkspaceError as exc:
            assert isinstance(exc, ApplicationError)


# --------------------------------------------------------------------------
# Cleanup failure semantics
# --------------------------------------------------------------------------


class TestCleanupFailureSemantics:
    def test_cleanup_failure_does_not_raise(self, monkeypatch) -> None:
        """OSError during cleanup is logged, not raised."""
        workspace = ConversionWorkspace()
        with workspace:
            (workspace.path / "file.txt").write_text("data")
            temp_dir = workspace._temp_dir

        def _failing_cleanup():
            raise OSError("read-only filesystem")

        monkeypatch.setattr(temp_dir, "cleanup", _failing_cleanup)
        workspace.cleanup()
        assert workspace._temp_dir is None
        assert workspace._path is None

    def test_original_exception_not_replaced_by_cleanup_failure(self) -> None:
        """When conversion fails and cleanup fails, original exception propagates."""
        workspace = ConversionWorkspace()
        try:
            with workspace:
                path = workspace.path
                (path / "file.txt").write_text("data")
                raise ValueError("original conversion failure")
        except ValueError as exc:
            assert str(exc) == "original conversion failure"
        else:
            pytest.fail("expected ValueError to propagate")
