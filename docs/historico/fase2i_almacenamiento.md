# Fase 2I — almacenamiento y ensamblado

## Formato de candidato

Los candidatos de producción nuevos se reducen a la página PDF de su número original después de las capas que requieren composición completa. Se conserva el índice `output_page=0`, recursos y fuentes mediante `insert_pdf`, y la escritura aplica `garbage=4`, `deflate=True` y `clean=True`. QA visual local, diferencia fuera de texto, auditoría tipográfica y pareja segura VLM leen la página indicada por el fit; los PDFs del piloto no se migran para mantener intacto el golden histórico.

La migración de artefactos antiguos es explícita y reanudable:

```bash
.venv/bin/python scripts/migrate_single_page_candidates.py --book books/the-secrets-of-astrology
```

El proceso toma el lock de producción, compara render a 220 dpi exactamente antes de reemplazar, actualiza hashes de fit/QA/revisión/checkpoint conservando la evidencia visual y añade el mapping en `work/production/candidate_hash_migrations.jsonl`. Ejecutarlo de nuevo es un no-op para los candidatos ya compactos.

## Ensamblado

`assemble-final` inserta candidatos `done_qa_ok` y `partial_deferred` (cuando pasa QA nativo). Para páginas `deferred_raster`, pendientes o fallidas inserta la página correspondiente del original. La comparación se genera página a página como original | candidato elegido; no concatena ni conserva libros completos por candidato. El informe incluye SHA-256, procedencia, costos normalizados y tamaño real de ambos archivos.

## Almacenamiento observado

- Antes de la migración: 156 candidatos, **9.7 GiB** en total según `du -sh`.
- Prueba temporal con tres candidatos reales (p3, p92, p191): 199,728,862 → 1,561,284 bytes, **99.22%** menos. Cada render a 220 dpi fue idéntico píxel a píxel. El directorio temporal se eliminó al terminar.
- Proyección lineal para 156 candidatos con esa muestra: aproximadamente **81 MB (77.5 MiB)**, liberando unos **9.6 GiB**. La cifra final cambia con el contenido gráfico de cada página.
- Ensamblado temporal de 192 páginas con los 28 candidatos autorizables por el QA guardado actual y originales en las demás páginas: PDF traducido **76,931,246 bytes (73.37 MiB)**; comparativa **97,279,614 bytes (92.77 MiB)**; total **166.14 MiB**. Se generó en `/tmp` y no escribió `out/` activo. La cifra del final cambia conforme se aprueben más páginas.
- Proyección neta incluyendo ambos PDFs finales: frente a los 9.7 GiB actuales de candidatos, quedarían ~77.5 MiB de candidatos y ~166 MiB de productos ensamblados; reducción total aproximada **9.46 GiB**.

## Piloto y golden

El piloto se mantiene con el formato de libro completo porque sus hashes y golden aprobado están ligados a los artefactos originales. Es evidencia fija de 20 páginas y no presenta el crecimiento de 156 candidatos de producción. Los diagnósticos nuevos, incluyendo recomposición y evidencia golden alterada, guardan candidatos extraídos de una página.

## Verificación reproducible

```bash
.venv/bin/pytest -q
.venv/bin/pytest tests/test_single_page_candidates.py tests/test_production.py tests/test_native_ordinals.py tests/test_pilot_assembly.py tests/test_legacy_cost_normalization.py -q
.venv/bin/python scripts/check_pilot_golden.py
.venv/bin/python scripts/migrate_single_page_candidates.py --book books/the-secrets-of-astrology
```

La migración real es el último comando y se entrega para que lo ejecute andres después de revisar los cambios de código. Durante esta implementación solo se ejecutó `migrate_candidates` sobre tres candidatos copiados a `/tmp`; ninguna llamada LLM/VLM, producción, cambio de glosario, aprobación o commit.

## Costos y ordinales

Los `cost_usd=0` históricos de `codex_sdk` no-cache cuentan como desconocidos (`cost_status=unknown_legacy_zero`) al resumir lotes, proyección e informe final. `costs.jsonl` no se reescribe. Las reglas editoriales de ordinales de Secrets están aisladas en una política ligada al hash del original; el módulo común conserva extracción mecánica, composición y QA.
