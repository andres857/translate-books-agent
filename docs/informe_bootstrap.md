# Bootstrap — Gate G1

Estado: G1 aprobada para el núcleo local. La suite ampliada pasa con 31 pruebas; ejecución independiente del orquestador: 31 passed in 1.59s. No se han ejecutado servicios de traducción reales.

## Entregables

- Configuración de Codex y ocho agentes con mapeo de modelos verificado.
- Configuración de scripts/API separada en `config/pipeline.toml`.
- Ocho skills con frontmatter validado; sus ocho entradas `scripts/run.py --help` ejecutan correctamente.
- Paquete Python, CLI y entorno reproducible mediante `uv.lock`, Python 3.12.
- Seis JSON Schemas: manifest, page, qa, review_queue, lote y blocks.
- Fuentes Noto Sans, Symbols y Symbols 2 con licencia OFL, URLs y hashes.
- Reglas globales, especificación operativa y registro de decisiones.
- Copia verificada y de solo lectura del libro elegido en `books/astrology-wisdom-stars/source.pdf`.

## Evidencia local

El PDF sintético `tests/fixtures/book/source.pdf` contiene tres páginas: texto y glifos, tabla con cifras y líneas vectoriales, e imagen con texto rasterizado. `tests/generate_fixture.py` permite regenerarlo. `tests/fixtures/g1-evidence.json` registra los hashes del original y del candidato de reinserción.

Se inspeccionaron visualmente los tres renders; la reinserción de prueba conserva la tabla, los números y la imagen. Las respuestas de traducción de tests son datos sintéticos y clientes simulados: no hubo traducción real del libro ni aprobación G3 del glosario del proyecto. Las aprobaciones de tests viven exclusivamente en sus fixtures aislados.

Pruebas: preservación de glifos/números/imágenes/vectores, protección antes de llamadas, hashes de aprobación, reinserción y rollback, límites de escala, idempotencia, contratos JSON y bloqueo ante QA incompleto. También se cubren términos de glosario anidados, reinserción en páginas mixtas, preservación raster fuera de la caja editada, prosa inglesa con cifras y bloqueo explícito de páginas rotadas. Los tests verifican invariantes, no certifican calidad editorial de una traducción.

## Límites que siguen activos

- No existe `OPENAI_API_KEY` en el entorno: disponibilidad de modelos, esfuerzos, tarifas y llamadas reales no probados.
- BabelDOC 0.6.4 instalado y opciones inspeccionadas. Ruta A bloqueada hasta verificar exclusión de contenido protegido en todas sus peticiones y adaptar los reintentos. Detalle en `docs/decisiones.md`.
- Integración Batch API no verificada: la configuración no activa por sí sola envío Batch.
- PaddleOCR y Paddle CPU instalados; importación real de Paddle 3.3.1 verificada sin CUDA. Pesos/modelos OCR no descargados ni probados sobre el libro.
- La reconstrucción raster se limita a cajas locales revisadas, fondo blanco plano y rotaciones ortogonales; curvas, fondos con textura y casos dudosos requieren revisión. LaMa, MinerU/Surya y LlamaParse son alternativas de diseño aún no integradas.
- QA VLM de producción pendiente; transporte Responses con imágenes enmascaradas probado con mocks. Detector local Lingua EN/ES implementado; las cadenas cortas y nombres propios requieren revisión contextual. Ninguna página real puede declararse terminada usando un mock.
- Ensamblado inicial admite un candidato completo con QA ligado por hash; la agregación de múltiples partes con QA por lote aún requiere implementación antes de producción.

Las 26 posiciones Unicode exigidas para planetas, signos, grados y primas tienen cobertura en el conjunto de fuentes descargado. El libro elegido no presenta páginas con rotación distinta de cero; los adaptadores rechazan explícitamente esos casos hasta implementar su transformación.

Costo API de traducción: **0 USD**, cero llamadas reales. Costos de la sesión de Codex: no disponibles. Siguiente fase: triaje del libro y propuesta de glosario para G3; no comenzar piloto antes de aprobación humana.
