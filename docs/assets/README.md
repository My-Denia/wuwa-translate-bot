# Repository assets

Images used by the project READMEs and the GitHub repository page. Everything
here is either original artwork made for this repository or a screenshot of
WuwaTerm's own interface. None of it contains Wuthering Waves characters, logos
or other official game art.

| File | What it is | Source |
| --- | --- | --- |
| `logo.svg` | The WuwaTerm mark: a W framed by Chinese term brackets 「 」 | Original, hand-written SVG |
| `readme/hero.png` | README banner | [`source/hero.html`](source/hero.html) |
| `readme/how-it-works-en.png`, `readme/how-it-works-zh.png` | The how-it-works diagram in each README language | [`source/how-it-works.html`](source/how-it-works.html) |
| `readme/screenshot-workbench.png` | Term lookup and sentence translation on the public beta | Screenshot, see below |
| `readme/screenshot-review.png` | The review workbench on the public beta, with parts of the card cropped out | Screenshot, see below |
| `social-preview.png` | 1280×640 image for Settings › Social preview | [`source/social-preview.html`](source/social-preview.html) |

## Rendered artwork

The HTML sources are rendered in Chromium with a transparent background (the
social preview is opaque), using the viewport and scale noted at the top of
each file, and then compressed as PNG. They use the public beta's own colours
and the Inter, Noto Sans SC and JetBrains Mono fonts. The diagram takes
`?lang=en` or `?lang=zh-CN`. The term pairs in the banner are real dictionary
results.

When the product changes what the diagram describes, change the source and
render both languages again rather than editing the PNG.

## Screenshots

Captured from <https://wuwaterm.denia-official.chatgpt.site> on 2026-09-13 in a
fresh browser session with no cookies, at 1280 px wide and 2× scale. The inputs
were `声骸` for lookup, `今汐装备了声骸` for translation, and the pair
`今汐装备了声骸。` / `Jinhsi equipped a Sound Bone.` for review. The results
are what the live service returned; nothing in the interface was edited. The
review image joins three regions of one card, marked by dashed lines. Request
IDs stay inside their collapsed panel, and no token, host, path or log appears.

Retake screenshots when the public interface changes enough that they would
mislead a reader.
