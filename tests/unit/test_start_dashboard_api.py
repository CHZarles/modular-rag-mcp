from scripts import start_dashboard_api


def test_dashboard_launcher_uses_config_for_host_and_port() -> None:
    args = start_dashboard_api._build_parser().parse_args([])

    assert args.host is None
    assert args.port is None


def test_dashboard_launcher_rejects_missing_frontend_build(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(start_dashboard_api, "WEB_INDEX", tmp_path / "missing.html")

    assert start_dashboard_api.main([]) == 2
    assert "npm --prefix web run build" in capsys.readouterr().err
