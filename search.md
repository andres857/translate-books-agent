# Traducir libros de astrología en PDF con agentes de IA (Codex, Claude Code): arquitectura recomendada, herramientas, costos y riesgos

La forma más óptima no es pedirle a Codex que "traduzca página por página" razonando sobre coordenadas, sino usar Codex (o Claude Code) como **ingeniero y orquestador** que construye y ejecuta un pipeline determinista: BabelDOC/pdf2zh-next como motor base para el texto nativo del PDF, PyMuPDF para correcciones "in place" y para el control fino de glifos, un glosario astrológico obligatorio, un modelo barato para traducir, y un paso aparte de OCR + inpainting solo para las cartas y diagramas rasterizados.

## TL;DR

- **Tu intuición es correcta, pero ya está implementada en gran parte:** extraer texto con coordenadas → traducir → borrar y reinsertar en el mismo rectángulo es exactamente lo que hacen BabelDOC (motor de pdf2zh-next) y la receta oficial de PyMuPDF. No hace falta inventarlo: Codex debe envolver estas herramientas en *skills* y scripts, procesar por lotes y verificar visualmente cada página.
- **Los gráficos son la parte difícil:** el texto vectorial dentro de diagramas se puede traducir como el resto, pero el texto "quemado" en imágenes rasterizadas (ruedas zodiacales escaneadas) no lo traduce ninguna herramienta de PDF que revisé (BabelDOC, DeepL, Azure en PDF digitales). Para eso hace falta una skill propia de OCR → borrado/inpainting → re-render, y en cartas natales a menudo lo mejor es **no traducir los glifos** (♈☉☽ son universales) y traducir solo etiquetas y leyendas.
- **Modelos baratos bastan para traducir** (un libro de 300 páginas cuesta menos de 1 USD en tokens con gpt-5-mini o Claude Haiku 5.5), siempre que el glosario esté fijado. El dinero conviene gastarlo en un modelo fuerte para construir el glosario y revisar una muestra, no en la traducción masiva. LlamaIndex/LlamaParse es **opcional**: útil como parser de respaldo con *bounding boxes* para PDFs escaneados o para indexar un glosario/memoria de traducción, pero no es necesario en el flujo base.

## Supuestos de este informe

- No se sabe si los PDFs son **digitales** (con capa de texto) o **escaneados**; se cubren ambos casos y el pipeline empieza con un paso de clasificación por página.
- Par principal inglés → español, pero la arquitectura es general (con advertencias para escrituras RTL o CJK).
- Los libros contienen cartas natales/ruedas zodiacales, tablas (efemérides, aspectos), glifos astrológicos y diagramas con texto incrustado, más terminología especializada.
- Precios consultados a finales de octubre/noviembre de 2026; cambian con frecuencia.

## Hallazgos clave

1. **El patrón "coordenadas → traducir → reinsertar" es estándar.** Artifex (creadores de PyMuPDF) publica una receta oficial de traducción: extraer bloques con `get_text("blocks")`, cubrir el original con un rectángulo blanco y escribir la traducción con `insert_htmlbox` en el mismo bbox, que "selecciona fuentes adecuadas" y "reduce automáticamente el texto si no cabe". Incluso lo hace en una capa opcional (OCG), de modo que el PDF puede alternar entre original y traducción.
2. **BabelDOC es hoy el mejor motor abierto para esto.** Su artículo (arXiv 2605.10845, mayo 2026) describe una representación intermedia (IR) que separa layout y semántica, extrae terminología automáticamente para un glosario dinámico, une párrafos partidos entre columnas/páginas y usa un motor tipográfico adaptativo que reduce la escala en pasos de 0,05–0,10 hasta que el texto traducido cabe en el bbox original, citando explícitamente la expansión inglés→español. En su benchmark de 200 páginas obtuvo BIoU de 50,0 % frente a 48,7 % de PDFMathTranslate y 19,8 % de DeepL (Doc).
3. **Pero BabelDOC no hace OCR de imágenes.** Su opción `--ocr-workaround` no es OCR: según el README, "añade bloques rectangulares blancos debajo de la traducción para cubrir el texto original y fuerza todo el texto a color negro", y solo sirve para PDFs escaneados que **ya** tienen capa OCR con fondo blanco y texto negro. La marca "in-figure text translation ✓" del artículo describe, por lo que muestran sus secciones de método, texto vectorial/seleccionable dentro de figuras, no texto rasterizado (inferencia propia: el artículo no menciona ningún modelo de OCR ni de inpainting).
4. **Los servicios comerciales no resuelven los gráficos rasterizados en PDF.** Azure Document Translation "no admite la traducción de texto de imágenes en documentos digitales" (sí traduce PDFs totalmente escaneados por OCR, y su API 2026-03-01 añade traducción de imágenes sueltas y PDF vía Document Intelligence en modo batch). Google cobra 0,08 USD/página y distingue PDF nativo (hasta 300 páginas) de escaneado (20 páginas). DeepL factura un mínimo de 50.000 caracteres por PDF y obtuvo el peor BIoU en el benchmark de BabelDOC.
5. **Para texto en imágenes existen pipelines maduros fuera del mundo PDF**: manga-image-translator (detección → OCR → traducción → inpainting → re-render) y Koharu (inpainting con LaMa, FLUX.2 Klein, etc.) muestran el patrón exacto que necesitas para cartas rasterizadas.
6. **Codex y Claude Code comparten el formato de skills** (`SKILL.md` con `name` y `description`, más `scripts/` y `references/`), con carga progresiva: el agente solo lee el SKILL.md completo cuando la tarea coincide. Codex lee skills de `.agents/skills` y políticas de `AGENTS.md`; `codex exec` permite ejecución no interactiva por lotes, con salida JSONL y `--output-schema`.

## Arquitectura recomendada (paso a paso)

### Fase 0 — Triaje por página (determinista, sin LLM)
Script con PyMuPDF que, para cada página, registra en un `manifest.json`: número de caracteres extraíbles, número y área de imágenes (`page.get_images()`), fuentes usadas (`page.get_fonts()`) y si la página parece escaneada (poco texto + una imagen a página completa). Clasifica cada página en: **texto nativo**, **mixta (texto + figura rasterizada)**, **figura vectorial con texto**, **escaneada**. Esto decide qué ruta sigue cada página y evita gastar tokens o GPU donde no hace falta.

### Fase 1 — Glosario y "hoja de estilo" antes de traducir nada
1. Extraer todo el texto (PyMuPDF o pymupdf4llm a Markdown) y pedir a un **modelo fuerte** que proponga candidatos de terminología: signos, planetas, aspectos, casas, dignidades, técnicas (p. ej. *trine → trígono*, *square → cuadratura*, *sextile → sextil*, *Midheaven → Medio Cielo*, *Ascendant → Ascendente*, *ruler → regente*, *detriment → exilio/detrimento*, *Part of Fortune → Parte de la Fortuna*).
2. Validación humana de las decisiones ambiguas (p. ej. *detriment*: "exilio" vs "detrimento"; *house cusp*: "cúspide").
3. Guardar como `glossary.csv` con columnas `source,target,tgt_lng`, que es exactamente el formato que acepta BabelDOC con `--glossary-files`; BabelDOC solo inyecta en el prompt las entradas que aparecen en cada segmento.
4. Añadir una lista de **"no traducir"**: glifos Unicode (♈–♓ en U+2648–U+2653, ☉ U+2609, ☽ U+263D, ☿–♇ en U+263F–U+2647), abreviaturas de efemérides y nombres propios de autores/técnicas si el estilo lo exige.

### Fase 2 — Ruta A: páginas con texto nativo (la mayoría de los libros digitales)
- **Motor base:** BabelDOC (o pdf2zh-next, que es su interfaz oficial) con un modelo compatible con OpenAI, `--glossary-files`, `--no-dual` si solo quieres la versión monolingüe, `--watermark-output-mode no_watermark` y `max-pages-per-part = 50` para dividir libros largos automáticamente.
- **Por qué este y no un script propio desde cero:** ya resuelve orden de lectura, columnas, notas al pie, párrafos partidos entre páginas, placeholders para fórmulas (útil para grados como 15°♌23′) y el ajuste de escala por párrafo.
- **Corrección fina con PyMuPDF "in place"** para lo que BabelDOC haga mal (etiquetas sueltas, leyendas de diagramas, tablas): extraer spans con `get_text("dict")` o `"rawdict"`, redactar solo el bbox exacto de cada span traducible, aplicar redacciones sin tocar imágenes ni vectores y reinsertar con `insert_htmlbox`.

Esqueleto de la función de reinserción (lo que una skill debería empaquetar como script):

```python
import pymupdf

def replace_block(page, rect, translated_html, css, min_scale=0.75):
    # 1) Borrar solo el texto original del rectángulo, sin tocar imágenes ni dibujos
    page.add_redact_annot(rect)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)
    # 2) Reinsertar la traducción; insert_htmlbox reduce la escala si no cabe
    spare_height, scale = page.insert_htmlbox(rect, translated_html, css=css, scale_low=min_scale)
    # spare_height == -1 => no cupo ni con la escala mínima: marcar para revisión
    return {"fit": spare_height >= 0, "scale": scale}
```

Puntos técnicos que importan:
- `insert_htmlbox` devuelve `(spare_height, scale)`; `spare_height = -1` significa que no cupo. Con `scale_low` limitas la reducción (p. ej. 0,75 = máximo 25 % más pequeño) y, si falla, la skill debe probar estrategias alternativas: ampliar el rect hacia el espacio libre, abreviar con el LLM ("acorta a ≤ N caracteres sin perder términos del glosario") o marcar la página para revisión manual.
- La fuente original incrustada **no es reutilizable** para el texto nuevo (suele ser un subconjunto sin "ñ", "á" o "¿"); el mantenedor de PyMuPDF lo indica expresamente. Usa una fuente libre parecida (familia serif/sans equivalente) con cobertura latina completa más Noto Sans Symbols / Symbols 2 como respaldo para glifos.
- Al terminar: `doc.subset_fonts()` y `doc.ez_save()`; en el ejemplo de Artifex esto bajó el archivo de 35 MB a 170 KB.
- Ojo: las redacciones eliminan **todo** el texto que toque el rectángulo; usa bboxes de span, no de bloque, cerca de diagramas, o borrarás glifos y grados.
- El texto reinsertado se añade al final del flujo de contenido de la página, así que la extracción "ingenua" posterior lo leerá al final (relevante si luego quieres EPUB o accesibilidad).

### Fase 3 — Ruta B: figuras y cartas con texto rasterizado
Skill propia, inspirada en manga-image-translator:
1. **Extraer** cada imagen con su xref y su rectángulo en la página.
2. **Detectar y leer texto** con OCR que dé cajas (PaddleOCR / PP-OCR, Surya, o un VLM que devuelva JSON con bbox); filtrar por regex los glifos, grados y números, que **no** se traducen.
3. **Clasificar** cada etiqueta: glifo (no tocar), número/grado (no tocar), palabra traducible (traducir con glosario).
4. **Borrar**: si el fondo es plano (lo típico en ruedas zodiacales impresas), basta un relleno con el color de fondo muestreado; si hay textura o líneas, inpainting con LaMa (o el inpainter de manga-image-translator / Koharu).
5. **Re-renderizar** el texto traducido con Pillow en la caja original (misma rotación para etiquetas radiales; mismo color), o mejor aún **dejar la imagen limpia y poner el texto como capa vectorial PDF encima** con `insert_htmlbox(..., rotate=...)`, lo que lo mantiene buscable y nítido.
6. **Reemplazar** la imagen en el PDF (`page.replace_image(xref, ...)` o insertar la nueva imagen en el mismo rect).
7. **Verificar** con un VLM: renderizar antes/después y preguntar "¿queda texto en inglés? ¿hay texto solapado o cortado?".

Para **diagramas vectoriales** (cartas generadas por software y exportadas a PDF), el texto suele ser texto PDF real: entra en la Ruta A, pero con redacción a nivel de span y sin reducir la escala, porque las etiquetas son cortas y la expansión es mayor en cadenas cortas.

### Fase 4 — Ruta C: páginas escaneadas
Opciones, de más a menos control:
- OCR con coordenadas (MinerU, PaddleOCR PP-StructureV3, Surya/Marker, o LlamaParse con `extract_layout`/`granular_bboxes`) → **reconstruir** la página: imagen original con el texto borrado (fondo limpio) + texto traducido como capa vectorial en los bboxes. Este es tu "copiar la página y escribir encima", hecho bien.
- Crear primero una capa OCR (p. ej. OCRmyPDF/Tesseract o ABBYY/Acrobat) y luego pasar por BabelDOC con `--ocr-workaround` (solo válido con fondo blanco y texto negro).
- Azure Document Translation para PDFs completamente escaneados (OCR integrado), aceptando que el texto en imágenes de PDFs mixtos no se traduce.

### Fase 5 — Control de calidad automático
- **Geométrico:** cada bloque con `fit == False` o con `scale < 0,8` va a la lista de revisión.
- **Lingüístico:** (a) comprobar que todos los términos del glosario aparecen en la traducción cuando el término fuente estaba; (b) detectar inglés residual con un detector de idioma; (c) comprobar que los glifos y números del original siguen presentes (conjuntos iguales).
- **Visual:** renderizar original y traducción a PNG (`page.get_pixmap(dpi=110)`), hacer un diff de píxeles fuera de las zonas de texto (si cambian zonas de ilustración, algo se rompió) y pedir a un VLM barato una evaluación por página (BabelDOC usa justamente Gemini-2.5-Flash como juez multimodal en su evaluación).
- **Humano:** revisar el 100 % de las páginas con gráficos y una muestra de las de texto.

### Fase 6 — Ensamblado
Unir los lotes con `doc.insert_pdf()`, regenerar marcadores/índice (`set_toc` con títulos traducidos), subconjuntar fuentes y guardar. Opcionalmente, conservar el original como capa OCG alternable, como en la receta de Artifex.

## Comparación de los tres enfoques de reconstrucción

| Enfoque | Cómo funciona | Ventajas | Desventajas | Cuándo usarlo |
|---|---|---|---|---|
| (a) Edición "in place" | Redactar el texto original y reinsertar la traducción en las mismas coordenadas (BabelDOC, PyMuPDF) | Conserva al 100 % ilustraciones, vectores y paginación; el resultado es casi idéntico al original | La expansión obliga a reducir la fuente; el espacio liberado no se puede "redistribuir"; fuentes nuevas | **Opción principal** para libros maquetados con muchas figuras |
| (b) Formato intermedio y re-maquetación | PDF → Markdown/HTML/DOCX (MinerU, Docling, Marker, LlamaParse) → traducir → volver a maquetar | Texto refluye libremente, sin problemas de expansión; ideal para EPUB | Pierde el diseño fino; posición de figuras aproximada; los parsers son unidireccionales | Libros mayormente de texto, o si quieres una edición nueva (EPUB/impresión) |
| (c) Superposición de capas | Mantener la página original (imagen o PDF) y poner encima un rectángulo de fondo + texto traducido, opcionalmente en capa OCG | Muy simple; reversible; funciona con escaneados | Si el fondo no es plano se notan los parches; texto original sigue en el archivo salvo que se redacte | Escaneados y prototipos; versión bilingüe alternable |

Mi recomendación: **(a) como ruta principal**, **(c) para escaneados y figuras rasterizadas**, y **(b) solo si el objetivo final es una nueva edición** o un EPUB.

## Tabla comparativa de herramientas

| Herramienta | Tipo / licencia | Qué hace en este flujo | Preserva gráficos | Texto en imágenes | Notas |
|---|---|---|---|---|---|
| BabelDOC (github.com/funstory-ai/BabelDOC) | Open source, motor | Traducción completa con layout, glosario CSV, autoextracción de términos, división en partes | Sí (in place) | No (solo vectorial; `--ocr-workaround` no es OCR) | Mejor BIoU de su benchmark; CLI marcada "principalmente para depuración" |
| pdf2zh-next / PDFMathTranslate (github.com/PDFMathTranslate/PDFMathTranslate-next) | Open source, app sobre BabelDOC | CLI/GUI/Docker; implementación de referencia de BabelDOC | Sí | No (v1 tenía OCR Tesseract experimental en modo rápido) | Artículo EMNLP 2025; v1 fija babeldoc<0.3.0 |
| PyMuPDF (github.com/pymupdf/PyMuPDF) | AGPL o licencia comercial | Extracción con coordenadas, redacción, `insert_htmlbox`, render para QA, ensamblado | Sí | No (es la herramienta para reinsertar) | Núcleo de tus skills; atención a la AGPL si distribuyes |
| DeepL Document | Comercial | Traducción de PDF de "caja negra" | Parcial | No | Mínimo 50.000 caracteres por PDF; BIoU 19,8 % en benchmark de BabelDOC |
| Google Cloud Translation (documentos) | Comercial | PDF nativo hasta 300 páginas, escaneado 20 | Parcial | Escaneados sí (OCR), con pérdida de formato | 0,08 USD/página (0,25 con modelo custom); glosarios |
| Azure Document Translation | Comercial | PDF nativo y escaneado; API 2026-03-01 con Document Intelligence en batch | Parcial | Solo en PDFs escaneados completos o imágenes sueltas; no en imágenes dentro de PDFs digitales | Límite 40 MB por documento |
| MinerU (github.com/opendatalab/MinerU) | Open source | Parsing de layout/OCR con bboxes para escaneados | N/A (extractor) | OCR sí | MinerU2.5-Pro obtuvo 95,69 en OmniDocBench v1.6 (arXiv 2604.04771, abril 2026), aunque según la clasificación de Roboflow (oct. 2026) PaddleOCR-VL-1.6 (96,34 %) lo supera; pipeline CPU 4 GB |
| Docling | Open source (MIT) | Parsing a Markdown/JSON con layout | N/A | OCR opcional | Licencia más permisiva; peor en escaneados |
| Marker / Surya | Open source (pesos RAIL-M) | Parsing rápido, OCR con bboxes | N/A | OCR sí | El README de Marker proyecta 0,18 s/página y "122 páginas por segundo en una H100" (estimación teórica; un usuario midió ~66 s para un PDF de 240 páginas) |
| PaddleOCR / PP-StructureV3 | Open source | OCR + layout + tablas con coordenadas | N/A | Sí, ideal para etiquetas en cartas | Buen candidato para la skill de figuras |
| Nougat | Open source | PDF académico → LaTeX/Markdown | N/A | No | Poco útil aquí (orientado a papers) |
| Unstructured / pdfplumber | Open source | Extracción de elementos/caracteres con coordenadas | N/A | No (pdfplumber) | pdfplumber útil para tablas de efemérides |
| ReportLab | Open source | Generar páginas PDF nuevas desde cero | N/A | No | Solo si re-maquetas (enfoque b) |
| manga-image-translator / Koharu | Open source | Detección → OCR → traducción → inpainting → re-render en imágenes | N/A | **Sí** | Patrón a copiar para cartas rasterizadas |
| LlamaParse (LlamaCloud) | Comercial | Parsing con bboxes por elemento, palabra, línea y celda | N/A | OCR/VLM sí | 1–45 créditos/página; layout +1 crédito |
| Immersive Translate | Comercial (extensión/app) | Traducción de PDF para lectura, usa BabelDOC en su modo PDF | Sí (para lectura) | No verificado | Útil para leer; poco control para producción editorial |

## Dónde encaja LlamaIndex (y dónde no)

Respuesta honesta: **para el flujo base de PDFs digitales no es necesario.** BabelDOC y PyMuPDF ya te dan coordenadas exactas del propio PDF, que son más precisas que las de cualquier parser externo. LlamaIndex aporta valor en tres lugares concretos:

1. **Parser de respaldo para escaneados y páginas difíciles (LlamaParse).** Con `extract_layout=True` devuelve, por página, elementos (tablas, figuras, títulos, texto, listas) con bbox normalizado 0–1, confianza y recorte de imagen; la versión actual (Parse v2) devuelve un árbol `items` con bboxes en puntos de página y, con `granular_bboxes`, cajas por palabra, línea y celda de tabla. Coste: Fast 1 crédito/página, Cost-effective 3, Agentic 10, Agentic Plus 45; 1.000 créditos = 1,25 USD y 10.000 créditos gratis al mes. Un libro de 300 páginas en modo Agentic (+1 crédito de layout) son unos 3.300 créditos ≈ 4 USD. Útil, pero MinerU o PaddleOCR hacen lo mismo localmente gratis.
2. **Memoria de traducción y glosario consultable (LlamaIndex open source).** Indexar pares origen→destino ya aprobados (frases, leyendas, definiciones) y recuperar los 3–5 más similares como *few-shot* en cada prompt. Esto mejora la consistencia de estilo en libros largos o en una serie de libros del mismo autor. Para un solo libro con un glosario CSV de 200–500 términos, un simple filtro por coincidencia de términos (lo que ya hace BabelDOC) es suficiente.
3. **LlamaExtract / workflows** para extraer estructurado (p. ej. tablas de efemérides a JSON) si quieres regenerar tablas desde cero en vez de traducirlas in place. Los *workflows/agents* de LlamaIndex se solapan con lo que ya hace Codex; no los añadiría.

## Cómo estructurar el trabajo con Codex (y equivalentes)

### Principio de diseño
Codex no debe ser el "traductor" página a página dentro de su bucle de agente: sería lento, caro y no determinista. Codex debe **escribir, probar y ejecutar** scripts; la traducción masiva la hacen llamadas directas a la API con un modelo barato, invocadas por esos scripts. El agente interviene en: construir el pipeline, depurar páginas que fallan, decidir estrategias para figuras difíciles y revisar el QA.

### Estructura del repositorio

```
astro-translate/
├── AGENTS.md                      # reglas globales del proyecto
├── glossary/astro_en_es.csv       # source,target,tgt_lng
├── glossary/do_not_translate.txt  # glifos, abreviaturas, nombres propios
├── fonts/                         # fuente de texto + Noto Sans Symbols/Symbols 2
├── books/<libro>/source.pdf
├── books/<libro>/work/            # manifest.json, lotes, renders, informes
└── .agents/skills/                # (en Claude Code: .claude/skills/)
    ├── pdf-triage/
    ├── glossary-build/
    ├── translate-native-pages/
    ├── translate-figure-text/
    ├── fit-and-reinsert/
    ├── visual-qa/
    └── assemble-book/
```

### Ejemplo de AGENTS.md (resumen)

```markdown
# Proyecto: traducción de libros de astrología EN→ES
- Nunca traduzcas texto "a mano" dentro de la conversación: usa los scripts de las skills.
- Procesa por lotes de 20 páginas; escribe el estado en work/manifest.json y retoma desde ahí.
- Glosario obligatorio: glossary/astro_en_es.csv. Los términos de do_not_translate.txt no se tocan.
- Nunca modifiques imágenes ni dibujos vectoriales salvo con la skill translate-figure-text.
- Una página solo está "terminada" si visual-qa la marca OK; si no, déjala en review_queue.json.
- Modelo de traducción: el definido en config.toml (barato); modelo de revisión: solo para review_queue.
```

### Skills a definir

| Skill | Entrada → salida | Script principal | ¿Usa LLM? |
|---|---|---|---|
| `pdf-triage` | PDF → `manifest.json` con tipo de página, fuentes, imágenes | PyMuPDF | No |
| `glossary-build` | Texto extraído → `glossary.csv` candidato + dudas | pymupdf4llm + LLM fuerte | Sí (una vez por libro) |
| `translate-native-pages` | Rango de páginas → PDF parcial traducido | BabelDOC con `--pages`, `--glossary-files` | Sí (barato, vía API) |
| `translate-figure-text` | Imagen/región → imagen limpia + etiquetas traducidas con bbox | PaddleOCR/VLM + relleno/LaMa + PyMuPDF | Sí (barato; VLM para verificar) |
| `fit-and-reinsert` | Bloques JSON (bbox, texto, estilo) → página corregida | PyMuPDF `add_redact_annot` + `insert_htmlbox` | Opcional (acortar textos que no caben) |
| `visual-qa` | Original + traducido → informe por página | Render PNG + diff + VLM juez + chequeo de glosario | Sí (barato) |
| `assemble-book` | Lotes → PDF final con TOC traducido, fuentes subconjuntadas | PyMuPDF `insert_pdf`, `set_toc`, `subset_fonts` | No |

Ejemplo de `SKILL.md` para la skill de reinserción:

```markdown
---
name: fit-and-reinsert
description: Reemplaza bloques de texto de una página PDF por su traducción en las mismas coordenadas,
  ajustando el tamaño. Úsala para corregir etiquetas, leyendas o párrafos que BabelDOC dejó mal.
  No la uses para texto dentro de imágenes rasterizadas (usa translate-figure-text).
---
1. Ejecuta `python scripts/extract_spans.py <pdf> <page>` → work/pXXX_spans.json (bbox, font, size, color).
2. Excluye spans cuyo texto esté en do_not_translate.txt o cuya fuente sea de símbolos astrológicos.
3. Traduce con `python scripts/translate_blocks.py --glossary glossary/astro_en_es.csv` (modelo de config.toml).
4. Reinserta con `python scripts/reinsert.py --min-scale 0.75`. Lee el JSON de resultado.
5. Si algún bloque devuelve fit=false: pide una versión más corta (≤ len(original)*1.1 caracteres)
   respetando el glosario y reintenta una vez; si vuelve a fallar, añádelo a review_queue.json.
6. Ejecuta la skill visual-qa sobre la página antes de marcarla como terminada.
```

### Procesamiento por lotes de libros largos
- Lanza lotes no interactivos con `codex exec --sandbox workspace-write` (la forma recomendada en lugar del obsoleto `--full-auto`), uno por rango de páginas, con `--json` para registrar eventos y `--output-schema` para que cada ejecución devuelva un informe estructurado (páginas OK, en revisión, coste).
- Usa `codex exec resume --last` para continuar un lote interrumpido; el `manifest.json` hace el proceso idempotente.
- Para paralelizar, ejecuta varios `codex exec` en worktrees o directorios distintos, cada uno con su rango de páginas, y une al final.
- **Claude Code** funciona igual: skills en `.claude/skills/<nombre>/SKILL.md`, instrucciones de proyecto en `CLAUDE.md` (copiar `AGENTS.md`), y modo no interactivo con `claude -p`. El formato de skill es un estándar abierto (Agent Skills) compatible entre Codex, Claude Code, Cursor y Gemini CLI, así que las mismas skills sirven para ambos.

## Elección de modelos y estimación de costos

### Dónde basta un modelo básico y dónde no

| Tarea | Modelo recomendado | Justificación |
|---|---|---|
| Traducción masiva de párrafos con glosario | Barato: gpt-5-mini / gpt-5.4-mini, Claude Haiku 5.5, o local (Qwen, gpt-oss) vía endpoint compatible OpenAI | Con glosario fijo y contexto de página, la calidad EN→ES es alta; BabelDOC usa gpt-4o-mini por defecto |
| Construcción del glosario y guía de estilo | Fuerte: GPT-5.4/5.5 o Claude Sonnet 5.5 | Decisiones terminológicas que se propagan a todo el libro |
| Etiquetas de cartas y textos muy cortos | Barato + glosario estricto | Son términos cerrados; casi todo sale del glosario |
| Acortar textos que no caben | Barato | Tarea acotada con límite de caracteres |
| OCR y layout | Modelos especializados locales (MinerU, PaddleOCR, Surya), no LLM generalistas | Más precisos en coordenadas y gratis |
| Juez visual de QA | VLM barato (familia Flash/mini/Haiku) | Revisión de muchas imágenes |
| Revisión de la cola de problemas | Fuerte | Pocas páginas, alto impacto |

### Precios de referencia (por millón de tokens, entrada/salida)
- OpenAI: gpt-5-nano 0,05/0,40 USD; gpt-5-mini 0,25/2,00 USD; gpt-5.4-nano 0,20/1,25; gpt-5.4-mini 0,75/4,50; GPT-5.4 2,50/15; GPT-5.5 5/30. La Batch API ofrece un 50 % de descuento. Atención: el snapshot `gpt-5-mini-2025-08-07` tiene apagado programado para el 11 de diciembre de 2026, así que fija el modelo en configuración y prevé migrar.
- Anthropic (página oficial de precios): Claude Haiku 5.5 a 0,10/0,50 USD para prompts de hasta 100.000 tokens; Claude Sonnet 5.5 a 2/10 USD; Batch con 50 % de descuento. Según la documentación de Anthropic, el tokenizador introducido con Claude Opus 4.7 (abril de 2026) genera "aproximadamente un 30 % más de tokens" para el mismo texto (la página de precios habla de "hasta un 35 %", y la guía de migración de un factor de 1x a 1,35x).

### Estimación para un libro típico (estimación propia)
Supuestos: 300 páginas × ~2.000 caracteres ≈ 600.000 caracteres ≈ 150.000 tokens de texto fuente. Con glosario, contexto y reintentos, unas 3× de entrada (~0,5 M tokens) y ~0,2 M tokens de salida.

| Opción | Coste aproximado por libro |
|---|---|
| gpt-5-mini (traducción completa) | ≈ 0,5 USD (≈ 0,25 con Batch) |
| Claude Haiku 5.5 | ≈ 0,15 USD (algo más por el tokenizador) |
| GPT-5.4 (todo con modelo fuerte) | ≈ 4–5 USD |
| Claude Sonnet 5.5 (todo con modelo fuerte) | ≈ 3 USD |
| Glosario + revisión con modelo fuerte (~10 % del libro) | ≈ 0,5–1 USD |
| LlamaParse Agentic + layout (si se usa) | ≈ 4 USD |
| Google Document Translation (alternativa comercial) | 300 × 0,08 = 24 USD |
| Sesiones de Codex/Claude Code como orquestador | Depende del plan; suele ser el mayor coste variable si el agente "razona" página a página, de ahí la regla de usar scripts |

Conclusión: el coste de tokens de traducción es casi irrelevante; lo caro es **tu tiempo de revisión** y el trabajo de las figuras. Optimiza para calidad del glosario y para un QA que reduzca la revisión humana.

## Consistencia terminológica en astrología

- **Glosario cerrado con categorías:** signos (Aries, Tauro, Géminis, Cáncer, Leo, Virgo, Libra, Escorpio, Sagitario, Capricornio, Acuario, Piscis), planetas y puntos (Sol, Luna, Mercurio… Nodo Norte, Quirón, Lilith, Parte de la Fortuna), aspectos (conjunción, oposición, trígono, cuadratura, sextil, quincuncio/inconjunción, semisextil, semicuadratura, sesquicuadratura), casas (Casa I… o Casa 1, decidir y mantener), dignidades (domicilio, exaltación, exilio/detrimento, caída), técnicas (tránsitos, progresiones secundarias, revolución solar, sinastría, carta compuesta).
- **Decisiones de estilo explícitas:** mayúsculas ("Ascendente" vs "ascendente"), números romanos o arábigos en casas, formato de grados (15°23′ ♌), abreviaturas en tablas (p. ej. "R" de retrógrado se mantiene; "Asc"/"MC" → "Asc"/"MC" o "AC"/"MC").
- **Glifos:** no son texto a traducir. Riesgo frecuente (observación propia, verificar por libro): muchos libros usan **fuentes astrológicas no Unicode** (p. ej. una letra "a" que se dibuja como ♈); al extraer texto aparecen letras sueltas sin sentido. La skill de triaje debe listar las fuentes por página y marcar las de símbolos para que esos spans **nunca se redacten ni se envíen al LLM**.
- **Fuentes de salida:** si tienes que escribir glifos, usa los códigos Unicode U+2648–U+2653 para signos y U+2609, U+263D, U+263F–U+2647 para luminares y planetas, con Noto Sans Symbols o Noto Sans Symbols 2 como respaldo; asteroides y aspectos menores tienen soporte de fuentes solo "moderado".
- **Memoria de traducción:** guarda cada par origen→destino aprobado en un CSV/SQLite; reutilízalo en libros siguientes del mismo autor o editorial (aquí es donde un índice de LlamaIndex empieza a tener sentido).

## Desafíos conocidos y cómo mitigarlos

| Desafío | Impacto | Mitigación |
|---|---|---|
| Expansión del español (Kwintessential: "English into Spanish: +15 / +30 %" en texto corrido; según W3C/IBM, las cadenas de hasta 10 caracteres crecen un 200–300 %) | Texto que no cabe; letra cada vez más pequeña | Escala mínima (0,75–0,8), ampliar el rect al espacio libre, reformular más corto con LLM, revisión de casos fallidos |
| Fuentes incrustadas en subconjunto | Faltan "ñ", tildes, "¿¡" | Fuente nueva con cobertura completa; no reutilizar la original |
| Columnas y notas al pie | Orden de lectura incorrecto, frases partidas | BabelDOC (une párrafos entre columnas/páginas y reconstruye notas al pie) |
| Tablas de efemérides | Casi todo son números y glifos | Traducir solo cabeceras y leyendas; no tocar celdas numéricas |
| Ruedas zodiacales rasterizadas | Ninguna herramienta de PDF lo hace sola | Skill OCR → relleno/inpainting → texto vectorial encima; dejar glifos |
| PDFs escaneados | Sin capa de texto | OCR con bboxes (MinerU/PaddleOCR/LlamaParse) y reconstrucción por capas |
| Texto en curva o rotado en diagramas | Re-render difícil | Mantener rotación por etiqueta; si es texto en arco, aceptar etiquetas rectas o revisión manual |
| Escrituras RTL/CJK (si cambias de par) | Inserción carácter a carácter falla | Usar `insert_htmlbox` (maneja shaping), nunca `insert_textbox` para árabe/hebreo |
| Licencia AGPL de PyMuPDF | Obligaciones si distribuyes el software | Uso interno sin problema; si ofreces el servicio a terceros, revisar licencia comercial de Artifex |

## Consideraciones legales (nota breve)

Traducir un libro es una **transformación** que genera una obra derivada: según CEDRO (España) "el autor de una obra debe autorizar una traducción", y la OMPI indica que para publicar una traducción se necesita autorización del titular de la obra original. El traductor tiene derechos sobre su traducción, pero no puede explotarla sin esa autorización mientras la obra no esté en dominio público. Para uso estrictamente personal el riesgo práctico es menor, pero distribuir, vender o publicar las traducciones requiere licencia del titular (normalmente la editorial). Además, revisa las condiciones de uso de datos del proveedor de API antes de subir libros completos: la política de privacidad de DeepL indica que en sus servicios gratuitos procesa el contenido y sus traducciones "durante un tiempo limitado para entrenar y mejorar nuestras redes neuronales", mientras que DeepL Pro borra los textos tras traducirlos.

## Recomendaciones (plan de acción)

1. **Piloto de 20 páginas representativas** (texto corrido, una tabla de efemérides, dos cartas natales, una página con notas al pie): pasa BabelDOC con gpt-5-mini y un glosario mínimo, y mide qué falla. Esto decide cuánto trabajo propio necesitas.
2. **Construye primero `pdf-triage` y `glossary-build`**; sin glosario validado no traduzcas el libro completo.
3. **Usa BabelDOC/pdf2zh-next como motor de la Ruta A** y PyMuPDF (`fit-and-reinsert`) solo para corregir; no reescribas lo que BabelDOC ya hace.
4. **Invierte el esfuerzo de ingeniería en `translate-figure-text`**, que es donde ninguna herramienta te resuelve el problema, empezando por la regla "glifos y números nunca se tocan".
5. **Automatiza `visual-qa`** y limita la revisión humana a la cola de problemas y a las páginas con gráficos.
6. **Deja LlamaParse como respaldo** para escaneados difíciles y LlamaIndex para la memoria de traducción cuando traduzcas varios libros; no los pongas en el camino crítico al principio.
7. **Mantén dos modelos en la configuración:** uno barato para traducir/QA y uno fuerte para glosario y revisión; con eso el coste en tokens se queda en unos pocos dólares por libro.

## Riesgos y limitaciones de este análisis

- El benchmark de BabelDOC lo publican sus propios autores, con documentos científicos, técnicos y de patentes, no libros ilustrados; la ventaja frente a DeepL puede ser menor en tu caso.
- Las capacidades de "texto en figuras" de BabelDOC se interpretaron a partir de su README y su artículo; no se revisó el código fuente. Conviene comprobarlo en el piloto.
- Los precios de modelos y servicios proceden en parte de agregadores (verificados contra páginas oficiales cuando fue posible) y cambian con frecuencia; las estimaciones de coste por libro son cálculos propios con supuestos explícitos.
- Las cifras de expansión del español (Kwintessential: +15 % a +30 %; W3C/IBM: hasta 200–300 % en cadenas de hasta 10 caracteres y en torno a un 130 % en las de más de 70) son rangos de la industria de la localización, no mediciones sobre libros de astrología.
- Ningún pipeline automático produce hoy una edición publicable sin revisión humana, sobre todo en las páginas con cartas y diagramas.
