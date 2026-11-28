import json
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import update_nix_hashes as nix_hashes  # noqa: E402

# the real v1.5.11 model entry: the hex is what SHA256SUMS holds, the SRI is what Nix wants
MODEL_HEX = "023ee206403c7fc96a9be13e1aec761dd798d73c6d2f876a6c09c392dbf9cbc5"
MODEL_SRI = "sha256-Aj7iBkA8f8lqm+E+Gux2HdeY1zxtL4dqbAnDktv5y8U="
EMPTY_HEX = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
EMPTY_SRI = "sha256-47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU="
OTHER_HEX = "ec38e8da142596baa913124ae50550de284b6916bf59577ef2f0cb9660c2f514"

SUMS = f"""\
{"a" * 64}  lxask-x86_64-v3
{"b" * 64}  lxask-aarch64
{"c" * 64}  lxask-1.5.12-cp310-abi3-manylinux_2_28_x86_64.whl
{MODEL_HEX}  lxask-round4-Q4_K_M.gguf
{OTHER_HEX}  embed.gguf
{"d" * 64}  LICENSE
{"e" * 64}  install.sh
"""


def hashes_file(tmp_path, release="v1.5.11", model=None):
    model = model or {"embed.gguf": "sha256-old", "lxask-round4-Q4_K_M.gguf": "sha256-old"}
    path = tmp_path / "hashes.json"
    path.write_text(json.dumps({"release": release, "model": model}, indent=2) + "\n")
    return path


def sums_file(tmp_path):
    path = tmp_path / "SHA256SUMS"
    path.write_text(SUMS)
    return path


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.body


def test_to_sri_matches_known_digests():
    assert nix_hashes.to_sri(MODEL_HEX) == MODEL_SRI
    assert nix_hashes.to_sri(EMPTY_HEX) == EMPTY_SRI


def test_parse_sums_reads_sha256sum_output():
    sums = nix_hashes.parse_sums(SUMS + f"{EMPTY_HEX} *binary-mode.bin\n\n")
    assert sums["lxask-round4-Q4_K_M.gguf"] == MODEL_HEX
    assert sums["binary-mode.bin"] == EMPTY_HEX
    assert len(sums) == 8


def test_parse_sums_rejects_other_lines():
    with pytest.raises(SystemExit, match="unexpected line"):
        nix_hashes.parse_sums("not a checksum line\n")


def test_refresh_keeps_names_and_order_and_sets_the_release():
    current = {"release": "v1.5.11", "model": {"lxask-round4-Q4_K_M.gguf": "x", "embed.gguf": "y"}}
    new = nix_hashes.refresh(current, nix_hashes.parse_sums(SUMS), "v1.5.12")
    assert new == {
        "release": "v1.5.12",
        "model": {"lxask-round4-Q4_K_M.gguf": MODEL_SRI, "embed.gguf": nix_hashes.to_sri(OTHER_HEX)},
    }
    assert list(new["model"]) == ["lxask-round4-Q4_K_M.gguf", "embed.gguf"]


def test_refresh_stops_when_a_listed_file_was_renamed():
    current = {"release": "v1.5.11", "model": {"lxask-round3-Q4_K_M.gguf": "x"}}
    with pytest.raises(SystemExit, match="renamed a model file") as error:
        nix_hashes.refresh(current, nix_hashes.parse_sums(SUMS), "v1.5.12")
    assert "lxask-round3-Q4_K_M.gguf" in str(error.value)
    assert "lxask-round4-Q4_K_M.gguf" in str(error.value)  # what the release lists instead


def test_unlisted_ignores_programs_wheel_and_licence_files():
    current = {"model": {"embed.gguf": "x"}}
    assert nix_hashes.unlisted(current, nix_hashes.parse_sums(SUMS)) == ["lxask-round4-Q4_K_M.gguf"]


def test_main_updates_the_file_then_has_nothing_left_to_do(tmp_path, capsys):
    sums = tmp_path / "SHA256SUMS"
    sums.write_text(SUMS)
    target = hashes_file(tmp_path)
    args = ["v1.5.12", "--sums", str(sums), "--file", str(target)]

    assert nix_hashes.main([*args, "--check"]) == 1
    assert json.loads(target.read_text())["release"] == "v1.5.11"  # --check changes nothing

    assert nix_hashes.main(args) == 0
    data = json.loads(target.read_text())
    assert data["release"] == "v1.5.12"
    assert data["model"]["lxask-round4-Q4_K_M.gguf"] == MODEL_SRI
    assert target.read_text().endswith("}\n")

    assert nix_hashes.main([*args, "--check"]) == 0
    assert "already up to date" in capsys.readouterr().out


def test_main_rejects_a_tag_that_is_not_a_release_tag(tmp_path):
    with pytest.raises(SystemExit, match="not a release tag"):
        nix_hashes.main(["main", "--file", str(hashes_file(tmp_path))])


def test_cargo_version_reads_the_first_version_line(tmp_path):
    (tmp_path / "rust").mkdir()
    (tmp_path / "rust" / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["crates/*"]\n\n[workspace.package]\nversion = "1.5.12"\nedition = "2021"\n'
    )
    assert nix_hashes.cargo_version(tmp_path) == "1.5.12"


def test_main_changes_only_the_release_when_the_files_are_the_same(tmp_path):
    model = {"embed.gguf": nix_hashes.to_sri(OTHER_HEX), "lxask-round4-Q4_K_M.gguf": MODEL_SRI}
    target = hashes_file(tmp_path, release="v1.5.11", model=model)
    assert nix_hashes.main(["v1.5.12", "--sums", str(sums_file(tmp_path)), "--file", str(target)]) == 0
    assert json.loads(target.read_text()) == {"release": "v1.5.12", "model": model}


def test_main_mentions_model_files_it_does_not_use(tmp_path, capsys):
    target = hashes_file(tmp_path, model={"embed.gguf": "sha256-old"})
    nix_hashes.main(["v1.5.12", "--sums", str(sums_file(tmp_path)), "--file", str(target)])
    assert "lxask-round4-Q4_K_M.gguf" in capsys.readouterr().err


def test_download_returns_the_sums_text(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        return FakeResponse(SUMS.encode())

    monkeypatch.setattr(nix_hashes.urllib.request, "urlopen", fake_urlopen)
    assert nix_hashes.download("v1.5.12") == SUMS
    assert seen["url"] == "https://github.com/arxhr007/lxask/releases/download/v1.5.12/SHA256SUMS"


def test_download_says_when_the_release_is_not_published(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(nix_hashes.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(SystemExit, match="HTTP 404.*published"):
        nix_hashes.download("v9.9.9")
