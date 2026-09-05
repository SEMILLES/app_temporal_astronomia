# Fase 16C — División y fusión de Sources

No se necesita migration 020. Se reutilizan el esquema post-019, `retired_at`, los
tres triggers, `source_revision`, `occurrence_revision` y `activity_event`.
No se modifican migrations ni se añaden columnas genealógicas.

## Operaciones

Solo Reviewer/Master acceden a «Dividir fuente» y «Fusionar fuentes». Se conserva
la acción independiente «Retirar / migrar fuente» de 16B. Analyst no ve las acciones
y sus crafted GET/POST reciben 404.

Dividir exige una asignación explícita y única para cada occurrence del origen:

- **Conservar original:** A sigue activa; debe moverse al menos una occurrence.
  Las que permanecen en A no se actualizan ni generan revisiones. También se
  permite moverlas todas y conservar A activa sin occurrences.
- **Reemplazar original:** todas pasan a dos o más destinos utilizados, distintos
  de A; A se retira. Una asignación faltante, duplicada o ajena al origen bloquea.

Los destinos pueden ser Sources activas o nuevas. Se pueden añadir y quitar
formularios de nuevos destinos; cada nuevo destino debe recibir occurrences.
La metadata del origen prellena esos formularios, incluido tipo, periodo, región,
caracterización, formatos y referencia. Se sugiere un nombre distinto y se permite
editar toda la metadata antes del preview. La copia es solo ayuda de formulario.

La fusión implementa exactamente **A+B→C nueva**, con dos orígenes activos distintos.
Ambos se retiran y todas sus occurrences pasan a C. El formulario de C puede partir
de A, de B o en blanco, y sigue siendo editable. C debe cumplir las validaciones
normales de Source. Toda Source nueva queda activa y protegida contra Analyst.

## Conservación y periodos

Las nuevas operaciones usan las mismas funciones de 16B para validar/crear
destinos, versionar y mover occurrences, ampliar periodos y retirar Sources vacías.
Se mantiene el `occurrence_id`. Antes de cada movimiento se guarda la revisión con
Source, Details/statuses, años, flags y demás campos documentales anteriores.
No se modifican gramática estructurada, assignments, Alternatives ni análisis.
Los Details se conservan literalmente, incluso si cambia el tipo de Source.

Se aplica la misma semántica de límites conocidos/NULL de 16B. Los conflictos se
presentan por destino y cada destino conflictivo requiere una decisión: ampliar
solo los límites necesarios o borrar exclusivamente los años fuera de rango.
No se reducen periodos, no se alteran años que caben y no se modifican occurrences
preexistentes del destino. Cada ampliación genera una revisión de Source.

## Preview y transacción

El preview no escribe y muestra los orígenes, su retiro/conservación, los destinos,
la metadata nueva, el reparto completo, cantidades y conflictos por destino.
La fusión incluye una comparación de los campos divergentes de A/B. Los IDs de
Sources nuevas se asignan exclusivamente al confirmar.

Se reutiliza el firmador de previews de 16B, con caducidad de una hora. El token
liga operación, rol, Source principal, especificación completa y huella de las
Sources/evidencias relevantes. No es intercambiable entre retiro, división y fusión.
Conservar la clave de aplicación permite validar tokens entre procesos; sin
`SECRET_KEY` se mantiene la clave efímera del proceso ya usada en 16B.

Confirmar requiere motivo no vacío, checkbox explícito y resolución de todos los
conflictos. Dentro de `BEGIN IMMEDIATE` se reconstruye y revalida el preview antes
de escribir. La creación de destinos, cambios de periodo, revisiones, movimientos,
retiros y activity tienen un único commit. Ante cualquier fallo se revierte todo.
Los triggers de 019 siguen impidiendo retirar Sources con occurrences o asignar
occurrences a Sources retiradas.

## Activity y consulta

Los eventos agrupados `source_split` y `sources_merged` contienen orígenes, modo,
destinos y reparto, movimientos con revisiones, Sources creadas/retiradas, periodos,
años eliminados, motivo y plantilla usada en fusión. Activity conserva actor, rol
y fecha; la creación usa además el evento habitual `source_created`.

El historial de cada origen y destino permite consultar «División de Source» y
«Fusión de Sources», incluidos los movimientos individuales desplegables.
La procedencia se conserva en eventos/revisiones, no en relaciones permanentes
entre Sources. Las publicaciones existentes permanecen como snapshots inmutables;
el catálogo live usa los destinos actuales de las occurrences.

## Archivos cambiados

- `source_structural.py`: funciones compartidas 16B/16C, preview y ejecución.
- `routes/source_retirement.py`: rutas de división/fusión, tokens e historial.
- `templates/fuentes.html`: acciones separadas de gestión.
- `templates/dividir_fuente.html`: reparto manual y formularios de nuevos destinos.
- `templates/fusionar_fuentes.html`: A+B→C y herencia editable.
- `templates/source_distribution_preview.html`: resumen y resoluciones por destino.
- `templates/source_history.html`, `templates/_source_distribution_history.html`:
  consulta estructurada de operaciones.
- `templates/source_operation_error.html`: retorno al formulario correspondiente.
- `tests/test_source_split_merge.py`: 23 tests, con subcasos para roles y modalidades.
- `tests/smoke_phase16c_source_split_merge.py`: smoke visual en copia temporal.
- Este informe de cierre.

## Validación final

Suite completa: **356 tests aprobados**, incluidos los 23 nuevos. Se comprueban
las reglas de división/fusión, integridad de la evidencia y revisiones, herencia,
resoluciones por destino, permisos, motivos, preview sin escrituras, tokens y
previews obsoletos, rollback parcial/final, activity y snapshots publicados.
`compileall` y `git diff --check` aprobados.

Smoke con Edge headless sobre copia temporal del baseline post-16B, sin migrations:
división conservando A y creando B, división reemplazando A, conflictos por dos
destinos con ampliar/eliminar, fusión heredando A y fusión heredando B con ampliación
de periodo. Se verifican campos heredados/editables, selección de metadata en
blanco, Analyst denegado e historial. Todas las operaciones usan Sources ficticias.

La copia termina con `integrity_check=ok`, cero violaciones FK, cero occurrences
sin Source o asociadas a Sources retiradas y cero publicaciones. Las capturas se
guardan fuera de Git en `import_inputs/astronomia/phase16c_smoke`.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe -m compileall -q -x '(\.venv|\.git)' .
git diff --check
.\.venv\Scripts\python.exe tests/smoke_phase16c_source_split_merge.py --output-dir import_inputs/astronomia/phase16c_smoke
```

Hashes verificados antes/después, sin cambios:

| Base | SHA256 |
|---|---|
| Working y baseline post-16B | `AADAEAABF2F69285C2153179B55E06C827F45698D420EA3703E114FE79252686` |
| Candidate | `3F7540A916AEC8DE91B1FEF01DE4A10D375304C915A6C1C675B7568FE2C78158` |
| Prototype | `DCCD19EDEAF96725951F194C66014A2DCE429F18B8DB285FB0CF8B34C1C93292` |
| Write-test | `B13DB525141341CEB7DF784ECF410146C1E203676E14A1F294E46E83B5B74D36` |

Cierre mediante un único commit local, sin push y sin operaciones en la working real.
