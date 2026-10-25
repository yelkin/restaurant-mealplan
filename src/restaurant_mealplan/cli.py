from pathlib import Path

import click
import dotenv
from ruamel.yaml import YAML

from restaurant_mealplan.ai import parse_item as parse_menu_item
from restaurant_mealplan.models import InputItem


@click.group()
def cli():
    dotenv.load_dotenv()


@cli.command("parse-item")
@click.option(
    "--input-yaml",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
def parse_item(input_yaml: Path):
    with input_yaml.open(encoding="utf-8") as file:
        input_item = InputItem.model_validate(YAML(typ="safe").load(file))

    image_path = Path(input_item.image)
    if not image_path.is_absolute():
        input_item.image = str(input_yaml.parent / image_path)

    menu_item = parse_menu_item(input_item)
    click.echo(menu_item.model_dump_json(indent=2))


if __name__ == "__main__":
    cli()
