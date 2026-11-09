---
name: continue-book
description: Retoma de forma idempotente y autónoma la traducción de un libro desde work/orchestrator_state.md y avanza todo lo posible sin preguntar al usuario. Pensado para /loop 1h tras cortes por límite de uso.
argument-hint: <slug>
---

Libro: `books/$0`. Estado: `books/$0/work/orchestrator_state.md`.

**AUTONOMÍA TOTAL.** El usuario exige el libro completo traducido y NO quiere preguntas.
- Nunca uses AskUserQuestion. Nunca termines con «¿quieres continuar?», «¿apruebas?» ni nada parecido.
- La delegación vigente (AGENTS.md, 2026-11-06) te da las decisiones de G4, G5, formas cortas, excepciones artísticas, correcciones editoriales y la limpieza ya aprobada. Decide tú y registra en `docs/decisiones.md` y `work/approval_history/` con `approved_by: "claude (delegación explícita de andres, 2026-11-06)"`.
- Si un lote o el piloto sale mal, no te detengas:
  1. Agrupa los defectos por causa.
  2. Lanza pipeline-engineer.
  3. Recompón.
  4. Vuelve a auditar.
- Solo cuenta como parada el límite de uso (código 75 o límite de la sesión). Lo que sea imposible, como el texto curvo de la Fase 4, se difiere con motivo registrado y se sigue con el resto.

1. Lee el archivo de estado. Si todos los pasos están `[x]`, informa y detén el loop.
2. **Límite de uso:**
   - Si existe `books/$0/work/quota_pause.json` con `resume_after_unix` en el futuro, informa de la hora y termina esta iteración sin hacer nada más.
   - Si estás en un límite de uso de la sesión, esta iteración fallará sola; la siguiente lo reintenta.
3. **Agentes en curso:** si la sección «Agentes en curso» no está vacía:
   - si su notificación de entrega llegó en esta sesión, revisa la entrega, marca el subpaso y borra la línea;
   - si el agente sigue vivo, termina la iteración sin lanzar nada;
   - si murió sin entregar (por ejemplo, por el límite de uso), relánzalo con el mismo encargo. Indica que primero revise el `git diff` de sus módulos y continúe desde ahí, sin rehacer.
4. **Si no hay nada en curso**, ejecuta los pasos pendientes en orden y avanza todo lo que puedas en esta iteración, siguiendo `docs/workflow_claude.md` y AGENTS.md:
   - lo largo, con `run_in_background` o como subagente, anotándolo en «Agentes en curso»;
   - como máximo 3 subagentes a la vez;
   - cada encargo con su propia verificación.
5. **Cierre de la iteración:** añade una línea a la «Bitácora» (fecha UTC, qué se hizo, evidencia) y da al usuario un resumen de 3 líneas como máximo.

**Prohibido:**
- Saltarse gates (G4/G5 bloqueantes).
- Traducir en la conversación.
- Tocar `source.pdf`, el glosario o las aprobaciones fuera de lo que exige el paso.
- Reintentar en bucle dentro de una misma iteración.
