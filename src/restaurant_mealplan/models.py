from pydantic import BaseModel


class InputItem(BaseModel):
    description: str
    image: str

class MenuItem(BaseModel):
    name: str
    description: str

    # Nutritional values per 100 g.
    carbs: float
    protein: float
    fat: float
    fiber: float
    calories: float

    # Weight in grams.
    weight: float



