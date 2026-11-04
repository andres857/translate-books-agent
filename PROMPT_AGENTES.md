# Sistema multiagente para traducir libros de astrología en PDF

Especificación operativa del prompt maestro entregado por el usuario. `search.md` es la fuente de investigación, solo lectura. `docs/decisiones.md` registra verificaciones y las decisiones posteriores aprobadas en conversación; estas prevalecen cuando aclaran el prompt inicial.

## Objetivo y límites

Construir, probar y operar un pipeline inglés → español que conserve maquetación, ilustraciones, cartas natales, tablas y glifos. Resultado por libro: PDF traducido e informe de calidad. El orquestador lee investigación, crea herramientas y agentes, delega explícitamente, aplica gates y se detiene ante decisiones humanas. No traduce párrafos en conversación.

Reglas globales en `AGENTS.md`: scripts/API para traducción; glifos/números/grados/abreviaturas/fuentes simbólicas intocables y excluidos de modelos; glosario aprobado; original inmutable; manifest reanudable; QA antes de terminar páginas; redacción por spans cerca de gráficos; fuentes nuevas; costos visibles; PyMuPDF AGPL sin cambio de licencia no aprobado.

## Dos capas de modelos

### A: agentes Codex

| Agente | Modelo solicitado | Razonamiento | Sandbox |
|---|---|---|---|
| orquestador | gpt-5.5 | high, también plan mode | workspace-write |
| pipeline_engineer | gpt-5.5 | medium; high solo tras dos tests fallidos | workspace-write |
| pdf_triage | gpt-5.4-mini | low | workspace-write |
| glossary_curator | gpt-5.5 | high | workspace-write |
| batch_runner | gpt-5.4-mini | low | workspace-write |
| figure_specialist | gpt-5.5 | high | workspace-write |
| qa_reviewer | gpt-5.4 | medium | read-only |
| issue_fixer | gpt-5.5 | high | workspace-write |
| assembler | gpt-5.4-mini | low | workspace-write |

Comprobar catálogo `/model` o mecanismo equivalente verificable; si un identificador no está disponible, mapear por niveles fuerte/medio/barato y registrar el cambio. El mapeo actual está en `docs/decisiones.md`; no modifica automáticamente modelos API. Configuración en `.codex/config.toml`; ocho roles independientes en `.codex/agents/*.toml`, cada uno con name, description, model, model_reasoning_effort, sandbox_mode y developer_instructions. Límite solicitado: max_threads=4, max_depth=1, job_max_runtime_seconds=3600. Respetar límites del runtime si son menores.

### 2.2 B: LLM de scripts mediante Codex

Toda llamada pasa por `astro_translate.llm.run_task(task, payload)`. El backend por defecto es el SDK oficial Python `openai-codex`, autenticado mediante ChatGPT; `codex_exec` es alternativa y `openai_api` respaldo optativo, únicamente si está habilitado y hay clave. No usar proxies ni endpoints privados de ChatGPT.

| task | Unidad de trabajo | Modelo solicitado → disponible | Esfuerzo efectivo | backend |
|---|---|---|---|---|
| translate_page | Bloques de varias páginas hasta 6000 caracteres; ids con página | gpt-5.4-mini → gpt-6-luna | low | codex_sdk |
| translate_labels | Etiquetas de una figura | gpt-5.4-mini → gpt-6-luna | low (minimal no disponible) | codex_sdk |
| shorten | Bloques que no caben en una página | gpt-5.4-mini → gpt-6-luna | low | codex_sdk |
| classify_labels | Etiquetas OCR dudosas, tras filtro local | gpt-5.4-mini → gpt-6-luna | low (minimal no disponible) | codex_sdk |
| glossary_candidates | Texto filtrado en trozos de ~30 páginas | gpt-5.5 → gpt-6-astra | high | codex_sdk |
| qa_judge | Imágenes antes/después de una página, con máscaras verificadas | gpt-5.4-mini → gpt-6-luna | low | codex_sdk |
| review | Una página de la cola de revisión | gpt-5.5 → gpt-6-astra | high | codex_sdk |

La capa B consume la cuota de la suscripción de ChatGPT; no aplica el descuento de Batch API. Los modelos efectivos y el mapeo se verifican contra el catálogo y se registran en `docs/decisiones.md`. Las tareas se configuran en `[tasks]` de `config/pipeline.toml`; no confundirlas con agentes de la capa A.

Desde Fase 1c translate_page agrupa varias páginas hasta 6000 caracteres, sin mezclar sus ids; se registra cached_input_tokens del proveedor. Cada llamada crea un hilo efímero en un directorio temporal vacío dentro de `work/.codex_llm_sandbox/` del libro, con perfil `translator`, sandbox de solo lectura, aprobaciones denegadas, web/MCP/herramientas/skills e instrucciones del proyecto desactivadas. El hecho de que el directorio esté vacío no basta: bloquear herencia desde ancestros. El prefijo fijo del prompt prohíbe comandos, archivos y herramientas y exige solo JSON.

Prompts en `prompts/<task>.md`, esquemas en `schemas/<task>.json`. Validar JSON, igualdad exacta de ids sin duplicados, marcadores y contenido protegido conservados, glosario y límites por id. Los valores originales protegidos se sustituyen localmente antes del backend; nunca se envían al modelo. Los números de ids o de restricciones operativas no son valores del libro.

Agrupar por página/figura; nunca llamar por frase. Ante validación fallida, reintentar una vez con el error concreto; después únicamente los ids problemáticos. Si vuelve a fallar: `review_queue.json`, motivo `llm_validation`. G1b mide por separado el porcentaje válido tras como máximo el primer reintento.

Caché SQLite `work/llm_cache.sqlite`, ligada a tarea, contenido normalizado, glosario/estilo/lista protegida, modelo, esfuerzo, prompt y esquema. Reusar resultados vigentes, conservar identidad de ids y no reutilizar una salida inválida. Concurrencia por defecto dos, sin superar cuatro sin medición.

Ante cuota agotada, aplicar backoff de 30, 120 y 600 segundos; si persiste, marcar la página pending, persistir manifest y `work/quota_pause.json`, terminar con código 75. Solo ejecutar fallback configurado; `openai_api` requiere también clave y deja gasto real identificado en costos. No presentar cuota de suscripción como USD API. Registrar tokens disponibles, latencia, caracteres, backend efectivo, caché y reintentos; estimar consumo con `scripts/estimate_quota.py`.

## Repositorio

`AGENTS.md`, este archivo, `.codex/config.toml`, `.codex/agents/`, `.agents/skills/`, `config/pipeline.toml`, `glossary/astro_en_es.csv`, `glossary/do_not_translate.txt`, `glossary/style_guide.md`, `glossary/dudas.md`, `fonts/`, `src/astro_translate/`, `tests/`, `schemas/` y `docs/decisiones.md`.

Por libro: `books/<libro>/source.pdf` de solo lectura, `work/manifest.json`, spans, renders, qa, costs.jsonl, review_queue.json y logs; `out/` para PDFs e informes.

## Skills del proyecto

Cada carpeta contiene SKILL.md con frontmatter name/description que indique cuándo usarla y cuándo no, scripts ejecutables y referencias cuando hagan falta.

| Skill | Entrada → salida | Implementación |
|---|---|---|
| pdf-triage | PDF → manifest | PyMuPDF, caracteres, imágenes, fuentes, escaneado; sin LLM |
| glossary-build | texto → candidatos y dudas | extracción filtrada + API fuerte; no autoaprobar |
| translate-native-pages | páginas → parcial | Extracción local + run_task por página + fit-and-reinsert; BabelDOC sujeto a decisión medida de Fase 1b |
| fit-and-reinsert | spans → página corregida | PyMuPDF, preservar imágenes y vectores, insert_htmlbox |
| translate-figure-text | región → imagen limpia + etiquetas | PaddleOCR/Surya/MinerU, relleno/LaMa, capa vectorial |
| scanned-page-rebuild | escaneo → página reconstruida | OCR local con bbox; LlamaParse respaldo limitado a contenido seguro |
| visual-qa | antes/después → qa/pXXX.json | render 110 dpi, diff, glosario, inglés residual, glifos, VLM |
| assemble-book | lotes → PDF/informe | insert_pdf, TOC traducido, subset_fonts, ez_save; sin LLM |

Reinserción: si spare_height=-1, ampliar hacia espacio libre; si falla, pedir una versión ≤len(original)*1.1 caracteres, con glosario, y reintentar una vez; luego revisión. Escala inferior a 0,80 exige revisión además de advertencia. Mínimo general 0,75, pero etiquetas vectoriales escala 1,00 (decisión humana posterior). Nunca aplicar un cambio fallido parcialmente al original.

Figuras: OCR local, filtro de contenido protegido, traducción de etiquetas seguras, limpieza exclusivamente de texto detectado y reinserción con orientación preservada. Dos fallos de QA llevan a revisión con capturas. No aplicar inpainting que dañe líneas/glifos. Rotaciones no soportadas van a revisión explícita.

QA visual: la aprobación humana posterior exige ocultar contenido protegido en copias temporales para el juez; validar preservación mediante comparación local. Una detección incierta no autoriza enviar la región completa al modelo.

Plantilla de traducción (con contenido protegido excluido antes de construir el prompt):

```text
Eres traductor editorial de astrología EN→ES (registro según style_guide.md).
Traduce SOLO el texto entre <src>. Respeta exactamente marcadores {v1},{v2}…
Términos obligatorios para este segmento: {entradas coincidentes del glosario}.
Ejemplos aprobados (memoria de traducción): {0–3 pares similares si existen}.
Devuelve JSON: {"translation": "...", "glossary_terms_used": [...]}.
<src>{texto_filtrado}</src>
```

## Fases y gates

1. **Bootstrap — orquestador + pipeline_engineer.** Crear estructura, configuración, agentes, skills y entorno con pymupdf, babeldoc/pdf2zh-next, paddleocr/Surya, pillow, openai y pytest. TDD con PDF sintético de tres páginas (texto, tabla, imagen con texto). **G1: pytest en verde**. Informar qué pruebas son locales y qué integraciones no han sido ejecutadas.
1b. **Backend Codex — pipeline_engineer (medium; high solo tras dos fallos de validación).** Verificar SDK real, firmas, autenticación ChatGPT, aislamiento, esquema/tokens/cuota; implementar llm.py con los tres backends, caché, agrupación, validación y pausa. Tests FakeBackend sin red para ids, placeholders, glifos, glosario, caché, cuota/exit75 y frontera de imports. Inspeccionar traductor inyectable BabelDOC y agrupación por página; medir cinco páginas si hay que comparar segmentos contra ruta PyMuPDF. No fingir una medición si no puede ejecutarse. Smoke real de tres páginas (texto, tabla, figura con etiquetas vectoriales) tras aprobación G3. **G1b HUMANA:** entregar docs/informe_backend.md con validez JSON después de como máximo un reintento, glosario, tiempos/tokens por página, proyección del libro y decisión BabelDOC; detenerse hasta aprobación del backend o elección alternativa. G3 es requisito de toda traducción real aunque la fase de backend aparezca antes del triaje en un proyecto nuevo; realizar la ingeniería con datos sintéticos y completar smoke una vez aprobada G3.
1c. **Reinserción — pipeline_engineer.** Backend/ruta aprobados parcialmente; comparación 3× dispensada por humano. No ejecutar piloto. Componer texto corrido por bloque/párrafo (scale_low=0,75), clasificar dibujos por intersección del bloque y mantener redacción por spans en etiquetas/gráficos. Etiquetas: ampliar → short_target aprobado en suplemento → LLM. Para cadenas <15 caracteres no usar 1,10. Agrupar varias páginas por llamada hasta 6000 caracteres e informar tokens, incluida caché del proveedor. Cerrar con páginas 7/15/121, cero spans fallidos, escala >=0,75, QA visual ejecutado y originales/glosario intactos; detenerse e informar.
1d. **Fidelidad — pipeline_engineer y revisión QA.** Autorizada sobre 7/15/121, sin G4. Proteger tokens con {nN}, no spans enteros con cifras; fuentes simbólicas completas se preservan. Fechas deterministas. Fuentes originales si has_glyph cubre texto/grupo; si no, reemplazo con estilo/ancho/peso/italic equivalente y color original. Uniformar escala por grupo. Separar títulos/cuerpo por tamaño/peso y mantener unidad de párrafos/citas; comprobar traducción consistente de frases repetidas. Pares de etiquetas completos o cortos conjuntamente. Verificar Í. Cierre: 0 inglés fuera de tokens, 0 fallos, escala>=0,75 uniforme, estilos preservados, diff fuera del texto=0. Entregar comparativas original|traducción y detenerse; aprobación exclusivamente humana visual.
2. **Triaje — pdf_triage.** Generar manifest del libro. **G2:** distribución por tipo y fuentes simbólicas; avisar y replantear si >30 % escaneadas.
3. **Glosario — glossary_curator.** Proponer CSV, lista protegida, estilo y dudas (p. ej., detriment: exilio/detrimento). **G3 HUMANA:** detenerse y presentar archivos para aprobar; ninguna traducción sin aprobación.
4. **Piloto — roles según ruta.** Elegir 20 páginas representativas: texto, notas al pie, tabla de efemérides real, al menos una página escaneada Ruta C, dos cartas natales y diagrama vectorial. Si el libro no contiene un tipo, documentarlo; no inventarlo. Ejecutar rutas y QA con el backend aprobado en G1b. Entregar `out/piloto.pdf` y `docs/informe_piloto.md` con fallos/costos/tiempos y consumo real frente al proyectado. **G4 HUMANA:** aprobar piloto antes de producción.
5. **Producción — batch_runner y figure_specialist.** Lotes de 20; A en paralelo dentro del límite real; B/C después según cada página. Reanudar por manifest. Antes de cada lote comprobar quota_pause.json; si la espera sigue vigente, no lanzar llamadas. El código 75 es pausa, no error ni motivo de bucle de reintentos: reprogramar o esperar instrucciones. Reportar backend usado, llamadas, hits de caché y páginas pausadas. Tres errores repetidos no debidos a cuota detienen el lote. **G5:** ≥90 % QA OK y cero páginas traducidas con backend distinto del aprobado sin registro en costs.jsonl; de lo contrario pausar y analizar antes de seguir.
6. **Revisión — qa_reviewer + issue_fixer.** Revisor read-only dictamina ok/revisar por página. Fixer corrige con skills y documenta intentos. **G6:** cola vacía o solo revisión manual humana explícita con capturas.
7. **Ensamblado — assembler.** PDF final, TOC traducido, fuentes subconjuntadas e informe con páginas/advertencias/costos/casos manuales. **G7 HUMANA:** revisión final. Revisar todas las páginas con gráficos y una muestra del texto.

Ejecución orientativa, solo tras gates previas:

```bash
codex exec --sandbox workspace-write --json \
  --output-schema schemas/lote.json \
  "Procesa páginas 41-60 de books/<libro> según PROMPT_AGENTES.md fase 5" \
  > books/<libro>/work/logs/lote_041_060.jsonl
```

`codex exec resume --last` retoma sesión; el manifest determina el trabajo que sigue vigente.

## Contratos y umbrales

Schemas en `schemas/`: manifest de libro con pages; page con page,type,chars,images(xref,rect,has_text),symbol_fonts,route,status,attempts; QA con page,verdict,residual_english,glossary_missing,glyph_set_equal,min_scale,non_text_pixel_diff,vlm_notes; review_queue como lista de page,reason,artifacts,suggested_action; lote con pages_ok,pages_review,tokens,cost_usd,errors. Se permiten extensiones de trazabilidad. Un costo desconocido usa null.

Defaults en config: min_scale=.75, warn_scale=.80, shorten_ratio=1.10, render_dpi=110, pages_per_batch=20, babeldoc_max_pages_per_part=50, use_openai_batch_api=false, max_non_text_pixel_diff=.01 y batch_ok_ratio=.90. Batch API no se usa con los backends Codex.

Al cerrar cada fase: informar resultado de gate, costos acumulados, limitaciones y próximo paso. En G1b/G3/G4/G7 detenerse y enlazar exactamente los archivos que debe revisar el humano. Registrar falsedades o incompatibilidades detectadas en `docs/decisiones.md` y avisar. No cambiar el plan de preservación o licencias sin autorización.

## Registro de aprobación G1b

Tras aprobación humana explícita del informe, el orquestador registra `books/<libro>/work/backend_approval.json` según `schemas/backend_approval.json`: approved=true, backend elegido, SHA-256 de config/pipeline.toml, approved_by y approved_at. El script solo verifica ese registro; no lo crea ni se autoaprueba. Una discrepancia de configuración impide usar esa aprobación para un backend distinto.

La ejecución distingue `--phase smoke`, `--phase pilot` y `--phase production`. Smoke se limita a las tres páginas de G1b y las cinco páginas de medición autorizada; no es piloto ni producción y no marca QA como completado. Pilot exige G1b y G3; production exige además G4 vigente. El esquema del lote incorpora backends_used, calls, cache_hits y pages_paused.


## Autorización humana actual: correcciones y piloto20

El usuario aprobó G4 con condiciones y autorizó ejecutar el piloto después de corregir negrita de signos p7, medianil y párrafos p15, y concordancia de Nodos p121. No se abre otra fase. Se aprueba la redacción final de dos adiciones a la guía G3: correcciones gramaticales objetivas registradas con el original, sin cambiar contenido/datos/estilo; comillas inglesas “ ” y simples ‘ ’ para citas internas. Historial y nuevos hashes en work/approval_history y glossary_approval.json. Selección en docs/seleccion_piloto.md; anexo Mercurio separado. Intentar juez VLM con exclusiones seguras y declarar cobertura real. Entregar veinte comparativas, consumo frente a proyección e incidencias; detenerse para revisión visual del piloto, sin producción. Esta autorización reemplaza las prohibiciones históricas de ejecutar el piloto, no autoriza producción.
