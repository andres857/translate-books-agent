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

### Ordinales — The Secrets of Astrology

Incidencia I3 de Fase 1: regla **determinista y local, pendiente de confirmación humana en G4**. No modifica `glossary/`, la guía G3, la configuración ni aprobaciones. Se aplica exclusivamente mediante el contexto de composición de este libro, después de validar las traducciones y los overrides guardados. La extracción, sus ids y sus hashes de origen siguen iguales; no se rebasean ni se invalidan traducciones válidas para esta corrección.

- `THE {n}{suf} SIGN OF THE ZODIAC` se compone como `EL {n}.er SIGNO DEL ZODÍACO` para 1 y 3; para los demás, `EL {n}.º SIGNO DEL ZODÍACO`. Se conserva la tilde en **ZODÍACO**. El sufijo español conserva tamaño, peso, color y origen del sufijo inglés. Se usa la fuente original cuando cubre el texto; para `º`, ausente de SohoPro-Bold, se usa el reemplazo equivalente de I1, sin cambiar la fuente de la cifra. El prefijo y el texto posterior se colocan con un solo espacio, anclados a la cifra y al sufijo.
- En títulos `{n}{suf} HOUSE`, la salida es `CASA {n}`: CASA usa el estilo de HOUSE. La cifra se desplaza después del espacio. El span numérico se copia del PDF original, sin volver a escribir sus glifos: conserva valor, fuente, tamaño y color. En los títulos de signo conserva también sus coordenadas y tinta. Los encabezados se componen a escala 1; si no caben o invaden otros elementos, se bloquean en vez de reducir la cifra.
- Otros ordinales: `.er` para 1/3 masculinos, `.º` para los demás masculinos y `.ª` para femenino solo con concordancia inequívoca. Los ya resueltos como «casa N» se conservan. No se infiere género de ordinales aislados sin contexto; quedan protegidos y pendientes. Los ocho ordinales gráficos de p173 siguen protegidos y diferidos a Fase 4.
- QA local valida el encabezado contra los spans originales y el texto, fuente, tamaño y color efectivamente extraídos del candidato antes de admitir la sustitución del sufijo. La cuenta global de cifras sigue siendo estricta; solo se admite el `º` añadido por un encabezado verificado. VLM sigue ocultando las cifras y los ordinales ingleses conservados; `.er/.º/.ª` son morfología editorial, no valores protegidos alterados. Los símbolos `º/ª` aislados siguen enmascarados. Estas pruebas no constituyen un juicio VLM ni aprobación visual.

Inventario nativo de las veinte páginas del piloto (24 apariciones, contrastado con las traducciones guardadas):

| Página | Apariciones | Tratamiento |
|---|---:|---|
| 34 | 1 (`1ST`) | Encabezado local: `EL 1.er SIGNO DEL ZODÍACO`. |
| 60 | 1 (`7TH`) | Encabezado local: `EL 7.º SIGNO DEL ZODÍACO`. |
| 141 | 5 (`1st/1ST`) | Encabezado, prosa y rótulo ya resueltos como «casa 1»; conservar. |
| 154 | 8 (`7th`) | Título local `CASA 7`; otras siete apariciones ya resueltas como «casa 7», incluidas cinco elipsis de «house». |
| 173 | 9 | Pie `4th house` ya resuelto como «casa 4». Ocho rótulos del diagrama: `3rd, 4th, 5th, 6th, 7th, 8th, 9th, 10th`, todos **texto nativo**, conservados para Fase 4. |
| 1, 5, 19, 28, 35, 69, 86, 87, 100, 101, 128, 169, 175, 179, 180 | 0 | Sin ordinales nativos con sufijo inglés detectados. |

No quedan otros ordinales ingleses pendientes por concordancia en los candidatos guardados. El inventario es de texto nativo; no presupone cobertura OCR de imágenes. No hace falta retraducción ni `--stage retranslate --units` para I3. Recomponer **34, 60 y 154** usando las respuestas existentes, manteniendo las reparaciones de tabla de 34/60 y su procedencia.

Comandos para la ejecución posterior desde la raíz (no ejecutados como parte de la implementación de I3):

```bash
.venv/bin/python books/the-secrets-of-astrology/work/pilot/rebuild_pilot.py --pages 34,60,154 --stage compose
.venv/bin/python books/the-secrets-of-astrology/work/pilot/rebuild_pilot.py --pages 34,60,154 --stage qa-local
.venv/bin/python books/the-secrets-of-astrology/work/pilot/rebuild_pilot.py --pages 34,60,154 --stage assemble
```

`assemble` recompone el paquete de veinte páginas según su contrato existente. Estos comandos son locales, no ejecutan la etapa `vlm`; las comparativas actualizadas y la confirmación editorial siguen pendientes de revisión humana en G4. Las pruebas I3 componen copias temporales de los encabezados reales; no reemplazan los candidatos ni la comparativa publicada del piloto.

## The Secrets of Astrology — decisiones delegadas de Fase 1 (Claude, delegación explícita de andres, 2026-11-06/07)

Registro de decisiones tomadas por Claude como orquestador bajo la sección «Autorización humana vigente (2026-11-06)» de AGENTS.md. Evidencia en `books/the-secrets-of-astrology/work/pilot/` (i*-delivery.txt, i*-evidence/, history/, rebuild-log.json).

1. **Fuente de reemplazo (I1).** SohoPro es slab serif; sin cobertura de acentos se usa RobotoSlab[wght] (Apache-2.0, fonts/provenance.json) instanciada al peso y calibrada en ancho. Itálicas slab siguen con RobotoSerif-Italic (limitación). Activado por libro (`classify_slab`), sin cambio para astrology-wisdom-stars.
2. **Orden de cuantificadores (I2).** Validador `numeric_order` rechaza «sustantivo + cifra» cuando el origen es «cifra + sustantivo»; la unidad se retraduce vía run_task. Retraducción correctiva por unidad (`--stage retranslate`), nunca por página completa.
3. **Ordinales nativos (I3).** «1.er/3.er» y «N.º» para signos; «CASA N» para títulos de casa; cifra original intacta. Confirmado como regla del libro. Ordinales gráficos del diagrama p173 → Fase 4.
4. **Listas con título en línea (I4)** y **etiquetas entre valores protegidos (I5).** Escala uniforme por columna/tabla; se aceptan escalas 0,76–0,80 con marca de revisión cuando el espacio no permite más (p179 0,76).
5. **Calidad textual (I6).** Repeticiones, tildes en mayúsculas, «zodíaco», estilo inline y espacio ante glifo se controlan en QA; los defectos de redacción se retraducen vía run_task. Términos extranjeros citados en cursiva e idénticos al original (p. ej. *plouton*) se conservan como `quoted_foreign_term`, no cuentan como inglés.
6. **Segmentación de leyendas.** Las líneas de una misma leyenda se unen antes de traducir (`--stage resegment`); solo se traducen las unidades unidas.
7. **p169 (I7).** Sin acortar: títulos pueden envolver y crecer hacia espacio libre; escala 1,00. No se aprobó ninguna forma corta.
8. **Texto curvo/raster** (portada, banda SHINE|NURTURE p86, rueda PISCES/ARIES p28, ordinales gráficos p173, 18 escaneadas) → Fase 4.

9. **p173, regresión I8 (2026-11-07).** El crecimiento conserva como mínimo el ancho de la unidad original y de sus límites de columna explícitos. Se prueba la caja original a escala 1 y los ensanches con hasta 1 pt de ajuste de interlínea antes de aceptar altura adicional; después se conserva la búsqueda I8 para huecos estrechos. No se recorta ninguna caja para esquivar guardas: una colisión bloquea el candidato. Causa comprobada: la búsqueda anterior recorría todas las alturas para cada ancho; aceptaba el título IC estrecho en dos líneas y el cuerpo en tres líneas en la reproducción local, antes de probar los ensanches que conservaban una/dos líneas. La caja geométrica no se había reducido respecto de `bbox`: el ancho efectivo elegido era menor que el de la composición anterior a I8. Con la corrección, IC usa 68,803 pt de ancho para el título (+16 pt sobre `bbox`) y 151,476 pt para el cuerpo (+8 pt), sin crecer en alto. MC conserva una/dos líneas y CLAVE una. La escala conjunta de títulos sigue en 0,99 por Descendente y su guía gráfica; no se rompe el grupo para forzar 1,00. Los ajustes individuales de IC/MC/CLAVE caben a 1,00. Evidencia local, sin recompilar el piloto: `work/pilot/i8-p173-evidence/scales.json`, pruebas reales en `tests/test_caption_growth.py` y comparativa de una copia temporal. Ninguna aprobación de página ni juicio VLM.
10. **Ordinales nativos de casas (decisión editorial comunicada en el encargo del 2026-11-07).** `approved_by: "claude (delegación explícita de andres, 2026-11-06)"`. Los ordinales sueltos que identifican casas en el diagrama se componen en femenino: `3.ª` a `10.ª` en p173. Esta decisión sustituye el aplazamiento de estos ocho rótulos en los puntos 3 y 8 y en el inventario histórico de I3. La detección es por patrón, sin número de página: cohorte de cifras/sufijos superíndices separados, distribución bidimensional, contexto de casas, ejes MC/IC y gráficos. Los casos sin contexto inequívoco siguen protegidos. Cada cifra copia el operador nativo original, conserva span, fuente, tamaño, color, posición y tinta a 220 dpi; `.ª` usa el tamaño, peso, color y origen del sufijo original, con fuente original si tiene cobertura o reemplazo equivalente. Escala 1 obligatoria. QA contrasta procedencia, texto, estilos, posición del superíndice y tinta de la cifra, incluida `10`; no exime la cifra del control de inmutables. Las máscaras VLM siguen ocultando todas las cifras y sufijos ingleses; reconocen morfología española entre spans adyacentes, sin descubrir fuentes protegidas ni `º/ª` aislados. La política de máscaras forma parte del hash de caché. Evidencia: `tests/test_native_ordinals.py`, `work/pilot/test_ordinal_masks.py` y `work/pilot/i8-p173-evidence/`. Se registra aquí la decisión ya recibida; no se escriben archivos `*approval*.json` por prohibición expresa del encargo. Sin LLM, sin producción y sin ejecutar `rebuild_pilot`.

### G4 delegada y ampliación G1b → production (2026-11-07)
Claude, por delegación explícita, aprueba el piloto de 20 páginas con condiciones (registro: `books/the-secrets-of-astrology/work/pilot_approval.json`, copia en `work/approval_history/G4_delegated.json`) y amplía `authorized_phases` de G1b con `production` (copia previa en `G1b_before_production.json`). Base: revisión visual completa de `out/piloto_comparativa.pdf` tras I1–I8 y D1/D3; 19/20 páginas incluidas, QA local 17/20 (p1, p28, p86 → Fase 4). Condiciones: Fase 2 antes de producción; Fase 4 antes del ensamblado final; escalas 0,76–0,82 aceptadas con revisión. D2 («zodiaco» en cabeceras) fue falso positivo de la revisión a baja resolución: el texto ya dice «zodíaco».

### Fase 2C — reglas editoriales delegadas comunicadas en el encargo (2026-11-07)

Se implementan las reglas recibidas de andres para la corrección general del lote 1. Este registro no concede una gate ni modifica la guía G3, el CSV original o archivos de aprobación. No se ejecutan llamadas LLM en esta fase de implementación.

- **Obras publicadas citadas:** una atribución junto a una cita destacada, con patrón autor + título y subtítulo, se conserva como texto nativo. Se excluye antes de la traducción y de la redacción PDF; así conserva también la itálica que pudiera estar codificada en la matriz. El caso comprobado es la atribución de Kim Farley en p9. No se extiende la excepción a prosa editorial sin atribución verificable.
- **Créditos:** `proper_name` exige identidad exacta entre fuente y candidato, capitalización y patrón de nombre/entidad, dentro de una página reconocida por varios roles editoriales. Direcciones, correo, URL e ISBN tienen motivos propios y estrechos. Cada fragmento exceptuado queda en `language_exceptions` del QA; no se exceptúan párrafos completos ni nombres traducidos. La palabra «Copyright» fuera de esos nombres sigue señalada como inglés residual y requiere retraducción.
- **Comillas G3:** la composición normaliza de forma determinista a “ ” y, para citas internas, ‘ ’, manteniendo los runs. QA marca comillas angulares o rectas en el texto traducido. Los títulos publicados conservados como original no se recomponen.

**Evidencia técnica:** `docs/historico/fase2c_correcciones.md` y `books/the-secrets-of-astrology/work/phase2c/`. Las comparativas son diagnósticas, no aprobaciones. La corrección de flujo entre columnas mejora p19 y p87 del piloto; se guardan sus nuevas comparativas y QA aparte, sin sustituir los candidatos ni el golden aprobado. La unidad con inglés residual de p4, la cita completa de p9 y el texto nativo de p11 quedan preparados para `llm.run_task`; no se redacta ninguna traducción nueva manualmente.

## The Secrets of Astrology — lote 1 tras Fase 2C (2026-11-07)

approved_by: "claude (delegación explícita de andres, 2026-11-06)"

- Fase 2C revisada: flujo de texto por cadenas de columnas (`column_flow.py`), índice con folios fijos, cita p9 en una sola petición, VLM con máscaras en producción, reglas editoriales (títulos publicados en idioma original, nombres propios en créditos, comillas “ ”). Evidencia: `books/the-secrets-of-astrology/work/phase2c/evidence/`.
- Retraducción de 4 unidades (p4, p9, p11) vía `llm.run_task` (2 intentos, 1 validation_error; costs.jsonl).
- Recomposición `produce --pages 1-20 --resume --recompose` (batch-02.json). Revisión visual delegada lado a lado de p3, p5–p7, p9, p12–p20: aceptadas (`review-visual`). Escala de cuerpo 0,85–0,89 (RobotoSlab Light, cuerpo 11 pt → 9,4–9,8 pt), dentro del mínimo general.
- p3: escala 0,78 en título display (sustituto sans pesado); aceptada con advertencia tras revisión visual.
- Excepción editorial: «Copyright ©» en créditos (p4) se conserva como mención legal habitual en ediciones en español; «DK Publishing Special Markets» es nombre propio de departamento.
- Pendiente lote 1: p11 (excepción de composición «No isolated point for selected source glyph») y p4 (excepción Copyright) → Fase 2D. G5 lote 1 provisional 14/16 = 87,5 %; se reevalúa tras 2D.

### Fase 2D — mención legal en créditos (2026-11-07)

`approved_by: "claude (delegación explícita de andres, 2026-11-06)"`.
Decisión editorial recibida en el encargo: conservar la mención exacta
**«Copyright ©»** como excepción lingüística estrecha en los créditos de
`books/the-secrets-of-astrology`, página PDF 4. Sustituye únicamente la instrucción
histórica de Fase 2C de retraducir esa mención. No exceptúa «copyright» aislado,
las condiciones legales que la rodean ni otros libros/páginas.

Implementada en `work/profile.toml`, sección `[[language_exceptions]]`, código
`legal_copyright`, página 4 y contexto `credits`. El perfil está ligado al hash
del original; QA exige la frase exacta tanto en fuente como en candidato y
reconoce la página por múltiples roles editoriales. La excepción queda registrada
en `language_exceptions` del QA. No se modifica G3 ni se crea un archivo de
aprobación: el encargo los prohíbe expresamente.

SHA-256 del perfil tras esta decisión:
`b7b452d7421def25635a94aec2ab0f1609d7de56be7089f1a8f5b8500b969745`.

Evidencia: `tests/test_phase2d.py::test_real_credits_copyright_exception` recompone
la respuesta guardada y verifica QA local satisfactorio en un temporal;
`test_copyright_exception_is_exact_book_page_and_credit_scoped` verifica los
límites de la excepción. Fixtures y hashes en `tests/fixtures/phase2c/`;
informe reproducible en `docs/historico/fase2d_correcciones.md`. Sin inferencia LLM/VLM ni
recomposición de los artefactos activos de producción.

## 2026-11-07 — Fase 2G, encargo de código offline

Por instrucción explícita de andres se reemplaza el wire de parejas de énfasis
por traducción plana y anotaciones de substrings. Las parejas/estilos originales
permanecen locales; el nuevo contrato se define en código, sin editar prompts,
schemas ni aprobaciones. Se implementa el escalado de validación a
`gpt-6-astra/high` solicitado, con procedencia y costos en la ejecución futura.
No se hizo ninguna llamada real, `produce` ni commit en este encargo.

Se acota el crecimiento al margen editorial original, se comprueba la tinta en
QA y se añade detección de aperturas españolas ausentes. Las correcciones
inequívocas son registros `grammatical_correction` ligados al original; las
ambiguas se preparan para retraducción. Se conserva la política G3 original.
Detalles, cambios geométricos declarados y comandos en `docs/historico/fase2g_enfasis.md`.
Esta entrada no es una aprobación nueva ni amplía permisos de producción.

## Cambio de modelo Codex por indisponibilidad (2026-11-07 16:18 UTC)
approved_by: "claude (delegación explícita de andres, 2026-11-06)"
- El catálogo de modelos de la cuenta ChatGPT (`~/.codex/models_cache.json`, fetched_at 2026-11-07T16:18:53Z) ya no incluye `gpt-6-astra`; Codex responde «The 'gpt-6-astra' model is not supported when using Codex with a ChatGPT account». Disponibles: gpt-6-luna, gpt-5.6-terra, gpt-5.6-luna.
- No es agotamiento de cuota (gpt-6-luna sigue respondiendo en la capa B), así que no es causa de parada. La implementación de código (Codex rescue) continúa con `gpt-6-luna --effort xhigh`, con revisión de diffs más estricta por Claude.
- Capa B: G1b solo aprueba luna/low y astra/high. No se aprueba otro modelo; las escaladas a astra/high fallarán y esas unidades quedan en revisión. Si astra vuelve al catálogo se reanuda su uso.

## Disco lleno durante el lote 9 (2026-11-07)
approved_by: "claude (delegación explícita de andres, 2026-11-06)"
- El sistema quedó al 100 % (740 MB libres); p174/176/177 fallaron con «No space left on device». Causa: cada candidato `p*-translated.pdf` es una copia completa del libro (~65 MB) y las recomposiciones de diagnóstico 2C/2E/2F duplicaban esas copias.
- Se eliminaron 121 PDF de diagnóstico (>10 MB) de work/phase2c, phase2e y phase2f, regenerables con scripts/recompose_production.py; se conservan PNG de evidencia, JSON, hashes e informes. Lista en el scratchpad de la sesión (deleted_pdfs.txt). Disco: 7,8 GB libres.
- Corrección pendiente (Codex): guardar candidatos de una sola página y que el ensamblado final los inserte.

## Fase 2I — almacenamiento, costos y política ordinal (2026-11-07)

Implementación offline, sin modificar artefactos activos de `work/production`, aprobaciones, glosario, configuración ni originales.

- Los nuevos candidatos de producción se reducen a una página después de terminar sus capas de composición; se mantienen recursos/fontes incrustados y se guardan con garbage collection y compresión. Los consumidores reciben `output_page=0`. `assemble-final` toma candidatos QA completos y candidatos nativos `partial_deferred`; páginas `deferred_raster` permanecen desde el original. La comparativa se compone por página sin concatenar PDFs completos por candidato.
- `scripts/migrate_single_page_candidates.py --book books/the-secrets-of-astrology` convierte legados idempotentemente. Antes de reemplazar cada archivo compara su render a 220 dpi píxel a píxel; registra `old_sha256 → new_sha256` en `work/production/candidate_hash_migrations.jsonl`, actualiza hashes de `fit`, QA, revisiones visuales y checkpoints equivalentes. La evidencia VLM se conserva porque el render y el origen son iguales. El piloto conserva su estructura completa histórica: su golden aprobado y rutas de evidencia dependen de ella; su tamaño (20 muestras) no causa el agotamiento de producción.
- `cost_usd=0` de llamadas `codex_sdk` no-cache históricas se presenta como desconocido; `costs.jsonl` es de solo anexado y no se reescribe. Cache hits locales de costo cero siguen siendo costo cero conocido. Los resúmenes de lote, proyección de cuota e informe final aplican esta normalización.
- `native_ordinals.py` mantiene el mecanismo de adaptación/render/QA. Las reglas MC/IC de Secrets viven en `secrets_ordinal_policy.py`, ligadas al SHA-256 exacto de su original (`517746c22d1897c06c6f5efde58564956c4389321f72b068494a25c5e7858a9c`). El conjunto de golden debe comprobar que el resultado no cambia.
- La configuración en `.codex/` quedó obsoleta: perfiles de agentes y skills stub todavía apuntan a `gpt-6-astra`, retirado del catálogo de la cuenta el 2026-11-07. No se editó `.codex/`. El cambio de modelo de capa B y los fallos de escalada se rigen por la decisión previa de cambio de modelo.

Ejecución de migración limitada a un directorio temporal con tres copias; el libro real se deja para el comando explícito anterior. Comandos, cobertura y tamaños medidos se registran en [`docs/historico/fase2i_almacenamiento.md`](fase2i_almacenamiento.md).

## G1b: backend `claude_cli` aprobado (2026-11-08)

- **Aprobación:** andres («si apruebo»). Es una aprobación humana directa, no delegada.
- **Backend:** `claude_cli` (`claude -p` oficial, suscripción OAuth).
- **Modelos:**
  - Sonnet 5.5: `low` para traducir y etiquetar; `medium` para el juez y el glosario.
  - Escalada: `opus`/high.
- **Sustituye a:** `codex_sdk`, cuya suscripción se perdió el 2026-11-07.
- **Evidencia:** smoke real de texto e imagen, `tests/test_claude_cli.py` y suite completa con 696 tests en verde. Ver `docs/workflow_claude.md`.
- **Configuración:**
  - `books/the-secrets-of-astrology/work/pipeline.toml` pasa a `claude_cli` y conserva las rutas de su glosario G3, que no cambian.
  - `backend_config_sha256`: `f41f09cc…196b`.
  - Copias de lo anterior en `work/approval_history/*.codex_sdk.2026-11-08*`.
- **Consecuencia:** G4 (`pilot_approval.json`) queda obsoleta por el cambio de config, y es lo correcto: el piloto se aprobó con gpt-6-luna. Antes de seguir con la producción, hay que repetir el piloto con Sonnet y pasarlo por el `book-auditor`. Las páginas piloto propuestas están en `work/structure/structure.md`.
- **Pendiente:** astrology-wisdom-stars y mercury-technical-annex usan `config/pipeline.toml` y no tienen aprobación para `claude_cli`. No se registra hasta que se retomen.

## 2026-11-08 — Gaps 8, 9 y 11 de `the-secrets-of-astrology` (pipeline-engineer, sin LLM)

`approved_by` de la política editorial del índice: «claude (delegación explícita de andres, 2026-11-06)», decisión del orquestador comunicada en el encargo. El resto son correcciones técnicas generales.

- **Triage v2 (G-ROUTE).** `core.classify_page`: una página con raster a página completa solo es escaneada (ruta C) si además no tiene texto nativo visible (≥8 letras dentro de la caja de página, sin modo 3 invisible, sin fuentes de símbolos). Las líneas de imprenta fuera de la caja no cuentan. Las aperturas 11/25/85/137/167 pasan a `mixed`/A; las 13 ilustraciones siguen en C. En wisdom-stars solo cambian sus aperturas y portadillas con texto. `triage` ya no reescribe estados si cambia la configuración: fusiona. **No se reescribió el manifest real.** Re-triaje seguro: `.venv/bin/python -m astro_translate retriage --book books/the-secrets-of-astrology` (simulación) y, revisado el resultado, añadir `--apply`. Solo toca `type`, `route` y `stages` (añade rutas nuevas como `pending`) y deja `retriage_history`.
- **Cintas A13 (G-DETECT).** Cada glifo lleva su propio ángulo (de +2,8° a −0,6°). Se aceptan como etiqueta recta, a lo largo de la cuerda, las series en orden de contenido con desviación angular ≤3,5°, ≤0,06°/pt y residuo de línea base ≤0,06 em. Las ruedas 26–31/177 y el trayecto de p175 siguen como curvas. Los separadores `|` no se inventarían. `native_redaction` verifica la geometría retenida por glifo (texttrace), no por la agrupación de líneas de rawdict.
- **Leyendas de constelación.** `chart_callouts.mark_figure_legends`: texto de ≤10 pt, todo en cursiva y alcanzado por una línea guía (trazo abierto de 1–2 segmentos, también discontinuo). `column_flow` excluye `figure_legend` de las historias de columna, con un cambio mínimo de enrutamiento. Afecta exactamente a 35, 39…81 (cada 4), 47 y 73.
- **Survey v2.** Usa el clasificador vigente y marca `retriage_pending`. Incluye además:
  - tablas: filas clave/valor repetidas con valores protegidos (p179) y filas de glifos con rótulo (p86, 87, 180, 182);
  - citas display de una sola unidad, con comillas vectoriales (p13 y p19);
  - etiquetas `index`/`glossary`;
  - OCR local PaddleOCR (`enable_mkldnn=False`; oneDNN falla en este equipo) para rasters sin texto nativo. Si el OCR no está disponible, se marca `None`, nunca `True`.
- **Índice y glosario (G-INDEX), `book_index.py`.**
  - **Detección:** es estructural. El índice ocupa 188–191; p192 («About the author») no es índice. El glosario ocupa 186–187.
  - **Traducción:** solo términos, rótulos y destinos de remisión, únicos, vía `run_task` (`translate_labels`). Las definiciones del glosario van por `translate_page`. Los localizadores nunca se envían.
  - **Orden:** es-ES palabra a palabra. Se ignora el artículo inicial; ñ va tras n; acentos y caja solo desempatan; los números se ordenan como números.
  - **Localizadores:** se copian literalmente.
  - **Composición:** se recompone en la misma rejilla de columnas, con interlínea, sangrías (subentrada y continuación) y tipografía originales. Las letras-cabecera se regeneran. La escala es uniforme y como mínimo 0,75; si no cabe, `Blocked`.
  - **Glosario:** una ficha por entrada, que nunca se parte.
  - **QA local:** multiconjunto de localizadores, folios, píxeles fuera de los marcos y desbordamiento.
  - **Enrutamiento:** `production.run` y `compose-offline --pseudo` derivan estas páginas a `book_index` mediante un cambio mínimo.

## 2026-11-08 — Gaps 5, 6, 7 y 12 de `the-secrets-of-astrology` (pipeline-engineer, composición, sin LLM)

Correcciones generales con test rojo sobre páginas reales (`tests/test_composition_gaps.py`). Ninguna regla por número de página en el código común.

- **¡/¿ (gap 6, p37/p41/p56).** La pérdida no estaba en el compositor: la memoria de cláusulas (`consistency.build_plan`) envía el núcleo sin el `!`/`?` final, que se repone localmente como sufijo, así que ningún traductor podía escribir la apertura. `resolve_plan` repone ahora `¡`/`¿` por oración, al inicio de la cláusula exclamativa o interrogativa (dentro de la comilla de apertura que la encierra). Es puntuación de composición y se aplica solo si falta. El wire y la clave de memoria no cambian.
- **Binding del perfil (gap 7, p34/p60).** El contenido de las unidades era idéntico; solo cambiaba el orden. Producción guarda primero las unidades del modelo y después las locales; el pseudo ordenaba por id. `profiles.units_digest` pasa a ser canónico (id, source y spans en orden de id), y `pseudo_page_payload` conserva el orden de producción, que es del que dependen los `composition_sha256` de los overrides guardados. Re-vinculación reproducible con `.venv/bin/python scripts/rebind_profile_units.py --book books/the-secrets-of-astrology --pages 34,60 --write`. El script exige que la traducción guardada tenga los mismos ids, sources y spans (si no, se niega) y recalcula `units_sha256` y `policy_sha256` solo en la tabla de esas páginas. Las páginas 28, 141, 173 y 175 conservan su hash, porque su orden ya era canónico. El cambio del perfil altera la dependencia de producción de todas las páginas (`Production.dependencies` incluye `profile.data`).
- **Ficha p46 (gap 7).** Un run de mismo estilo que cruza filas de la tabla («Symbol» + «Polarity») solo podía partirse adivinando palabras. El detector de filas (`structured_layout.field_cell_starts`) marca esos límites de celda y el wire lleva un marcador `{vN}` por celda (`consistency._annotate`). `resolve_plan` conserva el límite como `cell_start` y `prepare_saved_field_rows` parte por runs exactos. Las traducciones guardadas sin `cell_start` mantienen el comportamiento anterior. Las fichas cambian de `marked_source`, así que necesitan traducción nueva en el contexto `claude_cli`.
- **fit_failed (gap 5).** Ninguna escala baja de 0,75:
  - **p22.** El icono al inicio de la columna pasa a ser una banda de obstáculo (`story_bands`), no un sangrado de primera línea.
  - **p23.** Los fragmentos de un mismo párrafo cortados al rodear un icono o una pestaña de puzle (sin puntuación final, mismo estilo, línea siguiente) se unen en composición (`column_flow.prepare_wrapped_paragraphs`). Fluyen por rects con los bordes izquierdos originales y un interlineado escalado continuo, en el grupo de escala de sus hermanos.
  - **p53 y p57.** El crecimiento exige fondo liso solo en la franja añadida (`fidelity._growth_clear`); los guards de raster y vector siguen validando la tinta. Se añaden candidatas de crecimiento puramente horizontal y de párrafo «asentado 1–2 pt + crecimiento hacia abajo». El crecimiento o desplazamiento vertical hacia una leyenda vecina (`caption_growth` o `kind=caption`) solo puede ocupar la mitad del hueco original (`fidelity._share_vertical_gaps`), porque las leyendas se ajustan después que los cuerpos (regresión vista en p35).
  - **p47.** Lo resuelve el detector de leyendas de figura del ingeniero de detección (`figure_legend`).
- **TOC (gap 12, p4/p7).** Cada fila termina en su propio folio, no en el mínimo de la columna. La cabecera de otro tamaño forma su propio grupo de escala. Los espacios nativos entre el título y el folio en línea se redactan junto con el título.

## G-FONT 1–4: fuentes de reemplazo de The Secrets of Astrology (2026-11-08)

`approved_by: "claude (delegación explícita de andres, 2026-11-06)"`. Evidencia: `tests/test_font_fallback_fidelity.py`, `tests/test_slab_fonts.py` y el pseudo offline 1–60 en `books/the-secrets-of-astrology/work/pseudo-fonts/`.

1. **Acentos de mayúscula recortados (p45 «JUGUETÓN»).** La causa no era la cobertura: Roboto Slab sí tiene el contorno de Ó.
   - MuPDF Story recorta la tinta al rectángulo de colocación y sitúa la primera línea base a 0,8 em × interlineado del borde superior, sin tener en cuenta el ascendente de la fuente. Las mayúsculas acentuadas suben unos 0,9 em.
   - El pseudo previo perdía la tilde en 314 de 743 mayúsculas acentuadas de bloques normales y en 41 de 56 de citas display.
   - Corrección: `fonts.insert_htmlbox_headroom` amplía el rectángulo 0,6 em arriba y a los lados y aplica el mismo padding al body. La línea base, los cortes de línea y el espacio sobrante no cambian; la parte inferior no se amplía.
   - Se usa en la ruta de bloque de `fidelity` y en las citas display no centradas.
   - La tinta que queda fuera de la caja (`ink_bbox`) pasa por los mismos guardas vectoriales y raster y se excluye de `non_text_pixel_diff` solo en ese rectángulo.
2. **Clasificación.** `fonts.FAMILY_CLASSES` clasifica por nombre: Avenir y Futura como sans, Serifa como slab, Lilita One como display. Solo se aplica con `[fonts].classify_slab`, de modo que astrology-wisdom-stars conserva su elección validada. También queda declarado en `[fonts].classification` del perfil.
3. **Cursiva slab.** Se usa Bitter Italic[wght] (OFL, google/fonts) en lugar de Roboto Serif Italic.
   - Cobertura completa del español, con tinta.
   - Altura de x de 0,522 frente a 0,528 de Roboto Slab.
   - Las instancias se renombran por el Reserved Font Name.
4. **p3.** LilitaOne usa su propia publicación libre (`LilitaOne-Regular.ttf`, OFL) y RobotoSlab-Regular usa Roboto Slab[wght]. Ambas tienen procedencia en `fonts/provenance.json`.
5. **Peso y espaciado (perfil `calibrate_weight`, `calibrate_space`).**
   - El peso nominal sale del nombre (ExtraLight 200, Heavy/Black 900).
   - El eje wght se biseca para que las astas, ya con la calibración horizontal aplicada, igualen las del original medidas por render. Ejemplo: LightCompressed pasa a wght 462 con escala horizontal 0,50.
   - El espacio de palabra toma el avance del original: los reemplazos eran hasta un 32 % más anchos.
   - El peso declarado en OS/2 no cambia. La evidencia queda en `weight_calibration` y `metric_calibration`.
6. **Informe pseudo.** Un fallback verificado por `fonts.verify_fallback` (cobertura con tinta, clase, cursiva, ancho calibrado y peso) se lista como `font_fallback_applied` en una tabla informativa aparte. Si no está verificado, sigue siendo el defecto `font_fallback`.

Los candidatos de producción compuestos antes de este cambio usan las fuentes anteriores y deben recomponerse para heredarlo.

## Limpieza del sistema Codex (aprobada por andres el 2026-11-08)

Se aplica después de las entregas de los gaps 1–12.

**Se borra:**
- `.codex/` y `.agents/skills/`.
- Las instantáneas de diagnóstico `work/phase2c…2h/input_snapshot`.

**Se retira:**
- Los backends `codex_sdk` y `codex_exec`, junto con sus tests y la dependencia `openai-codex`, mediante pipeline-engineer.
- `openai_api` se queda como respaldo opcional.

**Se mueve:**
- Los scripts de fase (`prepare_phase2*`, `check_phase2*_golden`, `phase2*_report`) pasan a `scripts/legacy/`.
- Los informes `docs/historico/fase2*.md` pasan a `docs/historico/`.

**Se descarta:**
- El plan de retraducción 2H y las propuestas de formas cortas generadas por luna.
- La traducción con luna en caché y memoria: todo el libro se retraduce con Sonnet. Las traducciones de luna se guardan solo como referencia.

**Se conserva:** `short_forms/approved-v1.json`, aprobado por hash.
- **Limpieza aplicada (2026-11-09):**
  - Borrados `.codex/` y `.agents/` (siguen en git) y `work/phase2h/input_snapshot` (289 MB).
  - `docs/fase2*.md` movidos a `docs/historico/`.
  - Se mantienen en su sitio los scripts `check_phase2*_golden` y `prepare_phase2*`, porque los tests de regresión (`test_phase2e_*`, `test_phase2f`, `test_phase2g`) los importan.
  - Backends Codex retirados (2026-11-09, pipeline-engineer): `llm.py` ya no contiene `CodexSDKBackend`, `CodexExecBackend`, `_safe_overrides`, `_profile_overrides`, `_account_chatgpt` ni imports de `openai_codex`. `_make_backend('codex_sdk'|'codex_exec')` falla con «Codex backend retired 2026-11-08»; `doctor` marca esa configuración como error y ya no informa de versiones `openai-codex*`. Los nombres siguen en `SUBSCRIPTION_BACKENDS` y en los enums de `schemas/` como valores históricos legibles. Dependencia `openai-codex` retirada de `pyproject.toml` y `uv.lock` (el venv no se desinstaló). El default de `provider.backend` pasa a `claude_cli`. `config/pipeline.toml` conserva `codex_profile` porque forma parte del hash de la aprobación G1b vigente.

## 2026-11-09 — Cintas rotadas, etiquetas verticales y extracción de candidatos (pipeline-engineer, sin LLM)

Correcciones generales con test rojo sobre páginas reales (`tests/test_rotated_candidate_qa.py`). Evidencia: pseudo offline 86–135 en `books/the-secrets-of-astrology/work/pseudo-rot/`. Ninguna regla por número de página.

- **`ink_outside_edit_boxes` (28 de 29 páginas).** No venía de las cintas: `candidate_storage.extract_page` copiaba la página con `insert_pdf`, que omite `/Group` (grupo de transparencia con espacio ICC). Sin él, todos los colores planos se renderizaban un nivel distintos (p90: 46 % de los píxeles fuera de texto). Ahora se injerta el `/Group` original y el candidato de una página es idéntico píxel a píxel al original.
- **Pruebas rotadas tras la extracción.** Las pruebas `verified_rotated_spans` quedaban ligadas al hash anterior a la extracción, así que la QA no las veía: 74 spans «omitidos» en p89 y las `R` aisladas de HEART/PURPOSE contadas como inmutables alteradas. `candidate_storage.extract_candidate` extrae la página y vuelve a ligar solo las pruebas de ese mismo candidato. `production.compose_and_qa` guarda además el `fit.json` ya extraído.
- **Separadores `|`.** Se emiten como pruebas `preserved_separator` solo si `separator_character_proofs` confirma que los operadores nativos son idénticos. Un span de cinta con espacio («R ») lleva además una prueba gemela con solo letras, el mismo bbox y el mismo destino.
- **Margen editorial de etiquetas rotadas.** El límite es la medida más amplia evidenciada en la hoja (bordes propios unidos al reflejo de la página enfrentada) o el propio original, si ya la excedía (`editorial_margins.rotated_label_bounds`). Antes se usaba la medida de una lista lateral (p89: borde derecho 237 pt). La QA mide solo las palabras propias de la etiqueta. El compositor la respeta y, si hace falta, desplaza la etiqueta a lo largo de su misma línea base (`baseline_offset`).
- **Separación mínima.** 0,2 em entre la etiqueta nueva y los glifos nativos vecinos de la cinta. Todos los originales A13 tienen 0,23 em o más.
- **Tildes de mayúscula.** En las etiquetas rotadas y verticales, el área editada y todos los guardas (vector, raster, vecinos y página) usan la tinta real renderizada además de las cajas de glifo (`ink_bbox`). `insert_text` no recorta, así que no hace falta `insert_htmlbox_headroom`.
- **Fuentes de las etiquetas verticales.** `fonts.profile_font_options` reúne las opciones `[fonts]` del perfil (`classify_slab`, `classification`, `calibrate_weight`, `calibrate_space`). Lo usan `select_group_fonts` y `vertical_labels.fit_vertical`. Soho Pro pasa a Roboto Slab.

Los candidatos de producción compuestos antes de este cambio deben recomponerse.

- **Verificación de la redacción vertical (p86).** `apply_redactions` reescribe el contenido e instancia los Form XObjects del texto compuesto. Por eso los orígenes se recalculan en float32 y pueden moverse un ulp: la «b» de AstroSlab pasa de 490,43447876 a 490,43450928. La identidad redondeada a 1e-3 cambiaba de valor en el límite de 0,0005 y bloqueaba la página. Ahora `vertical_labels.redaction_preserved` exige una correspondencia uno a uno entre glifos: mismo carácter, misma fuente y origen a ≤0,001 pt, igual que `native_redaction`. Sigue bloqueando cualquier glifo perdido o desplazado y cualquier glifo de origen que quede en la página. No era un defecto de los cambios de cintas: el fallo dependía de la posición del texto del cuerpo.

## 2026-11-09 — Ajuste de composición 61–192 de `the-secrets-of-astrology` (pipeline-engineer, sin LLM)

Correcciones generales con test rojo sobre páginas reales (`tests/test_composition_fit61.py`), validadas con el pseudo offline 1–192 (`work/pseudo-fit/`; antes: `work/pseudo-fit-before/`). Ninguna regla por número de página en el código común; ninguna escala por debajo de 0,75.

- **Run-in (p91…p135).** Cada entrada puede subir hasta la mitad del hueco blanco que la separa del filete (o la tinta) de arriba (`run_in_layout`, `layout_top`). Primero se prueban las cajas con la parte superior original; la subida es la mínima necesaria. Las columnas de 91–135 pasan de 0,75–0,79 a 0,85. Las páginas de casas (143–165) tienen 2–4 pt de hueco: siguen entre 0,75 y 0,80 (revisión).
- **MERGE (composición).** La extracción canónica no cambia: ids, fuentes y traducciones guardadas siguen valiendo. `fidelity.split_style_breaks` separa en composición, antes del finalizador, las unidades que el bloque PDF fundió: un cambio de tamaño en un salto de línea (p22: «The Sun see pp.88—91», 8 pt, bajo el cuerpo de 11 pt) o una línea de otro peso que empieza más arriba y en otra columna (p170: «The Ascendant–Descendant axis» dentro de la cola de la llamada). Una primera parte de una sola línea es un título y la sigue gestionando `run_in_layout`. El corte se hace en el único límite de runs de estilo del destino que encaja con las dos partes de origen y conserva las cifras de cada una; la segunda parte se llama `<id>_split`. Las dos partes conservan el grupo de escala de la unidad en la que se imprimieron, así que la separación no cambia la escala de hermanos ajenos (p22: «Tu carta natal, véanse…» sigue a 1,00). Si no hay un corte único, la unidad queda entera y la QA local la marca con `merged_style_break` (acción `recompose`). Se probó primero separar en la extracción, pero cambiaba los ids de 18 páginas e invalidaba traducciones y fixtures guardados (p14, p15, p22, p128…); se descartó.
- **Ángulos espaciados.** «D C» e «I C» (p139) son tokens protegidos como MC/IC.
- **Bindings.** Una corrección unida (cita display `u0007_quote`) conserva sus miembros (`source_members`); `units_digest` los expande, así que p173 mantiene su `units_sha256` (mismo contenido). Una página con `composition_translations` revisadas (p175) es dueña de todos sus destinos: no genera trabajo de cita unida y las correcciones guardadas se registran como `superseded_by`. El hook de p141 exige el hash de origen; el del destino solo identifica la traducción revisada (una nueva sin el clítico aislado no se repara ni se bloquea). Hash nuevo de `profile_hooks.py` en `[hooks]`. No hizo falta `rebind_profile_units.py`.
- **Cita centrada (p97).** Puede crecer simétricamente (mismos centros) hacia espacio libre y plano dentro de los márgenes editoriales; el reparto en líneas usa la caja candidata.
- **Marca de agua pálida (p183).** La excepción de subyacente pálido verificado (tinta original sobre ambos tonos, bajo contraste) se aplica a todo bloque, no solo a historias.
- **Validador (p183).** Una repetición inmediata del origen autoriza una repetición del destino aunque el término aprobado tenga otro número de palabras («Aspect patterns» → «Configuraciones de aspectos»).
- **Holgura de tildes.** `caption_growth.insert_anchored` usa `insert_htmlbox_headroom` (p4: la «Á» de un pie perdía la punta). Las historias de `column_flow`, la capitular y el flujo en L ensanchan solo el recorte de cada marco (arriba y a los lados) en el overlay escrito; la tinta real se sigue comprobando. Las etiquetas protegidas usan `insert_text`, que no recorta.
- **Formas cortas.** Si un grupo no cabe a 0,75 se preparan a la vez todas las peticiones de forma corta de los miembros que no caben solos (sin inferencia). Los fallos llevan `reason` = `needs_short_form`, `group_blocked_by_sibling` o `fit_failed_at_0.75` (las etiquetas protegidas conservan su motivo histórico) y `needs_short_form`.
- **Pseudo.** Una palabra extranjera en cursiva por matriz («plouton», p128) se conserva como haría un traductor, y la composición restaura su cursiva.

## G4 provisional para el piloto Sonnet (2026-11-09)

- **Decisión delegada:** claude, por la delegación explícita de andres del 2026-11-06.
- **Alcance:** las 25 páginas piloto de `work/structure/structure.json` v2 (3, 4, 7, 11, 15, 22, 23, 25, 37, 46, 47, 86, 87, 89, 93, 138, 139, 159, 175, 176, 179, 183, 186, 188, 192), por la ruta `produce`.
- **Requisito previo:** pseudo-traducción 1–192 sin defectos bloqueantes salvo los aceptados:
  - formas cortas del pseudo en p83, p93, p138 y p179;
  - Fase 4: texto curvo y portada.
- **Copia del G4 anterior (luna):** `work/approval_history/pilot_approval.luna.2026-11-09.json`.
- **G4 definitivo:** depende del informe del `book-auditor`.
- **Auditoría de lotes heredados (2026-11-09):** los lotes 1–15 (luna/Codex) se excluyen del hook de auditoría porque son obsoletos. Lista en `work/audit/legacy_batches.json`.

## 2026-11-09 — Auditoría batch-16: ordinales, inglés residual, contexto de traducción y estado diferido (pipeline-engineer, sin LLM)

Correcciones generales con test rojo sobre páginas reales (`tests/test_audit_batch16.py`). Ninguna regla por número de página en el código común.

- **Ordinales de rueda partida en un pliego (p138/139, 170/171, 172/173).** Se extiende la decisión 10 (casas = femenino, `3.ª`…`10.ª`). `approved_by: "claude (delegación explícita de andres, 2026-11-06)"`, decisión comunicada en el encargo del orquestador. `secrets_ordinal_policy.house_diagram_lines` ya no exige que la rueda entera esté en una hoja:
  - Si la cohorte propia no basta, se usa la hoja enfrentada (misma paridad que `editorial_margins`).
  - La hoja enfrentada tiene que tener su propia cohorte, sin repetir ninguna casa, y entre las dos sumar al menos 6 casas.
  - Cada hoja necesita distribución 2D (≥5 % en cada eje).
  - El contexto house/MC/IC es el del pliego.
  - El sufijo admite espacio final («rd »). La pareja cifra/sufijo puede ir seguida de otra etiqueta en la misma línea PDF («11th» + «Friendships»).
  - Resultado: `11.ª`, `12.ª`, `1.ª`, `2.ª` en 138/172, y `3.ª` a `10.ª` en 139/171. p173 no cambia.
  - Una rueda se convierte entera o no se convierte (`native_ordinals.diagram_layout_conflicts`). En p170, «11th» toca la caja métrica del título de 48 pt (u0005). El guarda de candidatos de `fidelity` compara cajas de unidad, no tinta, así que no se podría colocar. En ese caso las cuatro etiquetas siguen protegidas y la QA de ordinales mantiene la página en `review`. El paso a guarda por tinta queda pendiente del ingeniero de composición.
- **QA de inglés residual sobre valores protegidos.** Nuevo código `english_ordinal_suffix`:
  - `text_quality.english_ordinal_issues` revisa el candidato: la cifra protegida seguida de st/nd/rd/th, tanto el sufijo volado como el inline.
  - Las fuentes de reemplazo cuyo nombre parece de símbolos («AstroSlab») cuentan como texto si su hash está verificado.
  - `text_issues` rechaza `{nN}ND`/`2nd` en el destino. Así lo rechazan el validador LLM y la memoria de frases.
  - Este control encontró p142–p145 («la casa 2nd», «CASA 2ND/3RD»), que se retraducen.
- **Homógrafos ES/EN.** `language.SPANISH_HOMOGRAPHS` es evidencia léxica, nunca un mapa de traducción. «• Romance» (p139) deja de contar como inglés residual.
- **Contexto de pies y etiquetas (p47).** El plan deduplicaba cláusulas sin el cuerpo de su página. Ahora `adapters.fidelity_translate`, con ayuda de `page_context.py`:
  - Traduce primero el cuerpo y empaqueta páginas enteras por llamada.
  - Después envía pies, etiquetas y callouts con `term_context`: las cláusulas del cuerpo de la misma página o pliego, ya traducidas, que comparten un nombre propio. Las acompaña una instrucción por fila; el prompt no cambia.
  - `llm._validate` rechaza la fila si nombra el objeto con el término de otro nombre.
  - Una memoria guardada que falla este control se retraduce y se reemplaza (`SentenceMemory.put(replace=True)`, solo tras un control cruzado).
- **QA de consistencia (`term_consistency_issues`).** Alinea por orden los nombres en mayúscula solo cuando las cuentas coinciden por frase. Marca una unidad cuando a la vez:
  - le falta la traducción unánime de un nombre;
  - usa en su lugar la traducción de otro nombre ausente de su origen;
  - o tiene una traducción minoritaria del nombre.

  Los términos del glosario quedan para el validador de glosario. En producción el pliego entra como evidencia. Barrido sobre las 174 traducciones guardadas: solo p47; 0 en piloto y en wisdom-stars.
- **Glosario en títulos apilados (p176).** El validador ya era insensible a la caja. El fallo venía de que el título THE WHEEL OF / RULERSHIP se traducía como dos unidades: cada línea pasaba por separado y la composición las unía («LA RUEDA DE LA REGENCIA»).
  - `page_context.stacked_glossary_pairs` detecta las líneas apiladas cuyo término aprobado cruza el salto.
  - Esas filas llevan `line_continuation`, y `_validate` exige la forma aprobada en la concatenación.
  - La memoria previa que lo incumple se rechaza.
- **Estado diferido (p176).** `finish_qa` da `partial_deferred` solo si se cumplen tres condiciones:
  - la QA nativa es correcta;
  - todas las regiones tienen bbox y motivo;
  - todo texto rotado omitido cae dentro de una región registrada.

  Si no, queda en `review` con `untranslated_text_outside_deferred_regions`. `native_failure_causes` nombra la causa. En p176 era `glossary_missing` (el título), no la región diferida: tras retraducir el título queda `partial_deferred`.

Retraducción (memoria rechazada): 47 y 176, y 142–145 cuando se resuelva el bloqueo G5. Recomposición sin traducción: 138 y 139, y 171–172 cuando se resuelva el bloqueo G5. p170 espera al guarda por tinta.

## 2026-11-09 — Composición del lote 16 (auditoría del piloto Sonnet; pipeline-engineer, sin LLM)

Correcciones generales con test rojo sobre páginas reales compuestas con la traducción Sonnet guardada (`tests/test_batch16_composition.py`). Ninguna regla por número de página; ninguna escala por debajo de 0,75. Ids, fuentes y traducciones guardadas no cambian.

- **Títulos alineados a la derecha (p11, p25).** La alineación se medía por span y el borde derecho incluía el espacio final de «STORY »/«THE ». Ahora se mide por línea con el borde de los glifos visibles (`fidelity.visible_lines`): varias líneas con el mismo borde visible (<1 pt) y arranques distintos son `right` (en todo el libro solo cambia p11). Una unidad `right` se ancla en el borde visible (`_ink_bbox`) y ninguna caja candidata lo sobrepasa. p11 pasa de 0,79 a 0,90.
- **Saltos de línea de listas (p4, p138, p139).** `fidelity.split_line_items` compone una unidad hija por línea (o segmento) de origen cuando el corte del destino tiene evidencia exacta: viñetas «•» al inicio de línea (mismo número en origen y destino), líneas de crédito (todas en mayúscula y cada salto en un límite de runs de estilo con la misma secuencia en el destino), filas de leyenda (un estilo, ≤5 palabras, anclas de glosario aprobado o token protegido como IC/MC) y saltos forzados de bloques legales (línea en blanco, o línea corta de un bloque centrado que termina en número protegido o fin de oración, con recuento igual en el destino). Cada pieza conserva sus números, viñetas y tokens protegidos; si no, la unidad queda entera. Una línea de origen es una línea compuesta (`single_line`). Las hijas heredan `kind` y grupo de escala; `finalize_paragraphs` no vuelve a unirlas. En todo el libro (174 traducciones guardadas) solo cambian p4, p138 y p139.
- **Pilas centradas (p4).** `fidelity.center_stacked_lines`: una cadena vertical de ≥3 unidades pequeñas adyacentes con el mismo eje visible (<1,5 pt) y arranques distintos es una columna centrada; sus miembros de una línea pasan de `left` a `center`. Solo afecta a p4.
- **Comillas decorativas (p15, p138).** Las comillas vectoriales que enmarcan texto display de ≥20 pt (`display_quotes.quote_mark_rects`/`framed`, mismo criterio de forma que el survey) son obstáculo en `_test_fit`: el texto nuevo no puede tener tinta sobre la comilla ni sobre la banda de la línea de origen a la que acompaña (la comilla de cierre sigue a la última palabra). Holgura de 1 pt, nunca mayor que la del original. Un texto enmarcado no se desplaza (`shifted_candidates`), así sigue anclado a la comilla de apertura. p15 queda a 0,91 (la cita crece hacia el blanco libre); en p138 «experiencia / cotidiana.» vuelve a dos líneas con la comilla detrás y se aleja del recuadro «CÓMO FUNCIONAN» (1,00). Sin cambios de escala en las demás citas del libro con traducción guardada (9, 13, 19, 23, 33, 59, 97, 111, 153, 173, 174).
- **Cabecera sobre marca de agua pálida (p93).** «La Luna» fallaba la guarda raster en todas las cajas (motas blancas dilatadas de la estrella pálida sobre la que ya está impreso «The Moon»), y el ajuste pedía forma corta. Excepción acotada (`raster_obstacles.build_region_obstacles(pale_footprint=…)`): dentro de la banda de línea del texto original (cajas de span ±0,1 em en vertical), los píxeles claros (RGB mínimo >175) sobre fondo claro (>180) no son obstáculo si la tinta original ya los pisa. Fuera de la huella, o con píxeles oscuros, la guarda no cambia. Además, `fidelity._short_form_request` no pide forma corta si el destino a 0,75 ya cabe en la caja más ancha: el fallo no es de anchura.
- **QA local.** `fidelity.composition_line_issues` (en `fidelity_qa`, acción `recompose`): `bullet_line_merge`, `quote_mark_overlap` y `right_edge_overflow`. Detecta los defectos en los candidatos auditados (p15, p25, p138, p139) y queda limpia en los recompuestos.
- **Guarda de ordinales nativos por tinta (p170).** El guarda de candidatos `native_ordinal` comparaba la caja de edición con las cajas métricas de las demás unidades: «11th» de la rueda de p170 solapa unos 0,6 pt la caja del título de 48 pt («THE ASCENDANT -DESCENDANT»), cuya tinta termina muy por encima. Ahora compara con la tinta real de los glifos (`fidelity.unit_ink_rects`, vía `safe_vlm._glyph_ink_bounds`; caja métrica solo si no se conoce la tinta). Con el guarda nuevo, la rueda de p170 se convierte entera (1.ª, 2.ª, 11.ª, 12.ª a 1,00). La exclusión `native_ordinals.diagram_layout_conflicts` en `adapt_headings` ya no es necesaria; la retira su dueño (ordinales).
- **QA `quote_mark_overlap`.** Compara píxeles original/candidato dentro de la zona de cada comilla, igual que la comprobación de composición. La versión inicial comparaba cajas de glifo (más anchas que la tinta) y daba un falso positivo en las citas fragmentadas históricas de `test_phase2c` (p15/p17 del fixture). La expectativa del test no cambia.
- **p147/p149 «SIGNOS Y 4 CASA PLANETAS».** No es de composición ni de restauración de marcadores: el destino guardado ya trae ese texto (el modelo recibe «{v4}AND 4TH HOUSE PLANETS» y suprime el sufijo). El mismo encabezado tiene tres traducciones en 141–165: correcta («SIGNOS Y PLANETAS DE LA CASA N»: 141, 151, 155, 157, 159, 161, 163), con inglés residual («CASA 2ND/3RD»: 143, 145) y con orden invertido («Y N CASA PLANETAS»: 147, 149, 165). Hay que retraducir `u0003` de 143, 145, 147, 149 y 165 (dominio de traducción/ordinales).
- **Pendiente menor (sin cambio):** en p4 «Impreso y encuadernado en China» ya no conserva el hueco previo, porque el bloque de ventas traducido ocupa una línea más; la separación de la fila «Círculo de la carta natal» respecto a la muestra roja (p139) es de unos 2 pt.

Los candidatos de producción compuestos antes de este cambio deben recomponerse.

## 2026-11-09 — Auditoría batch-17: separadores diferidos, tildes display, filetes, ordinales y consistencia (pipeline-engineer, sin LLM)

Correcciones generales con test rojo sobre páginas reales (`tests/test_audit_batch17.py`). Ninguna regla por número de página en el código común; ninguna escala por debajo de 0,75. Glosario, guía y aprobaciones intactos.

- **Separadores en franjas diferidas (p89, p93).** `production.uncovered_deferred_text` ya no cuenta los spans sin letras («|», viñetas, cifras sueltas) como texto sin traducir fuera de las regiones de Fase 4. Una palabra real fuera de todas las regiones sigue impidiendo `partial_deferred`. Con la QA guardada, p89 y p93 pasan a `partial_deferred`.
- **Tildes de mayúsculas display (p37, p46).** Nuevo `line_clearance.py`: holgura de tinta real (`safe_vlm._glyph_ink_bounds`) entre líneas consecutivas en cuerpos ≥17 pt. Mínimo `max(1 pt; 0,04 em)`, nunca más del que el original ya tenía en ese mismo lugar (descendentes sobre ascendentes en interlineados apretados siguen siendo legales). En `_test_fit` (inserción plana), si la tinta nueva queda más cerca de la línea superior (propia o vecina, esta medida con la tinta del original), se sube el interlineado de la unidad. El paso entre líneas crece en el incremento completo y la primera línea base baja la mitad (semi-interlineado). Hasta 4 intentos; si la caja no lo admite, se rechaza esa caja y escala, y las escalas menores lo resuelven. La unidad registra `accent_leading` (interlineado de origen y final). Resultado: p37 «VALIENTE E / INTRÉPIDO» a 0,98, con holgura ≥1 pt; p46 «CÁNCER» a 0,91, a ≥1,8 pt de «SIGNO». En el barrido de 174 páginas cambian 37, 42, 44, 46, 48, 88 y 102. QA: `line_ink_collision` en `composition_line_issues` (acción `recompose`).
- **Filetes y paneles (p176).** Nuevo `rule_limits.py`, sobre el render del original, así que sirve igual para vectores que para ráster de página completa (p176 lo es). Un filete es un tramo fino (≤4 pt) de color uniforme a ≤0,75 em sobre o bajo la tinta que cubre ≥80 % de su anchura. Un panel son ≥max(4 pt; 0,25 em) columnas contiguas de color plano junto a la banda de la tinta. El fondo es la moda de un anillo de 3 pt alrededor de la tinta, porque con negras muy gruesas la mediana de la caja daba negro. `_candidate_boxes` recorta las cajas al final del filete y a `min(holgura original; max(3 pt; 0,25 em))` del panel, sin encoger nunca la huella del original. No se aplica a ordinales nativos, etiquetas protegidas, historias, índice, callouts, pies con crecimiento propio ni columnas fijas. p176 «LA RUEDA DE LAS / REGENCIAS» queda a 0,86, con la tinta ≤373 pt (filete en 372,5). QA: `rule_or_panel_overflow`. Tras el render a 4× se vacía la caché de imágenes de MuPDF, porque una decodificación a otra resolución alteraba en un nivel de color las pruebas de píxeles posteriores (tinta de cifras de ordinales a 220 dpi).
- **Punto de ordinal volado (p138, p139, p171–173).** `native_ordinals.heading_overlay`: si el sufijo está elevado respecto de la cifra, el «.» va en la línea base de la cifra y solo «ª» conserva el origen volado (mismo estilo y tamaño del sufijo original). `qa_adjustments` admite esta forma y la antigua; la antigua la marca `point_issues` → `ordinal_point_off_baseline`. Los signos («4.º», sufijo en la línea base) no cambian.
- **Ordinal entre paréntesis (p139).** `native_ordinals.parenthesized_ordinals`, activado por `editorial.ordinals` del perfil: «(1st)» traducido «(1)» pasa a «(1.ª)» solo si la misma unidad dice «1st house» (concordancia inequívoca con «casa», femenino). Solo se añade la marca tras la cifra, en su propio run (también en los párrafos unidos). «(9th house)» → «(casa 9)» se conserva. Sin concordancia no se añade nada y la QA lo marca con `ordinal_suffix_dropped` (`text_quality.ordinal_suffix_issues`, en `page_issues`). En las 174 traducciones guardadas solo cambia p139.
- **Consistencia «middle of the heavens» (p23/p139).** G3 fija Midheaven/Medium Coeli = Medio Cielo e Imum Coeli = Fondo del Cielo, y el validador ya los exige (p186 cumple). No fija «middle/bottom of the heavens», así que lo marca la QA y no el validador. Nuevo `consistency.phrase_consistency_issues`:
  - Toma frases nominales de origen repetidas entre páginas: 3–6 palabras, sin pronombres, modales ni auxiliares, y ≥2 palabras de contenido traducidas, no copiadas como MC/IC.
  - Su versión española es la ventana mínima con las raíces compartidas, después de enmascarar las formas del glosario y las palabras copiadas. Esa ventana solo puede contener esas raíces, en el mismo orden, y palabras funcionales, sin cifras.
  - Se informa cuando difieren artículos, preposiciones o número.
  - En producción, `qa['consistency_warnings']` es un aviso menor y no bloqueante: la evidencia son las traducciones guardadas de las demás páginas.
  - Barrido del libro: 42 avisos en 37 páginas, entre ellos p23/p139 («mitad de los cielos» / «Mitad del cielo», «parte inferior de los cielos» / «Parte inferior del cielo»). El glosario no se toca.
- **Tests de batch-16 sobre artefactos retraducidos.** Cuatro tests leían los defectos de p47/p176 de los artefactos guardados, que ya estaban corregidos. Ahora comprueban que el artefacto actual está limpio y que el defecto auditado, inyectado sobre él, se sigue detectando.
- **Deriva previa (sin relación con este cambio).** Recomponer con el código actual, con o sin estas correcciones, deja p49 y p51 en `needs_short_form`, aunque su `fit.json` de producción está completo. p178 se bloquea con «Fidelity payload must cover every editable container exactly once». Hay otras diferencias de escala respecto de los `fit.json` de producción que vienen de cambios anteriores. Las únicas escalas que mueven estas correcciones son p4 (0,97 → 0,94, filete) y p48 (0,96 → 0,91, tilde).

Recomposición sin traducción: 4, 28, 37, 39, 41, 42, 44, 45, 46, 48, 85, 88, 89, 93, 100, 102, 116, 138, 139, 156, 167, 171, 172, 173 y 176. Retraducción: ninguna obligatoria. Los avisos de consistencia (p23/p139 y otros) quedan a decisión del orquestador.

## 2026-11-09 — inline_style por correspondencia del énfasis, no por posición (pipeline-engineer, sin LLM)

- **Falso positivo (p15, p23; auditor batch-17).** `text_quality._inline_issues` exigía la misma secuencia ligera/negrita que el original. Las citas display «**Buscar significado en el cielo** parece…» (p15) y «**El Sol** representa el **don creativo** y el **sentido de identidad** individuales…» (p23) anteponen o posponen sintagmas con toda legitimidad. Se marcaban `inline_style` → `retranslate`.
- **Regla nueva (`_emphasis_correspondence`).** Las anclas son locales y léxicas: palabras idénticas (incluidas MC/IC), cognados ortográficos mutuamente únicos (prefijo ≥4 y ≥60 % de la palabra corta) y pares del glosario G3 con una sola aparición a cada lado.
  - Con otro orden se acepta solo si hay correspondencia positiva: el mismo número de frases enfatizadas por estilo; cada una anclada o, como mucho, una sin ancla por estilo a cada lado, con una longitud entre 0,5× y 2×; y al menos un ancla confirmada.
  - Con el mismo orden también se marca la contradicción: énfasis que se extiende a una palabra que era ligera en el original, o frase enfatizada sin ninguna ancla que conserve el estilo.
  - Se tolera una palabra que queda fuera, junto a una frase anclada («the **sign on the MC**» → «el signo **del MC**», p173 del piloto).
- **Barrido de los 166 `fit.json` de producción.** Desaparece el aviso en p15 y p23. Aparecen dos defectos reales que el chequeo por secuencia no veía: p17 («Older **forms**» → «Las formas **más antiguas**») y p26 («most **essential creative gift**» → «don creativo más **esencial**»). Ninguna página más cambia.

## Candidatos no deterministas (2026-11-09)

Cada guardado de un candidato escribía un `/ID` aleatorio en el trailer del PDF. Así, cada recomposición cambiaba el `candidate_sha256` aunque la página fuera idéntica, y dejaba obsoletas `review-visual` y las aprobaciones ligadas al hash. Es una de las causas de la invalidación en cascada que sufrimos en la etapa Codex.

**Corrección:** guardado determinista, encargado a pipeline-engineer.
- **batch-19 (2026-11-09):** recomposición r4 con candidatos no deterministas. Su auditoría se hace tras el arreglo de /ID; marcador `work/audit/batch-19.inprogress`.

## 2026-11-09 — Candidatos deterministas, latín citado y «ª» volada una sola vez (pipeline-engineer, sin LLM)

Correcciones generales con test rojo sobre páginas reales (`tests/test_candidate_determinism.py`, `tests/test_latin_terms_and_ordinal_indicator.py`). Ninguna regla por número de página. Glosario, guía y aprobaciones intactos.

- **Guardado determinista (p37).** Había dos fuentes de azar. La primera, el `/ID` aleatorio del trailer. La segunda, que fontTools sellaba con la hora (`head.modified`) cada instancia estática nueva de las fuentes de reemplazo. Esa hora cambiaba el hash del archivo y, con él, el nombre de familia calibrada embebido (`AstroSlab-<hash>`). Ahora:
  - `core.save_deterministic` / `deterministic_pdf_bytes` es la única ruta de escritura. La usan `_save_pdf` (fidelity, verticales, rotadas, raster, índice) y `candidate_storage._save_compact` (`extract_page`/`extract_candidate`).
  - Borra `CreationDate`/`ModDate` de Info y escribe sin `/ID` (`no_new_id`). Después pone `/ID [<h><h>]`, donde `h` son los primeros 16 bytes del sha256 de esos bytes. El orden de objetos es la recolección determinista de MuPDF.
  - `fonts.fallback_font` instancia y calibra con `recalcTimestamp=False`. Los archivos ya cacheados no se regeneran, así que sus nombres no cambian.
  - Resultado: componer p37 dos veces en dos snapshots distintos da el mismo sha256, antes y después de la extracción.
  - Cada candidato cambia de hash una sola vez, en la próxima recomposición; a partir de ahí es estable.
- **Latín citado (p23).** `language.LATIN_ASTROLOGICAL_TERMS` es una lista cerrada de nombres latinos de varias palabras (Medium/Imum Coeli, Pars Fortunae, Caput/Cauda Draconis, Via Combusta, Anima Mundi). `source_identical_latin_terms` exime un nombre solo si aparece con la misma grafía en el origen de la propia unidad y en su destino. `fidelity_qa` lo añade a `language_exceptions`. No se usa Lingua con latín, porque da «The Ascendant» como latín. El inglés copiado del origen sigue contando, y una palabra suelta («medium») nunca cuenta como latín.
- **«ª» volada una sola vez (p138/p139, ruedas de casas).** Antes «ª» se dibujaba en el origen elevado del sufijo inglés. Como el glifo ya es volado, su tinta subía 2,2 pt por encima de las cifras («11. ª»). Ahora, con sufijo de origen elevado, «.ª» va en la línea base de la cifra y a su tamaño (`native_ordinals.indicator_style`, con la marca `superior_glyph` para que el run no comparta estilo con la cifra nativa).
  - La tinta de «ª» queda en la franja del «st» original: 0,9 pt de diferencia en la parte superior y nunca por encima de las cifras.
  - Esta corrección sustituye, en ese único punto, la parte de la decisión 10 según la cual `.ª` «usa el tamaño … y origen del sufijo original». La cifra nativa, su tinta y la QA de procedencia no cambian.
  - QA: `point_issues` añade `ordinal_indicator_raised_twice` (acción `recompose`). `qa_adjustments` sigue validando las formas anteriores, para que se puedan informar.
  - Los signos («4.º», sufijo en la línea base) no cambian.

Recomposición sin traducción: todas las páginas con candidato de producción. El código forma parte de la dependencia, así que todas quedan obsoletas, y además cada candidato cambia de hash una sola vez. En particular: 23 (QA), 138, 139 y 170–173 (ruedas). Las revisiones `review-visual` ligadas a hashes anteriores tendrán que rehacerse una vez.

## G5 con auditoría en paralelo (2026-11-09)

- **Decisión delegada:** claude, por la delegación explícita de andres del 2026-11-06.
- **Motivo:** andres criticó el retraso. Cada lote quedaba en `g5_blocked` solo por la revisión visual obligatoria, que exige auditoría, y la producción se detenía hasta que terminaba el auditor.
- **Nueva regla:**
  - Al cerrar cada lote se registra `g5_decision-NN.json` con `continue`. La evidencia es la auditoría de ese lote, que corre en paralelo.
  - G5 de cada lote se evalúa con el informe del book-auditor.
  - Los defectos se corrigen por causa y se recomponen sin retraducir.
  - G7 exige que todos los lotes tengan auditoría con G5 ≥ 0,90 tras las correcciones.
- **Primer caso:** batch-16 (piloto). Las auditorías de batch-17 y batch-18 dan 15/15 en alcance; batch-20 (r5 determinista) se está auditando.

## G4 definitivo, piloto Sonnet (2026-11-09)

- **Decisión delegada:** claude.
- **Base:** batch-20, recomposición determinista, con 15/15 en alcance según el book-auditor. Los bloqueantes de batch-16 están todos corregidos.
- **Revisión visual:** registrada en las 15 páginas.
- **Fase 4:** 86, 89, 93, 175 y 176 quedan como `partial_deferred`.

## Entrega inmediata del libro (2026-11-09, instrucción de andres)

andres ordena entregar el libro traducido «como salga», con su porcentaje de acierto, sin esperar a que G5 llegue al 90 % y sin cancelar trabajo en curso.

**Ejecución:**
- G5 deja de bloquear. Cada lote se cierra con `g5_decision` «continue».
- `assemble-final` ensambla todas las páginas con su mejor candidato.
- El informe final da el porcentaje de páginas OK según la QA local y según el book-auditor, y lista lo que no está OK.

**Correcciones en curso:** puntuación huérfana y frases entre páginas. Si llegan antes del ensamblado, se aplican con una recomposición.

## Puntuación original huérfana (batch-21, 2026-11-09)

- **Defecto:** un span que solo contiene puntuación (`,` `.` `:` `/` `-`) entra en la clase inmutable de `immutable_span` y se conservaba pintado junto al texto nuevo. p9: coma de «MAP,» (SohoPro-LightCondensed 50 pt, x 144,0–152,7), «MAP,A DEL TESORO». p21: coma de «astrology,» (x 429,4–436,4), coma doble. La comprobación de inmutables lo daba por bueno.
- **Corrección (composición, `orphan_punctuation.absorb_adjacent_punctuation`):** el span de puntuación que toca (misma banda, hueco ≤1,5 pt) el texto de una unidad compuesta pasa a `absorbed_punctuation` de la unidad que posee el texto precedente. Se redacta con ella, sale de los guardas y del control de inmutables. La puntuación española la pone la traducción. No se absorbe si el texto precedente no se recompone (título publicado conservado, cifra protegida), ni si es final de unidad y la traducción no termina con ese signo: en ese caso no se borra en silencio y lo marca la QA.
- **No se cambia la extracción:** `source`/`spans` de las traducciones guardadas siguen siendo los mismos (procedencia validada en `reinsert_fidelity`), así que no hay retraducción. Contrapartida: el LLM no vio esas comas; la traducción decide la puntuación.
- **QA (`orphan_source_punctuation`, acción `recompose`):** cualquier span original de solo puntuación (sin comillas, que pueden ser decorativas) que sigue idéntico en el candidato y cuyo vecino con letras se reescribió, o con texto nuevo que lo interseca o queda a menos de 3 pt en su banda. Bloquea `local_fidelity_ok`.
- **Barrido:** de 174 traducciones guardadas, solo p9 y p21 cambian. Los puntos finales de p15, 17, 23, 26, 28, 37, 173 y 176, y las comas de p175, ya eran `relocated_punctuation`.

## Frases entre páginas y QA gramatical determinista (batch-21, 2026-11-09; pipeline-engineer, sin LLM)

Tests sobre páginas reales en `tests/test_audit_batch21.py`. Ninguna regla por número de página. Los prompts de `prompts/` y la guía G3 no cambian.

- **Frase partida (p14 «…and Egyptian» | p15 «symbolism.»).**
  - `page_context.story_continuations` detecta las continuaciones. Un párrafo de cuerpo sin puntuación final se une al siguiente párrafo del mismo tamaño en orden de lectura (columna, luego y), si este empieza en minúscula. Puede ser en la misma página (`column`) o del último de la página N al primero de N+1 (`page`).
  - No se unen tres casos: el párrafo cerrado por un subtítulo, una línea apilada debajo de la cola o encima de la cabeza (citas de p175/p176) y las líneas de índice (≥3 números).
  - En las 192 páginas hay 3 uniones entre páginas (12→13, 14→15, 18→19) y unas 235 entre columnas, sobre todo entradas de signos partidas.
  - `consistency.build_plan(units, joins)` envía la última frase de la cola y la primera de la cabeza como una sola petición. Un marcador `{vK}` señala el corte y `resolve_plan` reparte cada lado en su unidad, con `story_continuation` (socio y clave).
  - Si la otra página no está en la selección, `adapters.fidelity_translate` añade solo el fragmento vecino, como contexto que no se escribe (`neighbour_fragment`). La memoria de frases guarda la respuesta conjunta, así que el lote siguiente la reutiliza sin llamada nueva.
- **Instrucción nueva, en código y no en el prompt.** `page_context.STORY_BREAK_INSTRUCTIONS` viaja por fila como `story_break_instructions` y forma parte de la clave de caché del bloque. Ordena traducir la frase entera y dejar el marcador una vez, en límite de palabra, con al menos una palabra a cada lado.
- **Validación con reintento `validation_error`** (`llm._validate` y la revalidación de cachés):
  - Marcador de corte ausente, repetido, con un lado vacío o dentro de una palabra.
  - Subtítulo que empieza en minúscula (`_initial_upper`, ver abajo).
  - `preposition_regime`, dentro de `text_issues`: régimen preposicional omitido en verbos que siempre lo rigen (carecer/adolecer/prescindir/percatarse/jactarse de, consistir en) en una oración de relativo. Para depender/insistir/renunciar/referirse, solo con sujeto explícito entre «lo que» y el verbo. Caso: p17 «lo que la astrología carece».
  - Las memorias y cachés guardadas que fallan se descartan y se piden de nuevo vía `run_task`.
- **Subtítulos.** `page_context.initial_upper_units` cubre las etiquetas `running_text` de tamaño ≥ cuerpo de su página, con mayúscula inicial en el original, que no son todo mayúsculas ni continúan una línea apilada. Las etiquetas de diagrama más pequeñas (p87 «The wounded healer») quedan fuera. El código de QA es `heading_initial_case` (retraducir). En las traducciones guardadas de producción solo aparece p21.
- **QA entre páginas** (`page_context.cross_page_continuity`, integrada en `production.compose_and_qa`):
  - Bloquea `cross_page_sentence_not_joint` si el fragmento de esta página se tradujo aparte.
  - Avisa `cross_page_partner_stale` si esta página ya tiene la versión conjunta y la vecina no.
- **Cifras protegidas: aviso, nunca cambio.** G3 dice «Conservar literalmente toda numeración… no convertir… separadores… La guía no autoriza normalizar ninguno de ellos». Por eso «25,000» (p14) y «siglo 4» (p12, también p15 «siglo 19») no se tocan. `text_quality.format_warnings` los anota en `qa['text_warnings']` (`english_thousands_separator`, `arabic_century_numeral`, acción `none`) y no afectan a `local_fidelity_ok`. Convertirlos exigiría un suplemento G3 aprobado.

Retraducción necesaria: 14, 15 (frase conjunta), 17 (régimen), 21 (subtítulo), 12, 13, 18 y 19 (uniones entre páginas, que ahora la QA exige conjuntas). Las frases ya válidas salen de la memoria; solo las uniones y las memorias rechazadas llaman al modelo.
- **batch-30 (p80–99) sin auditar (2026-11-09):** la auditoría se detuvo por la orden «para todo» de andres. Queda pendiente al retomar.
