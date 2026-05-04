# Surface Commons — Logo Maker

Open-source replacement for **Looka** / **Canva** paid logo generation
(~₹2,000/mo). Renders PNG logos via Pillow + DejaVu — no LLM, no GPU.

Part of the [Surface Commons](https://github.com/surface-commons) toolkit.
The canonical deployment runs as a FastAPI router on
[surface.agentnation.in](https://surface.agentnation.in) under
`/commons/logo`. This repo is a **standalone mirror** of the source so
anyone can read it, fork it, or run it locally without spinning up the
full Surface stack.

## Endpoints

| Method | Path                        | Body                                       | Returns                  | SC cost   |
|-------:|-----------------------------|--------------------------------------------|--------------------------|-----------|
| `POST` | `/commons/logo/generate`    | `{brand_name, style, color_hex?}`          | `image/png`              | 3 SC      |
| `POST` | `/commons/logo/variations`  | `{brand_name, style, count}`               | `application/zip` of N PNGs | 3·N SC |
| `GET`  | `/commons/logo/health`      | —                                          | JSON capability report   | free      |

`style` ∈ `minimalist` · `geometric` · `badge` · `wordmark`.
`color_hex` is optional — when omitted the renderer picks a deterministic
accent from a 12-colour palette seeded by the brand name.

## Sample output

`sample_minimalist.png` — minimalist style, brand `KoshaPay`, color `#7aa6ff`.
`sample_variations.zip` — 4 badge variations, deterministic palette walk.

## License

MIT. See the [Surface Commons charter](https://github.com/surface-commons).

## Source of truth

This file is mirrored from
[`Surface-V2/app/api/commons/logo_maker.py`](https://github.com/ratneshs230/Surface-V2/blob/master/app/api/commons/logo_maker.py).
PRs land there, not here.
