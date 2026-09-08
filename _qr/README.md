# QR system

Every QR code on a physical product points at `https://qr.mabdulhussein.com/<slug>`,
which is a redirect page in this repo. The destination can be changed later; the printed
code cannot. That indirection is the whole point.

```
python _qr/build.py
```

That is the only command. It is idempotent — run it as often as you like.

---

## Add a customer

1. Open `_qr/links.json` and add an entry to `links`:

   ```json
   {
     "slug": "acme-review",
     "type": "dynamic",
     "destination": "https://g.page/r/XXXXXXXX/review",
     "note": "Acme Cafe. Google review stand on the front counter, 6 printed Feb 2026."
   }
   ```

2. Run `python _qr/build.py`.
3. Send `_qr/out/acme-review.svg` to Fusion 360, or `_qr/out/acme-review.png` to the printer.
4. Commit and push. The redirect goes live once Pages rebuilds, usually under a minute.

**Scan the printed code before you ship it.** The redirect must be live first — a code
printed before the push resolves to a 404 until you push.

### Pick a slug carefully

Slugs are permanent. They are lowercase letters, digits and hyphens only. Use
`<customer>-<purpose>`: `acme-review`, `acme-menu`, `acme-wifi`. Never reuse a retired
slug for a different customer — old printed codes still point at it.

---

## Repoint an existing code

Change `destination`, keep `slug`, re-run the build, push. Every code already printed now
resolves to the new target. Nothing needs reprinting.

This is how you handle a customer changing their booking system, a menu URL moving, or an
Etsy listing being replaced.

---

## Retire a code

Prefer repointing it at something sensible — a "this promotion has ended" page — over
deleting it. A deleted entry means every printed code 404s.

If you do delete an entry, the build removes its folder and prints a loud warning telling
you exactly which printed codes just died. That warning is the most important output of
this script. If you see it and did not expect it, restore the entry in `links.json` and
re-run **before pushing**.

---

## Entry types

### `dynamic` — anything that resolves to a URL

Encodes `https://qr.mabdulhussein.com/<slug>` and generates a redirect page. Repointable
forever. Use it for Google review links, menus, booking pages, Etsy, Instagram — anything
that opens in a browser.

```json
{ "slug": "moose-etsy", "type": "dynamic",
  "destination": "https://www.etsy.com/shop/...", "note": "card in every shipped order" }
```

### `static` — formats a phone acts on without opening a URL

Encodes the value directly into the QR. **Not repointable** — the data is in the printed
code itself. Changing it means reprinting. Requires a `format`:

| format | `value` | produces |
| --- | --- | --- |
| `wifi` | `{ssid, password, auth, hidden}` | `WIFI:T:WPA;S:<ssid>;P:<pass>;;` |
| `vcard` | `{first_name, last_name, org, title, email, phone, url, nickname, note}` | vCard 3.0 |
| `tel` | `"+15551234567"` | `tel:+15551234567` |
| `sms` | `"+1555..."` or `{phone, body}` | `sms:+1555...?body=...` |
| `raw` | any string | encoded verbatim |

`auth` is `WPA` (default), `WEP`, or `NOPASS`. Special characters in an SSID or password
are escaped for you — do not pre-escape them.

A few older scanners prefer `SMSTO:<number>:<message>` over `sms:`. If you hit one, use
`"format": "raw"` with that value.

---

## Secrets

If this repo is public, `_qr/links.json` is readable on github.com by anyone, even though
GitHub Pages never serves it. WiFi passwords and personal phone numbers do not belong there.

Put them in `_qr/secrets.json`, which is gitignored:

```json
{ "ACME_WIFI_PASSWORD": "hunter2", "MOOSE_PHONE": "+15551234567" }
```

Reference them from `links.json` as `${ACME_WIFI_PASSWORD}`. The build substitutes them and
fails with a clear message if one is missing.

Because `secrets.json` is not committed, keep a backup somewhere safe. Losing it means you
cannot regenerate those codes.

---

## Output

`_qr/out/<slug>.svg` and `_qr/out/<slug>.png`, both gitignored — regenerate rather than commit.

- **SVG** is the one that matters for Fusion 360. Written as **filled, closed rectangles**,
  not stroked lines — Fusion imports strokes as open centerlines that cannot be extruded or
  embossed, whereas closed subpaths come in as closed profiles. Horizontal runs are merged
  into single rectangles to keep the profile count down (typically ~50% fewer than one per
  module). Sized in real-world millimetres — 40mm square by default, change `svg_size_mm`
  in `links.json` — so it imports at a known scale.
- **PNG** is at least 1200px, scaled in whole modules. Whole-module scaling means no
  fractional pixel edges, which matters for scan reliability on a textured print.
- Error correction **level Q** (~25% recoverable) with a 4-module quiet zone. Q is chosen
  because printed codes get scratched, resin-coated, and printed on curved surfaces.
- Black on white with the quiet zone included. Keep the light area light — inverted or
  low-contrast codes scan badly. If you engrave, the dark modules must be the recessed or
  darker material.

---

## Things that will break every code at once

- Deleting or editing `CNAME` (must stay `qr.mabdulhussein.com`).
- Removing the `qr` CNAME record at the DNS registrar.
- Adding a `.nojekyll` file — that publishes `_qr/`, leaking `links.json` at
  `qr.mabdulhussein.com/_qr/links.json`.
- Adding an `index.html` at the repo root — `qr.mabdulhussein.com/` is meant to 404 rather
  than list every slug.

## Checks the build runs

- Slug format, duplicate slugs, and slugs colliding with reserved paths.
- Dynamic destinations must be `http://` or `https://` URLs.
- Refuses to overwrite any `index.html` it did not generate.
- Only ever deletes folders carrying its own `<meta name="generator" content="qr-build">`.
- Warns on a missing `note`, on a phone number with no country code, and on codes dense
  enough (>100 modules) to be risky at small print sizes.
- Warns loudly, above everything else, when a slug has a folder but no entry.

Use `python _qr/build.py --dry-run` to see all of it without writing anything.
