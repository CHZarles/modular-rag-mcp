"""群晖导入脚本测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

from scripts import import_synology


def test_main_syncs_only_pdfs_then_runs_ingestion(tmp_path: Path, monkeypatch) -> None:
    run = Mock(return_value=Mock(returncode=0))
    ingest = Mock(return_value=0)
    monkeypatch.setattr(import_synology.shutil, "which", lambda name: "/usr/bin/rsync")
    monkeypatch.setattr(import_synology.subprocess, "run", run)
    monkeypatch.setattr(import_synology, "ingest_main", ingest)

    cache_dir = tmp_path / "synology"
    assert import_synology.main(
        [
            "--host",
            "nas.local",
            "--user",
            "charles",
            "--remote-path",
            "/volume1/Knowledge Base",
            "--cache-dir",
            str(cache_dir),
            "--collection",
            "nas-docs",
        ]
    ) == 0

    command = run.call_args.args[0]
    assert "--include=*.[pP][dD][fF]" in command
    assert "charles@nas.local:'/volume1/Knowledge Base/'" in command
    ingest.assert_called_once_with(
        ["--path", str(cache_dir), "--collection", "nas-docs"],
        settings_path=None,
    )


def test_dry_run_does_not_start_ingestion(tmp_path: Path, monkeypatch) -> None:
    run = Mock(return_value=Mock(returncode=0))
    ingest = Mock()
    monkeypatch.setattr(import_synology.shutil, "which", lambda name: "/usr/bin/rsync")
    monkeypatch.setattr(import_synology.subprocess, "run", run)
    monkeypatch.setattr(import_synology, "ingest_main", ingest)

    assert import_synology.main(
        [
            "--host",
            "192.168.1.10",
            "--user",
            "reader",
            "--remote-path",
            "/volume1/docs",
            "--cache-dir",
            str(tmp_path / "cache"),
            "--dry-run",
        ]
    ) == 0
    assert "--dry-run" in run.call_args.args[0]
    ingest.assert_not_called()
