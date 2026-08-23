from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from leadfinder.cli import app


def test_worker_uses_start_to_start_interval(monkeypatch) -> None:
    monotonic_values = iter((100.0, 113.5))

    monkeypatch.setattr("leadfinder.cli.get_settings", lambda: object())
    monkeypatch.setattr("leadfinder.cli._classifier", lambda: object())
    monkeypatch.setattr("leadfinder.cli._database", lambda: _FakeDatabase())
    monkeypatch.setattr("leadfinder.cli.bot_api_from_settings", lambda _settings: None)
    monkeypatch.setattr("leadfinder.cli.run_monitor", lambda *_args: object())
    monkeypatch.setattr("leadfinder.cli.asyncio.run", lambda _coroutine: {"ok": True})
    monkeypatch.setattr("leadfinder.cli.time.monotonic", lambda: next(monotonic_values))

    with patch("leadfinder.cli.time.sleep", side_effect=KeyboardInterrupt) as sleep:
        result = CliRunner().invoke(
            app,
            ["worker", "--interval", "60", "--discover-every", "0"],
        )

    assert result.exit_code == 0
    sleep.assert_called_once_with(46.5)


class _FakeDatabase:
    def create_all(self) -> None:
        pass
