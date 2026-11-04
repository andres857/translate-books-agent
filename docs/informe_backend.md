# Fase 1b — backend Codex

Actualización posterior: el humano aprobó parcialmente backend y ruta, dispensó el benchmark 3× y ordenó Fase 1c antes del piloto. Ver `docs/decisiones.md` y `work/backend_approval.json`; el resultado siguiente conserva la evidencia histórica de 1b.

Fecha: 2026-11-03. Libro: *Astrology: Using the Wisdom of the Stars in Your Everyday Life*, Carole Taylor, 258 páginas. **G3 aprobada por el usuario; G1b pendiente, con limitaciones abiertas.** No se ha aprobado ni ejecutado el piloto de 20 páginas o producción.

## Resultado del motor

Se implementó la entrada única `astro_translate.llm.run_task`, con `codex_sdk` por defecto, `codex_exec` alternativo y `openai_api` opcional. Configuración efectiva: **codex_sdk, gpt-6-luna/low, fallback=none**. Las tareas fuertes usan gpt-6-astra/high según el catálogo verificado. Las traducciones se ejecutaron con autenticación ChatGPT, sin clave de API.

El SDK instalado es **openai-codex 0.163.0**, con runtime incluido 0.163.0; CLI del entorno 0.165.0. Las firmas, diferencias respecto al prompt y señales de cuota están en [codex_sdk_api.md](codex_sdk_api.md). El SDK aplica el contenido del perfil `translator` mediante `config_overrides`; su app-server no admite `--profile`. La alternativa CLI sí admite `-p translator`.

Cada petición crea un hilo efímero, en directorio vacío, con sandbox de solo lectura, herramientas/web/MCP desactivados y rechazo de eventos de herramientas. Se comprobó que el contexto no carga AGENTS ni skills del proyecto. Se conserva la sobrecarga de instrucciones generales de Codex. El probe sintético real tardó 3,267 s y consumió 7.155 tokens de entrada y 15 de salida, sin herramientas.

## Prueba real

La muestra principal contiene página 15 de texto, página 7 con tabla de contenidos en columnas y página 121 con carta y etiquetas vectoriales. La página 7 **no es una tabla de efemérides**. Se añadieron las páginas 14 y 133 para medir cinco páginas.

| Página externa | Tipo de muestra | Spans seguros / totales | Caracteres enviados | Tokens entrada | Tokens salida | Latencia LLM |
|---|---|---:|---:|---:|---:|---:|
| 7 | Tabla en columnas | 41 / 81 | 493 | 9.335 | 814 | 17,85 s |
| 15 | Texto corrido | 50 / 52 | 1.620 | 9.715 | 1.260 | 25,50 s |
| 121 | Carta y etiquetas | 48 / 67 | 1.101 | 9.489 | 1.057 | 21,52 s |
| 14 | Texto complementario | 37 / 40 | 1.341 | 9.405 | 1.010 | 21,06 s |
| 133 | Diagramas de aspectos | 83 / 119 | 1.757 | 9.726 | 1.340 | 27,70 s |

**5/5 respuestas de translate_page válidas al primer intento: JSON, esquema, ids, placeholders y glosario. Cero reintentos de traducción.** La muestra principal cumple 3/3. Cada página requirió una llamada, nunca una llamada por frase. Estas métricas corresponden al primer envío real; las repeticiones de reinserción reutilizan la caché y no deben sustituirlas por tiempos cero.

La selección excluye spans completos con cifras, glifos, fuentes simbólicas y abreviaturas protegidas; se preservan localmente. Por eso las métricas de cumplimiento cubren los spans enviados y **no acreditan que toda la página haya quedado en español**. La validación terminológica es determinista, sin evaluación editorial humana todavía. No se ha ejecutado un juez visual de estas páginas ni se ha marcado ninguna como terminada por este smoke.

Evidencia: `books/astrology-wisdom-stars/work/backend_smoke/pNNNN-translation.json`, informes por página y `work/costs.jsonl`. Los tiempos son de la llamada completa al SDK, incluida su sobrecarga, no solo de generación.

### Reinserción y acortado: resultado distinto de la traducción

La reinserción reutilizó las traducciones de las tres páginas principales desde la caché. Se ajustaron las cajas de extracción a la altura de los glifos mediante PyMuPDF para evitar colisiones falsas entre líneas; se mantienen validaciones de spans protegidos y redacciones individuales. Aun así, quedaron spans sin espacio suficiente.

El acortado agrupó los spans fallidos por página. Se realizaron **tres intentos por página (petición, corrección, ids problemáticos), nueve respuestas en total**, que fueron rechazadas por límites de caracteres; la primera respuesta de página 7 también incumplió una forma del glosario. No se aceptó ninguna salida inválida ni se cambió el glosario para hacerla pasar. Por tanto, el 100 % de éxito informado arriba **solo corresponde a translate_page**; no corresponde al flujo completo con acortado. No puede declararse cumplida toda la validación de G1b.

Las páginas 7, 15 y 121 permanecen en revisión. **No se entrega un PDF traducido como si hubiera pasado el ajuste o QA**; los JSON reales están disponibles para inspección y reutilización. Resolver la composición de líneas y el espacio seguirá siendo necesario antes del piloto. Las versiones aprobadas de glosario y estilo, los límites y el original no se alteraron.

Tras observar estos fallos se añadió un preflight local: si una forma obligatoria del glosario ya supera `max_chars`, se envía a revisión sin gastar otra llamada. No se ha repetido la inferencia para presentar como desaparecidos los fallos medidos. La ruta actual también aplica escala 1 a los spans de páginas con dibujos vectoriales como protección conservadora; distinguir texto corrido de etiquetas dentro de esas páginas sigue siendo una mejora de maquetación pendiente.

## Pruebas y comprobaciones finales

**55 tests pytest pasan**, confirmados también por el orquestador en una ejecución independiente. Incluyen FakeBackend, esquema/ids/glosario/placeholders, protección de contexto, caché por bloque y por petición, partición de textos largos, límite imposible de acortado, pausa y código 75, propagación de cuota desde reinserción, exclusión de imports externos a llm.py, parsing JSONL de CLI, configuración Responses y aislamiento de MCP global/perfil/ancestros. La cuota agotada, CLI alternativo y API de pago se probaron con simulaciones; no se agotó deliberadamente la suscripción ni se hizo una llamada API de pago.

Se validaron todos los esquemas JSON y archivos TOML. G3 sigue vigente por hash. El original conserva SHA-256 `d13ab81406a66d3ab9863a9c2350fb326e8356d914f2f303ba3e6d4cbdc88046` y permisos 0444. No existen aprobaciones automáticas de G1b o G4. Las capturas originales para revisar el ajuste están en `work/backend_selection/p007.png`, `p015.png` y `p121.png`, enlazadas desde la cola de revisión.

## Proyección observable

`scripts/estimate_quota.py` calcula sobre cinco páginas una media de **9.534 tokens de entrada, 1.096,2 de salida y 22,72 s por página**. Extrapolar una pasada de traducción nativa a las 258 páginas da **2.459.772 tokens de entrada, aproximadamente 282.820 de salida, 258 llamadas y 97,72 minutos acumulados de llamadas**. No es una predicción del tiempo total ni de la calidad final.

La muestra no es aleatoria y el libro incluye 29 páginas escaneadas. OCR, etiquetas adicionales, acortados, reintentos y QA requieren trabajo adicional. La concurrencia dos puede reducir tiempo de pared, pero no se afirma una aceleración medida. El SDK no expone la cuota restante como una asignación de tokens: **no se puede convertir esta proyección en porcentaje de suscripción ni garantizar que alcance para todo el libro**.

```bash
.venv/bin/python scripts/estimate_quota.py \
  --book books/astrology-wisdom-stars --pages 7,14,15,121,133 \
  --output books/astrology-wisdom-stars/work/backend_smoke/quota_projection.json
```

No hubo cargos por API de pago. Los tokens consumen la suscripción existente; no se ha calculado su precio ni capacidad. El registro distingue tokens ausentes de cero, caché de llamadas reales y backend efectivo del solicitado.

Consumo real completo de esta fase: **132.257 tokens de entrada y 6.438 de salida**, en 15 llamadas (un probe, cinco traducciones y nueve intentos de acortado), más tres eventos de caché sin tokens nuevos. Desglose: traducción 47.670/5.481; acortado 77.432/942; probe 7.155/15. Evidencia consolidada en `work/backend_smoke/measurement_summary.json`; el probe se registró retrospectivamente con esa marca explícita. La proyección anterior solo extrapola translate_page y no oculta el consumo de los intentos fallidos.

## BabelDOC y alcance de la ruta

BabelDOC 0.6.4 permite inyectar `BaseTranslator` en `TranslationConfig`. Su interfaz recibe segmentos y parámetros de rate limit sin una identidad estable de página. El middle-end clásico llama por párrafo; el alternativo agrupa al superar 200 tokens o cinco párrafos y contiene agrupaciones entre páginas/columnas. Además, algunos manejadores capturan errores del traductor y continúan, lo que impediría confiar en una pausa de cuota sin adaptar el middle-end.

**Decisión de implementación: usar la ruta propia PyMuPDF con agrupación por página.** No se implementó un proxy ni un `CodexTranslator` que fingiera agrupar peticiones independientes. Integrar BabelDOC conservando el contrato requeriría una fase de recolección por página y adaptación del IR/middle-end, además de verificar la exclusión integral del contenido protegido.

La ruta propia tiene una medición real de cinco páginas, presentada arriba. **No existe todavía una comparación de tiempo extremo a extremo contra BabelDOC en esas cinco páginas; no se ha demostrado el umbral de 3×.** La elección se fundamenta en cumplir agrupación y protección con la interfaz implementada, no en una supuesta ventaja medida de velocidad. Este punto se presenta explícitamente para la decisión humana de G1b.

También se ejecutó el `process_page` real de BabelDOC sin LLM, con un IR controlado formado por los spans seguros extraídos localmente: produjo **7, 7, 9, 8 y 14 lotes**, respectivamente, para páginas 7, 14, 15, 121 y 133. La ruta propia produjo una llamada real por página. El ensayo caracteriza el planificador; la segmentación no procede del frontend de BabelDOC y se omitieron lotes entre páginas/columnas. Por tanto, esos conteos no son una predicción de llamadas de producción ni una comparación de calidad. Script: `scripts/measure_babeldoc_batching.py`; evidencia: `work/backend_smoke/babeldoc_batching.json`.

## Aprobación pendiente

Revisar este informe, [la API comprobada](codex_sdk_api.md), [las decisiones](decisiones.md), `config/pipeline.toml` y los artefactos de la muestra. **No se declara G1b superada automáticamente:** falta resolver el acortado y no se completó la comparación de latencia exigida por la opción segmentaria de BabelDOC. El humano puede aceptar explícitamente estas limitaciones para el backend/ruta, exigir su resolución antes de aprobar o elegir otro backend/fallback.

La decisión propuesta corresponde a **codex_sdk con fallback=none y ruta propia PyMuPDF**. Aceptarla no equivale a aprobar calidad de maquetación, G4 ni producción. No existe `work/backend_approval.json` hasta que el usuario tome esa decisión.
