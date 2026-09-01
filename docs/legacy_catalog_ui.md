# Port de la interfaz histórica del catálogo LeSiCo

## Procedencia auditada

- Repositorio: https://github.com/SEMILLES/lesico-catalogo-original
- Rama: `main`
- Commit: `ab5a06d5f3a9cf2e2da82dc664571f7a46996bcd`
- Archivos: `.nojekyll`, `index.html`, `estilos.css`, `aplicacion.js`,
  `catalogo.json`, `README.md` e `INSTRUCCIONES.txt`.

La auditoría se hizo sobre un clon temporal; no se incorporó su `.git`, no se
añadió un remote y el repositorio no es submódulo de esta aplicación.

## Qué se reutilizó y adaptó

- `index.html`: encabezado SEMILLES, nota metodológica, composición de dos
  paneles, buscador, lista de conceptos, panel de detalle y semántica básica.
- `estilos.css`: tipografía Arial/Helvetica, paleta, espaciado, fichas, botones
  de variantes, tablas, paneles secundarios y breakpoint móvil de 760 px.
- `aplicacion.js`: normalización de texto, filtrado inmediato, contador de
  resultados, selección visual y pestañas accesibles. Se reescribió para operar
  sobre HTML renderizado por Flask, sin solicitar datos legacy.
- La presentación histórica de ocurrencias, fuentes, enlaces, composición y
  relaciones se mapeó a la proyección canónica nueva.

## Qué se descartó

- `catalogo.json`: era la fuente legacy; nunca se copia ni se consulta.
- El `fetch("catalogo.json")`, metadatos de generación y transformación del
  modelo antiguo en JavaScript: la autoridad ahora es la proyección SQLite o el
  snapshot publicado.
- Filtros de campo semántico, tipo de variación y presencia de video: los dos
  primeros no tienen un campo canónico equivalente; el tercero no es honesto
  mientras la proyección no exponga media dedicada.
- Pestañas sociolingüísticas y “otros datos”: frecuencia, geografía, registro,
  etimología e iconicidad no existen en la proyección nueva.
- El grafo SVG inferido desde la nomenclatura legacy: las relaciones nuevas se
  muestran como lista explícita, sin inferir transitividad.
- Placeholders de video: no se muestra un reproductor cuando el snapshot no
  contiene media. Los enlaces canónicos de ocurrencias sí se presentan.

## Mapeo de modelos

| Interfaz legacy | Modelo canónico nuevo |
| --- | --- |
| `concepto.id` | `concept.preferred_label` |
| `alternativa.id` | `alternative.name` (`CONCEPTO-working_label`) |
| identidad de alternativa | `alternative_id`, solo para enlaces estables |
| `alternativa.ocurrencias` | `alternative.occurrences` |
| fuente plana | `occurrence.source` |
| composición | `alternative.morphology` y `components` |
| diferencia fonológica | `concept.relations`, únicamente relaciones current explícitas |
| video legacy | sin equivalente en la proyección actual; no se inventa |

El catálogo interno renderiza `build_catalog_projection(connection)`. El
externo renderiza exclusivamente `json.loads(publication.snapshot_json)`. Los
templates, CSS y JavaScript son compartidos; solo cambian el banner y las URLs.
