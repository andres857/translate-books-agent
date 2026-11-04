---
name: visual-qa
description: "Evalúa PDF original y candidato para decidir QA por página. No usar para aprobar glosarios, pilotos ni declarar calidad sin evidencias."
---

Lee `AGENTS.md` y `docs/decisiones.md`.

Usa `--book books/<libro> --candidate ruta.pdf --edits ruta.json`. Renderiza a 110 dpi. Compara píxeles fuera de las zonas editables, conservación de glifos y números, glosario e inglés residual; inspecciona ajustes/solapes. Escala <0.80 o diferencias >0.01 requieren revisión; etiquetas vectoriales no se reducen. Para VLM, usa `run_task("qa_judge", ...)` con el backend aprobado y copias con contenido protegido oculto, nunca altera el PDF para ello. Regiones inciertas obligan revisión manual. Juez ausente o controles incompletos producen revisar, no ok. Guarda qa/pXXX.json y renders; actualiza estado y cola atómicamente. Requiere revisión humana del 100 % de páginas con gráficos. El revisor de QA read-only solo lee estos artefactos y devuelve dictamen.

Desde la raíz del repositorio:

```bash
.venv/bin/python .agents/skills/visual-qa/scripts/run.py --help
```

El script delega en el CLI del paquete y propaga su código de salida; un fallo nunca debe tratarse como una página terminada.

Fase 1d (prioridad humana actual): números/grados/glifos se protegen como tokens {nN} en prosa, no omitiendo todo el span. Fuentes simbólicas completas se preservan. Fuente original con has_glyph y cobertura total; fallback estilísticamente equivalente y coherente en el grupo. Mantener color/peso/italic, escalas uniformes, pares de etiquetas y consistencia de frases repetidas. Exigir cero inglés no protegido y diff fuera del texto cero en muestra 7/15/121. Entregar lado a lado y detenerse; G4 no aprobado.
