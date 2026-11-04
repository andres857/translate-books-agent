# Piloto de veinte páginas — revisión visual pendiente

Se ejecutó el piloto autorizado de *Astrology: Using the Wisdom of the Stars*, Carole Taylor, con `codex_sdk`, `gpt-6-luna/low`, `fallback=none` y reinserción propia PyMuPDF. Se entrega una muestra de revisión, no un libro terminado ni una aprobación de producción. Los pendientes editoriales se conservan explícitos en la cola de revisión.

## Entrega y selección

- [PDF traducido de veinte páginas](../books/astrology-wisdom-stars/out/piloto.pdf).
- [Veinte comparativas original | traducción](../books/astrology-wisdom-stars/out/piloto_comparativa.pdf), en el mismo orden.
- [Anexo técnico de Mercurio](../books/mercury-technical-annex/out/anexo_mercurio.pdf) y [su comparativa](../books/mercury-technical-annex/out/anexo_mercurio_comparativa.pdf).
- [Renders independientes Poppler](../books/astrology-wisdom-stars/work/pilot/poppler/) y [renders individuales de comparación](../books/astrology-wisdom-stars/work/pilot/comparisons/).

Páginas PDF: **1, 6, 7, 10, 14, 15, 24, 25, 32, 33, 34, 35, 63, 69, 121, 122, 135, 146, 248 y 250**. Los folios impresos pueden diferir. [Selección y función de cada página](seleccion_piloto.md).

La portada es una página raster real, sin texto nativo: no se rasterizó artificialmente una página para probar Ruta C. El OCR local tardó 111,12 s; se tradujeron título y subtítulo, pero quedan etiquetas curvas pendientes. El anexo externo es la tabla real de **ingresos de Mercurio por signo (1900–1944)**, p501/folio486 de Woolfolk; no es una efeméride diaria de longitudes. Sus datos y abreviaturas permanecen intactos. Solo se tradujo el pie editorial y no se incorporó al libro de Taylor. [Detalle de Ruta C y anexo](informe_ruta_c_anexo.md).

No se localizaron notas al pie académicas mediante el examen del texto nativo; se incluyeron créditos, leyendas y remisiones. Esto no certifica su ausencia en imágenes. Evidencia: `work/pilot/footnote_survey.json`.

## Correcciones previas y guía aprobada

- **p7:** nombres de signos y planetas en negrita, con verificación de fuente/peso en el PDF resultante.
- **p15:** medianil de 10,9794 pt, sangría inicial de 8,5 pt y separación entre párrafos sin líneas en blanco. La cita conserva una traducción consistente con el cuerpo.
- **p121:** «Simbolizan» concuerda con Nodos; el informe de página registra el texto original y la corrección.
- Se incorporaron exactamente las dos reglas finales aprobadas: corregir únicamente errores gramaticales objetivos sin cambiar contenido, datos o estilo, registrando el original; usar comillas inglesas dobles y simples para citas interiores. [Guía vigente](../glossary/style_guide.md) y [diff aprobado](cambio_estilo_piloto.diff). La autorización por hash y su historial están en `work/glossary_approval.json` y `work/approval_history/`.

El original y el CSV principal de 118 términos no cambiaron. La excepción **PLANETS** sigue limitada a la ilustración de p7; no ampara otras palabras inglesas en ilustraciones.

## Composición y comprobaciones

Las diecinueve páginas nativas tienen reinserción completa, cero unidades fallidas y escala mínima ≥0,75. La portada tiene sus dos regiones intervenidas completas, escala mínima 0,805. La diferencia medida fuera de las regiones editables es **0 a 110 dpi** en las veinte páginas. Esto verifica conservación gráfica fuera de esas regiones; no acredita por sí solo la fidelidad semántica de todo el texto.

Dieciséis de las diecinueve páginas nativas satisfacen los controles locales de fidelidad implementados. p6 requiere revisión léxica; p32 y p33 conservan texto circular sin traducir. Ninguna página se marca como terminada por el mero hecho de caber. Todas quedan pendientes de revisión visual humana del piloto.

Se corrigieron durante el piloto capitulares, separación título/cuerpo, tablas segmentadas, párrafos mal fragmentados, texto sobre iconos, leyendas rotadas y agrupación del índice. La protección de ilustraciones compara tinta nueva con obstáculos raster, además de las cajas y vectores. El ensamblado conserva los grupos de transparencia `/Group`: la página ensamblada coincide píxel a píxel con su candidato a 110 dpi en los veinte casos. Se complementó PyMuPDF con pypdf para preservar esos objetos; no se cambió la decisión AGPL de PyMuPDF.

En la última inspección de p146 se detectó una continuación «her MC» separada y una adición semántica indebida («casa»). Se unió al párrafo y se reutilizó la traducción previa válida «su MC», con contexto y hashes vigentes, sin nueva inferencia. La corrección y su procedencia constan en `work/pilot/p0146-continuation-repair.json`; no es una modificación de contenido de la autora.

Las fuentes originales se reutilizan con cobertura y comprobación de extracción tras composición. Las sustituciones mantienen los parámetros de estilo y color; la equivalencia visual entre familias requiere revisión. La Í mayúscula fue comprobada en los renders. Las entradas traducidas del índice tienen escala uniforme; las referencias numéricas originales protegidas mantienen su tamaño, una limitación visual explícita.

Evidencias: [composición por página](../books/astrology-wisdom-stars/work/pilot/composition-status.json), [integridad](../books/astrology-wisdom-stars/work/pilot/composition-integrity.json), [prueba de ensamblado](../books/astrology-wisdom-stars/work/pilot/assembly-proof.json), [informe estructurado](../books/astrology-wisdom-stars/work/pilot/pilot-report.json).

## Juez visual con enmascarado seguro

Se habilitó y ejecutó el juez sobre las **diecinueve páginas nativas**. Recibe copias diagnósticas que excluyen imágenes y vectores, ocultan la tinta de números/glifos protegidos y normalizan los colores. Las máscaras usan límites de glifos reales, fuentes y matrices de transformación; se comprueba la identidad de las fuentes de reemplazo por hash. El original y el PDF entregado no reciben esas máscaras. [Diseño y límites](safe_vlm.md).

La cobertura visible del texto varía: alrededor del 94 % en p7, más del 99 % en p15, y aproximadamente 78–80 % en el índice p250. **No evalúa ilustraciones, colores, símbolos ni todo el texto enmascarado.** La portada raster no tiene evaluación VLM completa. Estas áreas requieren controles locales y revisión gráfica humana.

El juez sirve como señal adicional, todavía no como puerta autónoma de producción. Ha confundido continuaciones originales y máscaras con recortes, e incluso términos españoles como Aries o Ascendente con inglés. También puede omitir los rótulos circulares ingleses de p33: el control local prevalece sobre un «ok» del VLM. Las respuestas originales se guardan sin sustituirlas por un veredicto inventado.

Las alertas de continuidad de p14/p15 y de Meridiano en p248 se cotejaron con el original. Las cajas grises sobre referencias numéricas no son defectos del PDF entregado. p25 conserva un ajuste denso que merece revisión visual; la leyenda inclinada de p69 debe juzgarse en el render completo, pues las barras y máscaras afectan la copia diagnóstica.

## Incidencias para revisión humana

| Página | Incidencia o límite pendiente |
|---|---|
| 1 | Ocho adjetivos curvos raster continúan en inglés: charismatic, practical, caring, progressive, mystical, courageous, focused, optimistic. No tienen excepción aprobada. El título usa una fuente de reemplazo aproximada porque no hay fuente incrustada. |
| 6 | «Copyright» requiere revisión léxica. Nombres propios, razón social, dirección y correo disparan falsos positivos del detector; no deben traducirse automáticamente. La alerta VLM sobre «Producer» no coincide con el render, que dice «Productor». |
| 32, 33 | Persisten nombres ingleses en los círculos: 102 y 99 fragmentos rotados registrados, respectivamente. Las tablas sí se reinsertaron. |
| 34, 122, 146 | Se conserva notación ordinal inglesa protegida, como 1ST/11TH/9th. Su adaptación al español requiere una regla explícita para no alterar los números; no se considera una excepción artística. |
| 25 | Revisar densidad, saltos y remisiones junto a iconos en el render completo; los controles de tinta nueva no detectan invasión de la ilustración. |
| 135 y sustituciones Medium | Los pesos y colores auditados se conservan, pero el contraste visual de una familia sustituta puede diferir del original. |
| 146 | Leyenda pequeña a escala 0,79: advertencia de legibilidad. La composición del cuerpo requiere inspección visual, además de las pruebas geométricas. |
| 248 | «Lights» y «Luminaries» confluyen en «Luminarias», con definiciones tautológicas. No se alteró el glosario G3; pendiente decisión terminológica. |
| 250 | Entradas traducidas a escala 0,75 (6 pt desde 8 pt), referencias protegidas a 8 pt. Contraste de tamaño pendiente. Se conserva el orden alfabético inglés del original; no es todavía un índice español reordenado. |

Estas incidencias impiden presentar la muestra como «cero inglés» o como lista para producción. La cola conserva capturas y acciones sugeridas; no se ocultan con una aprobación automática.

## Consumo real frente al proyectado

La tabla de consumo se genera al cierre desde `costs.jsonl`; incluye reintentos y separa la caché SQLite de los tokens de entrada cacheados por el proveedor. Los rechazos que no expusieron uso no se contabilizan como cero. La proyección previa se basaba en tres páginas de Fidelidad, con otra distribución de contenido; no es una comparación controlada de rendimiento.

| Alcance | Entrada | Entrada cacheada por proveedor | Salida | Latencia acumulada |
|---|---:|---:|---:|---:|
| Proyección previa para 19 p. nativas | 77,615 | No comparable | 21,704 | 405.72s |
| Real 19 p. nativas, con reintentos | 210,375 | 39,168 | 55,034 | 1040.97s |
| Libro principal: traducción + etiquetas + QA | 514,682 | 124,032 | 58,508 | 1196.50s |
| Prueba inicial de VLM, separada | 9,559 | 0 | 94 | 4.59s |
| Anexo externo, separado | 8,892 | 0 | 50 | 3.47s |

El libro principal acumula **54 intentos de servicio y 24 eventos de caché local**. Los tokens publicados son sumas conocidas mínimas: hay 4 intentos sin uso expuesto (tres rechazos de esquema y una interrupción). La entrada cacheada del proveedor está incluida en la entrada total; no se suma de nuevo.

La traducción nativa consumió al menos **2.71×** la entrada proyectada y **2.54×** la salida. Influyen los reintentos de validación, la corrección editorial y una muestra más compleja. El uso del juez visual es adicional a aquella proyección.

La extrapolación del estimador para 258 p. es aproximadamente 2,83 millones de tokens de entrada y 711 mil de salida para translate_page, incluidos reintentos medidos; excluye QA, etiquetas y errores sin uso conocido. Es una referencia de trabajo, no presupuesto fiable para producción ni porcentaje de cuota. Sus campos complete solo se refieren a las llamadas medidas que selecciona, no a todos los intentos.

Los tiempos son suma de latencias de servicio, no duración de calendario; no incluyen OCR, renderizado, pruebas ni trabajo de corrección. La estimación no convierte tokens a porcentaje de cuota de ChatGPT: ese saldo no está expuesto. No se usó OpenAI API de pago ni fallback. [Datos completos](../books/astrology-wisdom-stars/work/pilot/usage-report.json) y [proyección del estimador](../books/astrology-wisdom-stars/work/pilot/projection-after.json).

## Estado de entrega

La ejecución del piloto está terminada y **detenida para revisión visual humana**. No se inicia producción ni se registra aprobación final del piloto. La cola, el manifest y los hashes de entrega quedan asociados a esta muestra. La suite final registra **165 pruebas aprobadas en 22,78 s**, incluida la reparación de la continuación MC: [log de pruebas](../books/astrology-wisdom-stars/work/pilot/tests-final.log).

Las pruebas automatizadas verifican los controles implementados; no sustituyen la revisión editorial de las incidencias anteriores.
