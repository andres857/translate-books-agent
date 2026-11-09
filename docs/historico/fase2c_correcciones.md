# Fase 2C — correcciones generales del lote 1

Implementación offline con páginas reales de Secrets. No se ejecutaron traducciones, retraducciones ni VLM reales; no se modificaron glosarios, configuración, prompts, schemas, originales ni aprobaciones. No se hicieron commits. Los cambios previos del repositorio se conservan.

## Resultado para revisar

Se recompusieron las traducciones disponibles de las páginas PDF 1–20 en `books/the-secrets-of-astrology/work/phase2c/`. Hay **20 comparativas PNG** en `evidence/`, una por página; las páginas sin candidato conservan el original en ambos lados y llevan su estado en la cabecera.

- **13 páginas con candidato y QA local satisfactorio**: 3, 5–7 y 12–20.
- **p4:** los nombres y contactos quedan exceptuados de forma estrecha; «Copyright» permanece como inglés residual. La unidad está preparada para retraducción. No se amplía `proper_name` para aprobarla.
- **p9:** candidato diagnóstico con atribución original preservada; la cita aún contiene la traducción histórica defectuosa. QA la marca `fragmented_display_quote`, no aprobada. Su retraducción completa está preparada.
- **p11:** texto nativo de capítulo clasificado previamente como escaneo. No hay traducción guardada. Se prepararon sus dos unidades; se conserva el original para esta entrega.
- **p1, p2, p8 y p10:** sin texto nativo; `deferred_raster`, con inventario. No son errores de composición ni QA aprobado.
- **p3:** escala mínima 0,78; requiere revisión visual por estar por debajo de 0,80. La fuente de título sustituida es una sans pesada con cobertura real; su dibujo no reproduce exactamente las formas redondeadas de Lilita One.

Las máscaras están verificadas y listas para los **15 candidatos nativos disponibles**. También se comprobó la máscara del original de p11; sin candidato traducido no se declara un juicio de esa página. Cobertura por página en `report.json` y los manifiestos `qa_safe`. No se hereda VLM histórico a PDFs nuevos.

## Causas y correcciones

| Caso | Causa comprobada | Corrección |
|---|---|---|
| C1 | Ajuste independiente de cada caja; un subtítulo de tamaño distinto podía terminar dentro de una unidad de cuerpo. La extracción de p16 había convertido líneas de prosa con cifras en unidades pequeñas. | Un único `Story` de MuPDF por cadena de columnas; párrafos y subtítulos explícitos, runs conservados, escala conjunta mediante búsqueda binaria. Las unidades existentes se reutilizan sin inferencia. |
| C2 | p9 se tradujo en tres fragmentos. El corte separó la expresión inglesa entre dos unidades y el modelo repitió texto al resolver cada fragmento por separado. No hay spans originales duplicados. El protocolo anterior obligaba a mantener el orden de estilos. | Petición única para toda la cita. Marcadores de apertura/cierre por segmento de énfasis: las parejas pueden cambiar de orden; no pueden cruzarse, anidarse, vaciarse o duplicarse. Validación de repeticiones y procedencia. Ejecución pendiente. |
| C3 | El tratamiento D1 solo reconocía tablas de fechas. Algunos folios pertenecen al mismo span PDF que el título. Además, producción no aplicaba la prueba de redondeo geométrico del piloto. | Detección por alineación repetida de folios, columna de títulos con escala uniforme y mínimo 4 pt de separación. Redacción por caracteres conserva los folios incrustados. QA verifica caracteres, posición, fuente, color y tinta a 220 dpi. El redondeo solo se acepta con tinta idéntica. |
| C4 | La nota `protected raster masks unverified` era obsoleta: los QA de producción sí contenían resultados VLM reales. Faltaba unificar la protección de sufijos ordinales aislados. En p3, la eliminación PDF dejaba operadores de sombreado. | Nota coherente con el estado real; máscaras de ordinales completas, conteo de cobertura protegida y alternativa mediante dispositivo MuPDF que solo pinta texto. No se envían raster, vectores, sombreado ni valores protegidos. |
| C5 | Título publicado traducido en p9; nombres propios y contactos de créditos considerados inglés; comillas históricas inconsistentes. | Conservación nativa de la atribución con título publicado; excepciones estrechas y trazables en `language_exceptions`; normalización determinista de comillas entre runs y control en QA. Reglas registradas en `docs/decisiones.md`. |
| C6 | p1 intentaba validar un candidato piloto que no se usa. p3 mezclaba título/subtítulo en orden inverso; sus fuentes subset anunciaban caracteres sin contornos. p17 fallaba por cajas aisladas y por una silueta pálida situada detrás del texto original. | Diferir páginas sin texto antes de exigir candidato. Separación de líneas de portada, comprobación de contornos y colocación en la línea base original. p17 usa la cadena de columnas y prueba local de fondo pálido con tinta original en ambos tonos; las ilustraciones oscuras siguen protegidas. |

## Diseño del flujo de columnas

`column_flow.py` reconoce cuerpo de ancho normal, familia y tamaño compatibles, extensión vertical suficiente y columnas dentro del mismo marco. No exige subtítulos para reconocer una cadena de varias columnas; también admite un marco único con subtítulos. Ordena de izquierda a derecha y de arriba abajo dentro de cada columna. Los encabezados de página, títulos grandes, leyendas, listas en línea y tablas protegidas conservan sus compositores.

Los marcos son las cajas de las columnas originales, sin crecimiento libre sobre ilustraciones. La secuencia conserva párrafos, sangría de primera línea, runs de negrita/itálica/color y espacios anteriores/posteriores de subtítulos. Los cambios de tamaño en línea propia se convierten en párrafos de encabezado, incluso si estaban al final de un bloque de cuerpo. Las continuaciones de columna se unen sin cambiar palabras; p13 mantiene el primer párrafo sin sangría. La composición conserva `story_members`, `composed_ids` y `story_placements` para auditar cada unidad y su procedencia.

Se prueba escala 1; si no cabe, se verifica 0,75 y se busca la mayor escala válida con doce iteraciones binarias. El mismo documento `Story` continúa en el siguiente marco. Solo se publica si todo el texto cabe y pasa las guardas de tinta vectorial, raster y contenido nativo protegido. El QA comprueba la tipografía realmente extraída del PDF y diferencia cero fuera de los spans originales y los marcos editados; no excluye todo el medianil del diff.

MuPDF ignora las reglas CSS de mantener un subtítulo junto al párrafo siguiente. La inspección de p19 detectó el problema y produjo una regresión roja adicional. Ahora se mide cada subtítulo completo con la misma fuente, escala y ancho; antes de confirmar cada marco se comprueba su altura y el espacio para una línea de cuerpo. Si quedaría cortado o huérfano, se reduce el área de colocación hasta antes del subtítulo y se repite `Story.place`; solo `draw` consume el contenido. El subtítulo fluye íntegro al siguiente marco. `story_placements` registra el área usada y los subtítulos desplazados; QA verifica además que cada título completo se extraiga dentro de una sola columna.

Escalas de los stories: p12 0,8874; p13 0,9497; p14 0,8874; p15 0,8723; p16 0,8978; p17 0,8524; p18 0,8839; p19 0,8674; p20 0,9257.

## Golden y compatibilidad

El golden congelado no se modifica. La recomposición real de las veinte páginas conserva **18 candidatos idénticos byte a byte**, sin invalidar la evidencia previa. Cambian únicamente:

- **p19:** tres columnas de cuerpo y dos subtítulos pasan a una cadena; escala uniforme 0,8674. QA local satisfactorio.
- **p87:** dos columnas de cuerpo pasan a una cadena; escala uniforme 0,9412. QA local satisfactorio. El verificador de flexión del libro conserva su alcance de hashes, consultando las unidades originales guardadas en `story_members`.

Los candidatos nuevos y sus QA quedan en `work/phase2c/golden/p0019/` y `p0087/`. Los artefactos previos del piloto, manifest y cola se restauran al terminar el replay. Los VLM de esos dos candidatos nuevos se invalidan correctamente. No se reutiliza una aprobación del candidato anterior.

`scripts/check_phase2c_golden.py` ejecuta el runner real con su guarda de inferencia, compara los archivos, acepta solo las diferencias declaradas mediante `--expected-changes` y comprueba QA local de esos cambios. Si el replay falla, no interpreta un informe histórico como ejecución satisfactoria.

## Archivos

- Nuevos: `src/astro_translate/column_flow.py`, `editorial_rules.py`, `style_spans.py`, `composition_corrections.py`.
- Composición/QA: `fidelity.py`, `contents_layout.py`, `composition_patterns.py`, `profiles.py`, `fonts.py`, `fidelity_audit.py`, `raster_obstacles.py`, `text_quality.py`, `safe_vlm.py`.
- Producción y peticiones: `production.py`, `__main__.py`, `adapters.py`, `llm.py`, `translation_context.py`. Las instrucciones de parejas de énfasis invalidan solo su caché; los prompts del repositorio permanecen intactos.
- Libro: `work/pilot/qa87_morphology.py`, `docs/decisiones.md` y este informe.
- Scripts: `recompose_production.py`, `prepare_phase2c_retranslations.py`, `check_phase2c_golden.py`.
- Regresiones: `tests/test_phase2c.py` y prueba de recomposición sin retraducir en `tests/test_production.py`.

No hay números de página específicos añadidos al código común de estas correcciones. Las páginas explícitas se limitan a tests, comandos, informes y el verificador local ya existente de p87.

## Comandos reproducibles

Desde la raíz del repositorio. Las primeras cinco órdenes son offline:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest -q books/the-secrets-of-astrology/work/pilot
.venv/bin/python scripts/check_phase2c_golden.py --expected-changes 19,87
.venv/bin/python scripts/prepare_phase2c_retranslations.py --book books/the-secrets-of-astrology --pages 1-20 --plan books/the-secrets-of-astrology/work/phase2c/retranslation-plan.json
.venv/bin/python scripts/recompose_production.py --book books/the-secrets-of-astrology --pages 1-20 --output books/the-secrets-of-astrology/work/phase2c
```

**Pendientes, no ejecutadas:** la siguiente orden hace una petición agrupada mediante `llm.run_task` para cuatro unidades: el residual de p4, la cita completa de p9 y las dos unidades nativas de p11. Revalida el plan contra fuente y dependencias, respeta gates y conserva marcadores/procedencia/costos. Guarda la corrección de cita separada; en p4 sustituye solo la unidad solicitada y en p11 guarda la traducción faltante:

```bash
.venv/bin/python scripts/prepare_phase2c_retranslations.py --book books/the-secrets-of-astrology --pages 1-20 --plan books/the-secrets-of-astrology/work/phase2c/retranslation-plan.json --execute --output books/the-secrets-of-astrology/work/production/composition_corrections.json
```

Después, para recomponer el intervalo del lote 1 con las traducciones guardadas y ejecutar QA/VLM de producción:

```bash
.venv/bin/python -m astro_translate produce --book books/the-secrets-of-astrology --batch-size 20 --pages 1-20 --resume --recompose
```

`--recompose` exige páginas explícitas y traducciones guardadas ligadas al original. Evita volver a traducir por cambios de código y evita reutilizar el candidato piloto antiguo de las páginas seleccionadas. Revalida todos los spans al insertar. El modo ordinario `--resume` mantiene su política de reutilización. El historial de lotes se conserva: la recomposición registra una nueva ejecución del intervalo, sin borrar `batch-01.json`.

## Validación

Última ejecución completa: **442 pruebas pasadas en 348,85 s**; suite local del libro: **34 pasadas en 0,82 s**. La regresión golden real pasa con 18 páginas idénticas y únicamente p19/p87 modificadas; se restauraron los 676 archivos originales del snapshot del piloto. Los 52 archivos protegidos y registros de costos auditados mantienen sus hashes; no aparecen archivos protegidos nuevos. `git diff --check` pasa.

Los 15 candidatos cumplen los controles mecánicos de números, glifos, spans inmutables, imágenes, vectores, texto completo, escala uniforme, colores y fuentes incrustadas. Todos tienen diferencia cero fuera de las áreas de texto editadas y máscaras verificadas para el juez nativo. QA local completo: 13 páginas; las incidencias lingüísticas de p4/p9 y la traducción faltante de p11 siguen explícitas. No se ejecutó ningún juez real.

Abrir `books/the-secrets-of-astrology/work/phase2c/evidence/index.html` para recorrer las veinte comparativas y las dos mejoras del golden. La evidencia estructurada y los hashes están en `verification.json`; los logs finales son `pytest.log`, `book-tests.log`, `golden.log` y `golden/rebuild.log`.

Resultados y hashes finales en `work/phase2c/verification.json`. Logs rojos TDD: `red.log`, `red_underlay.log`, `red_font.log`, `red_display.log`, `red_native.log`, `red_pair_prompt.log`, `red_plain_columns.log` y `red_heading_frames.log`. Las pruebas mecánicas con respuestas simuladas no constituyen integración LLM real. La recomposición, verificación de fuentes, render, máscaras y golden sí usan los PDFs y traducciones existentes.
