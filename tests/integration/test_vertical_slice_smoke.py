from pathlib import Path

from aletheia_lab.config import load_yaml


def test_vertical_slice_config_is_eval_first() -> None:
    config = load_yaml(Path("configs/project.yaml"))

    assert config["project"]["mode"] == "eval_first"
    assert config["vertical_slice"]["fault_type"] == "data_drift"
    assert config["vertical_slice"]["target_cases"] == 15
