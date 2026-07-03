"""
build_index.py
===============
Regenerates the `TRANSLATIONS` block inside index.html from src/languages.json,
so editing languages.json is the single source of truth for both index.html
and loop_map.html.

Run standalone after editing languages.json:

    python -m src.build_index

It also runs automatically at the end of the main pipeline (see pipeline.py).
"""

import json
import os
import re

DIR_NAME = os.path.dirname(os.path.abspath(__file__))

_KEY_MAP = {
    "title": "index_title",
    "map_title": "index_map_title",
    "presentation_title": "index_presentation_title",
    "report_title": "index_report_title",
}


def build_index(languages_path: str = None, index_path: str = None) -> None:
    languages_path = languages_path or os.path.join(DIR_NAME, "languages.json")
    index_path = index_path or os.path.join(os.path.dirname(DIR_NAME), "index.html")

    with open(languages_path, "r", encoding="utf-8") as f:
        languages = json.load(f)

    translations = {}
    for lang, dict_ in languages.items():
        translations[lang] = {
            local_key: dict_[remote_key]
            for local_key, remote_key in _KEY_MAP.items()
            if remote_key in dict_
        }

    js_block = "const TRANSLATIONS = " + json.dumps(translations, ensure_ascii=False, indent=2) + ";"

    with open(index_path, "r", encoding="utf-8") as f:
        html = f.read()

    pattern = re.compile(r"const TRANSLATIONS = \{.*?\};", re.DOTALL)
    if not pattern.search(html):
        raise RuntimeError(f"TRANSLATIONS block not found in {index_path}")
    html = pattern.sub(lambda _: js_block.replace("\\", "\\\\"), html, count=1)

    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"  → synced TRANSLATIONS in {os.path.abspath(index_path)} from {os.path.abspath(languages_path)}")


if __name__ == "__main__":
    build_index()
