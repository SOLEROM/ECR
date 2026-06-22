# ccFleet logo

A single **neutral placeholder mark** (`svg/mark.svg`) — three connected nodes, a
generic "fleet" motif. It is used as the header brand (inlined in
`web/templates/base.html`) and the favicon.

```
logo/
├── svg/mark.svg   ← the mark (scalable; the only logo asset)
└── README.md
```

## Replace it with your own brand

1. Drop your SVG in as `svg/mark.svg` (keep the filename so the favicon link in
   `base.html` keeps working), **or** point the `<link rel="icon">` in `base.html`
   at a new file.
2. The header shows the mark **inlined** in `base.html` (so it can follow the theme
   via the `.logo-mark` CSS in `web/static/css/main.css`) next to the `ccFleet`
   wordmark — edit that SVG block + wordmark to match your brand.

The mark uses the app palette: green `#3fb950`, dark `#0d1117`/`#161b22`, muted
`#6e7681`. No build step and no PNGs — a browser scales the SVG losslessly.
