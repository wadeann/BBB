from types import SimpleNamespace

import pytest

from a_share_agent import cli


@pytest.mark.parametrize('argv,expected', [
    (['walk-forward-stability'], None),
    (['--backend', 'production', 'walk-forward-stability'], 'production'),
    (['walk-forward-stability', '--backend', 'production'], 'production'),
    (['--backend', 'production', 'walk-forward-stability', '--backend', 'fake'], 'fake'),
])
def test_cli_parser_backend_selection(monkeypatch, tmp_path, argv, expected):
    seen = []
    monkeypatch.setattr('sys.argv', ['a-share-agent', '--root', str(tmp_path), *argv])
    monkeypatch.setattr(cli, 'configure_runtime_logging', lambda *a: SimpleNamespace(info=lambda *a: None))
    monkeypatch.setattr(cli, 'cmd_walk_forward_stability', lambda args: seen.append(args.backend))
    cli.main()
    assert seen == [expected]
