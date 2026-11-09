---
name: produce-batch
description: Ejecuta un lote de producción de 20 páginas con QA local, auditoría visual por book-auditor y gate G5 bloqueante.
argument-hint: <slug> <pagina-inicio>-<pagina-fin>
---

Libro: `books/$0`, páginas `$1`.

1. Comprueba las precondiciones:
   - G1b, G3 y G4 vigentes;
   - `astro_translate doctor` OK;
   - sin `work/quota_pause.json` activo.
2. Lanza `.venv/bin/python -m astro_translate produce --book books/$0 --batch-size 20 --pages $1 --resume` con `run_in_background` y escribe el log en `work/production/`.
3. Al terminar:
   - si salió con código 75 (límite de suscripción), informa de la hora de reset y del comando para reanudar, y detente;
   - si salió con código 76 (G5 bloqueado, `work/production/g5_block.json`), ve al paso 4. Solo se avanza con una recomposición que pase G5 o con un `g5_decision-NN.json` justificado (`decision`, `approved_by`, `reason`, `evidence` = informe del auditor);
   - si no, lanza `book-auditor` en background sobre `$1`.
4. Lee `work/audit/<lote>.json`.
   - Si `g5.pass` es falso, **no sigas con el siguiente lote**. Agrupa los hallazgos por `probable_cause` y lanza `pipeline-engineer` por causa. Después recompón con `produce --pages $1 --resume --recompose` y vuelve a auditar.
5. Convierte cada `rule_proposal` en una regla de QA o un caso de regresión, mediante `pipeline-engineer`.
6. Registra la decisión G5 en `docs/decisiones.md` con el ratio real del informe. Nunca pongas un ratio distinto del medido.
