---
name: fit-and-reinsert
description: "Reinserta párrafos y etiquetas de texto nativo con PyMuPDF, ajuste de espacio y QA. No usar para texto rasterizado ni spans protegidos."
---

Lee `AGENTS.md` y `docs/decisiones.md`.

Entrada `--blocks ruta.json`: ver schemas/blocks.json; `page` empieza en 1. Cada bloque aporta bbox, source, target y opcionalmente vector/rotate. Requiere G3 vigente incluso para traducciones aportadas. Nunca regeneres glifos, cifras o grados. Verifica intersecciones y redacta solo texto permitido conservando imágenes y dibujos explícitamente. Usa fuente nueva de fonts/. Si insert_htmlbox no cabe: ampliar únicamente a espacio libre; si sigue sin caber, agrupar los bloques fallidos de la página y pedir mediante `run_task("shorten", ...)` una versión ≤len(original)*1.1 con glosario y reintentar una vez. Si falla, rollback y review_queue. Escala <0.80 implica revisión; vector=true exige escala 1.00. Ejecuta visual-qa antes de marcar como terminada. Consulta references/input.md para el contrato.

Desde la raíz del repositorio:

```bash
.venv/bin/python .agents/skills/fit-and-reinsert/scripts/run.py --help
```

El script delega en el CLI del paquete y propaga su código de salida; un fallo nunca debe tratarse como una página terminada.

Actualización Fase 1c (prioridad humana): texto corrido por bloque/párrafo; span solo para etiqueta o texto intersectando dibujos visibles. Escala por bloque, no por página. Etiquetas: ampliar, luego short_target aprobado por hash en suplemento sin alterar CSV G3, luego LLM. Para menos de 15 caracteres no aplicar 1,10. Cero spans fallidos y escala >=0,75 son requisitos de la muestra; QA debe ejecutarse. No ejecutar piloto20/G4.

Fase 1d (prioridad humana actual): números/grados/glifos se protegen como tokens {nN} en prosa, no omitiendo todo el span. Fuentes simbólicas completas se preservan. Fuente original con has_glyph y cobertura total; fallback estilísticamente equivalente y coherente en el grupo. Mantener color/peso/italic, escalas uniformes, pares de etiquetas y consistencia de frases repetidas. Exigir cero inglés no protegido y diff fuera del texto cero en muestra 7/15/121. Entregar lado a lado y detenerse; G4 no aprobado.
