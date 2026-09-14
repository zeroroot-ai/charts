# Zitadel login branding — declared brand source

These files are the single declared source for the hosted-login branding.
The `zitadel-login-branding` hook Job (templates/jobs/zitadel-login-branding-job.yaml)
mounts them via ConfigMap, PUTs `label-policy.json` to Zitadel's instance label
policy, uploads the SVG marks as logo/icon assets, and activates the policy.

## Token → hex mapping

The palette is `@zeroroot-ai/brand` — ONE locked light brand, "acid concrete"
(ADR-0064). Zitadel's label policy only accepts sRGB hex, so each oklch token
is converted with the CSS Color 4 reference math (oklch → oklab → linear sRGB →
gamma-encoded, clamped to gamut):

| policy field             | brand token     | oklch                    | hex       |
|--------------------------|-----------------|--------------------------|-----------|
| `primaryColor(Dark)`     | `--highlight`   | `oklch(0.440 0.135 132)` | `#346000` |
| `backgroundColor(Dark)`  | `--background`  | `oklch(0.915 0.005 100)` | `#e3e3df` |
| `fontColor(Dark)`        | `--foreground`  | `oklch(0.160 0.010 100)` | `#0e0d09` |
| `warnColor(Dark)`        | `--destructive` | `oklch(0.520 0.215 28)`  | `#c70009` |

### Why primary is `--highlight` and not `--primary`

ADR-0064 rule 1: acid (`--primary`, `oklch(0.860 0.215 128)` → `#a8ea27`) is a
FILL. It fails contrast against text sizes on the concrete ground, so green
TEXT is `--highlight`, which is dark enough to read.

The login app gives us no way to honor that split. Upstream derives one ramp
from `primaryColor` and spends it on both roles: `bg-primary-light-500` fills
buttons (with an auto-contrast foreground), and `text-primary-light-500` sets
small links — "Resend code", the copy-to-clipboard control, the password-reset
link. Acid there is unreadable. `--highlight` is readable in both roles: it
carries text on the concrete ground, and upstream picks white on it as a fill.

Light and Dark variants carry the same values: the platform ships ONE light
brand (`themeMode: THEME_MODE_LIGHT`), the dark fields are set only so no
surface can ever fall back to Zitadel's stock blue.

If the brand tokens change, update the hex values here (re-derive with any CSS
Color 4 converter, e.g. `culori`'s `formatHex(oklch(...))`) — the login page
follows on the next chart upgrade.

## Marks

`logo.svg` and `icon.svg` are the canonical brand marks, geometry identical to
`@zeroroot-ai/brand` → `marks/crt.svg` and the dashboard's
`components/layout/logo.tsx`:

- `logo.svg` — the CRT mark: bezel + slashed-zero on screen + stand.
- `icon.svg` — the compact slashed-zero.

The brand package paints both in `currentColor`, which only resolves when the
SVG is inlined. Zitadel serves an uploaded asset as a separate document, so
here `currentColor` is resolved to `--foreground` (`#0e0d09`) — ink on the
concrete ground, the same value `fontColor` carries.

One file per mark, not a light/dark pair: there is one brand and one ground.
The Job uploads each file into both the light and the dark slot so no slot can
fall back to Zitadel's stock mark.

### Re-upload semantics

The Job compares the bytes it holds against the bytes Zitadel is serving, and
re-uploads only when they differ. Editing a mark therefore ships it: the next
Sync replaces the asset. There is no manual delete step.
