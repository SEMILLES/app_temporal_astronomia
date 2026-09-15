# Fase Visual 1 — sistema base y pantallas piloto

## Diagnóstico inicial

- Rama verificada: `feature/evidence-analysis-media`; HEAD inicial: `86b4dd6`.
- El estado inicial contenía exclusivamente los dos archivos sin seguimiento autorizados: `docs/PHASE18C6G_A_CROSS_CONCEPT_STOP.md` y `tests/test_nomenclature_cross_concept_audit.py`. Se conservaron intactos.
- No existía un layout interno común. Ocurrencias, clasificación y conceptos usaban principalmente estilos del navegador; Alternatives y gestión tenían CSS incrustado, con tipografía, anchos y separaciones distintos.
- Los estilos existentes de `static/catalogo/` pertenecen al catálogo, fuera del alcance de los pilotos.
- Eran reutilizables las clases de jerarquía de Alternatives, sus listas de relaciones y metadata, así como los formularios con labels y los `details` existentes.
- Tablas, secciones, formularios y mensajes necesitaban una base compartida. La navegación se inyecta desde `access_control.py`: se estiliza mediante su ID existente sin modificar ese archivo.

## Arquitectura

`templates/_visual_base.html` aporta documento HTML, viewport, hoja compartida, salto al contenido, `main` y rol visible. Solo lo heredan los cinco templates que forman las cuatro pantallas piloto.

`static/visual-base.css` centraliza colores, tipografía, ancho de 1240 px, espaciado compacto, foco visible y adaptación a anchos pequeños. No incorpora dependencias, fuentes externas ni JavaScript nuevo. El banner de pruebas conserva su contenido y estilos originales.

### Componentes comunes

- Encabezado de página, barra de acciones, navegación de secciones y contexto de rol.
- Botones primarios, secundarios, neutrales, destructivos y deshabilitados.
- Labels, controles, fieldsets, ayuda, indicadores de campos obligatorios y errores.
- Tablas con contenedor desplazable enfocable, metadata, identificadores secundarios y columnas numéricas.
- Mensajes de éxito, advertencia, error e información.
- Contexto de clasificación, estados textuales, previews y bloques de origen/destino.
- Historiales plegables y jerarquía Concepto / Alternative / Occurrence.

## Cambios por pantalla

1. **Ocurrencias:** encabezado con acción principal de registro, tabla compartida, glosa destacada, ID secundario, estados legibles y acciones separadas. Incluye mensaje de listado vacío; no añade filtros ni ordenamientos.
2. **Clasificar ocurrencia:** OCC ID explícito, glosa y fuente separadas, concepto de referencia y clasificación vigente en columnas diferenciadas. Las alternativas para una nueva decisión mantienen su sección independiente. El error del servidor queda junto a la decisión, asociado al formulario mediante `aria-describedby`. Se conservan los scripts y el funcionamiento de los campos dinámicos.
3. **Conceptos / Alternatives:** tablas uniformes, etiquetas destacadas, IDs secundarios, label de registro asociado y ordenamiento existente. Alternatives conserva la jerarquía semántica, las secciones de conceptos activos/sin alternativas, las retiradas plegadas y el orden de ocurrencias. Su CSS local se sustituye por la hoja compartida.
4. **Gestionar Alternative:** navegación de secciones, ID visible, formularios y tablas uniformes, nombres accesibles en celdas editables, origen/destino destacados y retirada visualmente diferenciada. Los previews estructurales muestran `Estado | Alternativa | Actual | Después`, conservando `= Sin cambio`, `↻ Cambia`, `↻ Cambia de grupo` y `+ Nueva`. Checkbox, motivo y confirmación siguen en bloques separados. Los historiales existentes pasan a `details` cerrados inicialmente.

## Archivos

### Creados

- `templates/_visual_base.html`
- `static/visual-base.css`
- `tests/test_visual_pilots.py`
- `docs/VISUAL_PHASE1.md`

### Modificados

- `templates/ocurrencias.html`
- `templates/clasificar_ocurrencia.html`
- `templates/conceptos.html`
- `templates/alternativas.html`
- `templates/gestionar_alternativa.html`
- `tests/test_post_smoke_functional.py`: únicamente el selector de la columna ID en el preview, ahora precedida por Estado; conserva la expectativa funcional del orden.

## Verificación

Se usaron exclusivamente bases sintéticas temporales o en memoria. No se ejecutaron migraciones sobre bases existentes ni se inició la aplicación contra una base real.

Referencia previa: 211 tests ejecutados; 210 correctos y un fallo legacy en `test_review_lists_alternative_and_reject_does_not_modify_canonical` (`409 != 302`). No se modificó ese test ni su expectativa.

Resultado final: **212 tests, 211 correctos y el mismo fallo legacy previo**, sin regresiones nuevas. La ejecución focalizada incluyó `test_visual_pilots`, `test_alternative_routes`, `test_alternative_structural`, `test_alternative_admin`, `test_alternative_workflow`, `test_alternative_video`, `test_occurrence_submission_decoupling`, `test_occurrence_legacy_provenance`, `test_occurrence_grammar_routes`, `test_occurrence_grammar_history`, `test_post_smoke_functional` y `test_immediate_acceptance_routes`.

`git diff --check` terminó sin errores. Se revisaron `git status --short` y `git diff --stat`; los cambios se limitan a los archivos de presentación, tests de templates y este reporte. `git diff --stat` no incluye los archivos nuevos sin seguimiento.

La prueba de navegador recorre cinco vistas a 1280 y 390 px y verifica carga real del CSS, ausencia de desbordamiento general, nombres accesibles de campos, navegación, banner, rol, secciones plegadas, controles dinámicos y ausencia de escrituras durante la inspección. Usa Edge headless mediante Playwright ya disponible. Las capturas opcionales se guardan fuera del repositorio con `VISUAL_SCREENSHOT_DIR`.

También se compararon contra HEAD los scripts, nombres de campos y llamadas a `url_for` de los cinco templates: se conservan sin cambios.

## Pendientes recomendados — Fase Visual 2

- Extender el layout gradualmente a registro/edición, revisión y confirmación inmediata, revisando cada flujo.
- Extraer la navegación inyectada a un template solo cuando se autorice expresamente intervenir en esa arquitectura.
- Revisar densidad con listados largos, textos extensos y grupos con muchas alternativas; evaluar mejoras móviles después de validar el uso de escritorio.
- Ampliar la asociación de errores a campos concretos cuando el contrato de validación permita identificarlos sin interpretar mensajes de texto.
- Mantener el catálogo y sus estilos independientes hasta una fase explícita.

Backend, permisos, workflows, nomenclatura y rutas permanecen sin modificaciones. No se ejecutaron `git add`, commit, push ni acciones de Railway.
