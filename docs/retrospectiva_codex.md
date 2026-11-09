# Retrospectiva: orquestación con Codex (2026-11-04 → 2026-11-07)

Libro: *The Secrets of Astrology* (192 p.). La sesión principal ocurrió el 2026-11-07 y duró unas 17 h. Se cerró al perder la suscripción de Codex (plan free; la suscripción había expirado el 2026-09-03).

## Resultado al cierre

**Estado del manifest**

| Estado | Páginas |
|---|---|
| `done_qa_ok` | 27 |
| `review` | 121 |
| `partial_deferred` | 17 |
| `deferred_raster` | 15 |
| `failed` | 12 |

**Consumo y validación**
- G5 (≥90 % de QA OK por lote) no se cumplió en ninguno de los 15 lotes o recomposiciones: los ratios quedaron entre 0,0 y 0,33.
- `costs.jsonl`: 492 llamadas, 4,24 M tokens de entrada y 0,38 M de salida. 484 fueron con gpt-6-luna/low.
- `llm_validation/`: 83 registros de fallo, en 34 peticiones.
- Tests: de 180 a 612.

## Línea de tiempo

| Fase | Contenido | Ronda Codex |
|---|---|---|
| 1 (I1–I8, D1–D3) | Piloto de 20 páginas: fuente slab, ordinales, listas, tabla de aspectos, calidad textual, captions | ~14 tareas |
| 2A | Perfiles por libro | 27 min |
| 2B | Orquestador `produce`, G5, `assemble-final` | 25 min (1 prompt roto) |
| 2C | Flujo de columnas, TOC, cita p9, máscaras VLM | 60 min |
| 2D | Aperturas de capítulo, `©` | 14 min |
| 2E | `partial_deferred` para texto curvo, citas display, flexión del glosario | 44 min |
| 2F | Fichas de signo, callouts, formas cortas | 28 min + **319 min parado sin vigilante** |
| 2G | Protocolo de énfasis `flat-v1`, márgenes, «¡» | 56 min |
| 2H | Reporte de defectos agrupado (libro entero) | 2 h (2 intentos fallidos por astra) |
| 2I | Candidatos de una página (9,7 GiB → 78 MB) | 38 min (límite de uso) |

## Causas raíz

1. **No se modeló la estructura del PDF antes de traducir.** El `triage` solo distinguía native, mixed y scanned. Cada uno de estos patrones se descubrió en un lote distinto, y cada uno costó una ronda de Codex de 30–60 min:
   - columnas encadenadas
   - TOC
   - fichas label/valor
   - callouts con línea guía
   - citas display
   - aperturas a 190 pt
   - texto curvo glifo a glifo
   - cajas de color
   - márgenes
2. **Los defectos se descubrían lote a lote**, no en el libro entero. El reporte agrupado por causa (`book_defect_report.py`) llegó en 2H, a las 13 h de empezar.
3. **El modelo barato era frágil con los protocolos de marcadores.** gpt-6-luna/low dejó 58 fallos de glosario, 18 de énfasis y 11 de placeholders. Su única escalada era astra, que desapareció; la escalada fallida abortó la retraducción 2H entera en lugar de mandar la unidad a revisión.
4. **La QA local no veía defectos visuales.** Estos los detectaba solo Claude mirando renders:
   - textos duplicados («don creativo», «LA BÚSQUEDA»)
   - callouts fusionados
   - contraste
   - márgenes
   - «¡» faltante
   - mayúsculas sin tilde
   - espacios iniciales

   55 de las 59 lecturas de Claude fueron PNG comparativos. Esta revisión no tenía rol, criterios escritos ni registro estructurado.
5. **Las gates se ignoraron.**
   - G5 falló en todos los lotes y la producción siguió.
   - `decisiones.md` registró «14/16 = 87,5 %» frente a un `qa_ok_ratio 0.125` en el log.
   - G4 y la ampliación de G1b se autoaprobaron por delegación.
6. **La invalidación de QA en cascada.** Cada cambio de código dejaba obsoletas las revisiones y las salidas del VLM (121 páginas en `review`, 16 `vlm_not_current`).
7. **Infraestructura.**
   - Cada candidato era una copia del libro completo (65 MB), y el disco se llenó.
   - El sandbox bwrap estaba bloqueado por AppArmor; se arregló a mano y no se documentó.
   - No se verificó el plan de la cuenta ni la disponibilidad de modelos al arrancar.
8. **Orquestación.**
   - 2F se lanzó sin vigilante.
   - 27 tareas Codex secuenciales.
   - Las reglas propias de un libro se colaron en el código común (`native_ordinals`).

## Qué cambia con el workflow de Claude Code

Ver `docs/workflow_claude.md`.

| Causa | Mitigación |
|---|---|
| 1 | Comando `survey` y agente `structure-analyst` antes de cualquier traducción |
| 2 | `compose-offline --pseudo`: compone el libro entero con pseudo-traducción, sin LLM, y entrega el reporte de defectos agrupado el primer día |
| 3 | Sonnet 5.5 como modelo base. La escalada (opus/high) es configurable; si no está disponible, la unidad va a `review` y el lote sigue |
| 4 | Agente `book-auditor` (Opus, high) con `docs/criterios_aceptacion.md`. Cada defecto nuevo se convierte en regla de QA y en caso de regresión |
| 5 | G5 bloqueante en el código y un hook que impide cerrar un lote sin informe del auditor |
| 6 | Revisión visual solo sobre la recomposición final; dependencias de QA por página |
| 7 | `doctor` ampliado: disco, login, modelos y cuota |
| 8 | Subagentes en background con notificación nativa; Stop hook ante trabajo pendiente |
