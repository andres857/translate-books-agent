# Criterios de aceptación por página

Los aplica el agente `book-auditor` y el orquestador los usa como evidencia de G5. Una página es **OK** solo si no tiene ningún defecto **bloqueante**. Los defectos **menores** se registran, pero no impiden el OK.

Compara siempre **original | traducción**, renderizados a la misma escala. Para detalle fino (tildes, glifos), recorta a ≥200 dpi: a baja resolución hubo lecturas erróneas, como la de «zodíaco» (D2).

## Bloqueantes

| Código | Criterio | Ejemplos reales |
|---|---|---|
| `EN` | Inglés residual fuera de las excepciones registradas (`artwork_exception.json`, tokens MC/IC, siglas) | «THE RAM» en la rueda p26–31; ordinales 1ST/7TH |
| `MISSING` | Texto del original que falta en la traducción, o bloque vacío | Subtítulo perdido en p15 |
| `DUP` | Texto duplicado o repetido | «don creativo» ×2 (p23); «LA BÚSQUEDA» duplicada (p9); «de la Tetera de la Tetera» (p69) |
| `ORDER` | Orden alterado: palabras de la cita revueltas, columnas leídas fuera de su orden, subtítulo dentro de un párrafo | «TESORO MAPA» (p9); p14, p15, p26 |
| `OVERFLOW` | Texto que se sale de su caja, columna o margen, o que invade otro bloque o la ilustración | Márgenes en p40, 44, 48, 52 y 56 |
| `OVERLAP` | Solape de texto con texto, con números o con dibujos | «Raíz Si8bolo», «0°Orbe» (p179); TOC «trascendencia124» (p7) |
| `MERGE` | Unidades distintas fundidas en una: callouts, filas de ficha | «Símbolo ♊Polaridad Positiva…» (p38, 42…); callouts p43 |
| `NUM` | Número, grado o glifo alterado, desplazado o perdido | «Los signos 12» (p28) |
| `CONTRAST` | Texto ilegible sobre su fondo | «INFANTIL» sobre rojo (p37) |
| `SCALE` | Escala por debajo de 0,75, o texto ilegible por tamaño | p47 `column_story_does_not_fit` |
| `GRAMMAR` | Error gramatical u ortográfico objetivo | Falta «¡» o «¿»; «FRENTE A.» (p45); «EDUCACION» y «JUGUETON» sin tilde; «encontrarla Sagitario» (p69) |
| `GLOSSARY` | Término del glosario G3 no respetado | — |

## Menores (se registran)

| Código | Criterio |
|---|---|
| `STYLE` | Énfasis inline (cursiva o negrita) perdido o desplazado, o cambio de familia de la fuente (slab frente a serif) |
| `ALIGN` | Centrado, caja (mayúsculas) o interlineado distintos del original sin afectar la lectura (p33) |
| `SPACE` | Espacio inicial espurio, o falta el espacio antes de un glifo («Símbolo♈») |
| `SCALE_WARN` | Escala entre 0,75 y 0,80 |
| `CONSISTENCY` | La misma frase de origen tiene traducciones distintas en el libro |

## Fuera de alcance (no cuentan en G5)

Se aceptan con estado diferido y registro en `review_queue.json`:
- `deferred_raster`: páginas escaneadas y texto dentro de imágenes.
- `partial_deferred`: texto curvo o rotado (Fase 4).

## Formato de salida del auditor

Archivo: `work/audit/<lote>.json`.

```json
{"batch": "batch-01", "pages": [
  {"page": 23, "verdict": "ok|defect|deferred", "candidate_sha256": "…",
   "findings": [{"code": "DUP", "severity": "blocking|minor",
                 "bbox": [x0, y0, x1, y1], "evidence": "«don creativo» aparece dos veces",
                 "probable_cause": "composition|translation|font|source_structure",
                 "rule_proposal": "check de n-gramas repetidos entre bloques hermanos"}]}],
 "g5": {"in_scope": 18, "ok": 17, "ratio": 0.944, "pass": true}}
```

`rule_proposal` es obligatorio cuando el defecto no lo detectó la QA local. Esa propuesta alimenta el corpus de regresión.
