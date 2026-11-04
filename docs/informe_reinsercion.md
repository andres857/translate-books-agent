# Fase 1c — Reinserción

Alcance: páginas externas 7, 15 y 121 de *Astrology: Using the Wisdom of the Stars in Your Everyday Life*. G1b aprobada parcialmente para backend y ruta; no autoriza G4. **Fase 1c terminada según su criterio de reinserción. Se detiene el trabajo aquí; no se ejecutó G4.**

## Resultado y verificación final

| Página externa | Unidades fallidas | Escala mínima | Diferencia fuera del texto | Preservación completa | QA |
|---|---:|---:|---:|---|---|
| 7 | 0 | 0.7522 | 0.0000 | glifos, cifras y caracteres protegidos: sí | revisar |
| 15 | 0 | 0.7570 | 0.0000 | glifos, cifras y caracteres protegidos: sí | revisar |
| 121 | 0 | 0.8179 | 0.0000 | glifos, cifras y caracteres protegidos: sí | revisar |

Cero unidades fallidas, todas las escalas >=0,75 y visual-qa local ejecutado en las tres páginas. Solo se usó una forma corta: **Desc.**, en una etiqueta de figura. La salida de tres páginas conserva el mapeo externo 7→1, 15→2, 121→3. No es el PDF final del libro.

**75 tests pytest pasan**, verificados de nuevo por el orquestador. Se renderizó la última muestra con Poppler a 110 dpi y se inspeccionaron sus tres páginas. Se detectó y corrigió una regresión real: la redacción con cajas de extracción pequeñas podía borrar letras de líneas protegidas adyacentes porque MuPDF usaba métricas de fuente más amplias. Ahora se validan zonas de redacción contra las cajas reales de todos los caracteres, seleccionando los propios sin intersectar los ajenos. QA añade `protected_spans_equal`, que verifica carácter, fuente y posición; no basta conservar solo el número de la línea. Las tres páginas pasan esa conservación y tienen diferencia de píxeles fuera del texto igual a cero.

PDF: `books/astrology-wisdom-stars/out/reinsercion_1c.pdf`. QA detallado y hash: `work/reinsertion/qa_1c.json`. Renders finales independientes: `work/reinsertion/final-review-1.png`, `final-review-2.png`, `final-review-3.png`.

El PDF original conserva su SHA-256 y permisos 0444. Los cuatro archivos G3 aprobados siguen intactos; las formas cortas se guardaron aparte con aprobación propia. El guard de fases rechaza piloto y producción con la aprobación parcial actual. No existe `pilot_approval.json`.

## Decisiones aplicadas

- Texto corrido compuesto por párrafos con `insert_htmlbox`, mínimo 0,75. Las etiquetas y texto que intersecta dibujos visibles se redactan por spans. Se ignoran únicamente marcos de imprenta cuyos trazos quedan fuera de la página visible; se conserva el fondo raster.
- La escala se decide por unidad. Los dibujos presentes en otra zona de la página no fuerzan escala 1 a todo su texto.
- Etiquetas: expansión libre, forma corta aprobada, LLM como último recurso. Para menos de 15 caracteres, presupuesto basado en geometría, no 110 % del inglés. No hubo llamadas nuevas de acortado en esta fase.
- Suplemento aprobado `glossary/astro_en_es_short.csv`: Asc., Desc., Sag., Cap., signo, signos, Merc., Júp., Sat. y Nept. Solo figuras, tablas y leyendas; nunca texto corrido. «carta natal» sigue completa. El CSV G3 original permanece intacto.
- La comparación 3× de BabelDOC quedó dispensada por decisión humana, registrada en `docs/decisiones.md`. La aprobación por hash está en `work/backend_approval.json`, alcance «backend y ruta» y fase autorizada `reinsertion`.

## Llamadas y caché

| Métrica, páginas 7/15/121 | Antes: llamadas por página | Después: agrupación |
|---|---:|---:|
| Llamadas translate_page | 3 | 1 |
| Entrada | 28.539 tokens | 11.128 tokens |
| Entrada reutilizada por proveedor | No registrada | 11.008 tokens |
| Entrada no cacheada | Desconocida | 120 tokens |
| Salida | 3.131 tokens | 2.483 tokens |
| Latencia acumulada | 64,86 s | 123,09 s |

La llamada agrupada incluyó 3.153 caracteres, con ids que identifican la página, y validó JSON, ids, marcadores y términos al primer intento. La reducción observada de entrada es 61,01 %. La latencia aumentó; no se afirma mejora de velocidad. La agrupación de spans/párrafos también cambió entre ensayos: es una comparación de las mismas páginas, no un experimento aislando una sola variable.

`usage.last.cached_input_tokens` es caché del proveedor, distinta de los resultados recuperados desde SQLite. No se imputa el uso compartido íntegro a cada página ni se supone que este 98,92 % de caché vaya a repetirse. Los datos históricos no registrados permanecen desconocidos, no cero. Estos tokens no equivalen a un porcentaje de cuota de suscripción.

Nueva inferencia de 1c: 11.128 tokens de entrada y 2.483 de salida. Los ajustes de composición reutilizan el resultado local. Cargos por API de pago: 0 USD; se consume la suscripción ChatGPT existente. Evidencias: `work/reinsertion/report.json`, `quota_comparison.json`, `work/costs.jsonl`.

## Reproducción sin inferencia

```bash
.venv/bin/python scripts/reinsert_sample.py --book books/astrology-wisdom-stars
```

El script exige el alcance autorizado, usa los JSON ya traducidos, valida la procedencia exacta de los spans, agrupa sin reescribir contenido, aplica el suplemento aprobado, ejecuta QA local y produce una muestra de tres páginas. La unión conserva saltos recuperables de título/cuerpo y normaliza únicamente espacios; no traduce texto manualmente.

## Límites editoriales que no se ocultan

El cierre geométrico no aprueba la traducción completa del libro. Persisten fragmentos ingleses dentro de spans protegidos con números; el sistema conservador los deja intactos. QA también puede señalar coincidencias terminológicas que atraviesan líneas y falsos positivos del detector de idioma. Los reportes mantienen esas observaciones para revisión.

No se enviaron al VLM páginas raster sin máscaras verificadas de contenido protegido. Se ejecutó visual-qa local, con renders, diferencias, glifos/números, terminología e idioma; el juez VLM y la revisión humana siguen pendientes. Por ello las páginas deben permanecer `review`, no `qa_ok` ni `done`.

## Futuro piloto

No se ejecutó el piloto. `docs/seleccion_piloto.md` reserva una página raster real del libro actual para Ruta C y una tabla real de ingresos de Mercurio en otro PDF disponible, como anexo técnico separado. No se hará pasar la tabla de contenidos por efemérides ni se mezclará la fuente externa con el libro final. La selección y autorización posterior del piloto siguen pendientes.
