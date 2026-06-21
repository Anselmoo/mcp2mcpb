import asyncio
import io
import tarfile
import zipfile
from pathlib import Path
from typing import Any

import pytest

from mcp2mcpb import bundler
from mcp2mcpb.exceptions import BundleError
from mcp2mcpb.models import (
    BundleMode,
    EntryPoint,
    LaunchSpec,
    PackageMeta,
    PackageSource,
    Registry,
    Runner,
    ServerType,
)


class _FakeProc:
    def __init__(self, returncode: int, stderr: bytes = b"") -> None:
        self.returncode = returncode
        self._stderr = stderr

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", self._stderr


def test_python_install_spec_includes_extras():
    src = PackageSource(registry=Registry.PYPI, name="pkg", version="1.0.0")
    launch = LaunchSpec(runner=Runner.PYTHON, extras=["mcp"])
    spec = bundler._python_install_spec(src, launch)
    assert spec == "pkg[mcp]==1.0.0"


def test_python_install_spec_no_extras():
    src = PackageSource(registry=Registry.PYPI, name="pkg", version="1.0.0")
    launch = LaunchSpec(runner=Runner.PYTHON)
    assert bundler._python_install_spec(src, launch) == "pkg==1.0.0"


# ── License shipping (complete mode) ──────────────────────────────────────────


def _wheel(path: Path, *, has_license: bool) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        if has_license:
            zf.writestr("pkg-1.0.0.dist-info/LICENSE", "MIT text")
        zf.writestr("pkg-1.0.0.dist-info/METADATA", "Name: pkg\n")
    return path


def _py_meta() -> PackageMeta:
    return PackageMeta(
        name="pkg",
        version="1.0.0",
        description="d",
        author="a",
        server_type=ServerType.PYTHON,
        entry=EntryPoint(
            command="python", args=["-m", "pkg"], entry_file="server/pkg/__main__.py"
        ),
    )


def _src() -> PackageSource:
    return PackageSource(registry=Registry.PYPI, name="pkg", version="1.0.0")


async def test_reference_mode_writes_nothing(tmp_path: Path):
    dest = tmp_path / "bundle"
    dest.mkdir()
    archive = _wheel(tmp_path / "pkg-1.0.0-py3-none-any.whl", has_license=True)
    await bundler.bundle(
        _src(),
        _py_meta(),
        dest,
        BundleMode.REFERENCE,
        archive,
        LaunchSpec(runner=Runner.UVX),
    )
    assert list(dest.iterdir()) == []


async def test_complete_mode_ships_license(tmp_path: Path, monkeypatch):
    async def _fake_vendor(spec: str, server_dir: Path) -> None:
        server_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(bundler, "_bundle_python", _fake_vendor)
    dest = tmp_path / "bundle"
    dest.mkdir()
    archive = _wheel(tmp_path / "pkg-1.0.0-py3-none-any.whl", has_license=True)
    await bundler.bundle(
        _src(),
        _py_meta(),
        dest,
        BundleMode.COMPLETE,
        archive,
        LaunchSpec(runner=Runner.PYTHON),
    )
    assert (dest / "LICENSE").read_bytes() == b"MIT text"


async def test_complete_mode_without_license_writes_no_file(
    tmp_path: Path, monkeypatch
):
    async def _fake_vendor(spec: str, server_dir: Path) -> None:
        server_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(bundler, "_bundle_python", _fake_vendor)
    dest = tmp_path / "bundle"
    dest.mkdir()
    archive = _wheel(tmp_path / "pkg-1.0.0-py3-none-any.whl", has_license=False)
    await bundler.bundle(
        _src(),
        _py_meta(),
        dest,
        BundleMode.COMPLETE,
        archive,
        LaunchSpec(runner=Runner.PYTHON),
    )
    assert not (dest / "LICENSE").exists()
    assert [p.name for p in dest.iterdir()] == ["server"]


def _npm_tarball(path: Path, *, has_license: bool) -> Path:
    with tarfile.open(path, "w:gz") as tf:
        members = {"package/package.json": "{}"}
        if has_license:
            members["package/LICENSE"] = "MIT"
        for name, content in members.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


def _node_meta() -> PackageMeta:
    return PackageMeta(
        name="pkg",
        version="1.0.0",
        description="d",
        author="a",
        server_type=ServerType.NODE,
        entry=EntryPoint(
            command="node",
            args=["server/index.js"],
            entry_file="server/index.js",
        ),
    )


async def test_complete_mode_npm_ships_license(tmp_path: Path, monkeypatch):
    async def _fake_node(archive: Path, server_dir: Path) -> None:
        server_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(bundler, "_bundle_node", _fake_node)
    dest = tmp_path / "bundle"
    dest.mkdir()
    archive = _npm_tarball(tmp_path / "pkg-1.0.0.tgz", has_license=True)
    src = PackageSource(registry=Registry.NPM, name="pkg", version="1.0.0")
    await bundler.bundle(
        src,
        _node_meta(),
        dest,
        BundleMode.COMPLETE,
        archive,
        LaunchSpec(runner=Runner.NODE),
    )
    assert (dest / "LICENSE").read_bytes() == b"MIT"


# ── _uv_available (cached probe) ──────────────────────────────────────────────


async def test_uv_available_false_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bundler, "_UV_AVAILABLE", None)
    monkeypatch.setattr(bundler.shutil, "which", lambda _: None)
    assert await bundler._uv_available() is False


async def test_uv_available_true_when_probe_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bundler, "_UV_AVAILABLE", None)
    monkeypatch.setattr(bundler.shutil, "which", lambda _: "/usr/bin/uv")

    async def fake_exec(*args: Any, **kwargs: Any) -> _FakeProc:
        return _FakeProc(0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    assert await bundler._uv_available() is True


async def test_uv_available_false_on_oserror(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bundler, "_UV_AVAILABLE", None)
    monkeypatch.setattr(bundler.shutil, "which", lambda _: "/usr/bin/uv")

    async def boom(*args: Any, **kwargs: Any) -> _FakeProc:
        raise OSError("cannot spawn")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", boom)
    assert await bundler._uv_available() is False


# ── _run ──────────────────────────────────────────────────────────────────────


async def test_run_success(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_exec(*args: Any, **kwargs: Any) -> _FakeProc:
        return _FakeProc(0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    await bundler._run("echo", "hi")  # no raise


async def test_run_raises_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_exec(*args: Any, **kwargs: Any) -> _FakeProc:
        return _FakeProc(1, stderr=b"it broke")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(BundleError, match="it broke"):
        await bundler._run("false")


# ── _bundle_python (both runner branches) ─────────────────────────────────────


async def test_bundle_python_uses_uv_when_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[tuple[str, ...]] = []

    async def fake_uv_available() -> bool:
        return True

    async def fake_run(*cmd: str) -> None:
        captured.append(cmd)

    monkeypatch.setattr(bundler, "_uv_available", fake_uv_available)
    monkeypatch.setattr(bundler, "_run", fake_run)
    await bundler._bundle_python("pkg==1.0.0", tmp_path / "server")
    assert captured[0][0] == "uv"


async def test_bundle_python_falls_back_to_pip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[tuple[str, ...]] = []

    async def fake_uv_available() -> bool:
        return False

    async def fake_run(*cmd: str) -> None:
        captured.append(cmd)

    monkeypatch.setattr(bundler, "_uv_available", fake_uv_available)
    monkeypatch.setattr(bundler, "_run", fake_run)
    await bundler._bundle_python("pkg==1.0.0", tmp_path / "server")
    assert captured[0][:3] == ("python", "-m", "pip")


# ── _extract_npm_tarball (dirs + files) ───────────────────────────────────────


def test_extract_npm_tarball_handles_dirs_and_files(tmp_path: Path) -> None:
    archive = tmp_path / "p.tgz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.addfile(tarfile.TarInfo("pax_global_header"))  # not package/ → skipped
        tf.addfile(tarfile.TarInfo("package/"))  # leading prefix only → skipped
        dir_info = tarfile.TarInfo("package/lib")
        dir_info.type = tarfile.DIRTYPE
        tf.addfile(dir_info)
        data = b"console.log(1);\n"
        file_info = tarfile.TarInfo("package/lib/index.js")
        file_info.size = len(data)
        tf.addfile(file_info, io.BytesIO(data))

    server = tmp_path / "server"
    bundler._extract_npm_tarball(archive, server)
    assert (server / "lib").is_dir()
    assert (server / "lib" / "index.js").read_bytes() == b"console.log(1);\n"


# ── _bundle_node (npm ci vs install) ──────────────────────────────────────────


async def test_bundle_node_uses_npm_ci_with_lockfile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[tuple[str, ...]] = []

    def fake_extract(archive: Path, server_dir: Path) -> None:
        server_dir.mkdir(parents=True, exist_ok=True)
        (server_dir / "package-lock.json").write_text("{}")

    async def fake_run(*cmd: str) -> None:
        captured.append(cmd)

    monkeypatch.setattr(bundler, "_extract_npm_tarball", fake_extract)
    monkeypatch.setattr(bundler, "_run", fake_run)
    await bundler._bundle_node(tmp_path / "p.tgz", tmp_path / "server")
    assert captured[0][:2] == ("npm", "ci")


async def test_bundle_node_uses_npm_install_without_lockfile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[tuple[str, ...]] = []

    def fake_extract(archive: Path, server_dir: Path) -> None:
        server_dir.mkdir(parents=True, exist_ok=True)

    async def fake_run(*cmd: str) -> None:
        captured.append(cmd)

    monkeypatch.setattr(bundler, "_extract_npm_tarball", fake_extract)
    monkeypatch.setattr(bundler, "_run", fake_run)
    await bundler._bundle_node(tmp_path / "p.tgz", tmp_path / "server")
    assert captured[0][:2] == ("npm", "install")


# ── bundle: binary server type is unsupported ─────────────────────────────────


async def test_bundle_binary_raises(tmp_path: Path) -> None:
    dest = tmp_path / "bundle"
    dest.mkdir()
    meta = PackageMeta(
        name="pkg",
        version="1.0.0",
        description="d",
        author="a",
        server_type=ServerType.BINARY,
        entry=EntryPoint(command="x", args=[], entry_file="x"),
    )
    with pytest.raises(BundleError, match="binary"):
        await bundler.bundle(
            _src(),
            meta,
            dest,
            BundleMode.COMPLETE,
            tmp_path / "p.whl",
            LaunchSpec(runner=Runner.PYTHON),
        )


# ── local wheel install (complete mode) ──────────────────────────────────────


def _demo_py_meta() -> PackageMeta:
    return PackageMeta(
        name="demo-pkg",
        version="2.3.0",
        description="d",
        author="a",
        server_type=ServerType.PYTHON,
        entry=EntryPoint(command="python", args=[], entry_file="server/__main__.py"),
    )


async def test_complete_python_installs_local_wheel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, ...]] = []

    async def fake_run(*cmd: str) -> None:
        calls.append(cmd)

    async def yes() -> bool:
        return True

    monkeypatch.setattr(bundler, "_run", fake_run)
    monkeypatch.setattr(bundler, "_uv_available", yes)
    monkeypatch.setattr(bundler, "_ship_licenses", lambda *a, **k: _noop())

    wheel = tmp_path / "demo_pkg-2.3.0-py3-none-any.whl"
    wheel.write_bytes(b"x")
    source = PackageSource(registry=Registry.PYPI, name="demo-pkg", version="2.3.0")
    launch = LaunchSpec(runner=Runner.UV_RUN, extras=["mcp"])

    await bundler.bundle(
        source,
        _demo_py_meta(),
        tmp_path / "b",
        BundleMode.COMPLETE,
        wheel,
        launch,
        local_wheel=wheel,
    )

    install_cmd = next(c for c in calls if "install" in c)
    assert str(wheel) + "[mcp]" in install_cmd


async def _noop() -> None:
    return None
