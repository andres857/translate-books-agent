# RESUMEN DE PROYECTO — Traducción de libros de astrología en PDF con agentes de IA (Codex)
Fecha de corte: 3 de noviembre de 2026. Usuario en Bogotá; conversación en español.

## 1. Objetivo
Traducir libros de astrología en PDF (inglés → español) con agentes de IA, conservando la
maquetación original: ilustraciones, cartas natales, tablas, índices y glifos astrológicos.
Se eligió la astrología a propósito por ser contenido difícil (muchas gráficas); la idea es
reutilizar después el sistema para otros libros y otros dominios.

Idea original del usuario (resultó ser la arquitectura correcta): copiar cada página, calcular
las coordenadas del texto, traducirlo y reescribirlo en el mismo lugar, generando un nuevo PDF
con las gráficas intactas; todo orquestado por Codex con skills propias.

## 2. Investigación (archivo search.md en la raíz del repo)
Conclusiones clave:
- Codex debe actuar como ingeniero/orquestador que construye y ejecuta scripts, no como
  traductor página a página dentro de su conversación.
- Patrón técnico: extraer texto con coordenadas → traducir → redactar (borrar) el original →
  reinsertar con PyMuPDF (add_redact_annot + apply_redactions sin tocar imágenes +
  insert_htmlbox con escala mínima ~0,75).
- BabelDOC/pdf2zh-next era el mejor motor abierto, pero finalmente se descartó (ver 5).
- Ninguna herramienta de PDF traduce texto dentro de imágenes raster: hace falta una ruta
  propia de OCR → borrado/inpainting → texto vectorial encima (Ruta B).
- Glifos (♈–♓, ☉, ☽, ☿–♇), grados y números nunca se traducen ni se modifican.
- Un modelo barato basta para traducir si hay glosario; el modelo fuerte se reserva para el
  glosario y la revisión.
- LlamaIndex/LlamaParse es opcional (respaldo para escaneados o memoria de traducción).
- Nota legal: traducir y distribuir un libro requiere autorización del titular de derechos;
  para uso personal el riesgo es menor.

## 3. Arquitectura del pipeline
Rutas por tipo de página (decididas en el triaje):
- Ruta A: páginas con texto nativo → ruta propia PyMuPDF, traducción agrupada por página(s).
- Ruta B: figuras con texto raster → OCR, borrado, re-render vectorial.
- Ruta C: páginas escaneadas → OCR con cajas y reconstrucción por capas.
Fases: 0 triaje → glosario → traducción → figuras → escaneadas → QA → ensamblado.
QA: geométrico (escala, cajas fallidas), lingüístico (glosario, inglés residual,
glifos/números iguales), visual (diff de píxeles fuera del texto, juez VLM) y revisión humana.

## 4. Prompts generados (archivos en la raíz del repo)
- PROMPT_AGENTES.md: prompt maestro del sistema multiagente en Codex. Define agentes en
  .codex/agents/*.toml (orquestador, pipeline_engineer, pdf_triage, glossary_curator,
  batch_runner, figure_specialist, qa_reviewer, issue_fixer, assembler), skills en
  .agents/skills/, dos capas de modelos (capa A: agentes de Codex; capa B: llamadas LLM
  dentro de los scripts), fases y compuertas G1–G7 (G3, G4 y G7 son humanas).
  Niveles de razonamiento de Codex: minimal, low, medium, high, xhigh.
- PROMPT_CODEX_BACKEND.md: implementación de Codex como LLM de la capa B mediante el SDK
  oficial de Python (openai-codex), con módulo único llm.py, backends codex_sdk / codex_exec /
  openai_api, caché SQLite, validación, pausa por cuota (código de salida 75) y la nueva
  Fase 1b.

## 5. Decisión sobre el backend LLM
Se evaluaron tres métodos para usar la suscripción de ChatGPT/Codex como LLM:
1. SDK oficial de Codex o codex exec (ELEGIDO): estable y oficial; con sobrecarga fija de
   ~7.000 tokens de entrada por llamada (instrucciones generales de Codex).
2. Proxy local compatible con OpenAI (codex-openai-proxy de 0oAstro, de Securiteru,
   CodexAPI…): cómodo para BabelDOC, pero depende de un endpoint privado
   (chatgpt.com/backend-api/codex/responses), puede romperse (ya pasó el 27/05/2026), es zona
   gris de términos de uso y algunos escuchan en 0.0.0.0 por defecto. Descartado.
3. Adaptador propio sobre el SDK: posible, pero lento con BabelDOC.
Contexto: OpenAI tolera e incluso respalda el uso de la suscripción en herramientas de
terceros; desde el 29/09/2026 existe oficialmente "Sign in with ChatGPT" para socios
participantes.

BabelDOC descartado: su planificador partiría cada página en 7–14 lotes, y con ~7k tokens
fijos por llamada eso multiplica el consumo. El usuario dispensó la comparación de latencia 3×.

## 6. Estado actual del proyecto
Libro en curso: "Astrology: Using the Wisdom of the Stars in Your Everyday Life" (Carole
Taylor), 258 páginas, 29 escaneadas. El original es de solo lectura y conserva su hash
(SHA-256 d13ab814…).

Datos verificados del SDK (docs/codex_sdk_api.md):
- openai-codex 0.163.0; el SDK de Python ya es estable.
- El modelo se fija en thread_start(model=...); el razonamiento por turno con
  thread.run(..., effort="low") y output_schema por turno.
- El perfil "translator" se aplica con config_overrides (el app-server no acepta --profile).
- Aislamiento: sandbox read_only, ApprovalMode.deny_all, directorio vacío
  work/.codex_llm_sandbox/, project_doc_max_bytes=0, skills/plugins/MCP/web desactivados,
  hilos efímeros y rechazo de cualquier evento de herramienta.
- La cuota se detecta consumiendo thread.turn(...).stream() (usageLimitExceeded /
  rateLimitExceeded), porque .run() pierde el código estructurado.
- Modelos disponibles en la cuenta: gpt-6-luna (barato), gpt-6-astra (fuerte), gpt-6.1-sol
  y otros. No hay gpt-5.x ni "minimal". Mapeo aplicado: barato→gpt-6-luna/low,
  fuerte→gpt-6-astra/high, minimal→low.

Compuertas y fases:
- G3 (glosario y guía de estilo): APROBADA por el usuario.
- Fase 1b / G1b: APROBADA PARCIALMENTE. Backend codex_sdk, fallback=none, ruta propia
  PyMuPDF. Alcance "backend y ruta"; no cubre maquetación ni G4
  (work/backend_approval.json).
  Métricas: 5/5 páginas válidas al primer intento; consumo de la fase 132.257 tokens de
  entrada y 6.438 de salida; 0 USD de API.
- Fase 1c (reinserción): TERMINADA según su criterio geométrico (0 cajas fallidas, escala
  ≥ 0,75, diferencia fuera del texto 0, 75 tests). Salida de muestra:
  out/reinsercion_1c.pdf (páginas externas 7, 15 y 121).
  Agrupar varias páginas por llamada redujo un 61 % los tokens de entrada (28.539 → 11.128;
  11.008 de ellos cacheados por el proveedor); la latencia aumentó.
- Fase 1d ("Fidelidad"): ABIERTA. Se le enviaron al agente las tareas de la sección 8.
- G4 (piloto de 20 páginas): NO autorizada. Producción: no iniciada.

## 7. Decisiones editoriales tomadas
- Formas cortas aprobadas (archivo aparte glossary/astro_en_es_short.csv; el glosario
  G3 no se toca): Asc., Desc., Merc., Júp., Sat., Nept., Sag., Cap., signo, signos.
  "carta natal" siempre completa.
  Uso: solo en etiquetas de figuras, tablas y leyendas, nunca en texto corrido. Orden:
  ampliar caja → forma corta → LLM. Para nuevos signos, patrón de tres letras (Ari, Tau,
  Gém, Cán, Leo, Vir, Lib, Esc, Sag, Cap, Acu, Pis), previa propuesta.
- Etiquetas emparejadas (Ascendente/Descendente, MC/IC): ambas completas o ambas cortas.
- Lettering decorativo dentro de ilustraciones raster (p. ej. "PLANETS" en la página 7):
  se conserva y se registra en una lista de excepciones (página, bbox, texto, motivo),
  fuera del criterio de "0 inglés". Solo se traduce el texto funcional dentro de
  imágenes; ante la duda, preguntar. (Recomendado; CONFIRMAR qué respondió el usuario al
  agente.)
- Registro: el libro usa tú ("tu carta").
- PENDIENTES de decidir en la guía de estilo: variante del español (neutro latinoamericano
  o de España) y comillas («» frente a ""); en los renders salieron «».

## 8. Problemas detectados en la 1c → tareas de la Fase 1d
1. Protección demasiado amplia: los spans con números se protegen enteros y dejan frases
   mitad en inglés ("In this picture will be found the 12", "Astrology recognizes 10
   planets, including the", "12 zodiac signs.", "The 12 sections of the").
   Solución: proteger solo tokens (números, grados, glifos) como marcadores {nN}.
2. Fechas del índice en inglés → traducción determinista sin LLM ("21 de marzo–20 de abril").
3. Tipografía mezclada: el texto idéntico en ambos idiomas (Aries, Leo, Virgo, Libra, Venus)
   conserva la fuente original gris y el resto sale en otra fuente negra. Solución:
   reutilizar la fuente incrustada si tiene todos los glifos (has_glyph); si no, usar una
   fuente de estilo equivalente; conservar color y negrita/cursiva.
4. Escalas distintas por entrada en listas → escala uniforme por grupo.
5. Segmentación: títulos pegados al cuerpo ("Nodos Puntos donde…"), negritas perdidas
   (Planetas, Aspectos, Casas…) y la cita destacada partida en dos unidades.
6. Consistencia: la misma frase traducida de dos formas ("internas y externas" frente a
   "internas como las externas") → chequeo de frases repetidas y memoria de traducción.
7. Pareja Ascendente/Desc. inconsistente.
8. La "Í" mayúscula de ZODÍACO parece mal renderizada en la fuente de reemplazo.
Criterio de cierre de la 1d: 0 palabras en inglés fuera de tokens protegidos; títulos con su
peso y colores conservados; escala uniforme por grupo; se mantienen los criterios de la 1c;
renders lado a lado (original | traducción) para aprobación visual humana.

## 9. Pendientes futuros
- Muestra del piloto: incluir una página raster real (Ruta C) y una tabla de efemérides real.
  El agente propuso una tabla de ingresos de Mercurio de otro PDF como anexo técnico
  separado (docs/seleccion_piloto.md); la página 7 era un índice, no una tabla.
- G4 piloto → producción por lotes → Ruta B (figuras) → Ruta C (29 escaneadas) → juez VLM
  → revisión humana → ensamblado (G7).
- Las páginas de muestra siguen en estado "review" hasta que haya juez VLM y revisión humana.
- Lección aprendida: las métricas geométricas pueden estar en verde con la página aún mal.
  Las aprobaciones finales deben ser visuales y humanas, con renders lado a lado.

## 10. Visión a futuro (reutilización)
- No empezar de cero para otros libros: extraer un núcleo común.
  core/ (llm, glosario, caché, validación, cuota, QA lingüístico) + layout_inplace/ (lo
  actual) + reflow/ (nuevo) + profiles/ (reglas por tipo de contenido).
- Libros de solo texto: ruta reflow (extraer a Markdown con pymupdf4llm/Docling/Marker →
  traducir por capítulo con mucho contexto, lo que amortiza la sobrecarga de Codex →
  recomponer con Pandoc o Typst, a PDF y EPUB).
- Regla de triaje: si la maquetación forma parte del significado → in place; si es más de
  un 90 % texto corrido → reflow. Puede decidirse por capítulo.
- Dominios adecuados para la ruta de gráficas: libros de texto científicos, atlas de
  anatomía, manuales técnicos, guías de viaje y mapas, libros de cocina y catálogos,
  reglamentos de juegos e infografías, cómics e ilustrados infantiles (sobre todo Ruta B),
  tarot y numerología.
- Recomendación: terminar primero este pipeline (piloto y algún libro completo) y luego
  extraer el núcleo.

## 11. Conceptos clave explicados
- Glosario: fija la traducción de cada término. Aporta coherencia en las 258 páginas,
  exactitud (lo decide una persona una sola vez), verificación automática en QA, y permite
  que un modelo barato traduzca como experto. Es imprescindible porque cada llamada a Codex
  es un hilo efímero sin memoria entre páginas.
- Guía de estilo: decisiones de forma (tú/usted, variante, mayúsculas, comillas, formato de
  fechas, grados y casas).
- Memoria de traducción: pares de frases aprobadas; aporta coherencia a nivel de frase,
  ahorro de cuota y un activo que crece con cada libro.
- Es la práctica estándar del sector (libros de estilo editoriales, bases terminológicas y
  memorias en herramientas como Trados). El glosario, la guía y la memoria son el activo
  del usuario; el código es genérico.

## 12. Cómo retomar
Pegar este resumen al inicio de la nueva conversación junto con el último mensaje o informe
del agente (p. ej. el informe de la Fase 1d o sus renders lado a lado). Siguiente paso
esperado: revisar el cierre de la Fase 1d con los renders lado a lado y decidir si se
autoriza el piloto (G4).