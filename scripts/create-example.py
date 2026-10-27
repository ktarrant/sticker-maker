"""Create a deterministic software-only parsing sheet; no camera/scale assumptions."""

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def create_example(destination: Path):
    destination = Path(destination)
    if destination.exists():
        return  # Preserve an existing local example or its associated review job.
    image = Image.new("RGB", (1200, 800), "#204252")
    draw = ImageDraw.Draw(image)
    title = ImageFont.load_default(size=42)
    label = ImageFont.load_default(size=25)
    draw.text((60, 35), "STICKER MAKER · SOFTWARE TEST", font=title, fill="white")
    colors = ["#ca3e47", "#3177af", "#dd9c22", "#437b53"]
    for i, color in enumerate(colors):
        x = 60 + 285*i
        draw.rounded_rectangle((x, 140, x+245, 600), radius=24, fill="white")
        if i == 0:
            draw.ellipse((x+40, 210, x+205, 390), fill=color)
        elif i == 1:
            draw.polygon([(x+35, 390), (x+122, 200), (x+210, 390)], fill=color)
        elif i == 2:
            draw.rounded_rectangle((x+35, 220, x+210, 390), radius=30, fill=color)
        else:
            draw.polygon([(x+122, 200), (x+215, 305), (x+122, 410), (x+30, 305)], fill=color)
        draw.text((x+40, 490), f"LABEL {i+1}", font=label, fill=color)
    draw.text((60, 690), "Image coordinates only · not a physical calibration target", font=label, fill="white")
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("output/examples/labels.png"))
    create_example(parser.parse_args().output)
