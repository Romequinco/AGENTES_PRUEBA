import os
import pytz

SKILLS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "skills")

MADRID_TZ = pytz.timezone("Europe/Madrid")


def strip_markdown_fence(text: str) -> str:
    """Extrae el contenido de un bloque ```json ... ``` o ``` ... ```."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]  # quitar línea de apertura (```json o ```)
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return text


def load_instructions(filename: str) -> str:
    path = os.path.join(SKILLS_DIR, filename)
    with open(path, encoding="utf-8") as f:
        return f.read()
