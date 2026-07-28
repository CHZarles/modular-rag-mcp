def test_top_level_packages_are_importable() -> None:
    import core
    import ingestion
    import libs
    import mcp_server
    import observability

    assert all((core, ingestion, libs, mcp_server, observability))
