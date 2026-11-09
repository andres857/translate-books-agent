# Traducción de libros de astrología

Pipeline inglés → español con conservación de glifos, cifras y gráficos. Reglas en [AGENTS.md](AGENTS.md), fases en [PROMPT_AGENTES.md](PROMPT_AGENTES.md), decisiones verificadas en [docs/decisiones.md](docs/decisiones.md).

## Workflow actual: Claude Code (desde 2026-11-08)

La orquestación con Codex quedó obsoleta al perder la suscripción; ver [docs/retrospectiva_codex.md](docs/retrospectiva_codex.md). El flujo vigente está en [docs/workflow_claude.md](docs/workflow_claude.md):
- capa B: backend `claude_cli`, que ejecuta `claude -p` con Sonnet 5.5 sobre la suscripción, sin herramientas ni memoria y dentro de un sandbox;
- agentes: `.claude/agents/` (`structure-analyst`, `pipeline-engineer`, `book-auditor`);
- skills: `.claude/skills/` (`onboard-book`, `produce-batch`, `audit-pages`);
- hooks: `.claude/settings.json`;
- criterios de auditoría: [docs/criterios_aceptacion.md](docs/criterios_aceptacion.md).

`.codex/` y `.agents/skills/` se conservan solo como referencia histórica. Las secciones siguientes describen el diseño anterior basado en Codex.

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

## Estado de libros

- `books/the-secrets-of-astrology`: producción completa delegada a Claude por autorización explícita del 2026-11-06; actualmente en Fase 2, con retraducciones 2H y almacenamiento/ensamblado 2I en curso. La delegación cubre decisiones G4–G7 para este libro; el original sigue siendo inmutable.
- `books/astrology-wisdom-stars`: permanece en fase de piloto; no está autorizado para producción.

## Capas del pipeline

| Capa | Módulos | Responsabilidad |
|---|---|---|
| Orquestación | `core.py`, `production.py`, `adapters.py`, `__main__.py` | Gates, lotes reanudables, estado, comandos y flujo por libro |
| LLM y uso | `llm.py`, `translation_context.py`, `retranslation.py` | Backend oficial, validación, caché, protección y registro de consumo |
| Composición nativa | `fidelity.py`, `fonts.py`, `layout.py`, `*_layout.py` | Texto PDF, geometría, estilos, glifos y candidatos de página |
| Figuras y raster | `vertical_labels.py`, `native_rotated.py`, `raster_pilot.py`, `scanned_page_rebuild` | Etiquetas, regiones raster y reconstrucción local |
| QA y ensamblado | `safe_vlm.py`, `text_quality.py`, `pilot_assembly.py`, `candidate_storage.py` | QA local/VLM seguro, compactación y PDFs finales/comparativas |
| Políticas por libro | `profiles.py`, `secrets_ordinal_policy.py`, perfiles con hash | Hooks y excepciones editoriales ligadas al original |

## Gates y motor LLM

G3 exige aprobación humana del glosario y estilo; el usuario ya aprobó la versión actual para el libro elegido. La nueva G1b aprueba el backend tras su prueba de tres páginas; G4 aprueba el piloto antes de producción y G7 revisa el final. No crear aprobaciones para eludir estas gates.

Todas las llamadas pasan por `astro_translate.llm.run_task`. El backend vigente es `claude_cli` (`claude -p` oficial con el login de suscripción, sin clave de API; ver [docs/workflow_claude.md](docs/workflow_claude.md)). Responses API (`openai_api`) es un respaldo opcional, únicamente si se configura y existe `OPENAI_API_KEY`. No se usan proxies ni endpoints privados.

```bash
claude auth status
.venv/bin/python -m astro_translate doctor
```

Modelos, esfuerzos, prompts y esquemas se resuelven en `[tasks]` de `config/pipeline.toml`. Los agentes de `.claude/agents/` son una capa independiente.

Los backends `codex_sdk` y `codex_exec` se retiraron el 2026-11-08 (dependencia `openai-codex` incluida). Sus nombres siguen siendo válidos en registros históricos (`costs.jsonl`, aprobaciones, caché), pero configurarlos produce el error «Codex backend retired 2026-11-08» y `doctor` lo marca como error. Documentación histórica en [docs/codex_sdk_api.md](docs/codex_sdk_api.md).

Las respuestas se validan y cachean en `work/llm_cache.sqlite`. Costos/uso se registran en `work/costs.jsonl`; el consumo de suscripción no recibe descuento Batch API. Ante cuota persistente se guarda `work/quota_pause.json` y se termina con **75**: es una pausa que debe respetarse, no un error para reintentar en bucle.

Un pytest satisfactorio acredita el alcance de los tests, sin sustituir las pruebas reales ni la revisión editorial. El estado de G1 está en [docs/informe_bootstrap.md](docs/informe_bootstrap.md). [El informe de G1b](docs/informe_backend.md) contiene la prueba real de cinco páginas, la proyección y las limitaciones de acortado y comparación con BabelDOC. G1b se aprobó parcialmente para backend/ruta y se amplió a producción por delegación fechada; G4 también se aprobó con condiciones. Secrets continúa en producción delegada; wisdom-stars sigue en piloto.

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
