# Asignación de trabajo de conceptos

Rama `feature/analyst-work-assignment`, basada en `feature/occurrence-form-review`
(`f82903b`). Worktree: `.worktrees/analyst-work-assignment`.

## Decisiones

- Se reutiliza `collaborator`. No hay usuarios individuales autenticados ni un rol
  por colaborador: los permisos existentes dependen del prefijo privado de la ruta.
  Todos los colaboradores activos pueden recibir trabajo; `reviewer` y `master`
  administran las asignaciones. «Mi trabajo» es exclusivo del rol exacto `analyst`.
- El operador se toma de «Trabajando como», que es una identificación declarada,
  no una identidad autenticada. Puede quedar nulo, siguiendo la convención existente.
- La auditoría vive en la nueva entidad administrativa, con identificadores,
  copias del nombre, rol y fechas de creación/retirada. No se escribe en tablas previas,
  incluida `activity_event`. Reasignar tras retirar crea una nueva fila histórica.
- No hay estados lingüísticos automáticos ni bloqueos de edición. El diagnóstico
  de solo lectura distingue pendientes bloqueantes de morfología/relaciones y
  no bloqueantes de gramática, sin modificar alternativas u ocurrencias.
- Paginación de 50 conceptos, selección por página y búsqueda literal por etiqueta
  con `LIKE`, conforme a SQLite (no normaliza acentos). Los filtros se conservan tras guardar.
- Los formularios nuevos usan un token de sesión contra solicitudes cruzadas;
  se requiere `LESICO_SECRET_KEY` estable también para la prueba local.
- «Mi trabajo» reutiliza `lesico-collaborator-id` de «Trabajando como». Un script
  pequeño transmite la selección como `collaborator_id` en la URL; al cambiarla
  actualiza la consulta y reinicia la página. No se muestra el listado hasta
  sincronizarlo con la selección del navegador. JavaScript es necesario para ese
  mecanismo, igual que para la identificación actual. Sin selección válida no se
  consulta trabajo de otros colaboradores. La identidad sigue siendo declarada:
  este filtro no constituye una barrera de autorización individual.

## Esquema y rutas

Migración `migrations/025_concept_work_assignment.py`: nueva tabla
`concept_work_assignment`, sin cambios de columnas en tablas existentes.

Campos: `work_assignment_id`, `concept_id`, `analyst_id`, `analyst_name_snapshot`,
`created_at`, `created_by_collaborator_id`, `created_by_name_snapshot`,
`created_access_role`, `active`, `removed_at`, `removed_by_collaborator_id`,
`removed_by_name_snapshot`, `removed_access_role`.

Índice único parcial `(concept_id, analyst_id) WHERE active=1` e índice de filtro
por analista. Claves foráneas a `concept` y `collaborator`.

`GET /<prefijo-reviewer-o-master>/administracion/asignaciones`: listado y filtros.
`POST` en la misma ruta: `action=assign` o `action=remove`.
Sin acceso devuelve 404, igual que las rutas privadas existentes.

`GET /<prefijo-analyst>/mi-trabajo`: asignaciones activas del colaborador declarado,
exclusivo de `analyst`; `reviewer` y `master` reciben 404. Búsqueda por
ID exacto o etiqueta y páginas de 50 conceptos. «Abrir concepto» enlaza a la ruta
existente `alternatives.alternativas`, conservando el prefijo de acceso.

La migración 025 y su módulo `work_assignment_schema.py` permanecen inmutables.
La corrección de roles requiere la nueva migración 026, descrita más abajo.

La migración exige una ruta explícita, simula por defecto y crea un respaldo al
aplicar. Rechaza ejecución en Railway y en modo producción. El inicio con base
explícita valida la tabla; no ejecuta la migración automáticamente.

## Prueba local exacta (PowerShell)

Estos comandos crean una base **nueva y sintética** dentro del worktree. El creador
falla si ya existe; nunca sobrescribe datos.

```powershell
Set-Location C:\Users\jucer\Documents\app_temporal_astronomia\.worktrees\analyst-work-assignment
$python = '..\..\.venv\Scripts\python.exe'
$env:LESICO_ENV = 'development'
$env:LESICO_INSTANCE_MODE = 'pruebas'
$env:LESICO_SECRET_KEY = 'clave-solo-demo-local-asignaciones-2026'
$env:LESICO_MASTER_ROUTE = 'admin-local'
$env:LESICO_ANALYST_ROUTE = 'analista-local'
$env:LESICO_REVIEWER_ROUTE = 'revision-local'
if (-not (Test-Path -LiteralPath '.\work_assignment_demo.db')) {
    & $python scripts/demo_work_assignments.py
}
$env:LESICO_DATABASE_PATH = Join-Path $PWD 'work_assignment_demo.db'
& $python migrations/026_work_assignment_reviewer_roles.py --database $env:LESICO_DATABASE_PATH --apply
& $python -m flask --app app run --host 127.0.0.1 --port 5055 --no-reload
```

Abrir `http://127.0.0.1:5055/admin-local/administracion/asignaciones`.
Concepto A tiene Ana y Carlos; B tiene Carlos; C está sin asignar. Todos carecen
de alternativas. Seleccionar C y los dos analistas, asignar, probar filtros y retirar
un analista. Repetir una asignación no la duplica. El prefijo `analista-local`
no puede acceder a esta administración. Detener con Ctrl+C.

Recorrido de reunión: en `http://127.0.0.1:5055/admin-local/colaboradores`, registrar
«Andrés» si aún no existe en esta demo. Abrir «Asignación de trabajo», seleccionar
Concepto C y Andrés, y pulsar «Asignar seleccionados». Elegir Andrés en
«Trabajando como» y abrir `http://127.0.0.1:5055/analista-local/mi-trabajo`, que
recupera la selección: aparece C; «Abrir concepto» abre la página existente aunque
no tenga alternativas. «Mi trabajo» solo aparece bajo el acceso de analista.
Reviewer puede asignar y retirar desde
`http://127.0.0.1:5055/revision-local/administracion/asignaciones`.
Para demostrar una asignación compartida, asignar C también a Ana, cambiar el
selector entre ambas personas, retirar solo la de Andrés y comprobar que C
permanece para Ana. No guardar cambios lingüísticos durante la demo.

Para una **copia local desechable** de una base existente de la rama base:

```powershell
& $python migrations/025_concept_work_assignment.py --database C:\ruta\copia-local.db
& $python migrations/025_concept_work_assignment.py --database C:\ruta\copia-local.db --apply
& $python migrations/026_work_assignment_reviewer_roles.py --database C:\ruta\copia-local.db
& $python migrations/026_work_assignment_reviewer_roles.py --database C:\ruta\copia-local.db --apply
```

No se requiere migrar la demo: nace con el esquema actualizado. No apuntar estos
comandos a bases de producción o pruebas remotas.

## Migración 026 y política de roles

`026_work_assignment_reviewer_roles.py` reconstruye únicamente la tabla
administrativa dentro de la transacción del mecanismo existente `safe_database`.
Mantiene las claves foráneas activas, crea una tabla temporal de reemplazo, copia
los 13 campos explícitamente y compara el contenido en ambas direcciones antes
de reemplazar la tabla. Conserva IDs, snapshots, fechas, historial y el máximo
histórico de `sqlite_sequence`, incluso cuando todas las filas se eliminaron.
Recrea exactamente `one_active_concept_work_assignment` y
`idx_work_assignment_analyst`, y valida esquema y claves foráneas al terminar.

Los roles de creación y retirada admiten `reviewer` y `master`, nunca `analyst`.
Las filas activas exigen fecha y rol de retirada nulos; las retiradas exigen ambos
no nulos y un rol permitido. No se cambia ningún valor de las filas existentes.
Un esquema 025 con filas que violen estas restricciones provoca rollback.

Simula por defecto en memoria; `--apply` exige una ruta explícita y crea un
respaldo consistente antes del cambio. Repetir 026 valida el esquema y no lo
reescribe ni genera otro respaldo. Rechaza tablas referenciantes e índices o
disparadores inesperados antes de reconstruir. No se debe volver a ejecutar 025
sobre una base que ya tiene 026. El arranque con base explícita exige 026 y no la
aplica automáticamente. Las bases sintéticas nuevas nacen con el esquema 026.

La política central y los decoradores coinciden: «Mi trabajo» exige `analyst`
exacto; «Asignación de trabajo» exige `reviewer` mínimo y se muestra en REVISIÓN.
Las demás opciones de ADMINISTRACIÓN siguen exclusivas de `master`.

## Validación

Diagnóstico y enlaces: 6 pruebas específicas correctas, junto con las 20 pruebas
de asignaciones y migración (26/26). Inspección de herramientas, reglas SQL y
límites de propuestas: [WORK_ASSIGNMENT_DIAGNOSTICS.md](WORK_ASSIGNMENT_DIAGNOSTICS.md).

Corrección de permisos + 026: 13 pruebas de asignaciones y acceso y 7 de migración
026 pasan (20/20), además de 6 pruebas de selección de base. Se comprueban
operaciones HTTP de Reviewer, roles reales en auditoría, restricciones SQL,
preservación exacta del historial, contador AUTOINCREMENT e idempotencia. Un fallo
simulado al finalizar la reconstrucción comprueba rollback completo. La prueba
histórica de colaboradores sigue sin modificarse.

```powershell
& $python -m unittest discover -s tests -p test_work_assignments.py -v
& $python -m unittest discover -s tests -p test_work_assignment_migration_026.py -v
& $python -m unittest discover -s tests -p test_collaboration_activity.py -v
& $python -m unittest discover -s tests -p test_database_selection.py -v
& $python -m unittest discover -s tests -p test_production_readiness.py -v
```

Las pruebas crean únicamente bases sintéticas temporales. Cubren asignación,
duplicados, historial, retirada, filtros, paginación, autorización, token de sesión,
rollback y preservación del contenido de todas las tablas previas. La prueba de
migración comprueba simulación, respaldo, aplicación e idempotencia.

Resultado de la primera implementación: 8 pruebas nuevas, 6 de selección de base y
11 de arranque seguro pasan. Colaboradores: 7 pasan y 1 falla
(`test_rollback_removes_success_event`: `cannot start a transaction within a transaction`).
El mismo fallo se reprodujo en el worktree de la rama base, sin esta implementación.
La ampliación tiene 11 pruebas específicas, incluido el recorrido en Chromium
sin interfaz: asignar desde administración, cambiar la selección, consultar,
retirar y comprobar la separación entre colaboradores. Todas las solicitudes
del navegador se interceptan localmente contra Flask con una base sintética.
Las pruebas también abren la ruta existente del concepto con la aplicación real,
verifican búsqueda, paginación, estados vacíos y la migración 025. No se accede a
bases remotas. El fallo histórico de colaboradores se deja sin modificar.

Archivos de implementación: `work_assignment_schema.py`, `work_assignments.py`,
`routes/work_assignments.py`, `templates/asignacion_trabajo.html`, migración 025;
integración en `database.py`, `app.py` y `templates/_internal_header.html`.
También se añaden las pruebas, el creador de demo y este documento.

Archivos de la ampliación: `work_assignments.py`, `routes/work_assignments.py`,
`templates/_internal_header.html`, `templates/mi_trabajo.html`, `static/my-work.js`,
`tests/test_work_assignments.py` y este documento. No cambian rutas lingüísticas.
