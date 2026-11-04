# QA visual con contenido protegido excluido

`astro_translate.safe_vlm` prepara dos imágenes diagnósticas para el juez: texto editorial nativo del original y del candidato, en su posición real, con cifras, grados, glifos y abreviaturas protegidas ocultos. El juez puede señalar solapes, cortes y posible inglés residual en el texto visible. **Su respuesta no acredita una página completa ni sustituye la revisión humana del piloto.**

La autorización actual permite el piloto de veinte páginas, con revisión visual posterior; no permite producción. Las llamadas pasan por `astro_translate.llm.run_task("qa_judge", ...)`, con el backend Codex aprobado y registro en `work/costs.jsonl`. La preparación de imágenes no llama a ningún modelo.

## Qué se envía y qué se conserva localmente

Se copia una página del PDF en memoria y se eliminan de esa copia todas las imágenes raster y los dibujos vectoriales, incluidos fondos de página completa. Se excluyen anotaciones, enlaces y widgets. El original y el candidato permanecen intactos. La copia se verifica: no debe conservar imágenes, dibujos ni operaciones de pintura desconocidas.

El texto se renderiza a 110 dpi y se normaliza a negro sobre blanco, conservando forma, posición y opacidad de los glifos. Esto permite leer también texto originalmente blanco sobre fondos oscuros que se han retirado. Por esa normalización, el juez **no evalúa color, contraste original, relaciones con ilustraciones ni solapes entre texto y arte**.

Las posiciones protegidas se cubren con gris opaco en ambas imágenes, calculadas por separado. Se protegen los tokens configurados, categorías numéricas Unicode, grados y símbolos, siglas astrológicas, glifos privados y spans completos de fuentes simbólicas. Las fuentes Type3 o caracteres no clasificables reciben tratamiento conservador.

Los nombres de algunas fuentes editoriales generadas empiezan por `AstroSerif`. Para evitar confundirlas con fuentes astrológicas, solo se consideran fuentes editoriales confiables cuando el SHA-256 de sus bytes incrustados coincide con uno de los archivos de fuente entregados por el compositor. Los alias de nombre se leen de esos mismos bytes verificados. Un nombre parecido por sí solo no basta; las fuentes explícitamente simbólicas y las fuentes protegidas indicadas por el llamador siguen excluidas.

## Cotas reales de tinta y comprobación geométrica

Las máscaras no dependen únicamente del rectángulo nominal devuelto por la extracción de texto. Un dispositivo de MuPDF recoge el identificador real de cada glifo, el objeto de fuente que usa el renderizador, la matriz de texto completa y la transformación de página/contenedor. `fz_bound_glyph` calcula la cota de tinta con esas transformaciones. Se añade un margen de cuatro píxeles; nunca se admiten menos de dos.

Así se cubren también salientes de cursiva, inclinaciones y rotaciones. Si un glifo no tiene correspondencia verificable, usa trazado de contorno, pertenece a una fuente Type3 o el trazador no está disponible, se oculta la cota de tinta de la operación de texto completa. Esa alternativa reduce la cobertura y queda registrada. Si tampoco existe una cota verificable, la preparación se bloquea. **No se redactan caracteres para construir estas máscaras**, porque algunas codificaciones PDF desplazan las letras supervivientes al hacerlo.

La eliminación temporal de raster/vectores debe conservar texto, fuentes, tamaños y geometría nativa. La firma geométrica detectó en las páginas 24 y 33 una diferencia de redondeo de un float32: como máximo `0,00006103515625` puntos. Se permite esa discrepancia únicamente si los caracteres, fuentes y tamaños siguen idénticos, el cambio de coordenadas no supera `0,0001` puntos y un render independiente de toda la tinta nativa antes/después es **idéntico byte a byte** a la resolución de QA. Cualquier cambio de píxel o desplazamiento mayor bloquea la preparación. La prueba y su hash se guardan en `native_geometry_proof`.

Las pruebas sintéticas verifican ausencia total de tinta y bordes de antialiasing protegidos fuera de las máscaras, incluso con cursiva, inclinación, rotaciones de 90/270 grados y texto trazado. También comprueban imágenes de fondo completas, texto blanco, fuentes confiables por hash, alternativas conservadoras, integridad de artefactos y candidatos parciales.

## Cobertura real

La cobertura es la proporción de caracteres editoriales nativos visibles que quedan completamente fuera de las máscaras. Se descuentan también letras vecinas que una máscara puede ocultar. **No es el porcentaje de página, de contenido total ni de ilustraciones revisado.**

Ejemplos de los informes del piloto disponibles el 4 de noviembre de 2026:

| Página PDF | Original visible | Candidato visible |
|---|---:|---:|
| 7 | 94,01 % | 94,59 % |
| 15 | 99,93 % | 99,93 % |
| 32 | 97,32 % | 97,56 % |
| 33 | 97,14 % | 97,29 % |
| 121 | 96,96 % | 97,34 % |
| 250 | 78,40 % | 80,48 % |

El índice de la página 250 pierde más cobertura por la proximidad de letras y remisiones numéricas. La cobertura vigente de cada candidato está en su manifest y en `work/pilot/pNNNN-qa.json`; un cambio de PDF genera un conjunto nuevo de artefactos. El umbral mínimo de ocho caracteres visibles solo permite realizar una observación parcial: no convierte una cobertura baja en QA suficiente.

Una página exclusivamente raster, como la portada original, no aporta texto nativo visible y queda bloqueada para este juez. No se simula cobertura creando una capa de texto que oculte el estado real de la imagen.

## Uso e índices de página

```python
from astro_translate.safe_vlm import build_safe_pair, judge_safe_pair

pair = build_safe_pair(
    book,
    source_pdf,
    candidate_pdf,
    source_page=33,
    candidate_page=1,  # si el candidato contiene solo esa página
    protected_terms=protected_terms,
    protected_fonts=symbol_fonts,
    trusted_text_font_files=chosen_font_paths,
)
# Solo después de comprobar autorizaciones y estabilidad del candidato:
result = judge_safe_pair(book, pair, config_path, phase="pilot")
```

Ambos índices son **uno a partir de la primera página del archivo correspondiente**. Un candidato completo conserva, por ejemplo, `source_page=33, candidate_page=33`; uno parcial puede requerir `candidate_page=1`. El llamador debe mantener ese mapa; el módulo comprueba índices y dimensiones, pero no adivina qué página corresponde a cuál. Las pruebas comparan un candidato parcial y otro multipágina: producen imágenes idénticas para la misma página seleccionada y manifests diferentes por sus hashes de entrada.

`build_safe_pair` devuelve `SafePair(images, manifest, report)`. Los archivos quedan bajo `work/qa_safe/pNNNN/<hash>/`. El hash depende de los PDFs, índices, política, fuentes confiables, términos protegidos y parámetros de render. `verify_safe_pair` verifica la versión, rutas y hashes de las imágenes antes de llamar al juez. No se sobrescriben imágenes históricas al cambiar el candidato.

El orquestador debe comprobar también que el candidato sigue vigente. `scripts/pilot_vlm.py` compara su hash con el fit antes de preparar imágenes y de nuevo después de la respuesta: si cambió durante la llamada, conserva el resultado como histórico y no lo atribuye al PDF nuevo. Los hashes de imagen prueban integridad del artefacto, no vigencia automática de su PDF de origen.

`judge_safe_pair` valida G3 y la aprobación de backend/fase. Acepta `logging_phase` para separar una prueba técnica del consumo del piloto, sin cambiar la fase sometida a la gate. Guarda `judge.json` con JSON del modelo, cobertura, backend, tokens, caché, latencia y las limitaciones. Siempre devuelve `whole_page_qa_complete=false`.

## Controles complementarios y tratamiento de incidencias

Los siguientes controles siguen siendo locales o humanos:

- Conservación literal de cifras, grados, glifos y spans de símbolos, con sus posiciones cuando corresponda.
- Identidad de imágenes/vectores y diferencias fuera del texto editable; las regiones raster traducidas requieren comparación dentro/fuera de sus regiones autorizadas.
- Color, fuente, tamaño, peso, cursiva, escala y contraste original.
- Texto editorial rasterizado, etiquetas curvas, dibujos y relaciones entre texto e ilustraciones.
- Revisión humana de gráficos y aprobación visual del piloto.

Un resultado `ok` del juez se limita al texto visible. Un fallo, una región no inspeccionable o una respuesta ausente no se convierte en aprobación. Debe conservarse la incidencia y sus capturas.

El juez también puede confundir una continuación editorial legítima con un corte: las páginas 14–15 contienen una oración entre páginas, y la página 15 continúa una oración entre columnas. El contraste con el original y con la extracción local documentó esos casos en `work/qa_safe/diagnosis/vlm_findings_14_15_25.json`; no se cambió texto para satisfacer esa inferencia. En cambio, el mismo contraste confirmó un problema real de puntuación en la cita de la página 25 y lo remitió al compositor. Esta revisión de evidencia no aprueba G4 ni autoriza producción.

Referencias de implementación: `src/astro_translate/safe_vlm.py`, `tests/test_safe_vlm.py`, `scripts/pilot_vlm.py`. Evidencia inicial: `work/qa_safe/masking_audit.json`; evidencia geométrica: `work/qa_safe/diagnosis/p0024-geometry-proof.json`. Los manifests y los informes del piloto constituyen la evidencia específica de cada versión de página.
