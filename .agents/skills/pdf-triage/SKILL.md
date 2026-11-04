---
name: pdf-triage
description: "Clasifica páginas PDF y detecta fuentes simbólicas antes de traducir. No usar para traducir, editar ni hacer OCR."
---

Lee `AGENTS.md` y `docs/decisiones.md`.

Ejecuta el script con `--book books/<libro>`. Lee `work/manifest.json` y reporta tipos, rutas, fuentes simbólicas y anomalías. La numeración externa empieza en 1. Si más del 30 % son escaneadas, informa al orquestador para replantear el piloto. El triaje no implica que las imágenes carezcan de texto: `has_text=null` conserva la incertidumbre. Repetir el comando conserva el estado solo si el original no ha cambiado.

Desde la raíz del repositorio:

```bash
.venv/bin/python .agents/skills/pdf-triage/scripts/run.py --help
```

El script delega en el CLI del paquete y propaga su código de salida; un fallo nunca debe tratarse como una página terminada.
