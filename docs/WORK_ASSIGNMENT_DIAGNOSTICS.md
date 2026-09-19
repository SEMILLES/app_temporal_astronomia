# Diagnóstico de trabajo por concepto

Asignación de trabajo y Mi trabajo comparten cuatro pendientes calculados al leer
la página, sin estados persistidos ni escrituras lingüísticas:

- Morfología: alternativa activa sin morfología vigente; bloqueante. No exige
  materializar todos los componentes de una morfología compuesta.
- Relaciones fonológicas: alternativa activa con sufijo b-z sin relación vigente;
  bloqueante. Basta participar en cualquiera de los extremos. No se exige una
  fila inversa; se admiten varias relaciones. El sufijo a no exige relación.
- Gramática: ocurrencia con asignación vigente a una alternativa activa del
  concepto, sin gramática vigente; no bloqueante.
- Asignación a alternativa: ocurrencia con referencia conceptual vigente directa
  al concepto y sin asignación vigente. Es trabajo pendiente, no un error de
  integridad. Una referencia histórica o ausente no basta para incluirla aquí.

Un concepto sin alternativas sigue siendo trabajo válido y asignable. Las filas
históricas no satisfacen pendientes actuales. Se realizan tres consultas por
lote de conceptos: alternativas con EXISTS de morfología/relaciones, gramática
faltante y asignación faltante. No hay consultas por fila.

## Herramientas y permisos

Reviewer y Master reciben enlaces a `/alternativas/<id>/gestionar#morfologia`
y `#relaciones`. Analyst recibe la consulta de la alternativa concreta en
`/catalogo-interno/alternativas/<id>`, incluso sin evidencia asociada. Si falta
una etiqueta válida, el catálogo no expone esa alternativa y se indica que
requiere revisión. Nunca se sustituye ese enlace por una ocurrencia arbitraria.

Gramática usa `/ocurrencias/<id>/gramatica`; asignación usa
`/ocurrencias/<id>/clasificar`. Sus POST normales generan Aportes pendientes.
Reviewer/Master conservan sus herramientas de aceptación inmediata y gestión
canónica. Analyst no obtiene permisos canónicos nuevos.

## Límite de los Aportes de alternativa

La propuesta de correcciones a una alternativa existente no está disponible
para Analyst; se informa de forma impersonal en la interfaz. `submission` exige
una ocurrencia; el almacenamiento de morfología propuesta admite NEW; la decisión
USE_EXISTING no permite aceptar morfología ni relaciones. Resolverlo requiere
otra tarea sobre el modelo y sus restricciones. No se crea infraestructura
paralela ni se modifica el esquema en esta tarea.

Asignar a una alternativa existente conserva sus pendientes propios. Al aprobar
una alternativa nueva se exige morfología aprobada, incluso para propuestas
heredadas o con nota de revisión. Si la etiqueta final tiene sufijo b-z se exige
una relación vigente. El fallo revierte toda la operación. Se conserva el flujo
actual de propuestas y revisión, sin añadir formularios.

Quedan fuera el flujo por fuentes, ocurrencias sin referencia conceptual para
asignación, trazabilidad, localización, campos semánticos y colecciones.
Las migraciones 025 y 026 permanecen intactas.

## Validación

Las pruebas focalizadas cubren referencias/asignaciones vigentes e históricas,
relaciones en ambos extremos y múltiples relaciones sin duplicado inverso,
morfología parcial, gramática, tres consultas constantes, permisos y enlaces.
Los GET preservan el volcado de la base sintética; los POST de Analyst crean
Aportes sin alterar las tablas canónicas. La regresión de revisión léxica cubre
creación con morfología, rechazo sin escrituras y atomicidad de la revisión.

Resultado de esta revisión: las 26 pruebas focalizadas y las 62 de
`test_alternative_workflow.py` pasan. La selección adicional de interfaz léxica,
gramática y atomicidad conserva 25 fallos (incluidos subtests), reproducidos con
el código y pruebas originales del commit base 5ce0ec3. Corresponden a respuestas
409 en revisión y expectativas sobre alternativas retiradas; no se corrigen en
esta tarea. Los fixtures de creación nuevos incluyen ahora morfología aprobada.
