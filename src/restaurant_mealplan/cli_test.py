import json
from pathlib import Path

from click.testing import CliRunner

from restaurant_mealplan.cli import cli
from restaurant_mealplan.models import MenuItem


def test_parse_item_command(monkeypatch):
    yaml_path = Path(__file__).resolve().parents[2] / "data" / "rice-in-lotus.yaml"
    expected = MenuItem(
        name="Rice in lotus leaves",
        description="Steamed sticky rice with pork filling",
        carbs=35,
        protein=6,
        fat=5,
        fiber=1,
        calories=210,
        weight=180,
    )
    received = []

    def fake_parse_item(input_item):
        received.append(input_item)
        return expected

    monkeypatch.setattr("restaurant_mealplan.cli.parse_menu_item", fake_parse_item)

    result = CliRunner().invoke(cli, ["parse-item", "--input-yaml", str(yaml_path)])

    assert result.exit_code == 0, result.output
    assert received[0].image == str(yaml_path.parent / "rice-in-lotus.jpg")
    assert "Steamed sticky rice" in received[0].description
    assert json.loads(result.output) == expected.model_dump()
