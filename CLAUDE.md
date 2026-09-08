Renaming this repo breaks every QR code already printed on physical products. Never suggest it. Slugs are permanent for the same reason.

# r — QR redirect layer for the 3D printing business

Published via GitHub Pages at **https://qr.mabdulhussein.com/** (custom subdomain).
A code printed on a customer's product resolves to `https://qr.mabdulhussein.com/<slug>`.

## The permanence rule

Physical products carry these codes. Once a code is printed, engraved, or shipped, the
URL it encodes can never change. That makes the following irreversible:

| Action | Consequence |
| --- | --- |
| Renaming a slug | Every printed code with the old slug 404s. Dead. |
| Deleting an entry from `links.json` | Same. The build warns loudly, then removes the folder. |
| Deleting or editing `CNAME` | The whole subdomain stops resolving. **Every** printed code dies at once. |
| Removing the `qr` DNS record at the registrar | Same as above, and not visible from this repo. |
| Renaming this repo | Safe *only* while `CNAME` is intact, since the domain follows the repo — but do not rely on it. |

To retire a code, **repoint it** — change `destination`, keep the slug. That is the entire
reason the redirect layer exists. Never reuse a retired slug for a different customer.

## Layout

```
CNAME              qr.mabdulhussein.com — do not delete
robots.txt         Disallow: / — the whole subdomain is non-indexable
_qr/               sources. Underscore prefix means Jekyll never publishes it
  links.json       source of truth
  secrets.json     gitignored, optional, holds WiFi passwords etc.
  build.py         the only command
  out/             gitignored, generated SVG + PNG
<slug>/index.html  generated redirect pages, one per dynamic entry
```

## Rules for working in this repo

- **Never add a `.nojekyll` file.** Jekyll's skip-underscore-directories behavior is the only
  thing keeping `_qr/` — including `links.json` — off the public site. Adding `.nojekyll`
  publishes `_qr/links.json` at `qr.mabdulhussein.com/_qr/links.json`, leaking every customer
  note and WiFi password.
- **Never add an `index.html` at the repo root.** `qr.mabdulhussein.com/` must 404 rather than
  enumerate slugs.
- **Never link to a `/<slug>` page** from this repo, the resume site, or a sitemap.
- `_qr/out/` is gitignored on purpose. Generated images are reproducible; committing them
  bloats the repo with binaries.
- Run `python _qr/build.py` after any edit to `links.json`. It is idempotent.

## Public-repo warning

If this repo is public, `_qr/links.json` is readable on github.com even though Pages never
serves it. Anything sensitive belongs in `_qr/secrets.json` (gitignored) and referenced as
`${NAME}`. See `_qr/README.md`.
