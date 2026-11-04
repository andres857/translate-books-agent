---
name: translate-figure-text
description: "Traduce etiquetas rasterizadas detectadas por OCR en cartas y diagramas. No usar para texto PDF nativo, glifos o áreas sin texto detectado."
---

Lee `AGENTS.md` y `docs/decisiones.md`.

Usa `--book books/<libro> --regions ruta.json`; consulta el formato real con `--help` y el módulo de figuras. OCR local con cajas y confianza; filtra símbolos, números, grados, abreviaturas y etiquetas dudosas. No envíes una imagen sin filtrar a un LLM. Traduce etiquetas seguras mediante `run_task("translate_labels", ...)`, agrupadas por figura, y glosario aprobado. Limpia solo cajas detectadas; relleno para fondo plano; LaMa solo si integración validada y sin alterar líneas. Reinserta texto vectorial manteniendo rotación y fuente nueva. Un xref compartido requiere aislamiento para no cambiar otras instancias. Si el fondo o rotación no son seguros, revisión manual. Ejecuta QA local y juez con copias protegidas; dos fallos a cola con capturas. Devuelve {pagina,etiquetas_traducidas,etiquetas_omitidas,qa}.

Desde la raíz del repositorio:

```bash
.venv/bin/python .agents/skills/translate-figure-text/scripts/run.py --help
```

El script delega en el CLI del paquete y propaga su código de salida; un fallo nunca debe tratarse como una página terminada.

Todas las llamadas pasan por llm.run_task y respetan caché, cuota y backend aprobado. Las clasificaciones dudosas se agrupan con classify_labels; no enviar valores protegidos para clasificarlos.
