# Inspección previa y alcance del diagnóstico

## Herramientas existentes

- Gramática: `GET/POST /ocurrencias/<id>/gramatica` (`mostrar_gramatica`,
  `guardar_gramatica`). El POST normal llama `create_grammar_submission` y crea
  un Aporte pendiente, sin escribir gramática canónica. Reviewer/Master disponen
  de `gramatica/aceptacion-inmediata/preview` y `/confirmar`.
- Análisis léxico: `GET/POST /ocurrencias/<id>/clasificar`. El POST llama
  `create_alternative_submission`: EXISTING, NEW o UNSURE. El formulario incluye
  morfología y relaciones para NEW, y metadatos conceptuales. Genera un Aporte
  ALTERNATIVE pendiente. Reviewer/Master disponen de aceptación inmediata.
- Gestión canónica: `GET/POST /alternativas/<id>/gestionar`, exclusiva de
  Reviewer/Master. Secciones `#morfologia` y `#relaciones`; acciones `morphology`,
  previsualización de relaciones y `confirm_relation`, con sus controles actuales.
  Las antiguas rutas `/alternativas/<id>/editar` y `/actualizar` devuelven 404;
  no deben usarse como enlaces.
- Concepto: `/conceptos/<id>/alternativas` muestra sus alternativas y evidencia;
  `/conceptos/<id>/clasificaciones` permite consultar clasificación e historia.
  `/conceptos/<id>/editar` y `/actualizar` exigen Reviewer mínimo para metadatos
  canónicos. La clasificación de una evidencia se propone desde `/clasificar`.
- Revisión: `/aportes/pendientes`, `/aportes/<id>/concepto` y
  `/aportes/<id>/decidir`, con resolución conceptual y decisión léxica existentes.
  Reviewer/Master revisan; Analyst propone. La administración de catálogos
  controlados sigue exclusiva de Master. No se cambia ninguno de esos permisos.

## Límites comprobados

El formulario léxico no ofrece una propuesta autónoma para corregir morfología o
relaciones de una alternativa canónica existente: el payload las recoge para NEW,
y USE_EXISTING no actualiza las relaciones del destino. Para Analyst los enlaces
orientan a analizar evidencia mediante el flujo existente, no prometen corregir
directamente la alternativa. Sin evidencia con referencia conceptual compatible,
se enlaza al concepto para consultar y se indica que la resolución corresponde al
Revisor. No se crean ocurrencias, referencias ni propuestas automáticamente.

`submission_classification_proposal` y `submission_collection_proposal` ya existen.
`concept_classification.store_proposal` las guarda desde
`create_alternative_submission`; los triggers exigen una Submission ALTERNATIVE
pendiente. `submission_concept_resolution` aplica los metadatos mediante
`apply_metadata` durante la resolución del Revisor, con historial y control de
concurrencia. No son propuestas autónomas de Concept: necesitan una ocurrencia y
una decisión léxica. Por ello no se implementa ahora la propuesta independiente
de Campo semántico/Colección desde un Concept asignado. Falta definir cómo revisar
metadatos sin forzar una decisión léxica o inventar evidencia, especialmente para
conceptos sin alternativas. El formulario existente sigue disponible donde hay
evidencia compatible.

## Diagnóstico de solo lectura

Solo cuentan alternativas activas (`retired_at IS NULL`). Falta de morfología
vigente y sufijo b–z sin relación vigente son bloqueantes; sufijo a admite cero
relaciones. La gramática ausente de ocurrencias con asignación vigente al concepto
es no bloqueante. No se evalúan número de componentes materializados, ocurrencias
sin asignación, trazabilidad, localización ni video. Un concepto sin alternativas
sigue siendo trabajo válido y asignable.

El servicio consulta en bloque los conceptos de la página: una consulta de
alternativas con EXISTS para morfología/relaciones y evidencia compatible, y otra
de gramática faltante mediante asignaciones vigentes. No hace consultas por fila.
Las dos vistas comparten el mismo diagnóstico y un parcial de enlaces, sin
formularios nuevos ni una cola de revisión adicional.

## Validación local

`test_work_diagnostics.py`: 6 pruebas correctas. Comprueban sufijos a/b/c/d/z,
relaciones históricas, morfología vigente y compuesta sin componentes materializados,
gramática por asignación vigente, conceptos vacíos y un número constante de dos
consultas. Los GET de diagnóstico y sus enlaces preservan el volcado completo de
la base sintética. Analyst sigue obteniendo 404 en herramientas canónicas; sus
POST existentes de gramática y clasificación crean Aportes pendientes sin alterar
gramática, morfología, relaciones ni asignaciones lingüísticas canónicas.
Las 20 pruebas existentes de asignaciones y migración también pasan.

No cambian las migraciones 025/026, rutas lingüísticas ni formularios de análisis.
La propuesta autónoma de Campo semántico/Colección queda pendiente de la decisión
de dominio descrita arriba; no se creó infraestructura paralela.
