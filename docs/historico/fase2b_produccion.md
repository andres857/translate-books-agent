# Fase 2B — orquestador de producción

Implementación y pruebas offline. No se ha ejecutado producción real, ninguna inferencia real ni ningún commit. Los dobles de `run_task` y de gates viven exclusivamente en `tests/test_production.py`; sus resultados no constituyen traducciones ni evidencia VLM real. No se modifican aprobaciones, glosarios, prompts, schemas, configuración ni originales.

## Entrada y configuración

```bash
# Primer lote acotado a páginas PDF 1–20 (incluye reutilización del piloto).
.venv/bin/python -m astro_translate produce --book books/the-secrets-of-astrology --batch-size 20 --pages 1-20

# Reanudar ese lote, una vez vencido resume_after_unix si hubo cuota.
.venv/bin/python -m astro_translate produce --book books/the-secrets-of-astrology --batch-size 20 --pages 1-20 --resume

# Recorrer todas las páginas restantes; no ejecutado durante este encargo.
.venv/bin/python -m astro_translate produce --book books/the-secrets-of-astrology --batch-size 20 --resume
```

La fase es siempre `production`. La configuración predeterminada es `work/pipeline.toml` del libro, con fallback a la configuración común si no existe. `--config` permite una ruta explícita. Se verifican G3, G1b con autorización explícita de `production`, G4, hash de configuración y evidencia de la comparativa del piloto. La selección acepta rangos y comas; el tamaño de lote permitido es 1–20.

## Detección general y overrides

El perfil `composition = "patterns"` activa por defecto run-in, filas etiqueta/valor, callouts y rótulos verticales. La detección de ordinales incluye la separación de encabezados antes de traducir; la unión de leyendas usa la extracción canónica antes de construir el plan de traducción. Citas, párrafos, caption_growth y leyendas pasan por las reglas geométricas comunes. No hay listas de páginas del piloto en el código común.

La ampliación se contrastó contra las unidades de composición del golden. Solo se conservan estas desactivaciones puntuales con `policy_sha256`:

| Página | Override | Motivo |
|---|---|---|
| 28 | `run_in = false` | La partición validada de polaridades usa límites distintos del agrupador genérico. |
| 34 | `protected_rows = false` | La tabla usa la partición y respuestas de composición ya verificadas. |
| 60 | `protected_rows = false`, `split_ordinal = false` | La tabla y el encabezado ya se separan en su hook; repetir la partición cambia ids/geometría. |
| 173 | `callouts = false` | Mantiene independientes los títulos y leyendas de la carta validada. |
| 175 | `callouts = false` | Conserva la agrupación de cita/leyenda y su suplemento curvo. |
| 180 | `callouts = false` | Evita reagrupar las unidades con respuestas de composición existentes. |

También permanecen los overrides previos ligados a `units_sha256` y/o archivos con SHA: polaridades, tablas, clítico, leyenda seleccionada, puntuación de cita, respuestas de composición y planes/targets verticales/curvos. El centrado especial de captions pequeños en 86/87 sigue siendo una excepción local con `policy_sha256`. Todas las entradas por página llevan ese hash, además de los hashes de unidades/archivos que correspondan. La clasificación de familias y las decisiones editoriales son políticas del libro. Ninguna excepción impide ejecutar los detectores en las páginas restantes.

Los rótulos verticales nativos a 90° se detectan en cada página de producción, se traducen como grupo mediante `run_task`, se ajustan conjuntamente hasta 0,75 y se insertan de forma transaccional con guardas de tinta. La prosa vertical con cifras no se omite completa: pasa por la protección granular del LLM. Las fechas se resuelven localmente. Texto curvo u orientaciones no resueltas se inventarían para Fase 4.

## Flujo y estado

`production.py` coordina las etapas existentes:

1. Lee y valida el manifest; verifica hashes de checkpoints. Reutiliza estados terminados y vigentes, incluidos los que siguen en revisión, sin gastar nuevamente en ellos. Los fallos se pueden reintentar tras corregir la causa.
2. Importa los candidatos del piloto aprobados por G4 verificando SHA del candidato y del QA. Copia sus bytes a `work/production`, sin recomponerlos ni llamar al modelo. Conserva las deficiencias raster como diferidas, no como QA aprobado.
3. Extrae con `extract_fidelity_units`, incluyendo unión de captions antes de traducir. `fidelity_translate(..., phase="production")` deduplica cláusulas y usa memoria SQLite; agrupa varias páginas por llamada hasta 6000 caracteres. `llm.run_task` subdivide también unidades sobredimensionadas y valida la salida. Cada grupo completado se guarda en memoria antes de avanzar al siguiente.
4. Compone con `reinsert_fidelity` y hooks del perfil; aplica el suplemento vertical genérico. Guarda traducción, fuentes, ajuste, PDF, QA y checkpoint por página.
5. Ejecuta `fidelity_qa` y `text_quality` con la guía vigente. Ejecuta VLM mediante `build_safe_pair`/`judge_safe_pair` → `run_task("qa_judge")`. El enmascarado común protege también ordinales ingleses en mayúsculas. La cobertura es exclusivamente texto nativo visible: imágenes/vectores y valores protegidos están excluidos.
6. Escribe estado `done_qa_ok`, `review`, `failed` o `deferred_raster`; las incidencias incluyen razón y captura en `review_queue.json`. Las páginas con gráficos y las escalas <0,80 requieren revisión visual registrada. Un dictamen VLM favorable por sí solo no elimina ese requisito.

Los checkpoints `pNNNN-state.json` ligan cada etapa a los bytes de sus artefactos y a fuente, glosario/guía, configuración, aprobaciones, perfil, dependencias y código de QA/composición. Esto **no** introduce hashes de código en la memoria semántica de traducción. El lock exclusivo `.production.lock` serializa producción, revisión y ensamblado; las escrituras del manifest usan además el lock corto existente. JSON y PDFs/copias se instalan atómicamente.

Archivos por página nativa: `pNNNN-{translation,fit,fonts,qa}.json`, `pNNNN-translated.pdf`, `pNNNN-state.json` y renders. Si la composición falla, el ajuste y QA documentan el fallo; no se inventa un PDF traducido. Las escaneadas difieren directamente con QA/inventario y captura, sin traducción/composición.

## Pausas, errores y G5

- `QuotaPaused` deja pendientes las páginas inconclusas, conserva las etapas completadas, persiste `quota_pause.json`, cierra el lote como `paused` y devuelve **75**. Se respeta `resume_after_unix`; no hay bucle de reintentos del orquestador. `--resume` recupera tanto lotes pausados como lotes `running` interrumpidos abruptamente.
- Las llamadas válidas previas sobreviven en memoria/caché. La prueba de pausa durante traducción comprueba que sus ids no se vuelven a enviar; la pausa durante VLM comprueba que no se repite traducción ni composición ya guardada.
- Tres excepciones idénticas detienen el lote y la ejecución con código **2**. La llamada agrupada inicial cuenta dentro de las tres; los errores se registran en el lote.
- Cada `batch-NN.json` incluye páginas, incidencias, métricas G5, consumo y estados. G5 exige ≥90 % QA OK entre páginas no diferidas; una exclusión raster requiere bbox y motivo. Un lote sin páginas evaluables no declara G5 aprobado. El fallo ordinario de G5 produce `needs_fix` y permite continuar con el siguiente lote.
- El consumo se obtiene de `costs.jsonl`, que solo escribe `llm.py`. Sumas conocidas y cantidad de registros desconocidos están separadas para tokens, latencia y USD; las llamadas reales con costo desconocido no se presentan como gratuitas. Se muestran backends efectivos y discrepancias; registros sin backend impiden G5.

## Diferidos de Fase 4

La ruta C, páginas sin texto nativo, imágenes marcadas `has_text` y texto curvo/orientaciones no resueltas generan inventario `{bbox, reason, phase: 4}`. Puede ampliarse con `work/raster_inventory.json`:

```json
{"source_sha256": "SHA_DEL_ORIGINAL", "pages": {"N": [{"bbox": [0, 0, 100, 100], "reason": "texto raster pendiente de OCR", "phase": 4}]}}
```

No se ejecuta OCR ni se afirma cobertura del texto de imágenes sin clasificar; queda explícita esa limitación. La revisión visual de gráficos sigue siendo necesaria. Los diferidos no son fallos de ejecución ni páginas terminadas.

## Revisión y ensamblado

```bash
.venv/bin/python -m astro_translate review-visual --book books/the-secrets-of-astrology --page 36 --reviewer 'Nombre del revisor' --candidate-sha256 'SHA256_DEL_CANDIDATO' --notes 'Evidencia de revisión y hallazgos VLM resueltos'

.venv/bin/python -m astro_translate assemble-final --book books/the-secrets-of-astrology
```

`visual_reviews.json` conserva cada revisión por página y SHA del candidato, con revisor, fecha, fuente y notas. Resuelve falsos positivos del VLM y registra revisión de gráficos; no convierte fallos locales en aprobaciones. Se reaplica después de recomponer solo si el SHA sigue igual. Actualiza también G5 de los lotes cerrados afectados sin alterar su consumo registrado.

El ensamblado usa `pilot_assembly`/pypdf, preserva `/Group` y verifica igualdad de renders a 110 dpi de cada página ensamblada contra su candidato. Escribe:

- `out/<libro>_es.pdf`;
- `out/<libro>_comparativa.pdf` (original | candidato);
- `out/<libro>_provenance.json` (procedencia, SHA, índice, originales conservados y pruebas de transparencia/render).

Se usa candidato únicamente con evidencia vigente y QA local satisfactorio o revisión visual válida; en el resto se conserva el original y se registra. `complete_translation=false` identifica entregas parciales. El comando no inventa G7 ni modifica archivos de aprobación.

## Archivos de implementación y validación

- `src/astro_translate/production.py`: orquestación, checkpoints, gates, G5, revisión y ensamblado.
- `src/astro_translate/__main__.py`: tres comandos nuevos.
- `adapters.py`: ruta fidelity para production y persistencia incremental de grupos traducidos.
- `profiles.py` y `books/the-secrets-of-astrology/work/profile.toml`: detección general y excepciones verificadas.
- `vertical_labels.py`: extracción y composición vertical de producción.
- `llm.py`: límite absoluto de 6000 caracteres; se conserva el registro de consumo centralizado.
- `safe_vlm.py`: protección común de ordinales completos; nueva versión de máscara.
- `tests/test_production.py`: pruebas unitarias e integración offline con p36/p37/p102 reales y llamadas simuladas solo en tests.

La evidencia final de pytest, suite local, golden y hashes protegidos se guarda en `books/the-secrets-of-astrology/work/phase2b/verification.json` y sus logs. La prueba golden recompone realmente el piloto con las respuestas existentes; no regenera los valores esperados.

## Resultado de la validación

- `pytest -q`: **411 passed in 300.30s**.
- Suite local del libro: **34 passed in 0.80s**.
- Pruebas de producción offline: **18 passed in 21.25s**.
- Golden con recomposición real: **20/20 idénticos byte a byte, evidencia VLM conservada**.
- Auditoría: **48 archivos protegidos sin cambios ni adiciones**; `git diff --check` limpio.
- Se añadieron hashes declarativos a las restantes entradas de política por página al cerrar; se verificaron después con los tests de perfil y producción y el golden, sin cambiar ninguna política.
