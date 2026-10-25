import base64
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from ruamel.yaml import YAML

from restaurant_mealplan.ai import parse_item
from restaurant_mealplan.models import InputItem, MenuItem


def test_parse_item(monkeypatch):
    yaml_path = Path(__file__).resolve().parents[2] / "data" / "rice-in-lotus.yaml"
    with yaml_path.open(encoding="utf-8") as file:
        input_item = InputItem.model_validate(YAML(typ="safe").load(file))
    input_item.image = str(yaml_path.parent / input_item.image)

    expected = MenuItem(
        name="Rice in lotus leaves",
        description="Steamed sticky rice with pork filling wrapped in lotus leaves",
        carbs=35.0,
        protein=6.0,
        fat=5.0,
        fiber=1.0,
        calories=210.0,
        weight=180.0,
    )
    client = Mock()
    client.responses.parse.return_value = SimpleNamespace(output_parsed=expected)
    monkeypatch.setattr("restaurant_mealplan.ai.OpenAI", lambda: client)

    assert parse_item(input_item) == expected
    call = client.responses.parse.call_args.kwargs
    assert call["model"] == "gpt-5.4-mini"
    assert call["text_format"] is MenuItem
    assert call["input"][1]["content"][0]["text"] == input_item.description
    image_url = call["input"][1]["content"][1]["image_url"]
    assert image_url.startswith("data:image/jpeg;base64,")
    assert base64.b64decode(image_url.partition(",")[2]) == Path(input_item.image).read_bytes()
