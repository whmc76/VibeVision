import json
from pathlib import Path
from typing import Any


TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "app" / "workflow_templates"


def test_workflow_seed_fields_use_random_placeholder() -> None:
    seed_values: list[tuple[str, str, Any]] = []
    for template_path in TEMPLATE_DIR.glob("*.json"):
        template = json.loads(template_path.read_text(encoding="utf-8"))
        seed_values.extend(_find_seed_values(template, template_path.name))

    assert seed_values
    assert all(value == "__random_seed__" for _, _, value in seed_values), seed_values


def _find_seed_values(value: Any, filename: str, path: str = "") -> list[tuple[str, str, Any]]:
    if isinstance(value, dict):
        seed_values: list[tuple[str, str, Any]] = []
        for key, item in value.items():
            item_path = f"{path}.{key}" if path else key
            if "seed" in key.lower():
                seed_values.append((filename, item_path, item))
            seed_values.extend(_find_seed_values(item, filename, item_path))
        return seed_values

    if isinstance(value, list):
        seed_values = []
        for index, item in enumerate(value):
            seed_values.extend(_find_seed_values(item, filename, f"{path}[{index}]"))
        return seed_values

    return []
