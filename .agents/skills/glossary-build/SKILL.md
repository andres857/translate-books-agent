---
name: glossary-build
description: "Extrae candidatos de terminología astrológica y prepara G3. No usar para traducir páginas ni aprobar el glosario."
---

Lee `AGENTS.md` y `docs/decisiones.md`.

Ejecuta el script con `--book books/<libro>`. Para propuestas LLM usa el modo explícito del CLI y `run_task("glossary_candidates", ...)`, con el backend configurado, agrupando texto filtrado en trozos de unas 30 páginas. El inventario determinista sigue disponible sin inferencia; no presentarlo como una propuesta validada por el modelo. Codex usa login ChatGPT, sin exigir OPENAI_API_KEY; el respaldo API solo se permite según la configuración. Revisa `glossary/astro_en_es.csv`, `do_not_translate.txt`, `style_guide.md` y documenta dudas en `glossary/dudas.md`. CSV obligatorio `source,target,tgt_lng`. Presenta los archivos al humano y detente en G3. Solo tras aprobación explícita registra hashes del original, glosario, estilo y lista protegida en `work/glossary_approval.json`; no escribas approved=true por iniciativa propia.

Desde la raíz del repositorio:

```bash
.venv/bin/python .agents/skills/glossary-build/scripts/run.py --help
```

El script delega en el CLI del paquete y propaga su código de salida; un fallo nunca debe tratarse como una página terminada.
