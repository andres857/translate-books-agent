---
name: onboard-book
description: Incorpora un libro nuevo al pipeline de traducción - intake, doctor, triage, survey de estructura, pseudo-traducción offline y selección de piloto, antes de gastar tokens de traducción.
argument-hint: <slug> [ruta-al-pdf]
---

Libro: `books/$0`. Sigue `docs/workflow_claude.md`, pasos 1 a 4.

1. Si se da la ruta al PDF y `books/$0/source.pdf` no existe, cópialo ahí. Nunca sobrescribas un `source.pdf` existente. Registra su SHA-256.
2. Ejecuta `.venv/bin/python -m astro_translate doctor`. Si falla el disco, el login o los modelos, detente e informa.
3. Ejecuta `.venv/bin/python -m astro_translate triage --book books/$0`.
4. Ejecuta `survey`. Después lanza el subagente `structure-analyst` en background, con `run_in_background` y notificación.
5. Con `structure.md` en mano, lanza `pipeline-engineer` para cada *gap* marcado como `supported: false`. Hazlo una incidencia por vez, o hasta 3 en paralelo si tocan módulos distintos.
6. Ejecuta `compose-offline --pseudo` sobre el libro entero y revisa el reporte de defectos agrupado. Itera los pasos 5 y 6 hasta que no queden defectos bloqueantes de layout.
7. Propón el glosario (`glossary-build --api`) y prepara la aprobación G3. No la crees tú si no hay delegación vigente.
8. Informa:
   - arquetipos;
   - gaps resueltos y pendientes;
   - páginas piloto;
   - proyección de consumo.
