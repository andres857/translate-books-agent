---
name: translate-native-pages
description: "Procesa rangos de texto PDF nativo mediante el backend LLM aprobado tras las gates. No usar para texto quemado en imágenes ni saltar G3/G4."
---

Lee `AGENTS.md` y `docs/decisiones.md`.

Ejecuta con `--book books/<libro> --pages 1-20`. Verifica G3 y G1b; para producción requiere también G4. Todas las llamadas pasan por `astro_translate.llm.run_task("translate_page", ...)`, agrupadas entre varias páginas hasta 6000 caracteres, con ids que incluyen página. Lee los modelos/esquemas en `[tasks]` de config/pipeline.toml. Mantén el contenido protegido localmente y fuera del backend. La decisión sobre BabelDOC/PyMuPDF está en docs/decisiones.md; no usar un endpoint privado ni un proxy para conectarlos.

Antes de llamar, respeta `work/quota_pause.json`; salida75 significa pausa, no reintento en bucle. Caché SQLite evita repetir unidades vigentes. Procesa 20 páginas por lote; registra backend efectivo, llamadas, caché, tokens, latencia y páginas pausadas. Ningún cambio de backend puede quedar sin registro. Una salida permanece translated hasta QA; G5 exige ≥90 % QA OK y trazabilidad completa del backend. El smoke G1b de tres páginas debe identificarse explícitamente y no autoriza el piloto ni la producción.

Desde la raíz del repositorio:

```bash
.venv/bin/python .agents/skills/translate-native-pages/scripts/run.py --help
```

El script delega en el CLI del paquete y propaga su código de salida; un fallo nunca debe tratarse como una página terminada.

Fase 1c: `--phase reinsertion --pages 7,15,121`, únicamente con aprobación parcial de backend/ruta vigente. No autoriza G4. Registrar tokens totales y cached_input_tokens del proveedor una vez por llamada, sin duplicarlos por página.
