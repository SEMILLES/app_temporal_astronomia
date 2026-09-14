# Fase 18C6G: traslado de componente léxico

Implementación local sin cambios de esquema. Los dos archivos de auditoría
preexistentes se conservan sin modificación.

## Arquitectura

`alternative_structural.lexical_component` construye un grafo no dirigido con
todas las relaciones `is_current=1` y recorre su clausura mediante DFS. Devuelve
IDs ordenados, Alternatives y relaciones internas. No utiliza working labels.
Bloquea endpoints inexistentes o retirados, relaciones cross-concept, pares
inconsistentes y parámetros desconocidos. No repara datos.

`component_move_preview` utiliza la infraestructura estructural de clonación
SQLite en memoria y fingerprint de ambos conceptos. `_component_move_plan` y
`_execute_component_plan` son comunes a preview y aplicación: trasladan todos
los miembros y calculan/aplican la nomenclatura de origen y destino mediante
el calculador y el validador canónico existentes. El preview no escribe en la
base original. La capacidad superior a 26 conserva `VARIANT_CAPACITY_EXCEEDED`.

`apply_component_move` comprueba Reviewer/Master, motivo y fingerprint dentro
de `BEGIN IMMEDIATE`; repite simulación y derivación del componente. Aplica
ambos conceptos, verifica conflictos y registra `alternative_component_moved`.
El evento contiene selección, miembros, origen, destino, relaciones conservadas,
motivo e IDs de renumeración (null cuando no hay cambios). Se registra bajo la
Alternative seleccionada y se muestra en su historial estructural.

Solo se modifican concept_id y los working labels recalculados; no se actualizan
assignments, occurrences, referencias conceptuales históricas, morfología,
componentes morfológicos, media, videos ni relaciones. Cualquier excepción
revierte toda la transacción, incluidos eventos y cambios parciales.

## Interfaz y alcance

La gestión muestra movimiento individual para una Alternative aislada y
traslado de grupo para un componente relacionado. Lista todos los miembros;
el preview presenta origen, destino, IDs, relaciones preservadas, número de
occurrences/assignments vigentes y tablas de nomenclatura para ambos conceptos.
La confirmación exige motivo y token firmado ligado a operación/destino.
Analyst queda excluido por el control de rutas existente y por el nuevo servicio.

Se permite dejar vacío el origen: el calculador y el validador aceptan el mapa
vacío y no crean una renumeración si no existen cambios de etiquetas.

No hay selección parcial, división, reparación legacy, fusión con grupos del
destino, deduplicación ni reactivación. El grupo conserva su conectividad y se
numera independientemente de los grupos existentes. El algoritmo carga las
relaciones vigentes completas; no se añadió infraestructura de optimización.

## Validación

Pruebas en SQLite en memoria y archivos temporales sintéticos. Cobertura de
pares, transitividad desde B en A-B-C con D aislada, integridad, capacidad,
stale/replay, permisos, identidad, evidencias y media, preview igual a apply,
origen vacío, eventos y rollback tras una actualización parcial y al insertar
el evento final. Tests de Flask verifican renderizado y confirmación completa.

Suite de regresión: los ocho módulos solicitados en la especificación.
No se accedió a bases reales ni a Railway; no se ejecutó git add, commit o push.
