# Original React Bits DotGrid

`DotGrid.jsx` and `DotGrid.css` are the original component supplied in the latest user attachment. The isolated React layer is mounted behind the existing Hero without changing its text, cards or layout.

The example settings are retained: dotSize 5, gap 18, proximity 140, strength 1, bounce 0.6, tension 0.4, returnDuration 0.8, shockRadius 320 and shockStrength 5. Colors are adapted to the site: #34404D at rest and #B8C5D6 when active. The component renders with WebGL 2 directly; there is no simplified Canvas imitation. SideRays has been removed.

Mobile disables mouse and click interaction. Reduced motion remounts a static, paused scene without intro. The component handles resizing, device pixel ratio, settling and visibility.

Rebuild from catalog:

```powershell
npm ci --no-audit --no-fund
npm run build:effects
```

The generated web/hero-grid.js and web/hero-grid.css are included, so the Python launcher does not need a Node build at startup.

## Hero cards: BounceCards

The original BounceCards source from the user attachment is mounted as a second React island inside `#hero-art`. Optional `renderCard` and `cardHeight` props retain the existing rectangular gift cards and their captions; the original spring integrator, hover separation, click bounce and staggered entrance are reused. Resting z-order keeps the centre card in front. The catalogue sends public collection images through `hero-cards-ready`; it does not replace the React-owned DOM.

Desktop hover straightens the active card, scales it to 1.06, and moves neighbours apart by 55 px. Profiles retain the previous desktop/mobile card dimensions and resting positions. Reduced motion disables hover movement and renders cards at rest; touch devices retain scrolling without hover spread.
