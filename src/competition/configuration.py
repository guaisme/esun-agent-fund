from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, fields
from pathlib import Path

import yaml

from src.data.universe import ROOT
from src.portfolio.optimizer import OptimizerConfig
from src.strategy.availability import AvailabilityPolicy
from src.strategy.ranking import AlphaConfig
from .models import AccountingError, TransactionCosts, decimal
from .policy import CompetitionPolicy


@dataclass(frozen=True)
class Settings:
    competition: dict
    policy: CompetitionPolicy
    costs: TransactionCosts
    alpha: AlphaConfig
    optimizer: OptimizerConfig
    availability: AvailabilityPolicy
    agent: dict


def load_settings(root: Path = ROOT) -> Settings:
    directory = root / "config"
    competition = json.loads((directory / "competition.json").read_text(encoding="utf-8"))

    def read(name):
        value = yaml.safe_load((directory / name).read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise AccountingError(f"Configuration must be a mapping: {name}")
        return value

    def config(cls, value):
        return cls(
            **{field.name: value[field.name] for field in fields(cls) if field.name in value}
        )

    policy_values = dict(competition)
    policy_values["missing_execution"] = competition["missing_execution_policy"]
    if decimal(competition["initial_cash_ntd"]) != 1000000000:
        raise AccountingError("Official initial NAV must be NTD 1 billion")
    return Settings(
        competition,
        config(CompetitionPolicy, policy_values),
        TransactionCosts(
            decimal(competition["commission_rate"]),
            decimal(competition["sell_tax_rate"]),
            decimal(competition["cash_quantum_ntd"]),
            competition["cash_rounding"],
        ),
        config(AlphaConfig, read("alpha_strategy.yaml")),
        config(OptimizerConfig, read("optimizer.yaml")),
        config(AvailabilityPolicy, read("data_policy.yaml")),
        read("agent.yaml"),
    )


def code_fingerprint(root: Path = ROOT) -> dict:
    """Decision-affecting source/config/prompt content, never credentials or data."""
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "unknown"
    paths = sorted(
        {
            *root.glob("src/**/*.py"),
            *root.glob("research_agent/*.py"),
            *root.glob("config/*.yaml"),
            *root.glob("config/*.json"),
            *root.glob("config/*.csv"),
            root / "requirements.txt",
            root / "docs/official/D-Plan.schema_v4.2.json",
        }
    )
    hashes = {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
        if path.is_file() and not path.name.startswith("local")
    }
    digest = hashlib.sha256(
        json.dumps({"git_commit": commit, "file_hashes": hashes}, sort_keys=True).encode()
    ).hexdigest()
    return {"git_commit": commit, "code_version": digest, "file_hashes": hashes}


def archive_source(destination: Path, fingerprint: dict, root: Path = ROOT) -> str:
    """Save exact decision-affecting bytes locally so dirty-working-tree traces replay."""
    from zipfile import ZipFile, ZIP_DEFLATED

    destination.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(destination, "w", compression=ZIP_DEFLATED) as archive:
        for relative, expected in fingerprint["file_hashes"].items():
            content = (root / relative).read_bytes()
            if hashlib.sha256(content).hexdigest() != expected:
                raise AccountingError("Source changed while creating audit archive")
            archive.writestr(relative, content)
        archive.writestr("fingerprint.json", json.dumps(fingerprint, sort_keys=True))
    return hashlib.sha256(destination.read_bytes()).hexdigest()


def configure_agent_environment(settings: Settings) -> None:
    """Explicit process environment overrides YAML defaults; never handles keys."""
    import os

    names = {
        "max_retries": "AGENT_MAX_RETRIES",
        "initial_backoff": "AGENT_INITIAL_BACKOFF",
        "max_backoff": "AGENT_MAX_BACKOFF",
        "request_timeout": "GEMINI_REQUEST_TIMEOUT",
        "tool_timeout": "MCP_TOOL_TIMEOUT",
        "task_deadline": "AGENT_TASK_DEADLINE",
    }
    for name, environment in names.items():
        os.environ.setdefault(environment, str(settings.agent[name]))
