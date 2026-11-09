# Workflow de traducción con Claude Code

Este workflow reemplaza la orquestación con Codex, que ya no se usa. La retrospectiva de esa etapa está en `docs/retrospectiva_codex.md`.

## Capas

| Capa | Quién | Modelo |
|---|---|---|
| Orquestador | Sesión principal de Claude Code | La de la sesión |
| A: agentes de proyecto | `.claude/agents/*.md` | Opus 5.5, effort high |
| B: LLM de los scripts | `astro_translate.llm.run_task` con backend `claude_cli` (`claude -p`, suscripción, sin herramientas ni memoria, sandbox) | Sonnet 5.5: low para traducir; medium para el juez y el glosario. Escalada a opus/high |

En la capa B, todo el texto traducido pasa por `run_task`. Los agentes nunca traducen párrafos.

## Agentes

- **`structure-analyst`**: analiza la estructura del PDF antes de traducir. Produce `work/structure/structure.json` y `structure.md`.
- **`pipeline-engineer`**: implementa con TDD. Atiende una incidencia por vez y empieza con un test rojo sobre un caso real.
- **`book-auditor`**: hace la revisión visual con `docs/criterios_aceptacion.md`. Produce `work/audit/*.json` y la vista previa comparativa.

El orquestador lanza como máximo 3 subagentes simultáneos, y estos no crean otros subagentes. Cada tarea en background se lanza con `run_in_background` y avisa al terminar. Nunca se cierra un turno con trabajo pendiente sin esa notificación.

## Flujo para un libro nuevo (skill `onboard-book`)

1. **Intake**: copiar el PDF a `books/<slug>/source.pdf` y ejecutar `astro_translate doctor`, que comprueba disco, login de `claude` y disponibilidad de modelos.
2. **Triage**: ejecutar `astro_translate triage`, que crea el manifest.
3. **Survey**: ejecutar `astro_translate survey` y después el agente `structure-analyst`. Se obtienen arquetipos de página (columnas, fichas, TOC, callouts, citas display, texto curvo, escaneadas), fuentes y su cobertura, y riesgos. A partir de esto:
   - se genera el perfil `work/profile.toml`;
   - se eligen las páginas piloto, una o dos por arquetipo.
4. **Pseudo-traducción**: ejecutar `compose-offline --pseudo`, que compone el libro entero con texto expandido un 25 % y acentuado, sin LLM, y genera el reporte de defectos agrupado. El `pipeline-engineer` corrige por causa hasta que el reporte quede limpio. **Esto ocurre antes de gastar un solo token de traducción.**
5. **Glosario y guía (G3)**: requieren aprobación humana o delegada, registrada por hash.
6. **Piloto (G4)**: traducir las páginas piloto y pasarlas por `book-auditor`.
7. **Producción** (skill `produce-batch`): lotes de 20, cada uno con este ciclo:
   - `produce` y QA local;
   - `book-auditor`;
   - G5: si el ratio es menor de 0,90, el lote se para;
   - los defectos nuevos se convierten en una regla de QA y un caso en el corpus de regresión.
8. **Recomposición final, revisión visual y `assemble-final` (G7).**

## Gates

| Gate | Qué se aprueba | Quién |
|---|---|---|
| G1b | Backend (`claude_cli`) | Humano |
| G3 | Glosario y guía | Humano, o delegación registrada |
| G4 | Piloto | Humano, o delegación registrada con el informe del auditor |
| G5 | Lote ≥90 % OK | Bloqueante en el código y verificado por el auditor |
| G7 | Libro final | Humano |

## Paradas

- **Límite de suscripción** («hit your session/weekly limit»): sale con código 75 y escribe `quota_pause.json` con la hora de reset. Después se reanuda con `produce --resume`.
- **Modelo de escalada no disponible**: la unidad pasa a `review` y el lote continúa.
- **G5 fallida**: análisis y corrección por causa antes del siguiente lote.
- **Códigos de salida**: 0 OK; 1 `doctor` con errores; 2 bloqueado o tres errores repetidos; 75 cuota (`work/quota_pause.json`, reanudar con `produce --resume`); 76 G5 bloqueante (`work/production/g5_block.json`). Con 76, `produce` rechaza páginas fuera del lote bloqueado. Se permite recomponer ese mismo lote (`--pages <lote> --resume --recompose`): si alcanza ≥0,90, el bloqueo se archiva solo en `g5_block-resolved-<ts>.json`. Para avanzar sin alcanzarlo, el orquestador escribe `work/production/g5_decision-<NN>.json` con `{decision: continue|recompose, approved_by, reason, evidence}`. Las unidades con `ValidationFailure` (incluida la escalada no disponible), `ModelUnavailable` o `BackendError` quedan en `review` reintentable. Las `ValidationFailure` no cuentan para los tres errores repetidos.

## Lecciones de la primera ejecución con este workflow (2026-11-08/09)

Detalle y tiempos en `docs/sesion_2026-11-08_09.md`.

### Proceso
1. **Producir el libro entero antes de corregir.** No se corrige lote a lote ni página a página. El orden es:
   1. traducir y componer todas las páginas;
   2. auditar en paralelo;
   3. agrupar los defectos por causa sobre el libro entero;
   4. hacer una sola ronda de ingenieros;
   5. hacer una sola recomposición.

   El piloto con 4 rondas en serie costó unas 6 h, y la traducción, 10 min.
2. **G5 es una métrica del informe, no un freno.** El bloqueo por lote (salida 76) sirve para parar ante un fallo sistémico, como un error masivo o el backend caído, no para esperar la revisión visual. Cada lote se cierra con `g5_decision` «continue» y el auditor lo revisa en paralelo.
3. **El auditor trabaja en paralelo, nunca como puerta.** Los lotes se auditan mientras se produce el siguiente. Las firmas de `review-visual` se registran juntas cuando no hay un `produce` activo, porque usa el mismo lock de escritura.
4. **No convertir hallazgos menores en rondas.** Si el auditor dice que algo es un falso positivo, se registra como tal y se sigue. Corregir la regla de QA va en la ronda única por causa.
5. **Suites de tests:**
   - en cada iteración, cada ingeniero ejecuta solo los tests de sus módulos;
   - la suite completa (20–30 min) se ejecuta **una sola vez por ronda**;
   - nunca cortarla con un timeout corto, que obliga a repetirla.
6. **Ingenieros en paralelo con módulos asignados** y ediciones atómicas cuando hay una producción en curso, porque `produce` importa el código al arrancar cada lote.
7. **Producción por tramos explícitos, una pasada por tramo** (`--pages 60-79`, `80-99`…), no `--pages 1-192 --resume`. Con 1–192, el primer tramo ya compuesto vuelve a cerrarse y a bloquearse en cada ejecución. Si se relanza un tramo tras la salida 76, se abre un lote duplicado. La decisión G5 se escribe antes del tramo siguiente, no reintentando.
8. **Vigilantes:** usar el PID (`kill -0`) o un archivo marcador, nunca `pgrep -f` con un patrón que aparezca en la propia línea de comandos del vigilante, porque se encuentra a sí mismo. Lo mismo vale para `pkill -f`.

### Técnica, ya incorporada al código
- **PDF deterministas:** el `/ID` se deriva del contenido y las fuentes generadas no llevan marca de tiempo. Sin esto, cada recomposición cambiaba el sha e invalidaba revisiones y aprobaciones.
- **Al extraer la página única** se conserva el grupo de transparencia `/Group`.
- **Contexto de página** (`page_context`) para pies y callouts; los términos del glosario partidos entre líneas van en la misma unidad.
- **Detectores de layout:** tinta real frente a cajas, filetes y paneles, colisión de tildes entre líneas, comillas como obstáculo y una viñeta por línea.
- **QA nueva:** `bullet_line_merge`, `quote_mark_overlap`, `right_edge_overflow`, `english_ordinal_issues`, `line_ink_collision`, `rule_or_panel_overflow`, `term_consistency` y `ordinal_point_off_baseline`.

### Pendiente para el próximo libro
- Crear el corpus `tests/regression_pages/` por tipo de defecto, con los casos de `test_audit_batch1*.py` y `test_composition_*`.
- Ejecutar `survey` y la pseudo-traducción con la QA nueva antes del piloto. Muchos defectos del piloto, como la guía punteada bajo los pies, las tildes, los filetes y los créditos, son detectables sin LLM.
- Detector de pies de foto frente a guías punteadas: p39 y p43 lo tienen, y hoy solo lo ve el auditor.
