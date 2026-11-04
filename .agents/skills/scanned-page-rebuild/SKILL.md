---
name: scanned-page-rebuild
description: "Reconstruye páginas escaneadas mediante OCR local y texto vectorial. No usar para páginas nativas o para reenviar escaneos completos al LLM."
---

Lee `AGENTS.md` y `docs/decisiones.md`.

Usa `--book books/<libro> --regions ruta.json`. OCR con bbox mediante PaddleOCR; MinerU/Surya alternativos documentados. Mantén original como imagen de fondo y elimina exclusivamente texto traducible detectado en copias. Conserva glifos, cifras, tablas e ilustraciones. Traduce etiquetas/texto filtrado mediante `run_task` por página y glosario aprobado. Reinserta por cajas, conserva rotación y verifica QA. LlamaParse es respaldo opcional solo con entradas sin contenido protegido; no lo actives enviando el escaneo completo. Si el OCR no permite delimitar regiones seguras, revisión humana. `--ocr-workaround` de BabelDOC no ejecuta OCR.

Desde la raíz del repositorio:

```bash
.venv/bin/python .agents/skills/scanned-page-rebuild/scripts/run.py --help
```

El script delega en el CLI del paquete y propaga su código de salida; un fallo nunca debe tratarse como una página terminada.

Todas las llamadas pasan por llm.run_task y respetan caché, cuota y backend aprobado. Las clasificaciones dudosas se agrupan con classify_labels; no enviar valores protegidos para clasificarlos.
