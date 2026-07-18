"""Public project configuration and CLI smoke tests."""

from pathlib import Path

from typer.testing import CliRunner

from aletheia_lab.cli import app
from aletheia_lab.config import load_yaml


def test_project_config_describes_the_executable_benchmark() -> None:
    config = load_yaml(Path("configs/project.yaml"))

    assert "scope" not in config
    assert "vertical_slice" not in config
    assert "owner" not in config["project"]
    assert config["dataset"]["id"] == "telco_customer_churn"
    assert config["benchmark"] == {
        "fault_type": "data_drift",
        "target_cases": 15,
        "dataset_family": "tabular_classification",
    }
    assert config["evaluation"]["human_audit"]["sampling_strategy"] == (
        "census_all_contexts"
    )


def test_info_command_reports_configuration_without_project_plan_language() -> None:
    result = CliRunner().invoke(app, ["info", "--config", "configs/project.yaml"])

    assert result.exit_code == 0
    assert "Aletheia Lab Configuration" in result.stdout
    assert "telco_customer_churn" in result.stdout
    assert "Official case goal" not in result.stdout
