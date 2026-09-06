# Zitadel login branding — declared brand source (deploy#867)

These files are the single declared source for the hosted-login branding.
The `zitadel-login-branding` hook Job (templates/jobs/zitadel-login-branding-job.yaml)
mounts them via ConfigMap, PUTs `label-policy.json` to Zitadel's instance label
policy, uploads the SVG marks as logo/icon assets, and activates the policy.

## Token → hex mapping

The palette is derived from the dashboard's canonical brand tokens
(`zeroroot-ai/dashboard` → `app/globals.css`, the single violet-led dark brand
from dashboard PRD #649). Zitadel's label policy only accepts sRGB hex, so each
oklch token is converted with the CSS Color 4 reference math (oklch → oklab →
linear sRGB → gamma-encoded, clamped to gamut):

| policy field             | dashboard token | oklch                  | hex       |
|--------------------------|-----------------|------------------------|-----------|
| `primaryColor(Dark)`     | `--primary`     | `oklch(0.58 0.225 295)`| `#894fee` |
| `backgroundColor(Dark)`  | `--background`  | `oklch(0.17 0.012 280)`| `#0e0f15` |
| `fontColor(Dark)`        | `--foreground`  | `oklch(0.96 0.008 280)`| `#f0f1f7` |
| `warnColor(Dark)`        | `--destructive` | `oklch(0.54 0.210 22)` | `#cc1331` |

Light and Dark variants carry the same values: the platform ships ONE dark
brand (`themeMode: THEME_MODE_DARK`), the light fields are set only so no
surface can ever fall back to Zitadel's stock blue.

If the dashboard tokens change, update the hex values here (re-derive with any
CSS Color 4 converter, e.g. `culori`'s `formatHex(oklch(...))`) — the login
page follows on the next chart upgrade. The drift guard (deploy#868) asserts
the rendered ConfigMap matches these files.

## Marks

- `logo-dark.svg` — the canonical brand mark (`BrainCRT`, CRT bezel +
  slashed-zero + stand), ported from the dashboard's
  `components/layout/logo.tsx` with `currentColor` resolved to `--primary`
  (`#894fee`) and the power-LED animation removed (static asset).
- `icon-dark.svg` — the compact slashed-zero (`Brain`), same treatment.

Re-upload semantics: the Job only uploads assets when the active policy has no
dark logo yet (presence guard keeps steady-state runs restart-free). To force a
re-upload after changing an SVG, delete the asset first:
`DELETE /admin/v1/policies/label/logo_dark` (and `icon_dark`) with the IAM PAT,
then re-run the Job.
