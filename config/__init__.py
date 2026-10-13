from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = Path(__file__).with_name("settings.yaml")


def load_settings() -> dict:
    with SETTINGS_PATH.open(encoding="utf-8") as f:
        settings = yaml.safe_load(f)

    paths = settings["paths"]
    for key, value in list(paths.items()):
        paths[key] = str((ROOT / value).resolve()) if not Path(value).is_absolute() else value
    settings["paths"] = paths
    settings["root"] = str(ROOT)
    return settings