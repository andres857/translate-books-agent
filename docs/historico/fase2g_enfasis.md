# Fase 2G — énfasis plano, márgenes editoriales y apertura española

Trabajo de código y comprobación offline. No se ejecutaron LLM/VLM reales,
`produce` ni commits. No se modificaron glosarios, configuración, prompts,
schemas, original, aprobaciones, manifest, costos ni `work/production` del libro
activo. Los libros temporales de pruebas emplean proveedores simulados.

## Protocolo de énfasis

`llm.run_task` convierte **toda solicitud con `_style_pairs`** al contrato
`flat-v1`. Las parejas siguen identificando los estilos de origen localmente;
el proveedor recibe el texto completo sin `{vN}` y una lista de frases de origen
con claves de estilo. Las instrucciones y la ampliación del esquema están en
`emphasis.py` y en la construcción del wire; los archivos de `prompts/` y
`schemas/` no cambian.

La respuesta contiene `translation` plano y `emphasis`, una lista ordenada de
objetos con `style`, `text` y `occurrence`. Esta última es `null` cuando el
substring es único o un índice de ocurrencia empezando en uno. Las instrucciones
y la validación local exigen:

- Substrings exactos, no vacíos, ordenados, sin solapes ni cortes de marcadores.
- Claves de estilo existentes y presencia de todos los estilos enfatizados.
- Por cada estilo, cantidad original ±1, con mínimo uno; se permiten una fusión
  o división. Runs contiguos del mismo estilo se consolidan antes de contar.
- Ningún `{vN}` en la traducción y ninguna traducción vacía.
- Marcadores protegidos conservados; los valores se restauran localmente y se
  valida nuevamente el énfasis después de restaurarlos.
- Glosario sobre la frase plana completa, con la flexión determinista de E5,
  también al aceptar caché por bloque.

El estilo base se obtiene del original, priorizando Light/regular frente a
Bold/italic. Las claves distintas conservan localmente la fuente, peso, color,
tamaño y demás atributos originales completos. Una unidad de un solo estilo
usa `emphasis=[]`. La reconstrucción local alterna el estilo base y los rangos
validados, sin traducir ni adivinar fronteras léxicas.

Esto cubre citas completas, títulos con varios runs y las solicitudes de 2C,
2E y 2F que usan parejas. Las correcciones históricas ya guardadas siguen siendo
legibles mediante el decoder antiguo; no se reescriben. La ruta histórica de
`_style_markers` sin parejas sigue disponible para unidades cuya topología y
orden de estilos permanecen fijos.

Tras tres respuestas inválidas, se intenta `gpt-6-astra/high`, hasta tres
respuestas más, antes de enviar a revisión. La retroalimentación conserva el
motivo de validación; la tercera tentativa de cada nivel puede reducirse a los
ids fallidos. El escalado vuelve a validar el grupo completo. No es un cambio
de backend ni una habilitación de API de pago. Cada llamada real futura queda
en el journal normal con modelo/esfuerzo efectivos y
`escalation=emphasis_validation`; costo de suscripción desconocido es `null`.
La caché conserva procedencia efectiva, incluida la respuesta de Astra.

Solo las claves de solicitudes con parejas incorporan el nuevo contrato; las
unidades planas de un lote mixto conservan sus claves. Una respuesta inválida,
incluso tras restaurar valores protegidos, nunca entra en caché.

## Planes y ejecución posterior por el usuario

`work/phase2g/retranslation-plan.json` contiene **14 unidades**: las doce de 2E
y las citas completas p15/p17. El script recupera las traducciones del piloto
cuando no hay payload de producción, evitando inventariar como pendientes todos
los párrafos de esas dos páginas históricas. Las peticiones declaran `flat-v1`.

El plan adicional contiene p41 `u0004` y p56 `u0010`, detectadas por el nuevo QA
de apertura, y la cita completa p59 `u0000_quote`, ya pendiente desde 2F.
Preparar no hace inferencia. La ejecución comprueba dependencias y hashes,
persiste por grupo y conserva correcciones ajenas. Bajo lock comprueba cambios
concurrentes en los payloads seleccionados y en las correcciones de los mismos
ids, antes de escribir.

Desde la raíz del repositorio, después de coordinar con el escritor concurrente:

```bash
.venv/bin/python scripts/prepare_phase2e_retranslations.py --book books/the-secrets-of-astrology --pages 15,17,21-40 --plan books/the-secrets-of-astrology/work/phase2g/retranslation-plan.json --diagnostics books/the-secrets-of-astrology/work/phase2g/recomposition
.venv/bin/python scripts/prepare_phase2e_retranslations.py --book books/the-secrets-of-astrology --pages 15,17,21-40 --plan books/the-secrets-of-astrology/work/phase2g/retranslation-plan.json --diagnostics books/the-secrets-of-astrology/work/phase2g/recomposition --execute --output books/the-secrets-of-astrology/work/production/composition_corrections.json

.venv/bin/python scripts/prepare_phase2e_retranslations.py --book books/the-secrets-of-astrology --pages 41-60 --plan books/the-secrets-of-astrology/work/phase2g/additional-plan.json --diagnostics books/the-secrets-of-astrology/work/phase2g/recomposition
.venv/bin/python scripts/prepare_phase2e_retranslations.py --book books/the-secrets-of-astrology --pages 41-60 --plan books/the-secrets-of-astrology/work/phase2g/additional-plan.json --diagnostics books/the-secrets-of-astrology/work/phase2g/recomposition --execute --output books/the-secrets-of-astrology/work/production/composition_corrections.json

.venv/bin/python scripts/recompose_production.py --book books/the-secrets-of-astrology --pages 15,17,21-60 --output books/the-secrets-of-astrology/work/phase2g/after-retranslations
```

Las dos órdenes con `--execute` son inferencia futura, **no ejecutada aquí**.
La última recomposición es offline. Para incorporarla al flujo de producción
con su QA y su posible juez VLM, la orden posterior del usuario es:

```bash
.venv/bin/python -m astro_translate produce --book books/the-secrets-of-astrology --pages 15,17,21-60 --resume --recompose
```

La cita p59 no resuelve el ajuste pendiente de p49 ni las regiones raster/curvas.
El título histórico separado de p25 ya no puede invadir el margen izquierdo
para aparentar ajuste: su petición completa está en el plan principal. Su
ajuste definitivo solo puede evaluarse después de la nueva respuesta real.

## Márgenes y QA

`editorial_margins.py` obtiene los extremos horizontales del texto editorial
original. Excluye fuentes simbólicas, texto girado y bandas de cabecera/pie;
incluye cifras editoriales interiores, necesarias para el índice. La página
actual proporciona la medida cuando contiene texto; la enfrentada, reflejada,
es respaldo si falta esa evidencia. No se usa el borde físico como espacio
libre adicional.

Los candidatos de crecimiento se recortan a esa medida. Se conserva la huella
inicial de inserción de hasta 1,1 pt alrededor del texto nativo: ese padding
no es crecimiento de tinta. Las leyendas centradas se reducen simétricamente,
sin mover el centro. QA comprueba las palabras realmente renderizadas contra
el margen, además de los controles habituales de preservación.

El nuevo límite tiene efectos deliberados fuera de p36/p40. El golden declara
individualmente las páginas cambiadas; no se alteró el golden congelado. Los
replays se ejecutan en un checkout temporal, con escrituras externas y red
bloqueadas. Los cambios deben conservar imágenes, vectores, números, glifos,
estilos, texto completo, escala ≥0,75 y diff fuera de texto cero. Para una
incidencia histórica de QA ya existente se exige igualdad de las listas de
residual inglés, rotaciones pendientes y problemas textuales; no basta con
aceptar un booleano fallido. El resultado previo del runner se conserva en
`legacy_result.json`.

Se ajustaron dos expectativas históricas de tests: p35 admite la escala 0,88
que exige su margen, y p25 exige que el título fragmentado pendiente deje de
ajustarse invadiendo el margen. Las columnas p101 se acotan al menor de su marco
y el margen original; el test sigue exigiendo ajuste ≥0,80. Los tests de centro
de leyendas permanecen estrictos.

## Puntuación y registro gramatical

El QA detecta `!` y `?` sin apertura en la misma oración. Los marcadores,
cifras, glifos y siglas protegidas no cuentan como contenido de oración.
Solo se corrige automáticamente una unidad completa en mayúsculas, con un único
cierre, comienzo inequívoco y sin límites de párrafo ambiguos. Se inserta el
signo conservando el estilo del primer carácter y se registra
`type=grammatical_correction`, regla, original, target anterior/posterior e id.
No se modifica ninguna palabra. La corrección es idempotente.

p37 contiene dos correcciones de este tipo, en `u0011` y `u0016`; la segunda
es la señalada por el usuario. Los registros están en el fit de recomposición.
p41/p56 quedan en el plan adicional porque no cumplen la condición conservadora.

El reajuste de créditos de p4 expuso una entidad de dirección partida por
saltos de línea. La excepción local ahora prueba el nombre completo contra
origen, target guardado y texto renderizado. No se exime la prosa circundante.

## Verificación y artefactos

Las tres respuestas reales fallidas están congeladas sin cambios en
`tests/fixtures/phase2g/`. Se prueban directamente y como tres respuestas
consecutivas de un backend simulado antes del escalado. Los tests de eco
mecánico no son traducciones nuevas ni integración real con un modelo.

Logs TDD: `work/phase2g/red.log`, `red-punctuation.log`, `red-empty.log` y
`red-placeholder.log`. La ejecución inicial completa conserva fallos de
regresión y dos errores por espacio de disco; **no se considera validación
satisfactoria**. El intento `golden-final` también quedó incompleto por espacio.
Los replays posteriores extraen PDFs de una sola página con hash del candidato
completo y hash de la extracción, evitando duplicar el libro entero en cada
carpeta de evidencia.

Los resultados finales se detallan en `work/phase2g/verification.json`.
Comparativas: `work/phase2g/recomposition/evidence/` y
`work/phase2g/margins-final/evidence/`. No hubo evaluación VLM nueva.

Comandos de verificación:

```bash
.venv/bin/python -m pytest -q --basetemp=/tmp/phase2g-pytest-verified
.venv/bin/python -m pytest -q books/the-secrets-of-astrology/work/pilot books/the-secrets-of-astrology/work/backend_smoke
.venv/bin/python scripts/check_phase2g_golden.py --output books/the-secrets-of-astrology/work/phase2g/golden-final-verified
```

### Declaraciones precisas del golden y comprobaciones adicionales

Cambios de PDF esperados: **19, 35, 60, 69, 86, 87, 100, 101, 128, 154,
173, 175, 179 y 180**. p19/p87 ya estaban declaradas en 2C; las otras doce
cambian por el límite de margen. p86 conserva su residual inglés histórico `H`:
la comparación exige que no aparezca otra incidencia, sin convertir esa página
en QA aprobado. Las otras seis páginas del golden deben ser idénticas byte a
byte, incluida p28.

El primer recorte cambió innecesariamente un marcador de p28. La regla final
conserva un desplazamiento de hasta 1,1 pt **solo para signos tipográficos sin
cambio de texto y sin aumento de ancho**; no concede esa tolerancia a la prosa.
Hay regresiones explícitas tanto del marcador como de los límites estrictos de
los cuerpos de p36/p40 y equivalentes. La evidencia de equivalencia de candidatos
para las ocho páginas finales está en `native-padding-plan-equivalence.json`.

La restauración de valores protegidos puede convertir un substring único en
repetido (por ejemplo, dos marcadores distintos cuyo valor local es idéntico).
El protocolo conserva el rango validado y recalcula su ocurrencia localmente.
El camino inverso de validación de respuestas guardadas hace lo mismo al
remascarar; el LLM nunca necesita conocer los valores para desambiguarlos.

Las comparativas exploratorias de `recomposition/` también se compactaron a
PDFs de una página. Cada extracción conserva hashes y prueba de identidad de
píxeles respecto al candidato completo que pasó la comprobación. Los ocho
candidatos de `margins-final/` se conservan también como PDFs de una página,
con identidad de píxeles verificada a 110 dpi. El índice conjunto es
`work/phase2g/index.html`; el registro separado de las dos correcciones de p37
es `work/phase2g/grammatical-corrections.json`.

### Resultado final

- **593 tests pasados en 543,76 s**, sin omitidos (`pytest-verified.log`).
- **39 tests locales pasados en 0,87 s** (`local-verified.log`).
- **Golden final satisfactorio**, aislado y sin red: seis PDFs idénticos y las
  catorce diferencias declaradas; ninguna incidencia nueva. Resultado en
  `golden-final-verified/result.json` e aislamiento en `isolation.json`.
- Márgenes, estilo y preservación verificados en 36/37/40/44/48/52/56/60;
  diferencia fuera del texto cero. p56 conserva únicamente el pendiente de
  apertura preparado para retraducción. Las escalas son, respectivamente,
  0,97 / 0,88 / 0,90 / 0,81 / 0,90 / 0,95 / 0,90 / 0,89.
- Tres grabaciones idénticas a la evidencia original; hash del original
  `517746c22d1897c06c6f5efde58564956c4389321f72b068494a25c5e7858a9c`.
- `git diff --check` satisfactorio. Sin inferencia real, producción ni commits.

En el snapshot exploratorio 41–60, p47 tiene un fallo de ajuste de story de
leyenda y p49 sigue necesitando una forma corta revisada; p58 es raster y p59
necesita su cita completa. No se sustituyeron respuestas para ocultar esos
pendientes. Los planes, hashes de implementación, extracciones de páginas y
límites de la comprobación constan en `work/phase2g/verification.json`.
