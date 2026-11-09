# Fase 2A — fundamentos de reutilización

Implementación local, sin inferencias, cambios de aprobaciones ni commits. El golden toma los veinte `output_sha256` existentes al inicio del encargo; no se regeneraron sus valores esperados.

## Perfil y contratos

`books/<libro>/work/profile.toml`, versión 1, contiene:

- `source_sha256`: identidad obligatoria del PDF. Un perfil existente con hash incorrecto produce `Blocked`; nunca cae silenciosamente al comportamiento por defecto.
- `hooks.path/sha256`: módulo de excepciones dentro de `work/`, verificado antes de importarlo.
- `fonts.classification`: familias normalizadas → `serif`, `sans` o `slab`. Secrets conserva SohoPro como slab; Wisdom conserva su clasificación previa. Se comprobó que ninguna familia de Wisdom cambia de clase.
- `editorial`: ordinales nativos, unión de leyendas y excepción `quoted_foreign_term`. La clasificación se aplica después de verificar los estilos originales; no altera los spans de procedencia guardados.
- `patterns` y `pages`: políticas de composición y overrides revisados. `units_sha256` liga la secuencia de ids, fuentes de texto y spans originales. Los overrides de traducción, el alcance de la corrección del clítico y los planes/targets verticales y curvos tienen hashes propios.
- `files`: suplementos y recetas adicionales con path/hash. También se aislaron las recetas históricas de portada raster y del anexo Mercurio para eliminar ids de página literales del código común.

`profiles.py` resuelve el perfil desde el PDF original y ofrece hooks explícitos de extracción, composición, finalización, candidatos, máscaras, redacción y excepciones artísticas. `fidelity.py` y `fonts.py` llaman esos hooks. No se sustituyen funciones globales de composición. `composition_context()` queda únicamente como adaptador vacío para consumidores antiguos; los runners no asignan funciones del compositor.

La extracción genérica, los patrones y la política se mantienen separados: las decisiones específicas del piloto (centrado de captions pequeños, alcance revisado de listas, ids o particiones concretas) permanecen en el perfil. Ampliar su alcance requiere revisar geometría y actualizar el hash correspondiente. No se aplican los overrides de Wisdom a Secrets ni a PDFs desconocidos.

## Parches convertidos

| Parche original | Código genérico | Override conservado en el perfil/módulo del libro |
|---|---|---|
| `local_layout.py` | `composition_patterns.py`: unión de títulos/citas, interlínea mixta, celdas, redacción precisa, desplazamientos seguros y prueba de redondeo | Políticas de activación y familias |
| `layout28.py` | Usa los constructores y runs comunes | Partición de polaridades, título de tres líneas y puntuación trazada; hash de unidades |
| `layout141.py` | Geometría común | Unión ortográfica del clítico; hashes originales de fuente/target y hash del archivo de alcance |
| `layout173.py` | `structured_layout.partition_legend` y `partition_at_terms` | Unidad de leyenda seleccionada y glosario del libro |
| `chart_callouts.py` | `chart_callouts.py`: detección por tamaños/pesos, contigüidad y solape horizontal; candidatos compartidos | Activación de la cohorte revisada |
| `vertical_local.py` | `vertical_labels.py`: grupos, ajuste uniforme, dibujo y redacción transaccional | Plan y respuestas guardadas con hash |
| `table_repair.py` | `structured_layout.partition_field_rows` | Tamaño/bloque seleccionado, separación del encabezado y margen de ordinal |
| `quote_repair.py` | `structured_layout.restore_quote_punctuation` | Selección de la cita y cantidad de comas originales |
| `heading_repair.py` | `structured_layout.validate_nonempty_styles`, composición de títulos compartida | Traducción compuesta guardada y verificada por hash |
| `rotated_local.py` | Máscara vectorial con parámetro explícito, primitivas de curvas ya compartidas | Geometría de las dos curvas y respuestas guardadas con hash |
| Listas en línea, filas protegidas, leyendas, ordinales | `run_in_layout`, `protected_labels`, `captions`, `caption_growth`, `native_ordinals` | Alcance editorial y políticas particulares del libro |

Todas las condiciones por página de `fidelity.py` de Wisdom están en `books/astrology-wisdom-stars/work/profile_hooks.py`. Sus suplementos rotados y raster también están ligados al perfil. El test AST revisa todos los módulos de `src/astro_translate`, tanto comparaciones con páginas como ids literales de página.

## Memoria y caché

`translation_context.py` centraliza backend, modelo, esfuerzo, glosario, guía, lista protegida, prompt (incluidas las instrucciones adicionales de la petición nativa), schema, `MASK_VERSION` y `MEMORY_VERSION`. Las claves de respuesta y bloque añaden el contenido e instrucciones de la petición; las respuestas visuales incluyen hashes de imágenes. Ninguna clave semántica usa archivos `.py`, nombres de rutas de implementación o el antiguo `dependency_fingerprint`.

La revisión correctiva de una traducción usa un hash explícito del contenido original/target/runs que se está corrigiendo; no hereda hashes de código. Ese identificador local no se envía al modelo. Los logs de auditoría de rebuild pueden seguir registrando archivos de código: esa procedencia no invalida la memoria.

Migración local ejecutada por `work/phase2a/migrate_cache.py`:

- SQLite respaldado antes de migrar; filas antiguas intactas, transacciones SQLite y lock del libro.
- Frases revalidadas contra planes de fuente guardados y las reglas actuales. Prioridad determinista al contexto más reciente; 11 variantes históricas distintas se conservan en sus contextos originales y se registran como conflictos, sin sobrescribirlas.
- Cachés de bloques y respuestas copiadas solo cuando se reconstruye exactamente su clave antigua y la salida pasa de nuevo schema, glosario, marcadores, valores protegidos y calidad textual.
- 375 frases, 301 bloques y 3 respuestas añadidos. De 376 fuentes distintas antiguas, una no tiene variante válida migrable. Dos entradas históricas se rechazan por validación; una de ellas tiene otra variante válida. Ninguna se borra.
- Resultado: 927 filas de memoria, 792 bloques y 88 respuestas. Reejecución idempotente: cero nuevas copias.

También se migró el contexto vigente de Wisdom con su clave antigua exacta: **541 frases, 541 bloques y 6 respuestas** recuperados; una entrada incompatible se conserva sin reutilizar. Sus otros contextos históricos permanecen intactos. Evidencia: `books/astrology-wisdom-stars/work/phase2a/cache_migration.json`.

Evidencia de Secrets: `books/the-secrets-of-astrology/work/phase2a/cache_migration.json` y respaldo `llm_cache_before.sqlite`. No se interpretan mocks de las pruebas como llamadas de integración; la migración y el golden sí usan los artefactos reales, sin LLM.

## Evidencia VLM y golden

`qa_evidence.preserve_vlm` conserva `vlm`, `vlm_local_review` y el resto de campos `vlm_*` únicamente si coinciden los hashes de fuente y candidato. Se aplica al QA común, al rebuild y después de los suplementos, sin heredar el resultado hacia candidatos distintos.

Golden congelado: `tests/golden/secrets_pilot.json`. Verificación reproducible:

```bash
.venv/bin/python scripts/check_pilot_golden.py --rebuild
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest -q books/the-secrets-of-astrology/work/pilot
```

La primera orden ejecuta `rebuild_pilot.py --stage compose` con las veinte páginas, con la guarda de LLM existente; después compara los hashes de los archivos reales y la retención de VLM. Resultado real: **20/20 candidatos idénticos byte a byte; 20/20 evidencias VLM conservadas**. p1 conserva su estado original de composición incompleta: el golden no la declara aprobada.

Informe de ejecución: `books/the-secrets-of-astrology/work/phase2a/golden_result.json`. La comprobación rápida de los artefactos existentes también forma parte de pytest.

## Archivos principales

- Nuevos comunes: `profiles.py`, `composition_patterns.py`, `structured_layout.py`, `chart_callouts.py`, `vertical_labels.py`, `translation_context.py`, `qa_evidence.py`.
- Integración común: `fidelity.py`, `fonts.py`, `run_in_layout.py`, `llm.py`, `adapters.py`, `rotated_pilot.py`, `raster_pilot.py`; nombre del argumento ordinal aclarado en `native_ordinals.py`.
- Perfiles y módulos de libro en `books/{the-secrets-of-astrology,astrology-wisdom-stars,mercury-technical-annex}/work/`; adaptadores y runners de Secrets en `work/pilot/`.
- Tests: `test_book_profiles.py`, `test_semantic_context.py`, golden JSON; tests previos de wiring ajustados en `test_rebuild_pilot.py` y `test_caption_growth.py` conservando sus comprobaciones de salida. La fixture aislada de `test_i7_layout.py` copia ahora el perfil y sus dependencias además del PDF.
- Verificador: `scripts/check_pilot_golden.py`; migrador de claves históricas del adaptador: `scripts/migrate_legacy_memory.py`.

La migración inicial del contexto de Secrets se refinó para incluir las instrucciones adicionales del prompt. Se reemplazó únicamente ese espacio de claves provisional creado durante esta implementación, después de comprobar la presencia de todas sus fuentes en el contexto definitivo; no se eliminó ninguna fila del respaldo inicial.

## Validación final

- Suite completa: **393 passed in 274.17s (0:04:34)**.
- Suite local del libro: **34 passed in 0.65s**.
- Golden con recomposición real: **20/20 idénticos**, VLM conservado.
- `git diff --check` limpio; ningún archivo prohibido ni `costs.jsonl` modificado desde la captura inicial.
- Logs y comandos: `books/the-secrets-of-astrology/work/phase2a/verification.json`, `pytest.log`, `book-tests.log` y `golden.log`.
