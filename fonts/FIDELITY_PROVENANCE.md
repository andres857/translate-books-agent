# Fuentes de la muestra de fidelidad 1d

Las fuentes originales se extraen del PDF únicamente dentro de `work/fidelity/fonts/original/`. Se comprueba `Font.has_glyph(codepoint, fallback=False)` para todos los caracteres necesarios. Los grupos de etiquetas hermanas evalúan la unión de caracteres de sus miembros: si algún miembro necesita sustitución, el grupo usa la misma familia equivalente, incluso Aries o Leo cuando su traducción es idéntica.

Las fuentes de sustitución son las versiones variables romana e itálica de [Roboto Serif, repositorio oficial Google Fonts](https://github.com/google/fonts/tree/main/ofl/robotoserif), bajo OFL. La licencia recibida está en `RobotoSerif-OFL.txt`. Los archivos originales se guardan con sus nombres oficiales; los SHA-256 de originales, instancias y fuentes incorporadas constan en los informes por página `work/fidelity/pNNNN-fonts.json`.

`fontTools` fija `wght` (Light 300, Medium 500, Bold 700), `wdth`, `opsz` y `GRAD`. Después calibra horizontalmente las métricas contra caracteres comunes de la fuente original. Se transforman contornos, avances y apoyos laterales, además de posiciones horizontales de GPOS. La altura y el peso solicitados se conservan; no se transforman las fuentes simbólicas originales. Esta calibración es necesaria porque un eje `wdth=75` no implica que los glifos tengan exactamente el 75 % del ancho normal. El factor aplicado y las anchuras de referencia se registran por instancia.

Las instancias modificadas tienen nombres internos únicos `AstroSerif-<hash>-<estilo>`, con peso, cursiva y ancho declarados en OS/2. Así la auditoría puede comprobar en cada carácter del PDF final qué archivo, tamaño y color se usaron realmente.

Se retira GSUB de las instancias de sustitución para esta salida latina EN→ES: la versión instalada de Story generaba una asociación ToUnicode incorrecta para ligaduras como `fi` (por ejemplo, la extracción de `significativo` contenía una eta griega). CSS `font-variant-ligatures:none` no corregía el comportamiento. Se conservan GPOS y los contornos; las letras se componen por separado. Las pruebas verifican texto extraído, glifos españoles, caracteres numéricos, colores, nombres de fuente y SHA-256 de los archivos efectivamente incrustados.

No se ha modificado el PDF original, la fuente simbólica de las cartas, el glosario G3 ni la guía de estilo aprobada. La calibración y la retirada de ligaduras afectan solo a fuentes derivadas para el texto nuevo.
