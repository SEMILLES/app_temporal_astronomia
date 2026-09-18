# Asignación de trabajo de conceptos

Rama `feature/analyst-work-assignment`, basada en `feature/occurrence-form-review`
(`f82903b`). Worktree: `.worktrees/analyst-work-assignment`.

## Decisiones

- Se reutiliza `collaborator`. No hay usuarios individuales autenticados ni un rol
  por colaborador: los permisos existentes dependen del prefijo privado de la ruta.
  Todos los colaboradores activos pueden recibir trabajo; solo `master` lo administra.
- El operador se toma de «Trabajando como», que es una identificación declarada,
  no una identidad autenticada. Puede quedar nulo, siguiendo la convención existente.
- La auditoría vive en la nueva entidad administrativa, con identificadores,
  copias del nombre, rol y fechas de creación/retirada. No se escribe en tablas previas,
  incluida `activity_event`. Reasignar tras retirar crea una nueva fila histórica.
- No hay estados lingüísticos, bloqueos ni efectos sobre alternativas u ocurrencias.
- Paginación de 50 conceptos, selección por página y búsqueda literal por etiqueta
  con `LIKE`, conforme a SQLite (no normaliza acentos). Los filtros se conservan tras guardar.
- Los formularios nuevos usan un token de sesión contra solicitudes cruzadas;
  se requiere `LESICO_SECRET_KEY` estable también para la prueba local.

## Esquema y rutas

Migración `migrations/025_concept_work_assignment.py`: nueva tabla
`concept_work_assignment`, sin cambios de columnas en tablas existentes.

Campos: `work_assignment_id`, `concept_id`, `analyst_id`, `analyst_name_snapshot`,
`created_at`, `created_by_collaborator_id`, `created_by_name_snapshot`,
`created_access_role`, `active`, `removed_at`, `removed_by_collaborator_id`,
`removed_by_name_snapshot`, `removed_access_role`.

Índice único parcial `(concept_id, analyst_id) WHERE active=1` e índice de filtro
por analista. Claves foráneas a `concept` y `collaborator`.

`GET /<prefijo-master>/administracion/asignaciones`: listado y filtros.
`POST` en la misma ruta: `action=assign` o `action=remove`.
Sin acceso devuelve 404, igual que las rutas privadas existentes.

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
& $python scripts/demo_work_assignments.py
$env:LESICO_DATABASE_PATH = Join-Path $PWD 'work_assignment_demo.db'
& $python -m flask --app app run --host 127.0.0.1 --port 5055 --no-reload
```

Abrir `http://127.0.0.1:5055/admin-local/administracion/asignaciones`.
Concepto A tiene Ana y Carlos; B tiene Carlos; C está sin asignar. Todos carecen
de alternativas. Seleccionar C y los dos analistas, asignar, probar filtros y retirar
un analista. Repetir una asignación no la duplica. El prefijo `analista-local`
no puede acceder a esta administración. Detener con Ctrl+C.

Para una **copia local desechable** de una base existente de la rama base:

```powershell
& $python migrations/025_concept_work_assignment.py --database C:\ruta\copia-local.db
& $python migrations/025_concept_work_assignment.py --database C:\ruta\copia-local.db --apply
```

No se requiere migrar la demo: nace con el esquema actualizado. No apuntar estos
comandos a bases de producción o pruebas remotas.

## Validación

```powershell
& $python -m unittest discover -s tests -p test_work_assignments.py -v
& $python -m unittest discover -s tests -p test_collaboration_activity.py -v
& $python -m unittest discover -s tests -p test_database_selection.py -v
& $python -m unittest discover -s tests -p test_production_readiness.py -v
```

Las pruebas crean únicamente bases sintéticas temporales. Cubren asignación,
duplicados, historial, retirada, filtros, paginación, autorización, token de sesión,
rollback y preservación del contenido de todas las tablas previas. La prueba de
migración comprueba simulación, respaldo, aplicación e idempotencia.

Resultado de la validación: 8 pruebas nuevas, 6 de selección de base y 11 de
arranque seguro pasan. Colaboradores: 7 pasan y 1 falla
(`test_rollback_removes_success_event`: `cannot start a transaction within a transaction`).
El mismo fallo se reprodujo en el worktree de la rama base, sin esta implementación.
La demo y el registro de la ruta en la aplicación real se verifican también con
una base temporal nueva y el cliente HTTP de Flask. No se realizó inspección visual
en navegador ni se accedió a bases remotas.

Archivos de implementación: `work_assignment_schema.py`, `work_assignments.py`,
`routes/work_assignments.py`, `templates/asignacion_trabajo.html`, migración 025;
integración en `database.py`, `app.py` y `templates/_internal_header.html`.
También se añaden las pruebas, el creador de demo y este documento.
