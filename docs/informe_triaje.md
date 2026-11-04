# Informe de triaje — Astrology: Using the Wisdom of the Stars in Your Everyday Life

## Resultado G2

Triaje local del archivo `books/astrology-wisdom-stars/source.pdf`; el original permanece intacto. El SHA-256 del PDF (`d13ab81406a66d3ab9863a9c2350fb326e8356d914f2f303ba3e6d4cbdc88046`) coincide con `work/intake.json` y con `source_sha256` del manifest. El triaje clasifica las 258 páginas (numeración externa 1–258).

| Tipo heurístico | Páginas | Ruta asignada |
|---|---:|---|
| `mixed` | 228 | A (etapas A y B pendientes) |
| `native` | 1 | A |
| `scanned` | 29 | C |
| `vector_figure` | 0 | — |

La proporción escaneada es 29/258 = **11,24 %**, inferior al umbral de alerta de 30 %; no activa replanteamiento por volumen de escaneos. Ruta A es una asignación heurística de triaje y no una aprobación de que la ruta sea ejecutable: las etapas permanecen pendientes y las restricciones de protección de contenido descritas en `docs/decisiones.md` siguen vigentes.

## Imágenes, fuentes simbólicas y anomalías

El manifest registra 257 colocaciones de imagen. En las 257, `has_text` es `null`: la presencia o ausencia de texto dentro de cada imagen es **desconocida**, no negativa. La categoría `scanned` se infiere localmente por la capa de texto extraíble y la cobertura de imagen configuradas (`scanned_char_threshold=80`, `scanned_image_area_ratio=0.8`); no equivale a OCR ni a validación editorial. Las páginas restantes también pueden contener etiquetas en imágenes que requieren inspección/OCR local cuando corresponda.

Se detectaron nombres de fuentes que el clasificador señala como simbólicas en 100 páginas (con posible solapamiento entre fuentes):

- `SYEZBW+PRH17Astrology-Regular`: 69 páginas.
- `SYEZBW+AstrologyPiLTStd-1`: 44 páginas.
- `ZWBPVI+PRH17Astrology-Regular`: 5 páginas.

Estos conteos son por páginas con la fuente, no por glifos. Las fuentes son una señal de protección para las fases posteriores; el triaje no inspecciona semánticamente cada carácter ni declara que otros textos estén libres de glifos protegidos. El manifest no reporta anomalías (`warnings: []`).

## Validación, costos y límites

`books/astrology-wisdom-stars/work/manifest.json` validó sin errores contra `schemas/manifest.json` (JSON Schema Draft 2020-12). El hash del original coincide con intake. La clasificación y las rutas son heurísticas estructurales locales de PyMuPDF; no son revisión editorial ni QA página a página. No se llamó a modelos ni APIs: `work/costs.jsonl` registra operación local de triaje, 0 tokens y costo de API de 0 USD.

G2 queda informado. G3 sigue bloqueado hasta revisión y aprobación humana del glosario CSV y la guía de estilo; no iniciar traducción.
