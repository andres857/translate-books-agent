# Propuesta inicial de formas cortas — historial

**Actualización:** el usuario corrigió y aprobó 10 formas finales en `astro_en_es_short.csv`; la propuesta inicial de este documento no se debe usar. Ver `short_target_aprobado.md` y el registro de aprobación por hash.

Se propone añadir la columna `short_target` en un archivo separado. Este CSV conserva exactamente las 118 filas y los valores de `source,target,tgt_lng` del glosario aprobado. Contiene **12 formas cortas** y **106 celdas vacías**; una celda vacía significa que no se ha propuesto una alternativa corta. No sustituye ni modifica el glosario G3 vigente y ninguna propuesta queda autorizada para su uso por la creación de estos archivos.

Las formas se eligieron a partir de términos presentes en los JSON locales de traducción de las páginas externas 7, 15 y 121. La columna de páginas indica presencia del término en spans filtrados, no prueba que cada aparición sea una etiqueta ni que la forma corta haya superado ajuste o QA.

| source | target aprobado | short_target propuesto | Páginas con el término |
|---|---|---|---|
| Mercury | Mercurio | Merc. | 7, 15 |
| Jupiter | Júpiter | Júp. | 7 |
| Saturn | Saturno | Sat. | 7, 15 |
| Neptune | Neptuno | Nept. | 7, 15 |
| Sagittarius | Sagitario | Sagit. | 7 |
| Capricorn | Capricornio | Capric. | 7 |
| zodiac sign | signo del zodíaco | signo zodiac. | 15 |
| zodiac signs | signos del zodíaco | signos zodiac. | 7, 121 |
| birth chart | carta natal | c. natal | 15, 121 |
| natal chart | carta natal | c. natal | 15 |
| Ascendant | Ascendente | Ascend. | 121 |
| Descendant | Descendente | Descend. | 121 |

## Criterios y alcance

- Usar exclusivamente en etiquetas ordinarias que no quepan después de intentar ampliar a espacio libre. Mantener el término completo en texto corrido, títulos amplios y cualquier etiqueta donde sí quepa. La presencia en la página 15 no permite abreviar sus párrafos.
- Conservar puntos abreviativos y tildes. Las abreviaturas propuestas son decisiones editoriales para este proyecto, no una afirmación de que sean un estándar astrológico universal.
- «c. natal» conserva la indicación natal: no propone reducir todas las cartas a «carta». «signo zodiac.» y su plural conservan el concepto zodiacal.
- «Ascend.» y «Descend.» se proponen solo como alternativas a palabras desarrolladas ordinarias. No generan ni reemplazan siglas protegidas como ASC o MC, ni variantes tipográficas de abreviaturas existentes. Los tokens originales protegidos deben conservar su forma, posición y fuente.
- No aplicar a spans de fuentes simbólicas, glifos, cifras, grados, numeración de casas ni ninguna abreviatura protegida, incluso si visualmente se parece a una palabra de esta tabla. No construir abreviaturas a partir de un glifo.
- Las etiquetas vectoriales mantienen escala original. Una forma corta no autoriza reducción adicional, solapamiento ni alteración de imágenes o vectores. Si continúa sin caber, corresponde revisión.
- El motor debe preferir la locución completa y reconocer su forma corta aprobada en QA para la etiqueta específica. Esto no autoriza a aceptar esa forma en todas las apariciones del término.
- Cualquier uso requiere aprobación humana explícita de esta propuesta y vinculación de su hash al estado de aprobación correspondiente. La aprobación G3 existente permanece intacta y no cubre automáticamente esta nueva columna.

## Trazabilidad

Archivo propuesto: `glossary/astro_en_es_short_candidates.csv`.

SHA-256 del CSV propuesto: `9dd698be454a36f78e4b4d238548bb1b4fbff8db987455c88c2a866c838a1965`.

SHA-256 del CSV G3 de referencia: `7ec18ed0e873b3a949317137f5c684d0033f12cfd4066694b583fdf7362587fd`.

Fuentes de evidencia: `work/backend_smoke/p0007-translation.json`, `p0015-translation.json` y `p0121-translation.json`. Se consultaron localmente campos de texto filtrado y términos, sin traducir párrafos ni realizar llamadas adicionales al backend o API. Costo API adicional: 0 USD por ausencia de llamadas; costo de la sesión Codex no disponible.

Se comprobaron la igualdad exacta de las tres columnas originales, el número de filas, el límite de formas cortas, la reducción de caracteres y la integridad de los cuatro archivos editoriales G3 y del registro de aprobación. No se realizó reinserción ni QA visual de estas abreviaciones.
