# Fase 1d — Fidelidad

**Criterios técnicos cumplidos en páginas PDF 7, 15 y 121. Detenido para revisión visual humana. G4 no está aprobado y el piloto no se ha ejecutado.** Las tres páginas permanecen en estado `review`; no se presentan como calidad editorial aprobada.

## Resultados

| Página PDF | Fallos de reinserción | Escala mínima | Inglés editorial detectado | Diferencia fuera del texto | Escala uniforme por grupo |
|---|---:|---:|---:|---:|---|
| 7 | 0 | 0.95 | 0 | 0 | Sí |
| 15 | 0 | 0.91 | 0 | 0 | Sí |
| 121 | 0 | 1.00 | 0 | 0 | Sí |

QA local ejecutado a 110 dpi: glifos, números, siglas y spans simbólicos conservados; imágenes idénticas por hash y dibujos idénticos por geometría/estilo. Cero términos obligatorios ausentes y cero incidencias de consistencia. La auditoría independiente del PDF final verificó **3.276 caracteres** contra su color, tamaño y fuente previstos, además de la presencia de los archivos de fuente exactos por SHA.

**109 tests pasan** en la suite completa ejecutada por pipeline_engineer. Incluyen protección granular, fechas, memoria, caché/cuota, reinserción y auditoría de tipografía sobre PDFs reales. El orquestador comprobó independientemente los hashes del original/glosario, la vigencia de G3 y del backend, el bloqueo de piloto/producción y los tres renders finales de Poppler. El detalle ampliado confirma que la Í de ZODÍACO se dibuja correctamente.

## Cambios y evidencias editoriales

- La prosa con cifras usa marcadores `{nN}` sin transmitir sus valores al LLM, con restauración exacta. Se recuperaron los cuatro pasajes omitidos anteriormente en p15/p121. Las fuentes simbólicas y las siglas aisladas se conservan completas.
- **12/12 intervalos de fechas** convertidos mediante reglas locales EN→ES, sin LLM; sus 24 tokens numéricos permanecen iguales.
- Títulos y cuerpo se separan; los cambios de estilo dentro del párrafo se conservan por tramo. La cita p15 tiene tres líneas, escala **0,93**, y conserva su origen vertical. Las dos cláusulas compartidas aparecen **2/2 veces** con idéntica traducción en cita y cuerpo. También se verifica la repetición de «An Overview» en p7.
- Las columnas/listas y las etiquetas hermanas tienen escala común. Se corrigió la separación Capricornio/fecha y se alineó la columna de fechas. Los encabezados p15/p121 se mantienen en una línea mediante ampliación a la izquierda.
- **Ascendente y Descendente completos**, sin abreviaturas ni acortado LLM; MC/IC y las siglas del diagrama permanecen originales. El suplemento de formas cortas aprobado no se modificó ni fue necesario en esta muestra.
- La apertura «¿» del título se normaliza mediante una regla local para unidades interrogativas completas, con prueba de idempotencia.

## Fuentes y alcance visual

Se comprueba `has_glyph(..., fallback=False)` antes de reutilizar la fuente original. La cobertura se decide por grupo para que Aries/Leo y sus vecinos mantengan la misma familia. Los grupos sin cobertura española emplean instancias serif con peso 300/500/700, anchura y cursiva correspondientes al original. Los pesos numéricos y las marcas bold/italic se conservan; la forma y el contraste propios de la familia de reemplazo quedan visibles para la evaluación humana.

El ancho se calibra con los avances de caracteres comunes de la fuente original, porque el eje `wdth=75` de Roboto Serif no equivalía por sí solo a Soho Condensed. Esta equivalencia de estilo se registra aparte de la escala de composición de cada grupo. Se conservan los colores por carácter y se usan nombres internos únicos para verificar qué variante se aplicó realmente. Las instancias derivadas desactivan GSUB por un error comprobado de ToUnicode de las ligaduras fi/fl en Story; se conservan contornos, acentos y posicionamiento. Procedencia, licencia OFL y método en [fonts/README.md](../fonts/README.md).

El PDF original permanece con SHA `d13ab81406a66d3ab9863a9c2350fb326e8356d914f2f303ba3e6d4cbdc88046` y permisos 0444. El CSV G3 conserva SHA `7ec18ed0e873b3a949317137f5c684d0033f12cfd4066694b583fdf7362587fd`; no se modificaron los archivos editoriales aprobados.

## Excepción y revisión pendiente

El usuario autorizó conservar **PLANETS** como letras decorativas integradas en la ilustración raster de p7. Es la única excepción al criterio de cero inglés editorial. Registro: `work/fidelity/artwork_exception.json`. Las imágenes originales permanecen intactas.

No se ejecutó el juez VLM: no se ha verificado aún un enmascarado seguro del contenido protegido raster para ese envío. QA conserva el veredicto `revisar` y la cola apunta a las comparativas actuales para la revisión visual humana solicitada. Los controles técnicos satisfactorios no sustituyen esa aprobación ni habilitan G4.

## Uso del backend

Una llamada agrupada `codex_sdk`, `gpt-6-luna/low`, sin fallback: **12.255 tokens de entrada, 3.427 de salida, 0 `usage.last.cached_input_tokens`, 64,06 segundos**. JSON, ids, glosario y marcadores válidos al primer intento. Fechas, puntuación y ajustes de composición locales: cero llamadas adicionales.

La llamada de 1c registró 11.128 tokens de entrada, de los cuales 11.008 figuraron como cacheados por el proveedor. La llamada 1d incluye más texto y estilos: no es una comparación de rendimiento con carga idéntica ni garantiza una tasa de caché futura. La memoria local permitió repetir la composición sin inferencias.

Acumulado del libro en `costs.jsonl`: **155.640 tokens de entrada y 12.348 de salida**. No se activó la API de pago; el consumo corresponde a la suscripción y no permite deducir la cuota restante.

## Archivos para revisión

- [Comparativa original | traducción, tres páginas](../books/astrology-wisdom-stars/out/fidelidad_1d_comparativa.pdf).
- [Muestra traducida, tres páginas](../books/astrology-wisdom-stars/out/fidelidad_1d.pdf).
- Renders Poppler: [p7](../books/astrology-wisdom-stars/work/fidelity/poppler/comparison-1.png), [p15](../books/astrology-wisdom-stars/work/fidelity/poppler/comparison-2.png), [p121](../books/astrology-wisdom-stars/work/fidelity/poppler/comparison-3.png).
- [Detalle Í/ZODÍACO](../books/astrology-wisdom-stars/work/fidelity/renders/p0007-zodiaco-detail.png).
- Evidencia estructurada: [QA](../books/astrology-wisdom-stars/work/fidelity/qa_1d.json), [auditoría LLM/consistencia](../books/astrology-wisdom-stars/work/fidelity/llm_audit.json), [auditoría independiente](../books/astrology-wisdom-stars/work/fidelity/orchestrator_audit.json).

Reproducción local desde traducciones guardadas, sin red ni inferencias:

```bash
.venv/bin/python scripts/fidelity_sample.py --book books/astrology-wisdom-stars
.venv/bin/python scripts/finalize_fidelity_audit.py --book books/astrology-wisdom-stars
```
