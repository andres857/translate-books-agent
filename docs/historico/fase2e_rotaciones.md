# E1 — publicación parcial e inventario geométrico

La clasificación anterior `deferred_raster` tenía precedencia sobre todo el QA
nativo. Ahora una página con candidato y QA nativo satisfactorio puede quedar
`partial_deferred`: su prosa se conserva en el candidato publicado, pero la página
no es QA OK ni un fallo. Solo los parciales con `native_qa_ok=true` e inventario
justificado se excluyen del denominador de G5. Una avería de composición o de QA
nativo conserva `review`, aunque haya regiones pendientes. El inventario contiene
las cajas, fuentes, direcciones y spans originales, incluida la geometría de las
cifras, para que Fase 4 retome las regiones sin rehacer la prosa.

La inspección de operadores del PDF contradice la hipótesis de que la rueda de
p27 contiene palabras rectas simplemente giradas. Tanto nombres de signos como
`THE RAM` y fechas tienen un operador por glifo con tangente cambiante. En
`THE RAM` la dirección pasa de aproximadamente `(0.1412, 0.9900)` a
`(0.3630, 0.9318)`. El `rawdict` agrupa ocasionalmente dos letras bajo una sola
dirección aproximada; confiar solo en ese dato confundiría fragmentos curvos
con rótulos rectos. Las pruebas reales p26–31 verifican las direcciones de
`get_texttrace`. No hay rótulos rectos completos admisibles en esas seis páginas,
ni en el intervalo 21–40 completo.

`native_rotated.py` admite spans completos de al menos dos caracteres con
línea base recta y tangente constante probada por operador. No une letras
sueltas por conjetura. Compone el texto con el mismo ángulo y centro de línea
base, color original y escala uniforme por grupo tipográfico, mínimo 0,75.
La selección de fuentes es conjunta y comprueba cobertura/contornos; las
barreras de tinta vectorial, raster y texto vecino impiden ocupar otro sector.
El borrado utiliza la identidad de los operadores nativos. Las pruebas de
rotación arbitraria emplean un PDF sintético y no constituyen traducción real.

El compositor offline exige respuestas guardadas y ligadas a fuente, unidades,
glosario, estilo, lista protegida y configuración. Las pruebas geométricas
`verified_rotated_spans` solo retiran una región pendiente si sus hashes
corresponden al candidato actual. La ruta vertical previamente validada se
conserva. El script `prepare_rotated_labels.py` prepara lotes por figura/página;
solo `--execute` permite llamar a `llm.run_task`. Las fechas se resuelven
localmente y la frontera de `run_task` protege los números de los demás rótulos.

No se activó composición curva automática. Un círculo ajustado a centros de
cajas de glifos no basta para recuperar la línea base: el centro de tinta cambia
según la letra, existen palabras parcialmente recortadas por el borde y varias
filas concéntricas de fechas muy próximas. Falta cualificar conjuntamente la
segmentación completa de palabra/fila, el orden de cifras, la curva base y los
límites de sector antes de borrar esos operadores. Los spans curvos quedan
explícitamente inventariados; no reciben excepción de inglés ni se consideran
traducidos. No se afirma que sea imposible ajustar un círculo, sino que todavía
no hay una ruta robusta validada para estos casos.

Prueba roja inicial: nueve fallos en
`work/phase2e/red_rotations.log`. La suite específica posterior contiene doce
pruebas, incluidas exclusión G5 y conservación del estado de fallo nativo.
Se preparó offline `work/phase2e/rotated-plan.json`, con cero páginas y sin
inferencia. No hay unidades E1 que retraducir del lote 2 mediante este script.
