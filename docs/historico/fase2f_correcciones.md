# Fase 2F — fichas, leyendas y formas cortas del lote 41–60

Implementación y recomposición offline de respuestas ya guardadas. Sin LLM/VLM
real, producción real ni commits. El único `produce` ejecutado lleva `--dry-run`.
No se modifican glosario, configuración, prompts, schemas, original ni archivos
de aprobación. No se escribe en `work/production`, manifest ni costos del libro
activo; las pruebas usan temporales y la evidencia nueva reside en `work/phase2f`.
No se sustituyen carpetas de evidencia de fases anteriores.

## Causas y resultados

| Caso | Causa y corrección general | Evidencia |
|---|---|---|
| F1 | La separación de filas de 2E ya corrige el párrafo corrido, pero la fila «Symbol» cambiaba a un grupo especial de campos protegidos y quedaba a otra escala. Ahora conserva el grupo de sus hermanas. La detección depende de alineación, tamaño común y alternancia de peso; se elimina el umbral exclusivo de texto grande. Un valor Bold envuelto conserva íntegro su run, dentro de su caja original, sin adivinar límites de palabras traducidas. | p42/46/50/54: siete líneas físicas, seis pares; el último par conserva el salto original. Escalas uniformes 0,92/0,91/0,92/0,91. Glifo nativo intacto, Light/Bold, sin `glyph_spacing` y diff fuera del texto cero. Regresión previa p38 conservada. |
| F2 | Un bloque PDF puede intercalar líneas de leyendas distintas; p43 y p47 contienen esos bloques mixtos. `figure_captions.py` recupera párrafos por alineación de líneas, tamaño/estilo, proximidad y extremos de guías. Guías independientes impiden unir callouts vecinos. Las unidades completas ya válidas conservan sus ids y respuestas. Las particiones nuevas tienen ids ligados a sus spans y requieren retraducción. | Cuatro callouts de p43, tres de p47. Cinco peticiones nuevas: tres en p43, dos en p47. p39/51/55 conservan cuatro leyendas cada una. Alineación centrada en estos casos, crecimiento libre acotado y fuente italic comprobada en el PDF real. |
| F3 | La ruta fidelity no consumía suplementos separados de formas cortas. Ahora prueba primero todos los candidatos del texto completo hasta 0,75; registra únicamente la unidad que bloquea el grupo. Solo entonces consulta un suplemento revisado y vuelve a medir el grupo. | p49 `u0004`: 33 cajas candidatas ensayadas, ancho máximo 166,738 pt y presupuesto inicial 16 caracteres. No usa `len(source)*1,10` para CARING. El presupuesto es orientativo: la forma revisada debe superar también las guardas reales de tinta y el ajuste grupal. |
| F4 | No se reprodujo sustitución de color: el contraste bajo ya está en el original. Se comprueba cada run y cada carácter renderizado de los bloques mixtos en p49/53/57, incluyendo los estallidos. | Colores originales conservados. Las pruebas verifican también instancia de fuente y tamaño. No se cambia la paleta para declarar artificialmente corregido un contraste que pertenece al original. |

Las itálicas de las leyendas ya constan en `SohoPro-Italic`; no se encontró una
conversión general a redonda en la composición actual. La prueba nueva verifica
los flags de los spans realmente insertados y su alineación, además del plan.
El reemplazo slab italic sigue usando RobotoSerif-Italic según la decisión de
fuentes existente; no se introduce otra fuente ni se cambia su licencia.

F1 conserva los valores de los glifos mediante sus operadores nativos. El rótulo
se ancla a su línea base original y toda la ficha comparte escala. Las filas con
label/valor usan una única línea base por fila, comprobada en el PDF resultante;
se ignoran únicamente los espacios nativos sin tinta que MuPDF deja en otras
líneas de extracción.

## Estado de las veinte páginas

- **15 con QA local satisfactorio:** 41, 42, 44, 45, 46, 48, 50–57 y 60.
- **43/47:** `caption_retranslation_required`; no se publica el texto histórico
  fusionado como si fueran leyendas independientes. Cinco unidades preparadas.
- **49:** sigue pendiente una propuesta y su revisión explícita. Ninguna forma
  corta se ha generado, aprobado ni aplicado al libro real.
- **58:** página raster, diferida a Fase 4.
- **59:** candidato mecánicamente correcto, pero conserva la cita fragmentada
  detectada por 2E; se prepara una petición adicional de cita completa.

Hay **20 comparativas PNG** en `work/phase2f/evidence/`, además de dos imágenes
con las cajas de segmentación de 43/47. El índice es `evidence/index.html`.
Cuando no hay candidato admisible, se muestra el original a ambos lados y se
indica el estado: esas imágenes no constituyen traducciones terminadas.

Los 16 candidatos compuestos conservan imágenes, vectores, números y spans
protegidos; tienen tipografía auditada y diferencia cero fuera del texto.
p57 conserva advertencia por escala mínima 0,78. Ninguno recibió un juicio VLM
nuevo. Las máscaras existentes se prepararon localmente; no se enviaron imágenes.

## Suplementos de formas cortas

`short_forms.py prepare` consume diagnósticos del ajuste y crea un plan offline.
`propose --execute` es la única entrada nueva de inferencia: llama a
`astro_translate.llm.run_task("shorten", ...)`, por página/figura y hasta 6000
caracteres, con límite de caracteres, ancho/tamaño, significado, terminología y
conservación de marcadores. Las gates, costos, caché y cuota siguen en `llm.py`.
Una pausa de cuota sale con código 75; no hay reintentos manuales en bucle.

Las propuestas se guardan en `work/short_forms/proposals.json`. No se consumen
como traducciones. El comando `approve` exige un JSON de revisión aportado por el
revisor, que identifica el hash exacto de las propuestas y las solicitudes
seleccionadas. Crea `work/short_forms/approved-vN.json` bajo lock y escritura
atómica; conserva las versiones anteriores. La composición verifica hash,
revisor, dependencias, identidad de unidad, geometría, texto completo original y
estilos. Cambios invalidan el suplemento. Las propuestas sin firma no sirven.

La identidad de fuente se liga al archivo base, ejes y calibración, no al timestamp
de una instancia temporal; así la misma revisión funciona en offline y producción.
La forma corta vuelve a pasar por cobertura de fuente, grupos de escala y guardas
de tinta. Un suplemento no permite saltarse la geometría ni bajar de 0,75.
No se escribió ningún suplemento aprobado en esta sesión.

## Archivos del encargo

- `src/astro_translate/figure_captions.py`: segmentación espacial y reconocimiento de guías.
- `src/astro_translate/short_forms.py`: diagnósticos y suplementos revisados por hash.
- `fidelity.py`, `caption_growth.py`, `structured_layout.py`, `protected_labels.py`:
  integración de leyendas, escala grupal y filas/valores envueltos.
- `scripts/prepare_phase2f_retranslations.py`: migración selectiva de leyendas,
  con comprobación de spans y escritura CAS bajo lock; preserva bytes de unidades
  ajenas. Solo `--execute` hace inferencia y escribe respuestas de producción.
- `scripts/short_forms.py`: preparar, proponer y aprobar.
- `scripts/check_phase2f_golden.py`: replay real en checkout temporal con guardas
  de escritura y red; no restaura el manifest del libro activo.
- `scripts/phase2f_report.py`, `scripts/recompose_production.py`: evidencia offline.
- `tests/test_phase2f.py`, `tests/test_short_forms.py`, `tests/fixtures/phase2f/`:
  casos reales congelados, pruebas adversas y fixtures mecánicas temporales.

No se añadieron reglas por número de página al código común.

## Comandos offline

Desde la raíz del repositorio:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest -q books/the-secrets-of-astrology/work/pilot books/the-secrets-of-astrology/work/backend_smoke
.venv/bin/python scripts/check_phase2f_golden.py --output books/the-secrets-of-astrology/work/phase2f/golden
.venv/bin/python scripts/recompose_production.py --book books/the-secrets-of-astrology --pages 41-60 --output books/the-secrets-of-astrology/work/phase2f
.venv/bin/python scripts/prepare_phase2f_retranslations.py --book books/the-secrets-of-astrology --pages 41-60 --plan books/the-secrets-of-astrology/work/phase2f/retranslation-plan.json
.venv/bin/python scripts/prepare_phase2e_retranslations.py --book books/the-secrets-of-astrology --pages 59 --plan books/the-secrets-of-astrology/work/phase2f/quote-plan.json
.venv/bin/python scripts/short_forms.py prepare --book books/the-secrets-of-astrology --pages 41-60 --diagnostics books/the-secrets-of-astrology/work/phase2f --plan books/the-secrets-of-astrology/work/phase2f/short-form-plan.json
.venv/bin/python scripts/phase2f_report.py --book books/the-secrets-of-astrology
.venv/bin/python -m astro_translate produce --book books/the-secrets-of-astrology --pages 41-60 --resume --recompose --dry-run
```

## Comandos posteriores por el usuario — no ejecutados

Los planes deben prepararse de nuevo si cambian sus dependencias o respuestas
origen. Primero las leyendas; la cita p59 es una operación separada que conserva
las correcciones existentes de las otras páginas:

```bash
.venv/bin/python scripts/prepare_phase2f_retranslations.py --book books/the-secrets-of-astrology --pages 41-60 --plan books/the-secrets-of-astrology/work/phase2f/retranslation-plan.json --execute
.venv/bin/python scripts/prepare_phase2e_retranslations.py --book books/the-secrets-of-astrology --pages 59 --plan books/the-secrets-of-astrology/work/phase2f/quote-plan.json --execute --output books/the-secrets-of-astrology/work/production/composition_corrections.json
.venv/bin/python scripts/short_forms.py propose --book books/the-secrets-of-astrology --plan books/the-secrets-of-astrology/work/phase2f/short-form-plan.json --execute
```

La última orden imprime `proposals_sha256` y `review_request_sha256s`. Tras
revisar el texto, crear `work/short_forms/review.json` con este formato, sustituyendo
los valores entre ángulos por los hashes impresos y seleccionados:

```json
{
  "approved_by": "claude (delegación explícita de andres, 2026-11-06)",
  "proposals_sha256": "<hash exacto de las propuestas revisadas>",
  "request_sha256s": ["<hash de la solicitud seleccionada>"]
}
```

Importar esa revisión y ejecutar la recomposición autorizada posteriormente:

```bash
.venv/bin/python scripts/short_forms.py approve --book books/the-secrets-of-astrology --review books/the-secrets-of-astrology/work/short_forms/review.json
.venv/bin/python -m astro_translate produce --book books/the-secrets-of-astrology --pages 41-60 --resume --recompose
```

El último comando puede ejecutar el VLM de producción; no se ejecutó en 2F.

## Validación

TDD conservado en `work/phase2f/red*.log`: escala no uniforme en las cuatro
fichas reales, ausencia del segmentador de leyendas, unión indebida de callouts
con guías distintas, valor envuelto sin límite de palabra inequívoco, ausencia
de suplementos e identidad de propuesta dependiente del timestamp de la fuente.
Los tests incluyen regresiones reales y casos adversos derivados; las respuestas
simuladas prueban contratos y no se presentan como integración LLM.

El primer pytest completo pasó **542 pruebas en 566,70 s**. La ejecución final,
con todas las regresiones añadidas, pasó **548 pruebas en 512,78 s**
(`pytest-final.log`). La suite local final pasó **39 pruebas en 0,81 s**.

El golden real final (`work/phase2f/golden-final/result.json`) pasa: **18 PDFs
idénticos byte a byte y únicamente p19/p87 distintos**, como estaba declarado
en 2C. Las veinte páginas se recompusieron con el runner real en un checkout
copiado bajo `/tmp`, con inferencia/red prohibidas y bloqueo de escrituras fuera
de ese checkout. El manifest activo no se copió de vuelta ni se restauró.

El primer intento aislado omitió las instancias de fuente cacheadas; sus timestamps
cambiaron los hashes de los PDFs aunque el ajuste fuera idéntico. Se conserva ese
fallo en `golden-without-font-cache`. Los replay posteriores copian las instancias
originales sin modificarlas y recuperan la comparación byte a byte. No se cambió
el golden congelado ni se aceptó ese primer resultado como éxito.

Se inspeccionaron las veinte comparativas, la ficha p42 a resolución completa,
p53 y el inventario espacial de p43. `git diff --check` pasa. Los 39 archivos
protegidos inventariados al comienzo conservan sus hashes. La actividad concurrente
del usuario en producción y costos no se usa como evidencia de este trabajo.
