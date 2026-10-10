import hashlib
import json

from src.competition.configuration import code_fingerprint, load_settings
from src.data.universe import ROOT


def test_settings_actual_team_and_unconfirmed_gates():
    settings = load_settings()
    assert settings.competition["team_id"] == "TEAM_11586"
    assert not settings.policy.cash_rounding_confirmed
    assert not settings.policy.active_share_method_confirmed
    assert settings.availability.mode == "strict_verified"
    assert settings.optimizer.max_repairs == 12


def test_code_fingerprint_covers_prompts_configs_not_credentials():
    fingerprint = code_fingerprint()
    assert len(fingerprint["code_version"]) == 64
    paths = fingerprint["file_hashes"]
    assert "src/agent/investment.py" in paths and "config/alpha_strategy.yaml" in paths
    assert not any(".env" in p or p.startswith("data/") for p in paths)


def test_official_files_preserve_original_bytes():
    manifest = json.loads((ROOT / "docs/official/manifest.json").read_text(encoding="utf-8"))
    entries = manifest if isinstance(manifest, list) else manifest.get("files", [])
    assert entries
    for entry in entries:
        name = entry.get("filename", entry.get("file"))
        path = ROOT / "docs/official" / name
        if path.suffix in {".json", ".md"}:
            assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
