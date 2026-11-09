# Fase 2H — defectos agrupados del libro completo

Libro: `the-secrets-of-astrology`; fuente intacta, 192 páginas recompuestas desde
las traducciones guardadas y la instantánea de correcciones de 2G. El informe por
código/causa, con páginas y enlaces a comparativas PNG lado a lado, está en
[`work/phase2h/defects.md`](../books/the-secrets-of-astrology/work/phase2h/defects.md).
El detalle JSON y los planes reproducibles están en el mismo directorio.

## Método

[`scripts/book_defect_report.py`](../scripts/book_defect_report.py) toma copias
estables de respuestas, `composition_corrections.json`, perfil, lotes,
validaciones, QA y fit. Recompone las 192 páginas desde `input_snapshot`, guarda
QA y comparativas, y descarta los PDFs candidatos. El snapshot incluyó los lotes
`batch-01.json` a `batch-15.json` y las correcciones de citas presentes al
iniciarse la captura, además de 168 traducciones guardadas. La auditoría final
de hashes confirmó que esos lotes, payloads y `composition_corrections.json` no
cambiaron desde la captura. No hubo llamadas LLM/VLM ni ejecución de `produce`;
no se escribió en `work/production`, manifest ni `costs.jsonl`.

Cada grupo en `defects.md` tiene conteos y páginas antes/después, código y causa,
acción, y hasta dos PNG de evidencia. Los errores históricos se reproducen
contra las respuestas y el original guardados; los falsos positivos resueltos
quedan con causa en la sección histórica. La instantánea puede actualizarse con
nuevos lotes/correcciones y volverse a procesar sin tocar esos archivos.

Si llegan lotes o correcciones de 2G después de esta instantánea, refrescar
informe, evidencia y diagnósticos antes de regenerar el plan:

```bash
.venv/bin/python scripts/book_defect_report.py --book books/the-secrets-of-astrology
```

## Conteos y causas principales

| Grupo | Páginas antes → después | Causa / estado |
|---|---:|---|
| Estado de lote `needs_fix` / G5 | 124 → 30 | La mayoría de los lotes ya no necesita revisión; 30 páginas siguen marcadas. |
| Fallo de fit en composición | 0 → 11 | Limitación geométrica real en 11 páginas; requiere nueva composición o forma breve. |
| Traducción nativa pendiente | 0 → 10 | Sin candidato guardado para completar composición: p104, p105, p112, p122, p137, p178, p181, p182, p187, p192. |
| Retraducción requerida por QA local | 0 → 8 | p23, p67, p131, p148, p153, p173, p189, p191. |
| Formas de glosario rechazadas en validación guardada | 72 → 10 | Flexión/coordinación y formas compuestas aceptadas en más casos; persisten p23, p25, p67, p170, p178, p181–183, p187 y p192. |
| Placeholders cambiados | 28 → 5 | Persisten p23, p87, p105, p182 y p191. |
| Marcadores de estilo alterados | 18 → 4 | Persisten p23 y p181–183. |
| `repeated_ngram` histórico | 8 → 0 | Se comparan las repeticiones traducidas con las del original; p104/p105 son paralelismo ya presente en la fuente. |
| Errores históricos de escritura por disco lleno | 3 → 0 | Reanudación actual sin `No space left on device`. |
| Error histórico `ImportError` de `occurrence_at` | 1 → 0 | La importación actual se resuelve. |
| Overflow inferior en cajas raster de color | 6 → 0 | La tinta que salía de la caja desaparece al comprobar el borde contra el original. |
| Espacio inicial/doble en texto display | 1 → 0 | QA previo p185 corregido; regresiones de primera línea se prueban usando unidades guardadas de p41/p45/p53. |
| Mayúscula sin tilde con evidencia en libro/glosario | 0 → 4 | p37, p41, p71 y p75: falta corrección editorial en la traducción. |
| Abreviatura huérfana | 0 → 1 | p45, `LUZ FRENTE A. OSCURIDAD`; requiere retraducción de la unidad completa. |
| Puntuación de apertura `¡¿` | 3 → 3 | Persisten p67, p109 y p168; el control de 2G ya está activo. |
| Ambigüedad de límite de campo | 0 → 1 | p80: `Ambiguous saved field row boundary`; el plan incluye la unidad para retraducción agrupada. |

La sección de validación histórica incluye 56 registros limpios, agrupados en 16
casos de 16 unidades. Entre ellos están p112, donde el matcher acepta
`circuit through the zodiac` → `recorrido por el zodíaco`, y p122,
`Uranian` → `uraniano` en singular. Esto elimina esas discrepancias de
validación; p112 y p122 siguen en `native_translation_pending`, así que su
composición final aún requiere el payload faltante. El `defects.md` completo
también detalla énfasis, inline style, límites de caracteres, errores de
recomposición y cada página que permanece en revisión.

## Cambios realizados

- `src/astro_translate/text_quality.py`: QA para abreviaturas huérfanas, espacios
  iniciales/dobles en display, tildes en mayúsculas, overflow inferior de panel
  raster y repeticiones comparadas con el original; conserva los controles de
  márgenes y `¡¿` de 2G.
- `src/astro_translate/panel_frames.py`: comprueba localmente el borde inferior
  de una caja rasterizada antes de atribuir overflow.
- `src/astro_translate/glossary_forms.py`, `src/astro_translate/llm.py` y
  `src/astro_translate/fidelity.py`: reconoce flexiones autorizadas y términos
  de glosario relacionados en frases coordinadas/compuestas.
- `src/astro_translate/language.py`: no clasifica etiquetas formadas solo por
  palabras funcionales españolas como residuo inglés.
- `scripts/book_defect_report.py`: snapshot estable, recomposición offline,
  agrupación por causa, comparación antes/después y enlaces a evidencia.
- `scripts/prepare_phase2e_retranslations.py`: `--include-ids` agrega una unidad
  necesaria a la cola agrupada sin reemplazar las selecciones automáticas.
- `scripts/check_phase2g_golden.py`: permite guardar la comprobación en el
  directorio privado de 2H.
- Tests: `tests/test_book_defects.py`, `tests/test_language.py`,
  `tests/test_phase2e_prepare.py` y `tests/test_phase2g.py`.

## Planes y comandos

La cola agrupada se preparó sin ejecutar inferencia: **163 unidades** en total
(7 retraducciones, 153 traducciones faltantes y 3 unidades de cita). Incluye las
10 páginas con traducción nativa pendiente, las unidades pedidas por QA y
`p0080_u0006`. Para regenerar el plan:

```bash
.venv/bin/python scripts/prepare_phase2e_retranslations.py --book books/the-secrets-of-astrology --pages 1-192 --include-ids p0080_u0006 --diagnostics books/the-secrets-of-astrology/work/phase2h/input_snapshot/book/work/phase2h/recomposition --plan books/the-secrets-of-astrology/work/phase2h/retranslation-plan.json
```

Comando exacto de ejecución de la retraducción agrupada, **no ejecutado** en
2H; activa LLM y el usuario lo ejecutará cuando corresponda:

```bash
.venv/bin/python scripts/prepare_phase2e_retranslations.py --book books/the-secrets-of-astrology --pages 1-192 --include-ids p0080_u0006 --diagnostics books/the-secrets-of-astrology/work/phase2h/input_snapshot/book/work/phase2h/recomposition --plan books/the-secrets-of-astrology/work/phase2h/retranslation-plan.json --execute --output books/the-secrets-of-astrology/work/production/composition_corrections.json
```

Se prepararon **6 solicitudes de forma corta**, sin propuestas LLM ni
aprobaciones. Una está bloqueada geométricamente (p139, `D C`). Para volver a
crear el archivo de solicitudes:

```bash
.venv/bin/python scripts/short_forms.py prepare --book books/the-secrets-of-astrology --pages 1-192 --diagnostics books/the-secrets-of-astrology/work/phase2h/input_snapshot/book/work/phase2h/recomposition --plan books/the-secrets-of-astrology/work/phase2h/short-form-plan.json
```

El chequeo de `produce` para el libro completo se mantiene en dry-run:

```bash
.venv/bin/python -m astro_translate produce --book books/the-secrets-of-astrology --pages 1-192 --resume --recompose --dry-run
```

Este comando no se ejecutó durante 2H; la recomposición offline anterior cubrió
las 192 páginas. No se hicieron commits.

## Verificación

La recomposición guardó 192 páginas y cero llamadas LLM/VLM. La regresión
golden terminó con `regression_ok=true`: seis PDFs byte a byte idénticos y 14
páginas con cambios ya declarados; las comprobaciones de fase 2G pasaron. El
runner quedó aislado, sin red ni escrituras al manifest activo.

- Suite completa: **607 pasadas en 546,01 s**, sin omitidos (`pytest -q`).
- Suite local de defectos, QA de texto, fase 2G y lenguaje: **89 pasadas en
  23,06 s**.
- `tests/test_book_defects.py`: **12 pasadas**.
- `git diff --check`: limpio.
- Sin inferencia real, `produce`, escrituras de producción ni commits.

Salidas: `work/phase2h/recompose-final.log`, `work/phase2h/defects.json`,
`work/phase2h/retranslation-plan.json`, `work/phase2h/short-form-plan.json`,
`work/phase2h/golden-final/result.json` y
`work/phase2h/golden-final/isolation.json`.
