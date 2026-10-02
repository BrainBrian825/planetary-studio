"""Export the original logo into native icon containers, retaining transparency."""

from pathlib import Path
from PIL import Image

assets = Path(__file__).resolve().parent.parent / "src/planetary_studio/assets"
with Image.open(assets / "planetary-studio.png") as original:
    image = original.convert("RGBA")
    image.save(assets / "planetary-studio.icns")
    image.save(assets / "planetary-studio.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
