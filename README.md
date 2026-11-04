# Traducción de libros de astrología

Pipeline inglés → español con conservación de glifos, cifras y gráficos. Reglas en [AGENTS.md](AGENTS.md), fases en [PROMPT_AGENTES.md](PROMPT_AGENTES.md), decisiones verificadas en [docs/decisiones.md](docs/decisiones.md).

## Entorno local

```bash
uv sync --locked
.venv/bin/python -m pytest -q
.venv/bin/python -m astro_translate doctor
```

El entorno usa Python 3.12. Las dependencias están fijadas en `uv.lock`. Las fuentes Noto y su licencia OFL están en `fonts/`. PyMuPDF se usa bajo AGPL; BabelDOC también declara AGPL.

## Libro actual

`books/astrology-wisdom-stars/source.pdf` es la copia verificada y de solo lectura del libro de Carole Taylor elegido por el usuario. Los resultados se escriben en `work/` y `out/`; las entradas originales en `books/` permanecen intactas.

```bash
.venv/bin/python -m astro_translate triage --book books/astrology-wisdom-stars
.venv/bin/python -m astro_translate glossary-build --book books/astrology-wisdom-stars
```

Sin `--api`, glossary-build crea un inventario local de candidatos; no traduce el libro ni aprueba el glosario. Las ocho skills de `.agents/skills/` exponen los comandos de cada fase mediante `scripts/run.py`.

## Gates y motor LLM

G3 exige aprobación humana del glosario y estilo; el usuario ya aprobó la versión actual para el libro elegido. La nueva G1b aprueba el backend tras su prueba de tres páginas; G4 aprueba el piloto antes de producción y G7 revisa el final. No crear aprobaciones para eludir estas gates.

Todas las llamadas pasan por `astro_translate.llm.run_task`. Por defecto se usa `openai-codex` con login ChatGPT, sin clave de API. `codex_exec` es una alternativa oficial; Responses API es un respaldo opcional, únicamente si se configura y existe `OPENAI_API_KEY`. No se usan proxies ni endpoints privados.

```bash
codex login status
.venv/bin/python -m astro_translate doctor
```

Modelos, esfuerzos, prompts y esquemas se resuelven en `[tasks]` de `config/pipeline.toml`. Los modelos solicitados se mapearon por disponibilidad: tareas baratas a gpt-6-luna/low y fuertes a gpt-6-astra/high. Los agentes de `.codex/agents/` son una capa independiente.

El perfil dedicado está en `.codex/translator.config.toml` y en `$CODEX_HOME/translator.config.toml`. La CLI alternativa lo selecciona con `-p translator`; el SDK 0.163 aplica su contenido mediante config_overrides porque su app-server no admite ese flag. No se modifica el perfil del orquestador. Cada unidad usa un hilo efímero aislado, herramientas desactivadas y datos protegidos excluidos. Detalle de la API real en [docs/codex_sdk_api.md](docs/codex_sdk_api.md).

Las respuestas se validan y cachean en `work/llm_cache.sqlite`. Costos/uso se registran en `work/costs.jsonl`; el consumo de suscripción no recibe descuento Batch API. Ante cuota persistente se guarda `work/quota_pause.json` y se termina con **75**: es una pausa que debe respetarse, no un error para reintentar en bucle.

Un pytest satisfactorio acredita el alcance de los tests, sin sustituir las pruebas reales ni la revisión editorial. El estado de G1 está en [docs/informe_bootstrap.md](docs/informe_bootstrap.md). [El informe de G1b](docs/informe_backend.md) contiene la prueba real de cinco páginas, la proyección y las limitaciones de acortado y comparación con BabelDOC. G1b fue aprobada parcialmente para backend y ruta; la comparación 3× se dispensó por decisión humana. No cubre maquetación, acortado ni G4. Las fases 1c/1d se limitaron inicialmente a7/15/121. La autorización posterior permitió ejecutar el piloto20 con condiciones; la producción sigue sin autorización.

La muestra y su proyección se reproducen mediante scripts; volver a solicitar las traducciones vigentes usa la caché:

```bash
.venv/bin/python scripts/smoke_backend.py --book books/astrology-wisdom-stars --pages 7,15,121 --no-reinsert
.venv/bin/python scripts/estimate_quota.py --book books/astrology-wisdom-stars --pages 7,14,15,121,133
.venv/bin/python scripts/measure_babeldoc_batching.py --book books/astrology-wisdom-stars
```

El tercer comando caracteriza la agrupación de BabelDOC sin llamadas LLM; no mide el tiempo de su pipeline PDF completo. Los PDF finales siguen sujetos a las gates de maquetación y QA.

Las formas cortas aprobadas están en `glossary/astro_en_es_short.csv`, con autorización separada por hash en el work del libro. Solo etiquetas de figuras, tablas y leyendas; nunca texto corrido. El CSV original aprobado en G3 no se modifica.


## Fase 1d — antecedente del piloto

La muestra 7/15/121 cumple los criterios técnicos de Fidelidad: cero fallos, escala mínima 0,91, cero inglés editorial detectado (salvo PLANETS decorativo aprobado), conservación de imágenes/símbolos y diferencia fuera del texto = 0. Suite de 109 tests. El usuario revisó esta muestra y autorizó el piloto con cuatro correcciones previas, ya incorporadas. La aprobación final del piloto y la producción siguen pendientes.

[Informe de Fidelidad](docs/informe_fidelidad.md), [comparativa original | traducción](books/astrology-wisdom-stars/out/fidelidad_1d_comparativa.pdf), [muestra traducida](books/astrology-wisdom-stars/out/fidelidad_1d.pdf).

```bash
.venv/bin/python scripts/fidelity_sample.py --book books/astrology-wisdom-stars
.venv/bin/python scripts/finalize_fidelity_audit.py --book books/astrology-wisdom-stars
```

Estos comandos recomponen y auditan la muestra autorizada desde traducciones guardadas, sin llamar al modelo ni ejecutar el piloto.


## Piloto20 — detenido para revisión visual humana

Se ejecutaron veinte páginas (diecinueve nativas y una portada raster real), más un anexo técnico separado de ingresos de Mercurio. Se incorporó la redacción final aprobada de las reglas de corrección gramatical y comillas en la guía G3. El original y el CSV principal permanecen intactos.

[Informe del piloto e incidencias](docs/informe_piloto.md), [comparativas de veinte páginas](books/astrology-wisdom-stars/out/piloto_comparativa.pdf), [piloto traducido](books/astrology-wisdom-stars/out/piloto.pdf). [Anexo y sus límites](docs/informe_ruta_c_anexo.md).

La geometría y la conservación gráfica no equivalen a aprobación editorial. Quedan etiquetas curvas en inglés, notación ordinal, una colisión terminológica y advertencias de legibilidad. El juez VLM funciona sobre texto nativo enmascarado; no certifica ilustraciones ni contenido protegido. Todas las páginas del piloto quedan en revisión. **No iniciar producción sin la siguiente decisión humana.**
