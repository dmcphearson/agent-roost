"""Markdown files with frontmatter: reading it and rewriting lines in place."""
import json, os, re
from pathlib import Path


def unquote(value):
    value = value.strip()
    if value[:1] == '"':
        try:
            return json.loads(value)
        except ValueError:
            return value.strip('"')
    if value[:1] == "'" and value[-1:] == "'":
        return value[1:-1].replace("''", "'")
    return value


def frontmatter(text):
    """The YAML frontmatter roost reads, as a dict: plain `key: value` lines and lists of small maps
    (`sessions:` then `  - name: …` / `    id: …`). Anything else is skipped, never rewritten."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out, key = {}, None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace():
            k, sep, v = line.partition(":")
            if not sep:
                key = None
                continue
            key, v = k.strip(), v.strip()
            out[key] = [] if v in ("", "[]") else unquote(v)
            continue
        item = line.strip()
        if key is None or not isinstance(out.get(key), list):
            continue
        if item.startswith("- "):
            item = item[2:]
            out[key].append({})
        k, sep, v = item.partition(":")
        if sep and out[key] and isinstance(out[key][-1], dict):
            out[key][-1][k.strip()] = unquote(v)
        elif not sep:
            out[key].append(unquote(item))
    return out

def readme_lines(folder):
    """A project's README, or a Session recap given its file, as (path, lines with their endings,
    index of the closing ---)."""
    path = Path(folder) if str(folder).endswith(".md") else Path(folder) / "README.md"
    try:
        lines = path.read_text().splitlines(keepends=True)
    except FileNotFoundError:
        raise ValueError(f"{Path(folder).name} has no README.md")
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"its {path.name} has no frontmatter")
    end = next((i for i, l in enumerate(lines[1:], 1) if l.strip() == "---"), None)
    if end is None:
        raise ValueError(f"its {path.name} frontmatter isn't closed with ---")
    return path, lines, end


def write_lines(path, lines):
    """Replace a file in one step, so a reader never sees it half written."""
    tmp = path.with_name(f".{path.name}.roost-tmp")
    tmp.write_text("".join(lines))
    os.replace(tmp, path)


def yaml_text(text):
    """A YAML value that reads back as text exactly: plain when that's safe, quoted otherwise."""
    if re.fullmatch(r"[A-Za-z0-9][\w .()/+&,'@-]*", text) and not text.endswith(" "):
        return text
    return json.dumps(text, ensure_ascii=False)
