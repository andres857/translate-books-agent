# Plan — *The Secrets of Astrology* al español: piloto → G4 → producción

## Contexto
- Libro: `books/the-secrets-of-astrology/source.pdf` (192 p., DK/Carole Taylor). Objetivo final: libro completo en español con maquetación conservada.
- Estado verificado:
  - G1, G2, G3 y G1b vigentes (hashes coinciden). G1b autoriza `reinsertion|fidelity|pilot`, **no** `production`.
  - G4 está en `awaiting_human_visual_review`. Manifest: 20 páginas en `review`, 172 `pending`, 0 `done`.
  - Consumo hasta ahora: 62 llamadas codex_sdk, 687k tokens de entrada / 62k de salida, USD desconocido.
- Piloto (`out/piloto_comparativa.pdf`, páginas 1, 5, 19, 28, 34, 35, 60, 69, 86, 87, 100, 101, 128, 141, 154, 169, 173, 175, 179, 180):
  - 18 traducidas; p1 y p169 conservan el original.
  - Todas las QA tienen veredicto `revisar`.
- Decisiones tuyas en esta sesión:
  - Secuencia piloto → G4 → producción.
  - El texto dentro de imágenes queda para una fase posterior, registrado.
  - El texto sans sin cobertura se compone con una **sans equivalente**.
  - **Codex implementa** (`/codex:rescue --background`, una incidencia por vez, test que reproduzca el fallo primero); Claude revisa el diff, ejecuta pytest y el pipeline, e inspecciona los renders.

## Reglas operativas (todas las fases)
- No modificar `glossary/`, `config/`, `books/*/work/glossary/` ni archivos de aprobación. Si una corrección lo requiere: detenerse y explicar.
- `source.pdf` es inmutable. Escribir solo en `work/` y `out/` del libro. Nada fuera de las 20 páginas hasta G4. Nunca `--phase production` sin autorización nueva.
- Si sale el código 75 (`quota_pause.json`): detenerse y avisar.
- El código común (`src/astro_translate/`) es compartido con astrology-wisdom-stars: la suite completa debe seguir verde.
- No marcar nada como aprobado: G4, G5 y G7 son humanas.

## Fase 1 — Corregir los defectos sistémicos del piloto
Cada incidencia lleva el mismo ciclo:
1. Codex escribe un test rojo con un caso real del libro.
2. Codex aplica la corrección en `src/astro_translate/` o en los runners locales `books/the-secrets-of-astrology/work/pilot/*.py`.
3. Claude revisa el diff y ejecuta `.venv/bin/python -m pytest -q`.
4. Claude recompone las páginas afectadas y las inspecciona en el render.

| # | Incidencia (páginas) | Dirección de la corrección |
|---|---|---|
| I1 | Cuerpo sans → serif (19, 28, 100, 101, 141, 180) | Diagnosticar en `fonts.py`/`fidelity.py` la elección del reemplazo. Elegir una sans libre de métricas parecidas, guardarla en `fonts/` con licencia en `fonts/provenance.json` y mantener peso, ancho e itálica por run. |
| I2 | Número mal colocado: «Los signos 12» (28) | Restaurar los marcadores {nN} respetando la posición que dé el modelo. Test de orden de tokens en la validación de `llm.py`. |
| I3 | Ordinales nativos 1ST/7TH, «7THCASA» (34, 60, 154) | Regla determinista local, sin LLM, sin cambiar la cifra. Propuesta: «EL 1.er SIGNO», «EL 7.º SIGNO», «CASA 7» (como el cuerpo). Va a `docs/decisiones.md`, no a la guía G3. **Se confirma en G4.** |
| I4 | Listas con título en línea: escala baja y primera línea cortada (86, 87, 101, 141, 180) | Componer el título y el cuerpo como un solo párrafo con runs. Escala uniforme por columna ≥ 0,80 cuando quepa ampliando la caja hacia espacio libre. |
| I5 | Solapes en la tabla de aspectos: «Raíz Si8bolo», «0°Orbe» (179) | Ubicar las etiquetas por span con detección de colisión contra los valores protegidos vecinos. Separación mínima. |
| I6 | Calidad textual: «de la Tetera de la Tetera», «encontrarla Sagitario» (69), «EDUCACION» (100), zodiaco/zodíaco (34 frente a 60), cursiva perdida en *plouton* (128), «Símbolo♈» sin espacio | Nuevos controles en QA local: repetición de n-gramas, tildes en mayúsculas, ortografía del glosario, estilos inline conservados y espacio antes de glifo. La retraducción de unidades fallidas pasa por `run_task` (sin traducir en la conversación). |
| I7 | p169: la etiqueta no cabe a 0,75 | Primero ampliar a espacio libre. Si no basta, **detenerse**: la forma corta requiere un suplemento aprobado por ti. |

**Registrados y diferidos** (Fase 4, con captura en `review_queue.json`):
- portada p1;
- rueda PISCES/ARIES (28);
- banda SHINE|NURTURE y planetas rotados (86);
- ordinales gráficos 10TH/9TH (173).

**Cierre de la Fase 1**:
- Regenerar las 20 páginas en fase `pilot` con los runners existentes del libro (`work/pilot/native_runner.py`, `run_visual_qa.py`, `assemble_review_sample.py`; los argumentos se verifican antes de ejecutar).
- Reconstruir `out/piloto.pdf` y `out/piloto_comparativa.pdf`.
- Entregar:
  - (a) `pytest -q` sin fallos;
  - (b) tabla página → QA desde `work/pilot/p*-qa.json`;
  - (c) búsqueda de inglés con `pdftotext` sobre las 20 páginas, justificando cada coincidencia;
  - (d) `git status --porcelain -- glossary/ config/` vacío;
  - (e) consumo de esta ronda desde `costs.jsonl`.
- **Detenerse para tu G4.**

## Fase 2 — Preparar producción (después de G4, con autorización explícita)
- La fase `production` usará el compositor validado (`fidelity_translate`/`reinsert_fidelity`, `adapters.py:76`) en lugar de la ruta antigua (`adapters.py:77-136`).
- Sacar del código común las reglas por número de página de wisdom-stars (`fidelity.py:97,177-333`). Pasan a perfiles por libro atados al hash del PDF; el reemplazo de `local_layout.py` deja de ser un parche en tiempo de ejecución.
- Orquestador de lotes de 20 que lea `manifest.json`:
  - salta páginas con QA vigente y retoma las `pending`;
  - se detiene tras 3 errores repetidos;
  - aplica el criterio G5 de ≥90 %.
- Fusión de los PDF por página en un único candidato para `assemble-book`. Ruta de QA para páginas mixtas: VLM enmascarado + revisión humana de gráficos.
- Proyección de consumo actualizada con los datos reales del piloto (aprox. 2,3× la estimación previa).
- Tú amplías G1b a `production` y creas `pilot_approval.json`. Claude no lo hace.

## Fase 3 — Producción por lotes
Páginas nativas y mixtas (≈172) en lotes de 20; reporte por lote y G5.

## Fase 4 — Texto en imágenes
- Portada, 18 escaneadas, rótulos curvos/rotados y ordinales gráficos: OCR → borrado/inpainting → texto vectorial.
- O bien excepciones artísticas aprobadas por ti página por página.

## Fase 5 — Ensamblado y G7

## Verificación
- Por incidencia: test rojo → verde, suite completa verde, render de la página afectada inspeccionado (original | traducción).
- Por fase: las cuatro salidas (a)–(d) del cierre, QA sin errores abiertos no justificados, `git status` de glossary/config vacío, original con el mismo SHA-256.

## Configuración de Codex (implementador)
- Modelo `gpt-6-astra`, razonamiento `high` (ya es el valor en `~/.codex/config.toml` y `.codex/config.toml`).
- Cada encargo se lanza con `/codex:rescue --background --model gpt-6-astra --effort high`.
- No afecta a la capa B del pipeline (gpt-6-luna/low y gpt-6-astra/high, aprobados en G1b).

## Actualización 2026-11-06 — ejecución autónoma
Por delegación explícita del usuario (ver AGENTS.md, sección «Autorización humana vigente… delegación completa»), Claude ejecuta las Fases 1–5 sin paradas humanas, registrando cada gate como decisión delegada trazable. Parada solo por cuota agotada de Codex.
Orden: cerrar Fase 1 (I6, I7, regenerar 20 p.) → G4 delegada con informe → Fase 2 (generalización: perfil por libro, sin páginas fijas en código común, hash de memoria independiente del código, orquestador de lotes de 20 reanudable, fusión de PDFs, QA de páginas mixtas) → Fase 3 (producción por lotes, G5 ≥90 %) → Fase 4 (texto raster: OCR, borrado, texto vectorial; portada, 18 escaneadas, rótulos curvos/rotados) → Fase 5 (ensamblado `out/<libro>_es.pdf`, comparativa completa, informe final).

## Fase 6 — Generalización del sistema (añadida 2026-11-07, decisión delegada)
Objetivo del proyecto: sistema reutilizable para libros técnicos ilustrados (astrología con gráficas), no solo este libro. Criterio: un libro nuevo de diseño similar se procesa en ~1–2 h de máquina con revisión humana breve.
1. Inventario previo: `astro_translate survey --book` clasifica cada bloque contra el catálogo de patrones (columnas encadenadas, citas display, índices, fichas etiqueta/valor, callouts, rótulos rotados/curvos, aperturas, cajas de color, raster) e informa cobertura y patrones desconocidos antes de traducir.
2. Flujo traducir-todo → componer-todo offline → QA → agrupación de defectos por patrón → una ronda de correcciones (sustituye el descubrimiento lote a lote).
3. QA automática de lo que hoy detecta la revisión visual: repetición no adyacente, márgenes, caso de letra, ¡¿, abreviaturas huérfanas, espacios espurios, contraste, solapes; juez VLM lado a lado; revisión humana solo de lo marcado.
4. Corpus de regresión por patrón con golden de Secrets y wisdom-stars.
5. Comando de alta: perfil por hash + survey + produce + informe. Métrica: tiempo total y % de páginas QA OK automático en el siguiente libro.
Cambio inmediato: lotes restantes de este libro se traducen completos, se componen offline y se corrigen agrupando defectos por patrón.
