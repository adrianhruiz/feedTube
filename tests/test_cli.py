import pytest

from feedtube.cli import main


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


def test_podcast_folder_command_sets_and_shows(tmp_path, capsys):
    assert main(["podcast-folder"]) == 0
    assert capsys.readouterr().out.strip() == "(not set)"
    assert main(["podcast-folder", str(tmp_path / "pods")]) == 0
    assert capsys.readouterr().out.strip() == str(tmp_path / "pods")


def test_folder_command_unchanged(tmp_path, capsys):
    assert main(["folder", str(tmp_path / "v")]) == 0
    assert capsys.readouterr().out.strip() == str(tmp_path / "v")


def test_add_podcast_without_folder_fails(capsys):
    assert main(["add", "@Foo", "--name", "Foo", "--podcast"]) == 1
    assert "podcast folder" in capsys.readouterr().err



def test_jellyfin_command_sets_and_shows(capsys):
    assert main(["jellyfin"]) == 0
    assert capsys.readouterr().out.strip() == "(not set)"
    assert main(["jellyfin", "http://localhost:8096", "abc123"]) == 0
    assert capsys.readouterr().out.strip() == "http://localhost:8096 (api key set)"
    assert main(["jellyfin"]) == 0
    assert capsys.readouterr().out.strip() == "http://localhost:8096 (api key set)"


def test_jellyfin_command_requires_key_with_url(capsys):
    with pytest.raises(SystemExit):
        main(["jellyfin", "http://localhost:8096"])


def test_download_command_requires_folder(capsys):
    assert main(["download", "https://youtu.be/v1"]) == 1
    assert "download folder" in capsys.readouterr().err
