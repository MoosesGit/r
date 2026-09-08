#!/usr/bin/env python3
"""Build the QR redirect layer and the print-ready QR assets.

Usage:
    python _qr/build.py            # build
    python _qr/build.py --dry-run  # show what would change, write nothing

Reads _qr/links.json. For every "dynamic" entry it writes <slug>/index.html at the
repo root, which GitHub Pages serves as https://qr.mabdulhussein.com/<slug>. For
every entry it writes a print-ready SVG and PNG into _qr/out/.

Idempotent: re-running with an unchanged links.json rewrites identical bytes.

The single most important thing this script does is refuse to let a printed code
die silently. If a slug has a redirect folder but no entry in links.json, that code
is already out in the world on someone's counter and has just stopped working. That
is reported as a loud warning, not a log line.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import quote

try:
    import segno
except ImportError:  # pragma: no cover
    sys.exit(
        "segno is not installed.\n"
        "    python -m pip install segno\n"
        "It is pure Python and pulls in no system dependencies."
    )

# --------------------------------------------------------------------------- paths

REPO_ROOT = Path(__file__).resolve().parent.parent
QR_DIR = REPO_ROOT / "_qr"
OUT_DIR = QR_DIR / "out"
LINKS_FILE = QR_DIR / "links.json"
SECRETS_FILE = QR_DIR / "secrets.json"

# --------------------------------------------------------------------------- config

QR_ERROR = "q"          # error correction level Q (~25%), per spec
QR_BORDER = 4           # quiet zone in modules; 4 is the QR spec minimum
PNG_MIN_PX = 1200       # PNG is scaled up to at least this, on whole modules
SVG_SIZE_MM_DEFAULT = 40.0

# Written into every generated page. Nothing without this marker is ever deleted.
GENERATOR = "qr-build"

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SECRET_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")
VALID_TYPES = {"dynamic", "static"}
VALID_FORMATS = {"wifi", "vcard", "tel", "sms", "raw"}

# Root entries the pruner must never consider a slug folder.
PROTECTED = {"_qr", ".git", ".github", "node_modules", ".venv", "venv"}

# Colour only when attached to a terminal, so piping to a file or a log stays clean.
_COLOR = sys.stdout.isatty() and sys.platform != "emscripten"
RED = "\033[31m" if _COLOR else ""
YELLOW = "\033[33m" if _COLOR else ""
GREEN = "\033[32m" if _COLOR else ""
DIM = "\033[2m" if _COLOR else ""
BOLD = "\033[1m" if _COLOR else ""
RESET = "\033[0m" if _COLOR else ""

_warnings: list[str] = []


def warn(msg: str) -> None:
    _warnings.append(msg)


# --------------------------------------------------------------------------- errors


class BuildError(Exception):
    """A problem in links.json that must be fixed before anything is written."""


# --------------------------------------------------------------------------- secrets


def load_secrets() -> dict[str, str]:
    if not SECRETS_FILE.exists():
        return {}
    try:
        data = json.loads(SECRETS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BuildError(f"{SECRETS_FILE.name} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise BuildError(f"{SECRETS_FILE.name} must be a flat object of NAME -> value.")
    return {str(k): str(v) for k, v in data.items()}


def resolve(value: str, secrets: dict[str, str], where: str) -> str:
    """Substitute ${NAME} placeholders from secrets.json."""
    missing: list[str] = []

    def sub(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in secrets:
            missing.append(name)
            return match.group(0)
        return secrets[name]

    out = SECRET_RE.sub(sub, value)
    if missing:
        names = ", ".join(sorted(set(missing)))
        raise BuildError(
            f"{where}: no value for {names}.\n"
            f"    Add it to _qr/secrets.json (gitignored):\n"
            f'        {{ "{sorted(set(missing))[0]}": "..." }}'
        )
    return out


# --------------------------------------------------------------------------- payloads


def esc_wifi(value: str) -> str:
    """Escape a field for the WIFI: payload. Order matters: backslash first."""
    for ch in ("\\", ";", ",", ":", '"'):
        value = value.replace(ch, "\\" + ch)
    return value


def esc_vcard(value: str) -> str:
    for old, new in (("\\", "\\\\"), (";", "\\;"), (",", "\\,"), ("\n", "\\n")):
        value = value.replace(old, new)
    return value


def need(value: dict, key: str, where: str) -> str:
    got = value.get(key)
    if not isinstance(got, str) or not got.strip():
        raise BuildError(f"{where}: missing required field '{key}'.")
    return got.strip()


def build_wifi(value: dict, where: str) -> str:
    if not isinstance(value, dict):
        raise BuildError(f"{where}: wifi 'value' must be an object with ssid/password.")
    ssid = need(value, "ssid", where)
    auth = str(value.get("auth", "WPA")).upper()
    if auth not in {"WPA", "WEP", "NOPASS"}:
        raise BuildError(f"{where}: wifi 'auth' must be WPA, WEP, or NOPASS (got {auth!r}).")
    password = str(value.get("password", ""))
    if auth != "NOPASS" and not password:
        raise BuildError(f"{where}: wifi auth is {auth} but no password was given.")
    hidden = bool(value.get("hidden", False))

    payload = f"WIFI:T:{auth};S:{esc_wifi(ssid)};"
    if auth != "NOPASS":
        payload += f"P:{esc_wifi(password)};"
    if hidden:
        payload += "H:true;"
    return payload + ";"


def build_vcard(value: dict, where: str) -> str:
    if not isinstance(value, dict):
        raise BuildError(f"{where}: vcard 'value' must be an object.")
    last = esc_vcard(need(value, "last_name", where))
    first = esc_vcard(need(value, "first_name", where))
    lines = [
        "BEGIN:VCARD",
        "VERSION:3.0",
        f"N:{last};{first};;;",
        f"FN:{esc_vcard((value.get('first_name','') + ' ' + value.get('last_name','')).strip())}",
    ]
    optional = (
        ("nickname", "NICKNAME:{}"),
        ("org", "ORG:{}"),
        ("title", "TITLE:{}"),
        ("phone", "TEL;TYPE=CELL:{}"),
        ("email", "EMAIL;TYPE=INTERNET:{}"),
        ("url", "URL:{}"),
        ("note", "NOTE:{}"),
    )
    for key, template in optional:
        raw = value.get(key)
        if isinstance(raw, str) and raw.strip():
            lines.append(template.format(esc_vcard(raw.strip())))
    lines.append("END:VCARD")
    # vCard requires CRLF line endings.
    return "\r\n".join(lines)


def normalize_phone(raw: str, where: str) -> str:
    cleaned = re.sub(r"[^\d+]", "", raw)
    if not cleaned:
        raise BuildError(f"{where}: phone number {raw!r} has no digits.")
    if not cleaned.startswith("+"):
        warn(
            f"{where}: phone {cleaned!r} has no country code. A tel:/sms: code without "
            f"a leading + can fail for anyone roaming or dialing from abroad. "
            f"Consider '+1{cleaned}'."
        )
    return cleaned


def build_tel(value, where: str) -> str:
    raw = value if isinstance(value, str) else need(value or {}, "phone", where)
    return f"tel:{normalize_phone(raw, where)}"


def build_sms(value, where: str) -> str:
    if isinstance(value, str):
        return f"sms:{normalize_phone(value, where)}"
    if not isinstance(value, dict):
        raise BuildError(f"{where}: sms 'value' must be a string or an object.")
    phone = normalize_phone(need(value, "phone", where), where)
    body = value.get("body")
    if isinstance(body, str) and body.strip():
        return f"sms:{phone}?body={quote(body.strip())}"
    return f"sms:{phone}"


def static_payload(entry: dict, where: str, secrets: dict[str, str]) -> str:
    fmt = entry.get("format")
    if fmt is None:
        raise BuildError(
            f"{where}: static entries need a 'format' "
            f"({', '.join(sorted(VALID_FORMATS))})."
        )
    if fmt not in VALID_FORMATS:
        raise BuildError(
            f"{where}: unknown format {fmt!r}. "
            f"Expected one of {', '.join(sorted(VALID_FORMATS))}."
        )

    value = entry.get("value", entry.get("destination"))
    if value is None:
        raise BuildError(f"{where}: static entries need a 'value'.")

    # Resolve ${SECRETS} anywhere in the value before formatting.
    if isinstance(value, str):
        value = resolve(value, secrets, where)
    elif isinstance(value, dict):
        value = {
            k: resolve(v, secrets, where) if isinstance(v, str) else v
            for k, v in value.items()
        }

    if fmt == "wifi":
        return build_wifi(value, where)
    if fmt == "vcard":
        return build_vcard(value, where)
    if fmt == "tel":
        return build_tel(value, where)
    if fmt == "sms":
        return build_sms(value, where)
    # raw
    if not isinstance(value, str):
        raise BuildError(f"{where}: raw 'value' must be a string.")
    return value


# --------------------------------------------------------------------------- config load


def load_config() -> tuple[str, list[dict], float]:
    if not LINKS_FILE.exists():
        raise BuildError(f"{LINKS_FILE} not found.")
    try:
        data = json.loads(LINKS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BuildError(f"links.json is not valid JSON: {exc}") from exc

    base = data.get("base_url")
    if not isinstance(base, str) or not base.startswith(("http://", "https://")):
        raise BuildError("links.json needs a 'base_url' starting with https://")
    base = base.rstrip("/")

    links = data.get("links")
    if not isinstance(links, list):
        raise BuildError("links.json needs a 'links' array.")

    svg_mm = data.get("svg_size_mm", SVG_SIZE_MM_DEFAULT)
    try:
        svg_mm = float(svg_mm)
        if svg_mm <= 0:
            raise ValueError
    except (TypeError, ValueError):
        raise BuildError("'svg_size_mm' must be a positive number.") from None

    return base, links, svg_mm


def validate(links: list[dict]) -> None:
    seen: dict[str, int] = {}
    for i, entry in enumerate(links):
        where = f"links[{i}]"
        if not isinstance(entry, dict):
            raise BuildError(f"{where}: each entry must be an object.")

        slug = entry.get("slug")
        if not isinstance(slug, str) or not slug:
            raise BuildError(f"{where}: missing 'slug'.")
        if not SLUG_RE.match(slug):
            raise BuildError(
                f"{where}: slug {slug!r} is invalid. Use lowercase letters, digits and "
                f"hyphens only, starting with a letter or digit. Slugs become URLs and "
                f"are permanent."
            )
        if slug in PROTECTED:
            raise BuildError(f"{where}: slug {slug!r} collides with a reserved path.")
        if slug in seen:
            raise BuildError(
                f"{where}: duplicate slug {slug!r}, already used by links[{seen[slug]}]. "
                f"Two codes cannot share a slug."
            )
        seen[slug] = i

        entry_type = entry.get("type")
        if entry_type not in VALID_TYPES:
            raise BuildError(
                f"{where} ({slug}): 'type' must be 'dynamic' or 'static', got {entry_type!r}."
            )

        if entry_type == "dynamic":
            dest = entry.get("destination")
            if not isinstance(dest, str) or not dest.strip():
                raise BuildError(f"{where} ({slug}): dynamic entries need a 'destination'.")
            if not dest.startswith(("http://", "https://")):
                raise BuildError(
                    f"{where} ({slug}): destination {dest!r} must start with http:// or "
                    f"https://. A dynamic code redirects a browser, so it needs a URL. "
                    f"For WiFi, vCard, tel or sms use \"type\": \"static\"."
                )

        note = entry.get("note")
        if not isinstance(note, str) or not note.strip():
            warn(
                f"{slug}: no 'note'. Record which customer or product this code is on — "
                f"without it you cannot tell what breaks when you repoint it."
            )


# --------------------------------------------------------------------------- writing


REDIRECT_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<meta name="generator" content="{generator}">
<meta http-equiv="refresh" content="0; url={dest_attr}">
<title>Redirecting</title>
<style>
body {{ font-family: system-ui, sans-serif; background: #1f1f1f; color: #e0e0e0;
       display: flex; align-items: center; justify-content: center; min-height: 100vh;
       margin: 0; padding: 24px; text-align: center; }}
a {{ color: #cc4d30; }}
</style>
</head>
<body>
<main>
<p>Redirecting&hellip;</p>
<p>If nothing happens, <a href="{dest_attr}">continue here</a>.</p>
</main>
<script>location.replace({dest_js});</script>
</body>
</html>
"""


def render_redirect(destination: str) -> str:
    return REDIRECT_TEMPLATE.format(
        generator=GENERATOR,
        dest_attr=html.escape(destination, quote=True),
        # Script content is not HTML-decoded, so this must NOT be html.escape'd —
        # entities would land in the JS as literal text. json.dumps gives a valid
        # string literal; escaping "</" stops a destination closing the script tag.
        dest_js=json.dumps(destination).replace("</", "<\\/"),
    )


def write_if_changed(path: Path, content: str, dry_run: bool) -> bool:
    """Write only when bytes differ, so re-runs are no-ops. Returns True if changed."""
    data = content.encode("utf-8")
    if path.exists() and path.read_bytes() == data:
        return False
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return True


def is_generated(directory: Path) -> bool:
    """True only for a folder holding a redirect page this script produced."""
    index = directory / "index.html"
    if not index.is_file():
        return False
    try:
        return f'content="{GENERATOR}"' in index.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


# --------------------------------------------------------------------------- QR assets


def render_svg(qr: "segno.QRCode", size_mm: float) -> str:
    """Emit the code as filled, closed rectangles.

    segno's own SVG draws each row as a stroked line. Fusion 360 imports strokes as
    open centerlines, which cannot be extruded or embossed. Closed subpaths import as
    closed profiles instead. Horizontal runs are merged into single rectangles to keep
    the profile count low, which matters when Fusion has to solve every one of them.
    """
    rows = [list(row) for row in qr.matrix_iter(border=QR_BORDER)]
    n = len(rows)

    parts: list[str] = []
    for y, row in enumerate(rows):
        x = 0
        while x < n:
            if not row[x]:
                x += 1
                continue
            run = 1
            while x + run < n and row[x + run]:
                run += 1
            parts.append(f"M{x} {y}h{run}v1h-{run}z")
            x += run

    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
        f'width="{size_mm:g}mm" height="{size_mm:g}mm" viewBox="0 0 {n} {n}" '
        'shape-rendering="crispEdges">\n'
        f'<rect width="{n}" height="{n}" fill="#ffffff"/>\n'
        f'<path fill="#000000" d="{"".join(parts)}"/>\n'
        "</svg>\n"
    )


def write_qr(slug: str, payload: str, svg_mm: float, dry_run: bool) -> tuple[int, int]:
    """Write SVG + PNG for one code. Returns (png_px, module_count)."""
    qr = segno.make(payload, error=QR_ERROR)
    modules = qr.symbol_size(scale=1, border=QR_BORDER)[0]
    scale = max(1, math.ceil(PNG_MIN_PX / modules))
    png_px = modules * scale

    if not dry_run:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        # PNG: whole-module scaling only. A fractional module size blurs edges and
        # costs scan reliability on a curved or textured print.
        qr.save(
            OUT_DIR / f"{slug}.png",
            scale=scale,
            border=QR_BORDER,
            dark="#000000",
            light="#ffffff",
        )
        # SVG: closed profiles, sized in real-world mm so Fusion 360 imports to scale.
        (OUT_DIR / f"{slug}.svg").write_text(render_svg(qr, svg_mm), encoding="utf-8")
    return png_px, modules


# --------------------------------------------------------------------------- table


def truncate(value: str, width: int) -> str:
    flat = value.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    return flat if len(flat) <= width else flat[: width - 1] + "…"


def print_table(rows: list[dict]) -> None:
    headers = ("SLUG", "TYPE", "ENCODED VALUE", "DESTINATION")
    caps = (24, 8, 46, 46)
    cells = [
        [
            truncate(r["slug"], caps[0]),
            truncate(r["type"], caps[1]),
            truncate(r["encoded"], caps[2]),
            truncate(r["destination"], caps[3]),
        ]
        for r in rows
    ]
    widths = [
        max(len(headers[i]), max((len(c[i]) for c in cells), default=0))
        for i in range(4)
    ]
    line = "  ".join("-" * w for w in widths)
    print(BOLD + "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)) + RESET)
    print(DIM + line + RESET)
    for c in cells:
        print("  ".join(c[i].ljust(widths[i]) for i in range(4)))


# --------------------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the QR redirect layer.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change without writing or deleting anything",
    )
    args = parser.parse_args()

    try:
        base_url, links, svg_mm = load_config()
        validate(links)
        secrets = load_secrets()
    except BuildError as exc:
        print(f"{RED}links.json is not usable:{RESET}\n  {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"{YELLOW}DRY RUN — nothing will be written or deleted.{RESET}\n")

    # Pass 1 — resolve every entry before touching disk, so a bad entry halfway
    # down links.json can never leave a half-built tree behind.
    rows: list[dict] = []
    dynamic_slugs: set[str] = set()
    try:
        for i, entry in enumerate(links):
            slug = entry["slug"]
            where = f"links[{i}] ({slug})"

            if entry["type"] == "dynamic":
                dynamic_slugs.add(slug)
                destination = resolve(entry["destination"].strip(), secrets, where)
                encoded = f"{base_url}/{slug}"

                target = REPO_ROOT / slug
                if target.exists() and not target.is_dir():
                    raise BuildError(f"{where}: {target.name} exists and is not a directory.")
                if target.is_dir() and (target / "index.html").exists() and not is_generated(target):
                    raise BuildError(
                        f"{where}: {slug}/index.html exists but was not generated by this "
                        f"script. Refusing to overwrite it. Move it aside or pick another slug."
                    )
            else:
                encoded = static_payload(entry, where, secrets)
                destination = f"{entry.get('format', 'raw')} payload"

            rows.append(
                {
                    "slug": slug,
                    "type": entry["type"],
                    "encoded": encoded,
                    "destination": destination,
                }
            )
    except BuildError as exc:
        print(f"{RED}Build failed — nothing was written:{RESET}\n  {exc}", file=sys.stderr)
        return 1

    # Pass 2 — everything resolved, now write.
    written = 0
    for row in rows:
        if row["type"] == "dynamic":
            page = render_redirect(row["destination"])
            if write_if_changed(REPO_ROOT / row["slug"] / "index.html", page, args.dry_run):
                written += 1

        png_px, modules = write_qr(row["slug"], row["encoded"], svg_mm, args.dry_run)
        row["png_px"], row["modules"] = png_px, modules

        if modules > 100:
            warn(
                f"{row['slug']}: {modules}x{modules} modules. That is dense for an engraved "
                f"or small-format print — test-scan before committing to a run."
            )

    # ---- the safety check that matters most -------------------------------
    orphans: list[str] = []
    for child in sorted(REPO_ROOT.iterdir()):
        if not child.is_dir() or child.name in PROTECTED:
            continue
        if child.name.startswith((".", "_")):
            continue
        if not is_generated(child):
            continue  # not ours — never touch it
        if child.name not in dynamic_slugs:
            orphans.append(child.name)

    if orphans:
        print()
        print(f"{RED}{BOLD}{'!' * 72}{RESET}")
        print(f"{RED}{BOLD}  WARNING: {len(orphans)} PRINTED CODE(S) JUST STOPPED WORKING{RESET}")
        print(f"{RED}{'!' * 72}{RESET}")
        for slug in orphans:
            print(f"{RED}  /{slug}{RESET} has a redirect folder but no entry in links.json.")
            print(f"    Any code already printed with {base_url}/{slug} now 404s.")
        print()
        print(f"{YELLOW}  If this was intentional, nothing to do — the folder is being removed.")
        print(f"  If it was not, restore the entry in links.json and re-run BEFORE pushing.")
        print(f"  To retire a code safely, keep the slug and repoint its destination.{RESET}")
        print()
        for slug in orphans:
            if args.dry_run:
                print(f"{DIM}  would remove {slug}/{RESET}")
            else:
                shutil.rmtree(REPO_ROOT / slug)
                print(f"  removed {slug}/")

    # Stale assets in out/ are a printing hazard: a leftover SVG for a deleted entry
    # still looks importable but encodes a URL that now 404s. out/ is entirely
    # generated and gitignored, so anything not backing a current entry goes.
    if OUT_DIR.is_dir():
        current = {row["slug"] for row in rows}
        stale = sorted(
            asset
            for asset in OUT_DIR.iterdir()
            if asset.is_file() and asset.suffix in {".svg", ".png"} and asset.stem not in current
        )
        for asset in stale:
            if args.dry_run:
                print(f"{DIM}  would remove stale asset {asset.name}{RESET}")
            else:
                asset.unlink()
                print(f"  removed stale asset {asset.name}")

    # ---- report -----------------------------------------------------------
    print()
    print_table(rows)
    print()

    dyn = sum(1 for r in rows if r["type"] == "dynamic")
    stat = len(rows) - dyn
    png_range = f"{min(r['png_px'] for r in rows)}-{max(r['png_px'] for r in rows)}px" if rows else "n/a"
    print(
        f"{GREEN}{len(rows)} code(s){RESET}  "
        f"{dyn} dynamic, {stat} static  |  "
        f"redirect pages {'unchanged' if not written else f'{written} written'}  |  "
        f"ECC {QR_ERROR.upper()}, quiet zone {QR_BORDER} modules  |  "
        f"PNG {png_range}, SVG {svg_mm:g}mm"
    )
    print(f"{DIM}assets: {OUT_DIR}{RESET}")

    if _warnings:
        print()
        for message in _warnings:
            print(f"{YELLOW}warning:{RESET} {message}")

    if args.dry_run:
        print(f"\n{YELLOW}DRY RUN — no files were written.{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
