import base64
import mimetypes
from pathlib import Path

from openai import OpenAI

from restaurant_mealplan.models import InputItem, MenuItem


def parse_item(input_item: InputItem) -> MenuItem:
    image_path = Path(input_item.image)
    media_type, _ = mimetypes.guess_type(image_path.name)
    if media_type not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
        raise ValueError(f"Unsupported image type: {image_path.suffix}")

    image_data = base64.b64encode(image_path.read_bytes()).decode("ascii")
    response = OpenAI().responses.parse(
        model="gpt-5.4-mini",
        text_format=MenuItem,
        input=[
            {
                "role": "system",
                "content": (
                    "Estimate a menu item from its description and photo. "
                    "Write a concise English name and description. "
                    "Estimate carbs, protein, fat, fiber in grams and calories in kcal "
                    "per 100 g. Estimate the pictured portion weight in grams. "
                    "Use plausible values; the image cannot determine exact nutrition or weight."
                ),
            },
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": input_item.description},
                    {
                        "type": "input_image",
                        "image_url": f"data:{media_type};base64,{image_data}",
                    },
                ],
            },
        ],
    )
    if response.output_parsed is None:
        raise ValueError("OpenAI did not return a menu item")
    return response.output_parsed
