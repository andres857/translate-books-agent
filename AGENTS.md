# Traducción editorial de libros de astrología

Lee `PROMPT_AGENTES.md`, `docs/decisiones.md` y la skill de la fase. `search.md` y los PDFs originales son de solo lectura.

- Traducción masiva únicamente mediante scripts/API; nunca traducir párrafos en la conversación o en el contexto de agentes.
- Fase 1d: proteger tokens numéricos, grados y glifos mediante marcadores {nN} dentro del texto traducible; nunca enviar sus valores al LLM. Restaurar los mismos valores localmente. Los spans en fuentes de símbolos siguen protegidos completos, y las siglas astrológicas aisladas se conservan. La prosa que incluye números ya no se omite entera. Fechas EN→ES mediante script determinista, sin LLM. En QA visual excluir valores protegidos de la entrada del juez.
- Ninguna traducción real sin aprobación humana vigente del glosario CSV y guía de estilo. Cambios invalidan la aprobación por hash. No crear aprobaciones ficticias.
- `books/<libro>/source.pdf` es inmutable; escribir solo en `work/` y `out/` de ese libro. No sobrescribir PDFs de entrada.
- Estado reanudable en `work/manifest.json`, escrituras atómicas y locks. No repetir páginas con QA vigente.
- Una página solo termina con QA satisfactorio. Fallos a `review_queue.json` con motivo y capturas. Los casos manuales no cuentan como QA aprobado.
- Fase 1c: texto corrido se compone por bloque/párrafo con insert_htmlbox; redactar por span para etiquetas o texto que intersecta rectángulos de dibujos visibles. Preservar imágenes, vectores y contenido protegido; fondos raster no se eliminan. La clasificación y escala son por bloque, nunca por mera presencia de dibujos en la página.
- Fase 1d: reutilizar la fuente original extraída si has_glyph confirma cobertura completa del texto traducido. En grupos evaluar cobertura conjunta para no mezclar estilos de vecinos (incluidos Aries/Leo idénticos). Si falta cobertura, usar reemplazo equivalente serif/sans, ancho, peso e italic. Conservar color y énfasis por span; verificar Í mayúscula. No reutilizar fuentes de símbolos para texto nuevo.
- Escala mínima general 0,75; <0,80 exige advertencia/revisión. En Fase 1d las etiquetas también se ajustan por grupo con mínimo 0,75, comprobando que no tapen dibujos. Etiquetas: ampliar a espacio libre → short_target aprobado en suplemento separado → LLM como último recurso. Para menos de 15 caracteres no usar ratio 1,10; límite configurado y restricciones de geometría/glosario. No usar propuestas de formas cortas sin aprobación por hash.
- Registrar modelo, tokens y costos de llamadas en `work/costs.jsonl`; costos desconocidos no son cero. Nunca registrar claves.
- PyMuPDF se usa bajo AGPL. No cambiar la decisión de licencia sin preguntar.
- Lotes de 20 páginas, partes BabelDOC ≤50. Tres errores repetidos detienen el lote. G5 exige ≥90 % QA OK.
- Gates humanas obligatorias: G1b backend Codex, G3 glosario/estilo, G4 piloto, G7 final. Revisión humana de todas las páginas con gráficos y muestra del texto.
- Subagentes solo cuando la fase lo indique. Profundidad máxima uno: los subagentes no crean otros. En esta sesión máximo tres subagentes simultáneos más el orquestador.
- `pipeline_engineer` trabaja con TDD y pytest. No declarar G1 con pruebas omitidas ni presentar mocks como integración real. Los módulos incompletos deben bloquearse explícitamente.

Modelos de agentes y modelos API son capas independientes. Ver `docs/decisiones.md` para el mapeo autorizado por disponibilidad. No cambiar el modelo activo o permisos de la sesión por suponer que una configuración de proyecto lo hace.

## Capa B mediante SDK oficial Codex

- Toda llamada LLM de los scripts pasa por `astro_translate.llm.run_task`. Solo llm.py puede importar openai_codex u openai.
- Los hilos de Codex usados como LLM se ejecutan en `work/.codex_llm_sandbox/` con el perfil `translator`; nunca en la raíz del repo. Desactivar instrucciones de ancestros, skills, herramientas, web y MCP; sandbox read-only, sin aprobaciones, hilo efímero por unidad.
- Prohibido usar proxies o endpoints privados de ChatGPT. Usar SDK/CLI oficiales con login ChatGPT; API de pago solo según configuración explícita y clave disponible.
- Fase 1c: agrupar varias páginas nativas por llamada hasta 6000 caracteres, con ids que incluyan página; etiquetas por figura. No una llamada por frase. Cachear con SQLite y hashes de todas las dependencias. Nunca cachear salida inválida.
- Conservar ids, placeholders, glifos, números y términos aprobados. Los valores protegidos originales se excluyen antes de la llamada, se validan y restauran localmente.
- Ante cuota persistente: manifest pending, quota_pause.json y salida75. Respetar espera antes del lote; no reintentar en bucle.
- La capa B Codex consume cuota de suscripción; no aplica descuento Batch API. Registrar backend efectivo, tokens/latencia/caracteres, cache y fallback; USD desconocidos no son cero.
- G5 exige cero páginas con backend diferente del aprobado sin constancia en costs.jsonl. No saltar G1b antes del piloto20 o producción.

## Fase 1c y aprobación parcial G1b

G1b aprobada solo para backend codex_sdk, modelos luna/low y astra/high, fallback=none y ruta PyMuPDF. Comparación de latencia BabelDOC dispensada explícitamente. El registro work/backend_approval.json no aprueba maquetación, acortado ni G4; autoriza reinsertion y, por instrucción posterior explícita, fidelity sobre páginas 7, 15 y 121. No ejecutar piloto hasta nueva instrucción. Cerrar 1c con cero spans fallidos, escala >=0,75 y visual-qa ejecutado; informar resultados y detenerse. Glosario G3 y original permanecen intactos.

## Fase 1d Fidelidad — autorización actual

Solo páginas 7, 15 y 121, fase `fidelity`; no G4 ni producción. Protección granular y reutilización de fuentes autorizadas prevalecen sobre reglas anteriores de 1c. Escala uniforme por lista/columna/grupo de etiquetas hermanas, mínimo 0,75. Cambios de tamaño/peso delimitan unidades; título y cuerpo separados, énfasis inline conservado. Unir líneas del mismo párrafo aunque haya huecos. Igual frase de origen implica igual traducción; QA comprueba consistencia y cita destacada de p15. Pares Ascendente/Descendente: ambos completos primero; ambos cortos solo si necesario y con suplemento aprobado. MC/IC se conservan como tokens originales. Cerrar con cero inglés fuera de tokens protegidos, estilos/color/pesos conservados, escala grupal uniforme, cero fallos y diferencia fuera del texto cero. Entregar renders lado a lado y detenerse para revisión humana visual.

Excepción visual 1d aprobada: conservar las letras decorativas PLANETS de la ilustración raster de p7. Documentarlas como excepción explícita de inglés; nunca extenderla al texto editorial. Registro en work/fidelity/artwork_exception.json.


## Autorización humana actual: correcciones y piloto20

El usuario aprobó G4 con condiciones y autorizó ejecutar el piloto después de corregir negrita de signos p7, medianil y párrafos p15, y concordancia de Nodos p121. No se abre otra fase. Se aprueba la redacción final de dos adiciones a la guía G3: correcciones gramaticales objetivas registradas con el original, sin cambiar contenido/datos/estilo; comillas inglesas “ ” y simples ‘ ’ para citas internas. Historial y nuevos hashes en work/approval_history y glossary_approval.json. Selección en docs/seleccion_piloto.md; anexo Mercurio separado. Intentar juez VLM con exclusiones seguras y declarar cobertura real. Entregar veinte comparativas, consumo frente a proyección e incidencias; detenerse para revisión visual del piloto, sin producción. Esta autorización reemplaza las prohibiciones históricas de ejecutar el piloto, no autoriza producción.
