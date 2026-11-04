# Decisiones técnicas — traducción de libros de astrología

Estado: decisiones de escala y protección visual aprobadas por el humano en la conversación (respuesta «si»). Bootstrap local G1 aprobado (31 tests); triaje G2 completado. G3 aprobada explícitamente para los cuatro archivos actuales. G1b aprobada parcialmente para backend y ruta; Fase 1c Reinserción terminada según su criterio geométrico; Fase 1d Fidelidad con criterios técnicos cumplidos sobre 7/15/121 (109 tests). Revisión visual humana pendiente; G4 no aprobado.

Fuente técnica: `search.md`, leído completo (280 líneas), sin modificar. El prompt maestro de la conversación define las fases y gates. Este documento distingue decisiones comunes, propuestas pendientes y verificaciones aún no realizadas.

## Arquitectura elegida

Las tablas iniciales documentan la arquitectura de partida de search.md. Las decisiones posteriores de G1b/1c/1d prevalecen: Ruta A propia PyMuPDF, SDK Codex sin Batch API y protección/fuentes conforme a Fidelidad.

El agente orquesta ingeniería y ejecución; la traducción masiva se realiza exclusivamente mediante scripts y API. Inglés → español por defecto. Se conserva la paginación y la posición de ilustraciones, cartas, tablas y glifos; no se adopta una remaquetación general a Markdown/EPUB.

| Etapa | Herramientas y comportamiento |
|---|---|
| Triaje | PyMuPDF: texto extraíble, imágenes y sus rectángulos, fuentes y dibujos por página. Clasificar `native`, `mixed`, `vector_figure`, `scanned`. Avisar si las escaneadas superan el 30 %. |
| Glosario | PyMuPDF/pymupdf4llm para extracción; filtrar contenido protegido antes de cualquier API. Modelo fuerte propone candidatos, dudas y estilo. CSV `source,target,tgt_lng`. Aprobación humana obligatoria antes de traducir. |
| Ruta A | BabelDOC/pdf2zh-next para texto nativo, con glosario, salida monolingüe, sin marca de agua y partes de hasta 50 páginas. PyMuPDF corrige spans y etiquetas vectoriales. Verificar las opciones en la versión instalada. |
| Ruta B | OCR local con PaddleOCR como primera opción; Surya o MinerU como alternativas. Clasificar y proteger glifos/números; traducir etiquetas; limpiar únicamente texto detectado mediante relleno plano o LaMa; insertar etiquetas como texto vectorial y conservar orientación. |
| Ruta C | OCR con cajas y layout mediante PaddleOCR/MinerU/Surya; limpiar texto traducible de la imagen y reconstruir una capa de texto. LlamaParse queda como respaldo opcional, sujeto al tratamiento del contenido protegido descrito abajo. |
| QA | Render a 110 dpi, diferencias fuera de zonas editables, ajuste geométrico, glosario, inglés residual, conservación de glifos y números, juez visual y revisión humana. Un informe incompleto no cuenta como aprobación. |
| Ensamblado | PyMuPDF `insert_pdf`, `set_toc` con títulos ya traducidos mediante scripts, `subset_fonts` y `ez_save`. Generar PDF e informe con advertencias, costos y casos manuales. |

Una página mixta requiere A para su texto nativo y B para sus imágenes: `route` no basta para representar la ejecución completa; el manifest deberá conservar también el estado de cada etapa. `--ocr-workaround` no sustituye al OCR: el informe limita su uso a páginas con capa OCR existente y fondo blanco/texto negro.

LlamaIndex no entra en el camino crítico. Una memoria de traducción simple con pares aprobados basta inicialmente. La extracción automática de terminología de BabelDOC no puede alterar silenciosamente el glosario aprobado.

## Protección, fuentes y expansión

Reglas iniciales, modificadas explícitamente en 1d: la prosa con cifras se recompone mediante tokens, se permite reutilizar fuentes con cobertura comprobada y la escala mínima por grupo es 0,75 también en las etiquetas de la muestra; no se mantiene una escala 1 forzada por contener dibujos.

- No modificar `books/<libro>/source.pdf`; escribir resultados en `work/` y `out/`. Verificar la identidad del original al reanudar.
- No redactar, modificar ni enviar al modelo glifos, grados, números, abreviaturas protegidas o spans en fuentes de símbolos. Detectar también fuentes no Unicode, donde una letra aparente representa un glifo.
- Usar bboxes de spans cerca de imágenes y vectores; comprobar intersecciones con contenido protegido. Un bbox de span todavía puede solaparse con otros caracteres: la protección exige verificar la geometría real.
- Mantener las celdas numéricas de efemérides; traducir solo cabeceras y leyendas traducibles. Los marcadores de los prompts representan contenido excluido, sin enviar los valores protegidos.
- Usar fuentes nuevas configuradas en `fonts/`, con cobertura latina y Noto Sans Symbols / Symbols 2 de respaldo. No reutilizar fuentes originales incrustadas para texto nuevo ni reconstruir glifos que deben conservarse.
- Texto general: escala mínima 0,75; umbral de advertencia 0,80. Si no cabe, ampliar solo a espacio libre, luego solicitar una reformulación de hasta `len(original) * 1.10` caracteres respetando el glosario y reintentar una vez. Si falla, enviar a revisión. Una escala inferior a 0,80 requiere revisión, además de advertencia.
- Para etiquetas de diagramas vectoriales, `search.md:75` exige no reducir la escala. Decisión aprobada: excepción específica con escala 1,00, ampliación segura/reformulación y revisión si no cabe; no aplicarles automáticamente el mínimo general de 0,75.
- La guía de estilo no puede normalizar cifras, grados o abreviaturas existentes que las reglas declaran intocables. Distinguir estilo de texto nuevo de preservación literal del original.

## Estado, gates y costos

`work/manifest.json` mantiene el progreso por página. Las escrituras deben ser atómicas y coordinar procesos paralelos para evitar pérdidas de estado. Cambios de fuente, configuración o glosario deben invalidar resultados dependientes, no reutilizarlos sin comprobarlos.

Una página pasa a terminada solo tras QA satisfactorio. Los fallos se registran en `review_queue.json` con motivo y artefactos. G6 permite casos manuales explícitos, pero no convertirlos en `qa_ok`: el informe final debe mostrarlos.

G1 requiere pytest con un PDF sintético de tres páginas: texto, tabla e imagen con texto. Pruebas sin red y respuestas simuladas permiten comprobar el pipeline sin una traducción real ni eludir G3; no equivalen a validar servicios externos. G2 informa del triaje; G3 requiere aprobación humana del glosario y estilo; G4 aprobación humana del piloto; G5 al menos 90 % de QA satisfactorio por lote; G6 resuelve o explicita casos manuales; G7 revisión humana final.

Producción en lotes de 20 páginas; límite de 50 por parte de BabelDOC. Batch API cuando sea compatible con el adaptador y la tarea; configurar una bandera no demuestra que BabelDOC use Batch. Registrar modelo, tokens, operación, reintentos y costo en `work/costs.jsonl`. Si falta uso o tarifa verificable, registrar costo desconocido, no cero. No asumir que las estimaciones de `search.md` son precios actuales.

## Diferencias resueltas con aprobación humana

El apartado 0 del prompt ordena preguntar antes de continuar si hay contradicciones con `search.md`. Las siguientes propuestas fueron aprobadas explícitamente en la conversación:

1. **Escala y revisión.** `search.md:84` manda a revisión cada bloque con escala inferior a 0,80; el prompt, sección 5, solo exige marcar una advertencia. Decisión aprobada: conservar ambas señales y enviar la página a `review` hasta resolverla. Aplicar asimismo escala 1,00 para etiquetas vectoriales según `search.md:75`, como excepción al mínimo general del prompt.
2. **Contenido protegido y modelos visuales.** `search.md:68,73,86` contempla OCR por VLM y evaluación de renders; el prompt también exige un juez visual. Un render completo contiene los mismos glifos y números que su regla 1.2 prohíbe enviar al LLM. Decisión aprobada: OCR y comparación de contenido protegido locales; enviar al juez únicamente copias con zonas protegidas ocultas o recortes que las excluyan. Estas máscaras solo afectan a entradas temporales del juez, nunca al PDF. Si no se puede excluir con confianza el contenido protegido, pasar a revisión humana. LlamaParse solo recibirá regiones que cumplan esa misma condición si utiliza modelos generativos; no se enviará el escaneo completo sin resolverla.

La revisión humana del 100 % de páginas con gráficos y una muestra de texto (`search.md:87`) es un requisito adicional compatible con G4/G7; se conserva en el plan. La receta de redacción del informe declara preservar vectores pero solo especifica `images=...`: debe verificarse el parámetro explícito para dibujos en la versión de PyMuPDF instalada antes de usarla.

## Modelos de Codex: evidencia y mapeo

Se encontró `codex-cli 0.165.0`. `/model` es un comando del compositor interactivo, no un subcomando de shell: [documentación oficial de comandos](https://learn.chatgpt.com/docs/developer-commands?surface=cli). No se ha invocado `/model` en esta sesión ni cambiado su modelo activo.

Como evidencia local alternativa se leyó `/home/andres/.codex/models_cache.json`, cuyo `fetched_at` es `2026-11-04T02:26:38.290959473Z`. El catálogo de subagentes expuesto a esta sesión también permite `gpt-6-astra`, `gpt-6.1-sol` y `gpt-6-luna`; no permite los tres identificadores solicitados. La caché no prueba por sí sola disponibilidad de la API del proyecto.

Mapeo operativo de la capa A conforme a la regla de disponibilidad del prompt, materializado en los archivos de configuración:

| Nivel solicitado | Modelo solicitado | Sustituto disponible para delegación |
|---|---|---|
| Fuerte | gpt-5.5 | gpt-6-astra |
| Medio | gpt-5.4 | gpt-6.1-sol |
| Barato | gpt-5.4-mini | gpt-6-luna |

Se mantienen los esfuerzos de cada rol: high, medium o low según el prompt. Estos niveles constan en el catálogo disponible. El mapeo expresa funciones de trabajo; no afirma equivalencia de precios ni de rendimiento. Crear configuración no cambia retroactivamente el modelo ni los permisos de la sesión actual.

La capa B conserva por ahora los identificadores solicitados en el diseño. Su acceso y los valores de `reasoning.effort` deben verificarse por separado con el proveedor de API; no se infieren del catálogo de Codex. No se han realizado llamadas de inferencia. El reenvío de `reasoning.effort` por BabelDOC fue inspeccionado en el código instalado; su compatibilidad con el endpoint sigue pendiente de prueba real. Detalle en la sección de verificaciones de G1.

La [documentación oficial de subagentes](https://learn.chatgpt.com/docs/agent-configuration/subagents) confirma archivos independientes en `.codex/agents/` y actualmente muestra `max_concurrent_threads_per_session` en sus ejemplos. Debe validarse el esquema real de la CLI instalada antes de copiar `max_threads`, `max_depth` y `job_max_runtime_seconds`. La herramienta de esta sesión permite cuatro agentes activos en total, incluido el orquestador, por lo que aquí solo pueden ejecutarse tres subagentes simultáneos. No se permitirá delegación desde subagentes.

## Licencias y verificaciones pendientes

PyMuPDF se adopta bajo su opción **AGPL**, como exigen el prompt y `search.md`; no se cambia esa decisión sin preguntar. El informe menciona también una licencia comercial. Este registro no sustituye la revisión de obligaciones correspondiente a una futura distribución.

El checklist inicial de G1 exigía instalar y fijar dependencias compatibles; comprobar opciones reales de BabelDOC, preservación de vectores al redactar, rotaciones admitidas por `insert_htmlbox`, fuentes y licencias; implementar y ejecutar tests. El alcance verificado y las integraciones pendientes se detallan abajo y en `informe_bootstrap.md`. Las rotaciones arbitrarias/radiales no deben darse por soportadas solo por existir un argumento `rotate`.

No se encontraron PDFs en la inspección inicial del directorio. No existe repositorio Git inicializado. Hay `python3`, pero el comando `python` no está disponible fuera de un entorno virtual. En esa inspección inicial aún no se habían instalado dependencias ni creado agentes/scripts, a la espera de la decisión humana. Esa pausa ya se resolvió y el bootstrap local está completado.

Costos de API de traducción incurridos: **0 USD** (ninguna llamada). Costos de la sesión de Codex: no disponibles en estas herramientas. Gate G1 inicial: no ejecutada en esa primera inspección; resultado actual: **31 tests superados**, según el cierre registrado abajo.

## Actualización de inicio del bootstrap

Se encontraron tres PDFs añadidos en `books/` desde la inspección inicial: dos de Carole Taylor y uno de Joanna Martine Woolfolk. Se conservarán intactos y se copiará el elegido a su directorio de libro, verificando su hash. La elección se consultó mientras avanza G1.

## Verificaciones durante G1

- Catálogo consultado de nuevo con `codex debug models` (salida JSON filtrada a identificadores), código de salida 0. Confirma los sustitutos de capa A. Es una alternativa verificable al selector interactivo; no se ha ejecutado `/model` ni se ha cambiado la sesión activa.
- `codex --strict-config app-server --print-startup-config` acepta la configuración de proyecto y los campos `max_threads`, `max_depth` y `job_max_runtime_seconds` en CLI 0.165.0. Se conservan los nombres del prompt. Solo se publicó el código de salida, no configuración privada.
- Fuentes Noto Sans, Noto Sans Symbols y Noto Sans Symbols 2 descargadas del repositorio oficial `notofonts/noto-fonts`; licencia OFL y procedencia con SHA-256 en `fonts/`.
- Libro elegido explícitamente por el humano: *Astrology: Using the Wisdom of the Stars in Your Everyday Life*, Carole Taylor. Copia idéntica de 45.133.294 bytes en `books/astrology-wisdom-stars/source.pdf`, permisos 0444; hash y procedencia en `work/intake.json`. La entrada original permanece intacta.
- `OPENAI_API_KEY` y `OPENAI_BASE_URL` no están configuradas; no se imprimieron secretos ni se hicieron llamadas de inferencia. Las APIs se probarán con clientes simulados en G1.

### BabelDOC: reenvío real de razonamiento y límites

Inspección de BabelDOC 0.6.4 instalado: `babeldoc/main.py` define `--openai-reasoning`, `--openai-term-extraction-reasoning` y `--no-send-temperature`. `babeldoc/translator/translator.py`, clase OpenAITranslator, transforma el valor de razonamiento en `extra_body={"reasoning":{"effort":valor}}` y lo pasa a `client.chat.completions.create`. Sin el flag no envía el campo. Esto **no prueba** que el endpoint/modelo de capa B acepte esa forma: el script propio usa Responses y su `reasoning.effort`; no se equiparan las dos APIs. El traductor de BabelDOC contiene reintentos de RateLimit hasta 100, incompatibles con el límite operativo de tres errores repetidos si se ejecuta sin adaptación.

Las opciones `--pages`, `--glossary-files`, `--no-dual`, `--watermark-output-mode`, `--max-pages-per-part`, `--formular-font-pattern` y `--formular-char-pattern` existen en el código instalado. Las regex de fórmula por sí solas no demuestran que todas las peticiones omitan números y spans simbólicos. Hasta instrumentar ese recorrido, Ruta A debe bloquearse con motivo explícito. No se afirma integración Batch ni traducción real de BabelDOC por haber instalado el paquete.

Fuentes técnicas consultadas: [BabelDOC oficial](https://github.com/funstory-ai/BabelDOC), [PyMuPDF Page](https://pymupdf.readthedocs.io/en/latest/page.html), [Structured Outputs de OpenAI](https://developers.openai.com/api/docs/guides/structured-outputs). La evidencia de parámetros de BabelDOC anterior procede del código instalado, no de una suposición sobre el README.

## Cierre local de G1 y G2

G1: 31 pruebas pytest superadas, tres páginas sintéticas y renders inspeccionados; ver `docs/informe_bootstrap.md`. Dependencias adicionales de QA: Lingua EN/ES. Se verificó cobertura de las 26 posiciones Unicode requeridas con las tres fuentes Noto. G1 acredita operaciones locales y adaptadores simulados, no integración real de los servicios pendientes.

G2: libro de 258 páginas, 228 mixtas, 29 escaneadas y 1 nativa. Escaneadas 11,24 %, sin superar el 30 %. Hay fuentes astrológicas en 100 páginas y 257 colocaciones de imagen con presencia de texto aún desconocida. Ver `docs/informe_triaje.md`. Todas las páginas permanecen pendientes y el original conserva su hash de ingreso.

No hay clave API. El glosario de G3 se prepara como propuesta editorial preliminar apoyada en inventario determinista local, no como una llamada ficticia al modelo de la capa B. Esta limitación se presentará al humano junto con el CSV, la guía y las dudas.

## Fase 1b — cambio de capa B solicitado por el humano

El nuevo prompt tiene prioridad para la capa B: usar el SDK oficial Python `openai-codex` con la suscripción ChatGPT. `codex_exec` queda como alternativa; Responses API, solo respaldo explícito con clave. No se implementan proxies ni llamadas directas a endpoints privados. El cambio no altera la protección de los originales ni la licencia de PyMuPDF.

### G3 y selección de prueba

El usuario aprobó explícitamente los archivos actuales de G3 durante esta fase: CSV de 118 términos, guía, lista protegida y propuestas de dudas. Se registró `books/astrology-wisdom-stars/work/glossary_approval.json` con hashes del PDF y de los tres archivos usados por el gate. Los cuatro archivos editoriales aprobados permanecen sin modificaciones; la aprobación permite el smoke real de tres páginas, pero no aprueba G1b ni G4.

Páginas externas seleccionadas e inspeccionadas: 15 (texto corrido), 7 (tabla de contenidos con signos, fechas y planetas en columnas) y 121 (carta con etiquetas vectoriales). La página 7 no es una tabla de efemérides. Los renders de selección están en work/backend_selection. PyMuPDF detectó tablas estructurales en las páginas 6–9; la inspección visual confirmó la estructura de columnas de la página 7.

### SDK, perfil y modelos efectivos

Instalado `openai-codex 0.163.0`, runtime fijado `openai-codex-cli-bin 0.163.0`, frente a CLI host0.165.0. Detalle de pydoc/help y firmas en `docs/codex_sdk_api.md`. La documentación oficial describe el SDK Python como estable: se corrige el supuesto de beta. Se verificó `codex login status`: Logged in using ChatGPT.

El modelo se elige por hilo y effort/output_schema por turno. TurnResult ofrece respuesta, items y uso; el stream conserva señales estructuradas de cuota que el método run puede reducir a RuntimeError. El SDK no tiene un campo profile en CodexConfig y su app-server 0.163 rechaza tanto `--profile` como el override `profile`. Se lee el contenido de `translator.config.toml` y se aplica por `config_overrides`; la CLI alternativa sí usa `-p translator`.

Capa B efectiva: tareas baratas gpt-6-luna/low y fuertes gpt-6-astra/high. El catálogo SDK de la cuenta no incluye gpt-5.4-mini ni gpt-5.5, ni minimal para luna. Se aplica el mapeo por disponibilidad autorizado; no se afirma equivalencia de costo/rendimiento. Las tareas están en `[tasks]` de pipeline.toml. El `[models]` histórico deja de resolver las nuevas llamadas. La cuota ChatGPT no es facturación por tokens API ni recibe descuento Batch.

Perfil dedicado en `.codex/translator.config.toml`, instalado como `$CODEX_HOME/translator.config.toml` sin modificar config.toml personal ni credenciales. Solo lectura, aprobación never, web disabled, herramientas/skills/plugins/apps/subagentes desactivados; MCP heredado node_repl desactivado. No se usan opciones para ignorar políticas de seguridad.

Se comprobó con `codex -p translator -C <sandbox> debug prompt-input` que no aparecen las reglas específicas de AGENTS del proyecto, el catálogo translate-native-pages ni el bloque de skills. Permanecen instrucciones genéricas de Codex (~37.591 bytes en esa inspección): su sobrecarga se reflejará en los tokens medidos. Cambiar solo cwd no bastaba, porque work tiene ancestros dentro del repo; project_doc_max_bytes=0 y las exclusiones de skills evitan esa herencia. Este diagnóstico de contexto no sustituye comprobar ausencia de eventos de herramienta durante las pruebas reales.

Fuentes consultadas: [SDK oficial Codex](https://learn.chatgpt.com/docs/codex-sdk), [referencia CLI](https://learn.chatgpt.com/docs/cli/reference), y código de los paquetes instalados.

### Cierre de implementación de Fase 1b

Verificación independiente: 55 tests pytest pasan; esquemas y TOML válidos; original intacto y aprobación G3 vigente. `llm.py` es la única frontera de proveedores, con tres backends, caché SQLite por bloque/petición, agrupación por página, validación y pausa 75. El agotamiento de cuota en el acortado se propaga al lote, y QA conserva la identidad de página para su registro y pausa. Se deshabilitan también los MCP declarados en configuraciones de ancestros del sandbox.

Cinco páginas reales traducidas con SDK ChatGPT, JSON e invariantes válidos al primer intento. La reinserción de tres páginas necesitó acortado: nueve respuestas rechazadas por longitud, sin aceptar resultados inválidos. Se añadió comprobación local de términos obligatorios que por sí solos exceden el límite. No se alteró el glosario aprobado ni se declaró un PDF traducido/QA completado. Evidencia y métricas completas en `docs/informe_backend.md`.

Se adopta como implementación la ruta propia PyMuPDF por página. La interfaz inyectable de BabelDOC 0.6.4 no garantiza esa unidad; su planificador real, sobre IR controlado de cinco páginas, produjo 7/7/9/8/14 lotes frente a una llamada por página de la ruta propia. No se midió el frontend completo ni el ratio de latencia 3×. Esa limitación y la aceptación de esta decisión se presentan al humano en G1b, sin convertir la instrumentación parcial en una comparación completa.

Uso real acumulado de capa B: 132.257 tokens de entrada, 6.438 de salida; cero cargos API, con consumo de suscripción. La proyección de una sola pasada nativa sobre 258 páginas es aproximadamente 2,74 millones de tokens y 97,72 minutos acumulados de llamadas, sin OCR, acortado ni QA. No se puede inferir el porcentaje de cuota restante. **G1b sigue pendiente con limitaciones abiertas; G4 y producción no se ejecutaron.**


## Decisión humana G1b parcial y Fase 1c

El humano aprobó expresamente **codex_sdk**, gpt-6-luna/low para tareas baratas, gpt-6-astra/high para fuertes, fallback=none y la ruta propia PyMuPDF. Dispensó la comparación de latencia 3× con BabelDOC: considera suficiente que el planificador instrumentado produzca 7–14 lotes por página y cada llamada Codex cargue aproximadamente 7k tokens fijos. Se registra como justificación y dispensa humana, no como un ratio extremo a extremo medido.

Se creó `books/astrology-wisdom-stars/work/backend_approval.json` con **alcance = "backend y ruta"**, exclusiones maquetación/acortado/G4 y fases autorizadas limitadas a `reinsertion`. El hash de configuración completa conserva la instantánea; un hash adicional de proveedor/modelos/esfuerzos/ruta vincula el alcance autorizado sin confundir cambios de maquetación con cambios de backend. No hay autorización para el piloto.

Fase 1c autorizada exclusivamente sobre páginas 7, 15 y 121: texto corrido agrupado por párrafo, insert_htmlbox con mínimo 0,75; etiquetas y texto que intersecan dibujos visibles se redactan por span. La escala depende de cada bloque, no de la página completa. Los marcos de imprenta sin trazos dentro de CropBox no cuentan como dibujos visibles. Se conserva el fondo raster inmutable, imágenes=NONE y sin relleno de redacción; se verifican diferencias fuera del texto editado.

Orden de ajuste de etiquetas: ampliar hacia espacio libre, forma corta aprobada, LLM como último recurso. El CSV G3 no se modifica. Las propuestas viven en `glossary/astro_en_es_short_candidates.csv` con nueva columna short_target y requieren aprobación separada; el usuario corrigió y aprobó después 10 formas finales en `glossary/astro_en_es_short.csv`, con aprobación separada `work/shortforms_approval.json`. Solo etiquetas de figuras, tablas y leyendas; nunca texto corrido. Se rechazó «c. natal», conservando «carta natal». La propuesta inicial no está autorizada. Para cadenas menores de 15 caracteres el presupuesto se calcula desde el espacio útil, tamaño de fuente y traducción completa, sujeto al glosario, en lugar de aplicar 1,10 a la longitud inglesa. Esto no garantiza que la etiqueta quepa y no autoriza eliminar significado.

La agrupación translate_page ahora puede incluir varias páginas hasta 6000 caracteres, ids con página y costos repartidos sin duplicar el uso. Registrar uso total y `usage.last.cached_input_tokens`; el ahorro por caché del proveedor debe medirse, no inferirse de la caché local SQLite.

El criterio de cierre 1c es cero spans fallidos, escala >=0,75 y visual-qa ejecutado en las tres páginas. No equivale automáticamente a QA aprobado o G4. Para el futuro piloto se exige una tabla de efemérides real y una página escaneada de Ruta C; su selección se preparará sin ejecutar el piloto.


### Cierre de Fase 1c

Muestra de páginas 7, 15 y 121 reinsertada con cero fallos; escalas mínimas 0,7522 / 0,7570 / 0,8179. QA local ejecutado: conservación de glifos/cifras y de todos los caracteres protegidos con fuente y coordenadas; diferencia fuera del texto =0 en las tres. Se corrigió una eliminación lateral de letras por métricas de redacción más amplias que las cajas de extracción, incorporando bandas comprobadas contra cajas reales y regresión específica. Solo se utilizó «Desc.» del suplemento aprobado.

75 tests pasan en verificación independiente; muestra final renderizada con Poppler e inspeccionada. Sigue `review` por inglés residual en spans conservados, observaciones terminológicas y VLM/revisión humana pendientes. Cerrar el criterio geométrico de 1c no aprueba G4. El usuario ordenó detenerse aquí y no se ejecutó el piloto.

Una llamada agrupada de 3.153 caracteres consumió 11.128 tokens de entrada (11.008 cached_input_tokens del proveedor), 2.483 de salida y 123,09 s. Antes: 3 llamadas, 28.539/3.131 tokens y 64,86 s. Entrada 61,01 % menor; latencia mayor. No se conoce la caché histórica ni se extrapola ese porcentaje al límite de suscripción. Ajustes posteriores exclusivamente locales y sin más inferencias.

Informe: `docs/informe_reinsercion.md`; artefacto: `books/astrology-wisdom-stars/out/reinsercion_1c.pdf`; cierre: `work/reinsertion/phase_1c.json`. Futuros casos de piloto preparados en `docs/seleccion_piloto.md`, sin ejecutar ni incorporar la fuente externa al libro.


## Fase 1d — Fidelidad, nueva instrucción humana

El humano aceptó únicamente el criterio geométrico de 1c y **no aprobó G4**. Autorizó Fase 1d sobre 7/15/121 con revisión visual humana final. Se extendió `authorized_phases` a `fidelity`, sin aprobar piloto, producción ni calidad final.

La protección pasa de spans completos con cifras a tokens excluidos del LLM mediante {nN}; se restauran localmente sin cambiar sus valores. La prosa circundante se traduce y se recompone, por lo que las cifras de esa prosa pueden acompañar su nueva composición. Los spans de símbolos permanecen íntegros, y las siglas aisladas MC/IC se conservan. Las fechas se traducen determinísticamente, manteniendo números e intervalo, en formato «21 de marzo–20 de abril».

Se revoca para esta fase la prohibición anterior de reutilizar fuentes incrustadas: se extraen y se verifica has_glyph sin fallback implícito. Cobertura para un grupo se evalúa sobre la unión de sus textos, para que palabras idénticas como Aries/Leo no tengan otro estilo que sus vecinos. Si no alcanza, toda la familia/grupo relevante utiliza un reemplazo consistente en serif/sans, ancho, peso y cursiva. La inspección local de las fuentes SohoPro revela ausencia de Í y otros acentos españoles; la fuente Helvetica presente sí tiene cobertura, pero no es sustituto estilístico serif. Inventario en work/fidelity/font_inventory.json. No se modifican ni se usan las fuentes de símbolos para traducir texto.

Se registrarán grupos de escala y decisiones de fuente; se mantienen colores y énfasis. Unidades distintas para estilos de título/cuerpo, preservando énfasis inline cuando pertenece al mismo párrafo. La cita de p15 se unirá pese a huecos de extracción y compartirá traducción con la frase repetida en el cuerpo. Las llamadas seguirán agrupadas hasta 6000 caracteres, con caché y registro de uso.

Cierre pendiente: cero inglés fuera de tokens protegidos, títulos con peso/color original, escala uniforme por grupo >=0,75, cero fallos y diferencia fuera de texto=0; comparativas visuales original|traducción. Ni la creación de esta fase ni sus tests sustituyen aprobación humana.

### Excepción visual explícita de 1d

El usuario confirmó que las letras decorativas «PLANETS» de la ilustración raster de página 7 se conserven como arte original. Se exceptúa únicamente esa palabra integrada en la ilustración del criterio de inglés residual; no fechas, etiquetas, prosa ni otros textos. Registro: `work/fidelity/artwork_exception.json`. No se modificará esa imagen.


### Cierre técnico de Fase 1d

Páginas 7/15/121: cero fallos; escalas mínimas 0,95 / 0,91 / 1,00 y uniformidad por grupo; inglés editorial detectado=0; diferencia fuera del texto=0. Imágenes y dibujos idénticos, protección granular y spans simbólicos verificados. Ambas etiquetas Ascendente/Descendente completas, sin acortado. Doce fechas locales; cita/body de p15 con dos repeticiones exactas de cada cláusula, cita anclada a escala 0,93. Í comprobada visualmente.

109 tests pasan; auditoría independiente del PDF final verifica color, tamaño e instancia de fuente por carácter. Poppler renderizó la comparativa y se inspeccionaron sus tres páginas. Fuentes de reemplazo instanciadas por peso/estilo y calibradas al ancho original, nombres únicos y GSUB desactivado para evitar ligaduras con ToUnicode incorrecto; fuentes y licencia verificadas en fonts/provenance.json. No se modificó original ni glosario.

Una llamada real de 1d: 12.255 entrada, 3.427 salida, cached_input_tokens=0, 64,06s; sin inferencias adicionales al ajustar layout. Acumulado 155.640/12.348, sin API de pago. QA mantiene revisar: VLM no ejecutado y revisión visual humana pendiente. Se cierra el trabajo técnico solicitado y se detiene; **no G4 ni piloto**. Archivos: docs/informe_fidelidad.md, out/fidelidad_1d.pdf, out/fidelidad_1d_comparativa.pdf y work/fidelity/phase_1d.json.


## Autorización humana actual: correcciones y piloto20

El usuario aprobó G4 con condiciones y autorizó ejecutar el piloto después de corregir negrita de signos p7, medianil y párrafos p15, y concordancia de Nodos p121. No se abre otra fase. Se aprueba la redacción final de dos adiciones a la guía G3: correcciones gramaticales objetivas registradas con el original, sin cambiar contenido/datos/estilo; comillas inglesas “ ” y simples ‘ ’ para citas internas. Historial y nuevos hashes en work/approval_history y glossary_approval.json. Selección en docs/seleccion_piloto.md; anexo Mercurio separado. Intentar juez VLM con exclusiones seguras y declarar cobertura real. Entregar veinte comparativas, consumo frente a proyección e incidencias; detenerse para revisión visual del piloto, sin producción. Esta autorización reemplaza las prohibiciones históricas de ejecutar el piloto, no autoriza producción.

## Cierre del piloto20 autorizado con condiciones

Se incorporó la redacción final humana de ambas reglas de estilo, con aprobación G3 por nuevo hash e historial de la guía previa. Se corrigieron negritas de p7, medianil y sangría de p15, y concordancia de Nodos en p121. La ejecución posterior abarcó veinte páginas seleccionadas; la portada raster real prueba Ruta C parcialmente y la tabla real de ingresos de Mercurio se entrega como anexo técnico externo, sin insertarla en Taylor.

El juez VLM ya funciona sobre copias diagnósticas de texto nativo con tinta protegida enmascarada. No recibe las ilustraciones, vectores, colores ni valores protegidos; su cobertura y exclusiones se declaran por página. Sus falsos positivos y omisiones impiden usarlo como única gate de producción. Se complementa con verificación de fuentes, conservación de gráficos, protección numérica y guardas de tinta nueva sobre obstáculos raster. Quedan etiquetas curvas inglesas y decisiones editoriales pendientes, detalladas en docs/informe_piloto.md.

El ensamblado de la muestra usa pypdf6.21.4 para preservar /Group y formularios de transparencia: la unión mediante insert_pdf perdía el grupo de la página69 y alteraba el color al renderizar. La decisión de PyMuPDF bajo AGPL permanece intacta; pypdf es un complemento del ensamblado. Se verifican veinte renders ensamblados contra sus candidatos a110dpi y se inspeccionan comparativas Poppler independientes.

No se interpreta el éxito geométrico como QA editorial completo. El manifest y review_queue conservan las veinte páginas en revisión, con incidencias concretas y aprobación visual humana pendiente. No se inicia producción ni se crea autorización ficticia. El consumo se obtiene de costs.jsonl, incluidos reintentos conocidos, caché SQLite y usage.last.cached_input_tokens; los intentos sin uso expuesto siguen siendo desconocidos.
