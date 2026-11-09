# Fase 2E — correcciones generales del lote 2

Trabajo offline sobre `books/the-secrets-of-astrology`, sin LLM/VLM, producción real ni commits. Se conservan los cambios previos del repositorio. Original, glosarios, configuración, prompts, schemas y aprobaciones no se editan. Las traducciones utilizadas son respuestas guardadas, congeladas en `tests/fixtures/phase2e/` para las regresiones.

## Causas y correcciones

| Caso | Causa comprobada | Cambio |
|---|---|---|
| E1 | La presencia de cualquier curva convertía toda la página en `deferred_raster`. En p26–31 los operadores cambian de tangente por glifo, incluso en THE RAM y las fechas: no son palabras rectas radialmente rotadas. | `native_local_fidelity_ok` separa la comprobación del texto publicado. `partial_deferred` conserva candidato e inventario `deferred_regions/source_spans`, sin contarlo como QA OK ni fallo. Los fallos nativos siguen en revisión. Rotación recta arbitraria soportada con fuente, color, centro, ángulo y escala de grupo ≥0,75; peticiones por figura, fechas locales y protección granular. |
| E2 | La detección exigía comillas textuales y no reconocía las comillas dibujadas. Fragmentos de una misma cita se enviaban por separado; el QA solo detectaba repeticiones adyacentes. La composición no conservaba las líneas centradas ni el caso por run. | Una petición completa con parejas de énfasis; miembros excluidos del plan por frases, corrección con procedencia obligatoria antes de publicar. Repetición no adyacente de contenido contrastada con el original; caso Unicode por run; líneas centradas/escalonadas, baselines y margen de tinta acentuada. Se conserva la atribución nativa cuando el patrón autor/título está demostrado. |
| E3 | p22 escalaba también la sangría que evitaba el icono y limitaba el marco al texto original. Su referencia chocaba con la caja unión del story, aunque el espacio entre sus marcos estaba libre. En p23 el hueco entre runs esquivaba una pestaña morada del puzle. | Extensión del marco solo sobre fondo plano medido, con límites de arte y vecinos; sangría de icono fija; obstáculos por marco real. Título en una línea con runs, colocado a la derecha de la pestaña, sin cubrirla. |
| E4 | El punto final de p37 es un span separado de 14 pt junto a texto de 17 pt; la regla previa exigía tamaños iguales. No se reprodujo una alteración de color: fuente y candidato usan los mismos valores RGB. | Adjunción por línea base/font/color y proximidad, sin cambiar el carácter original; normalización del espacio antes de puntuación. QA comprueba el color real por carácter. Escala grupal 0,88 justificada por la colisión con el estallido inferior a 0,89; no se reduce un cuerpo de forma independiente. |
| E5 | Comparación literal del término aprobado; `Planetas` ya era insensible a caso, pero el singular gramatical `planeta` no pasaba el plural aprobado. | Flexión determinista conservadora de género/número y mayúsculas, común al validador y QA. Un término realmente ausente produce retroalimentación específica para el reintento existente. |
| E6 | Tras fallar la traducción de p21 no existía payload; el checkpoint fallido sin artefactos se descartaba y composición recibía `None`. | Guard explícita antes de componer y conservación del fallo de traducción en la selección del lote; no se intenta indexar un payload ausente. |
| E7 | En p38/p40 las métricas del título grande se cruzaban con el guard de la cifra; el aislamiento por rectángulos dejaba una S ajena junto al 2. No era un span mixto cifra/palabra. | Fallback por identidad de operadores nativos sobre la copia desechable. Se conserva exactamente valor/fuente/tamaño/color/origen/tinta de la cifra. La protección granular existente de spans mixtos tiene regresión explícita. La recomposición desbloqueada de p38 reveló además una tabla desplazada 20 pt: ahora se particionan sus filas desde estilos/runs guardados, con ambigüedad bloqueada; «Símbolo» permanece junto al glifo nativo y pasa QA. |
| E8 | El separador de encabezados exigía negrita o mayúsculas, ignorando SohoPro-Medium de 13 pt frente al cuerpo de 11 pt. | Diferencia de tamaño/peso en línea propia delimita párrafos también fuera de los stories, conservando runs e ids originales. |

Las curvas quedan pendientes de Fase 4. No se implementa colocación circular glifo a glifo: estimar un círculo no resuelve por sí solo segmentación de palabras, intervalos disponibles, fuentes deformadas y valores protegidos. El inventario conserva la evidencia para retomarlas; no se afirma que sea imposible. El plan de rótulos rectos de 21–40 contiene cero peticiones. Véase `docs/fase2e_rotaciones.md`.

Adicionalmente, p30 tenía una sangría falsa de 67,75 pt: el contenido PDF emitía primero el run de la derecha. Los títulos continuos de una línea usan sangría cero. En p25 se recupera la alineación derecha de una pila de títulos, permitiendo crecimiento libre a izquierda; cabe a 0,76. El texto histórico separado produce «EL SIGNOS…», por lo que se prepara su título completo y QA lo mantiene pendiente con `fragmented_display_title`, sin corregir palabras localmente.

## Evidencia y límites

Las comparativas están en `books/the-secrets-of-astrology/work/phase2e/evidence/`. Una hoja sin candidato muestra el original a ambos lados y un estado explícito; no se presenta como traducción terminada. Las citas históricas defectuosas conservan su texto hasta una retraducción real; las mejoras de geometría no las aprueban.

p37 conserva `CHILDLIKE/INFANTIL` en RGB `0x1d1b1b` y su cuerpo en `0x646363`; el contraste original ya es bajo. No se cambia la paleta. El grupo de cuerpo de 12 pt se compone uniformemente a 10,56 pt. Se probaron sus 44 cajas y 28 combinaciones adicionales por factor; 0,89 toca el estallido raster, 0,88 lo despeja. Prueba y diagnósticos en `scale37/` y `test_phase2e_scale.py`.

El detector ampliado descubre también las citas fragmentadas de p15/p17 en el fixture histórico de 2C. Se declara como **nueva incidencia de QA semántico**, manteniendo los controles de ajuste, estilo, preservación y diff. No se crea una excepción ni una aprobación. Su tratamiento requiere dos peticiones completas adicionales fuera del lote 21–40.

El test histórico local de morfología p87 se adapta al nuevo contrato E5: un id ajeno puede usar la flexión general determinista, pero no recibe ninguna resolución contextual del hook (`records=[]`). El cambio de fuente/hash revisado sigue bloqueado y las restricciones contextuales siguen probadas.

Resultado de recomposición de las veinte páginas:

- **8 con QA local completo:** 22, 34–40.
- **5 con QA nativo satisfactorio y curvas pendientes:** 27–31 (`partial_deferred`).
- **4 candidatos con retraducción pendiente:** 23, 25, 26 y 33. En p26 también quedan curvas inventariadas.
- **p21:** sin traducción guardada; solicitud preparada.
- **p24/p32:** sin texto nativo, diferidas a Fase 4.

Los **17 candidatos** tienen ajuste completo, tipografía comprobada y diferencia cero fuera del texto. La escala mínima es 0,76 en p25; p23 usa 0,77 por su cita histórica defectuosa. Ambas conservan advertencia/revisión. Esto no equivale a QA global aprobado ni a una ejecución de VLM.

Las 12 peticiones preparadas son: p21 `u0001_quote` y `u0000/u0003–u0008`; p23 `u0001_quote`; p25 `u0001_title`; p26 `u0008_quote`; p33 `u0000_quote`. No requieren retraducción los arreglos geométricos de p22/p30/p37/p38/p40. El plan histórico separado contiene únicamente las citas completas p15/p17.

## Ejecución posterior por el usuario

Preparar el plan es offline. `--execute` es la única entrada de inferencia del script, con `llm.run_task`, dependencia de G3/G1b, protección de valores, reintentos, costos y salida 75 ante cuota. Conserva correcciones de otras páginas y completa la topología canónica de páginas sin payload.

```bash
.venv/bin/python scripts/prepare_phase2e_retranslations.py --book books/the-secrets-of-astrology --pages 21-40 --plan books/the-secrets-of-astrology/work/phase2e/retranslation-plan.json
.venv/bin/python scripts/prepare_phase2e_retranslations.py --book books/the-secrets-of-astrology --pages 21-40 --plan books/the-secrets-of-astrology/work/phase2e/retranslation-plan.json --execute --output books/the-secrets-of-astrology/work/production/composition_corrections.json
.venv/bin/python -m astro_translate produce --book books/the-secrets-of-astrology --pages 21-40 --resume --recompose
```

La última orden puede llamar al juez de producción y **no se ejecutó**. Se añadió y probó `produce --dry-run`: solo inventaría páginas y traducciones existentes, sin construir el escritor, crear artefactos ni inferir; no afirma que las gates o el QA estén aprobados.

Para las incidencias adicionales p15/p17, preparar y ejecutar por separado el mismo script con `--pages 15,17` y `--plan books/the-secrets-of-astrology/work/phase2e/historical-retranslation-plan.json`; no están incluidas en la orden de recomposición del lote 2.

## Archivos

Composición/QA: `fidelity.py`, `profiles.py`, `run_in_layout.py`, `native_ordinals.py`, `punctuation_layout.py`, `structured_layout.py`, `column_flow.py`, `panel_frames.py`, `caption_growth.py`, `composition_patterns.py`, `editorial_rules.py`, `display_quotes.py`, `display_titles.py`, `text_quality.py`, `language.py`. Validación y solicitudes: `llm.py`, `glossary_forms.py`, `adapters.py`, `composition_corrections.py`. Producción: `production.py`, `native_rotated.py`, `__main__.py`.

Scripts: `prepare_phase2e_retranslations.py`, `prepare_rotated_labels.py`, `recompose_production.py`, `phase2e_report.py`, `check_phase2c_golden.py`. Este último ahora conserva el reporte incluso si un fit fallido carece de `output`, sin interpretar un QA histórico como replay exitoso.

Regresiones: `tests/test_phase2e_*.py`, fixtures `tests/fixtures/phase2e/`, adaptación declarada de `tests/test_phase2c.py` y test local p87. No se agregan reglas por número de página al código común.

## Validación

Ejecución final: **523 pruebas pasadas en 457,57 s**, suite local del libro **39 pasadas en 1,27 s**, golden del piloto satisfactorio y `git diff --check` limpio. El log final de pytest es `work/phase2e/pytest-final-verified.log`; los logs anteriores conservan fallos e iteraciones TDD y no representan el resultado final.

Los resultados finales, inventario exacto de peticiones, hashes y logs se detallan en `work/phase2e/verification.json`. Los logs `red-*.log`/`red_*.log` conservan las regresiones rojas anteriores a cada arreglo. Las pruebas con backend simulado verifican contratos y orquestación, no son integración real con un modelo.

El registro de costos tuvo una modificación concurrente ajena a estas herramientas: conserva íntegramente sus 174 registros iniciales y añade 20 de producción de páginas 41–59, terminados a las 09:21:30 UTC. Se verificó el hash del prefijo original; evidencia en `concurrent-cost-log.json`. No se borraron ni reescribieron esos registros. Los otros 52 archivos protegidos auditados conservan sus hashes.

El replay golden real conserva 18 candidatos idénticos byte a byte y únicamente los cambios p19/p87 ya declarados en 2C. Se restauraron los 676 archivos del piloto. El último helper de filas se contrastó además contra identidad sobre los 19 planes nativos completos del piloto (p1 conserva su receta raster): todos idénticos, con hashes en `golden/final_field_rows_plan_equivalence.json`.

Límite del histórico 2C: los primeros replay, antes de añadir `--output`, reutilizaron `work/phase2c/golden/` y reemplazaron sus resultados. Por ello, la comparación byte a byte certificada es contra el golden congelado del piloto, aceptando exclusivamente los cambios p19/p87 documentados en 2C; no se afirma una comparación independiente contra los PDF originales de aquella ejecución 2C. La suite hermética 2C mantiene sus regresiones geométricas y mecánicas, con las nuevas incidencias semánticas p15/p17 expresamente declaradas arriba. El runner usa ahora salida separada.

Comandos de comprobación reproducibles (offline):

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest -q books/the-secrets-of-astrology/work/pilot books/the-secrets-of-astrology/work/backend_smoke
.venv/bin/python scripts/check_phase2c_golden.py --expected-changes 19,87 --output books/the-secrets-of-astrology/work/phase2e/golden
.venv/bin/python scripts/recompose_production.py --book books/the-secrets-of-astrology --pages 21-40 --output books/the-secrets-of-astrology/work/phase2e
.venv/bin/python scripts/phase2e_report.py --book books/the-secrets-of-astrology
```

## Sustitución del protocolo por Fase 2G

Las doce peticiones anteriores usan ahora texto plano y substrings enfatizados,
con validación/reconstrucción local y escalado a Astra tras los reintentos.
El plan actualizado está en `work/phase2g/retranslation-plan.json` e incluye
además las citas históricas p15/p17: catorce unidades en total. Los comandos
vigentes, incluidas preparación, ejecución explícita y recomposición, están en
[docs/fase2g_enfasis.md](fase2g_enfasis.md). El plan histórico de esta fase se
conserva como evidencia; no debe ejecutarse sin regenerarlo con el script actual.
