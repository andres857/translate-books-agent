---
name: assemble-book
description: "Ensambla páginas verificadas, TOC y reporte de calidad al final de G6. No usar antes de QA ni para traducir títulos o aprobar G7."
---

Lee `AGENTS.md` y `docs/decisiones.md`.

Usa `--book books/<libro> --parts ruta.json --toc ruta.json --qa ruta.json`. Solo páginas con QA válido y hashes coherentes; los casos manuales deben estar explícitos, nunca contados como aprobados. Valida secuencia, sin duplicados ni omisiones. El TOC ya debe venir traducido por scripts, con niveles/páginas válidos. Une con insert_pdf, aplica set_toc, subset_fonts y ez_save. Escribe únicamente out/ y genera informe_calidad.md con total de páginas, escala, costos conocidos/desconocidos y casos manuales. Renderiza e inspecciona el PDF final. Presenta G7 al humano y detente.

Desde la raíz del repositorio:

```bash
.venv/bin/python .agents/skills/assemble-book/scripts/run.py --help
```

El script delega en el CLI del paquete y propaga su código de salida; un fallo nunca debe tratarse como una página terminada.
