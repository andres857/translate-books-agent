# SDK oficial de Codex — API verificada

Verificado en este entorno con `uv add openai-codex`: **openai-codex 0.163.0**, dependencia **openai-codex-cli-bin 0.163.0**. La CLI global es 0.165.0. El lock conserva ambas versiones; el SDK usa su runtime incluido por defecto. Se ejecutó `python -m pydoc openai_codex`, `help(Codex)`, `help(Codex.thread_start)` y `help(thread.run)` sobre un hilo real efímero, sin ejecutar inferencia para esa inspección.

La [documentación oficial](https://learn.chatgpt.com/docs/codex-sdk) describe Python como versión estable; el supuesto de beta del prompt ya no coincide con lo publicado. La implementación se apoya en las firmas del paquete instalado, no en ejemplos de TypeScript.

## Firmas verificadas

`Codex(config: CodexConfig | None = None)` inicia el app-server local mediante JSON-RPC. El context manager cierra sus recursos.

`Codex.thread_start` admite `model`, `cwd`, `config`, `ephemeral`, `sandbox`, `approval_mode`, `base_instructions`, `developer_instructions` y otras opciones. El modelo se fija con `model='...'`.

`thread.run(input, *, effort=None, model=None, output_schema=None, sandbox=None, approval_mode=None, ...)` devuelve `TurnResult`. El razonamiento se fija directamente por turno con `effort='low'` o `'high'`. No hace falta simular un campo inexistente en `thread_start`.

`thread.turn(...)` acepta las mismas opciones y devuelve `TurnHandle`, con `stream()`, `interrupt()` y `run()`. Se prefiere consumir su stream para detectar errores estructurados y cualquier intento de herramienta antes de aceptar el resultado.

`CodexConfig` admite `codex_bin`, `launch_args_override`, `config_overrides`, `cwd`, `env`, y metadatos de cliente. No hay argumento `profile`. La prueba real encontró una diferencia: app-server 0.163 rechaza tanto `--profile translator` como el override legacy `profile="translator"`. El backend SDK lee `translator.config.toml` y aplica sus valores mediante `config_overrides`; el backend CLI usa `-p translator`. Se usa el mismo contenido del perfil, sin alterar configuración global. Se deshabilitan además los nombres MCP declarados en configuración global y perfil.

## Aislamiento

Presets expuestos: `Sandbox.read_only`, `Sandbox.workspace_write`, `Sandbox.full_access`. Se usa **read_only** y **ApprovalMode.deny_all** (la otra opción es auto_review). Solo lectura no significa que no existan herramientas: por eso se deshabilitan shell, exec, apps, plugins, navegador, web y subagentes en el perfil, y se rechazan eventos de herramienta.

El directorio vacío está bajo `work/.codex_llm_sandbox/`, con un subdirectorio efímero por llamada. Como ese directorio tiene ancestros dentro del proyecto, cambiar solo cwd NO basta: `project_doc_max_bytes=0` impide cargar AGENTS de ancestros; `skills.include_instructions=false`, `features.skip_host_skill_discovery=true` y desactivación de plugins evitan catálogo/instrucciones de skills. El perfil translator tiene web disabled y MCP deshabilitados explícitamente. No se ignoran reglas de seguridad mediante `--ignore-rules`.

No se copian credenciales. El SDK y la CLI usan su almacén de autenticación existente. La entrada del libro solo llega como contenido filtrado/JSON y, para VLM, como imágenes previamente enmascaradas por QA. Un hilo nuevo y `ephemeral=True` por llamada: no se reutiliza historial. No hay medición que justifique reutilizarlo.

## Salida y uso

`output_schema` existe y se pasa por turno. La validación local de JSON Schema, ids, marcadores, números/glifos restaurados y glosario sigue siendo obligatoria.

`TurnResult` contiene `id`, `status`, `error`, `started_at`, `completed_at`, `duration_ms`, `final_response`, `items` y `usage`.

`usage` es `ThreadTokenUsage | None`, con `last` y `total`; cada `TokenUsageBreakdown` incluye `input_tokens`, `output_tokens`, `cached_input_tokens`, `reasoning_output_tokens`, `total_tokens` y opcionalmente `cache_write_input_tokens`. Con hilos efímeros se registra `last`, sin sumar otra vez un acumulado del historial. Si falta usage se registra null, nunca cero inventado.

## Cuota y errores

El protocolo generado declara `CodexErrorInfo` con `usageLimitExceeded` y `rateLimitExceeded`. `TurnCompletedNotification.turn.error.codex_error_info` conserva esa señal. El método de alto nivel `.run()` llama `_raise_for_failed_turn` y convierte el error a `RuntimeError(message)`, perdiendo el código estructurado. El adaptador consume `thread.turn(...).stream()` para conservarlo; no se inventa una excepción `SubscriptionQuotaExceeded` del SDK.

Para codex_exec se inspeccionan eventos JSONL `turn.failed`/`error` y, cuando solo queda texto, mensajes conocidos de cuota. Un error genérico, autenticación o modelo no disponible no se clasifica automáticamente como cuota. Las pruebas de cuota son simuladas; no se agota deliberadamente la suscripción.

Backoff y persistencia pertenecen al pipeline, no al SDK. Tras el calendario configurado se guarda quota_pause.json, la página queda pending y la CLI termina con 75. No se activa facturación API por disponer accidentalmente de una clave: requiere configuración explícita.

## Autenticación y modelos comprobados

`codex login status` devolvió **Logged in using ChatGPT**. También se consultaron cuenta y catálogo con el SDK oficial sin imprimir identidad ni credenciales. El catálogo ofrece gpt-6-luna, gpt-6-astra, gpt-6.1-sol y otros; no gpt-5.4-mini ni gpt-5.5. Los modelos disponibles anuncian low/medium/high/xhigh y niveles superiores, pero no minimal. El mapeo por disponibilidad autorizado en el proyecto conserva funciones: barato→gpt-6-luna, fuerte→gpt-6-astra, minimal→low. Es un mapeo de funciones, no una equivalencia de tarifas.

## CLI alternativa

La CLI verificada admite `codex exec --ephemeral --sandbox read-only -m MODEL -c model_reasoning_effort='"low"' --output-schema FILE --json -`, perfil `-p translator`, `--skip-git-repo-check` y prompt por stdin. Con `--json`, stdout es **JSONL de eventos**, no un único documento de traducción; hay que extraer el mensaje final del agente y el evento de uso. No se analiza stderr como respuesta del modelo.

## Evidencia de BabelDOC

BabelDOC 0.6.4 permite inyectar `BaseTranslator` mediante `TranslationConfig(translator=...)`. Sin embargo, `BaseTranslator.translate/llm_translate` reciben texto y parámetros de rate limit, no una identidad estable de página. El middle-end clásico traduce por párrafo; `il_translator_llm_only.process_page` sí agrupa `BatchParagraph`, pero descarga al superar 200 tokens o cinco párrafos e incorpora lotes entre páginas/columnas. No equivale a una llamada por página. Además, `il_translator.translate_paragraph` captura excepciones y continúa, lo que podría ocultar una pausa de cuota. No basta reemplazar la clase por Codex para obtener agrupación garantizada por página: necesitaría una fase previa de recolección/IR y devolución sincronizada. No se expondrá un servidor compatible ni se harán llamadas por frase bajo el nombre de agrupación.

La ruta propia PyMuPDF extrae spans seguros de toda una página, agrupa hasta el límite de caracteres y reinserta conservando contenido protegido. Su medición real y la comparación acotada con el contrato segmentario se documentan en el informe del backend; no se afirmará un ratio de tiempos no medido.

## Prueba de aislamiento real

El probe sintético devolvió JSON válido `{status: ok}` en 3,267 s con 7.155 tokens de entrada y 15 de salida (gpt-6-luna, low). El stream no mostró herramientas; cualquier item distinto de mensaje/razonamiento se rechaza. `debug prompt-input` del perfil no contenía AGENTS del proyecto ni catálogo de skills. Las instrucciones generales de Codex siguen presentes y explican parte de la sobrecarga. No se equipara read-only con ausencia de lectura; se deshabilitan herramientas y se inspeccionan eventos.
