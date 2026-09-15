# Fase Visual 2 — interfaz interna compartida

## Estado inicial y alcance

Rama `feature/evidence-analysis-media`, HEAD inicial `997351e` (Fase Visual 1). El estado inicial contenía exclusivamente los dos archivos históricos sin seguimiento autorizados, que se conservaron intactos.

Había cinco pantallas piloto modernizadas, pero otros 29 documentos internos conservaban tipografía del navegador, formularios sin distribución, tablas sin contenedor desplazable y estilos locales. La navegación se generaba como una cadena HTML dentro de Python; su aspecto dependía de qué hoja cargaba cada documento.

El usuario autorizó una excepción limitada en `access_control.py` para extraer presentación, manteniendo consultas, permisos, condiciones de acceso, rutas, enlaces, redirects y errores HTTP.

## Arquitectura aplicada

- `inject_internal_navigation()`, dentro de `install_access_context()`, conserva su registro `after_request`, sus retornos anticipados y la misma consulta de colaboradores activos, con el mismo orden.
- El HTML y el script de la cabecera se trasladaron a `templates/_internal_header.html`. Python pasa `collaborators`, `role`, `root`, `show_review` y `show_admin`. Las dos últimas variables usan exactamente las expresiones anteriores: `ROLE_LEVEL[role] >= ROLE_LEVEL["reviewer"]` y `role == "master"`.
- La selección de colaborador sigue en `localStorage`; el mismo script restaura la selección e incorpora `collaborator_id` a los formularios POST. No se añadió una consulta para obtener un supuesto colaborador de sesión.
- `_visual_base.html` proporciona CSS, viewport, enlace para saltar al contenido, `main` y contexto de rol. Se añadió `extra_head` para recursos particulares sin duplicar el documento.
- `_internal_role.html` comparte la presentación del rol. Los **34 templates operativos internos** heredan ahora `_visual_base.html` (cinco originales y 29 migrados).
- `visual-base.css` centraliza los componentes nuevos y los estilos antes incrustados en revisión, confirmación inmediata, conflicto manual y gestión de video.
- El banner continúa a cargo de `install_instance_presentation()` y `_instance_banner.html`; se conserva su condición de entorno y no se duplica.

### Límites conservados expresamente

Las respuestas HTTP >= 400 mantienen el retorno anticipado de la cabecera; conservan sus códigos y comportamiento. Cuando la ruta devuelve un template interno de error, ese template sí utiliza la base visual. Los errores devueltos directamente como texto no se sustituyeron por otra respuesta.

Los endpoints de catálogo continúan excluidos de `inject_internal_navigation()`. El catálogo interno carga la hoja común y muestra el contexto de rol, manteniendo su interfaz, enlaces, búsqueda, scripts y layout propios. No se añadió el selector de colaborador ni navegación operativa allí: hacerlo cambiaría qué controles/enlaces muestra una página, fuera de la excepción autorizada. Las ramas públicas del template no cargan estos elementos.

## Pantallas migradas

| Grupo | Templates |
| --- | --- |
| Inicio | `trabajo.html` |
| Registro de ocurrencias | `nueva_ocurrencia.html`, `editar_ocurrencia.html`, `gramatica_ocurrencia.html`, `resumen_registro.html` |
| Fuentes | `fuentes.html`, `editar_fuente.html`, `fuentes_retiradas.html` |
| Operaciones e historial de fuentes | `retirar_fuente.html`, `dividir_fuente.html`, `fusionar_fuentes.html`, `source_distribution_preview.html`, `source_history.html`, `source_period_conflicts.html`, `source_protection.html`, `source_operation_error.html` |
| Borradores y aportes | `borradores.html`, `aportes.html`, `revision_aportes.html` (pendientes y detalle), `confirmar_aceptacion_inmediata.html` |
| Conceptos y alternativas | `editar_concepto.html`, `editar_alternativa.html`, `gestionar_video_alternativa.html` |
| Administración | `colaboradores.html`, `actualizar_catalogo.html`, `publicaciones.html` |
| Conflictos | `conflictos.html`, `nuevo_conflicto.html`, `detalle_conflicto.html` |

Además se adaptaron los partials de creación/historial de fuentes, resolución conceptual y revisión/preview léxico. La auditoría encontró como pantallas adicionales relevantes la edición legacy de alternativa, gestión de video, protección de fuentes, errores de periodo y previsualizaciones estructurales.

## Componentes y cambios visuales

- Inicio presenta bloques de trabajo en dos columnas de escritorio y una en móvil, con los mismos enlaces y condiciones por rol. No añade estadísticas.
- Registro, gramática y resumen comparten jerarquía de paso, título, contexto y acciones. El resumen usa datos descriptivos y una barra de enlaces; corrige los textos visibles «Ver / editar ocurrencia» y «Clasificar ocurrencia». El historial gramatical es plegable.
- Fuentes conserva sus **13 campos de creación** en cuatro grupos: identificación, alcance/formato, periodo y descripción/contenido. Las etiquetas envuelven sus controles. La edición también usa una distribución en columnas adaptable.
- La tabla de fuentes conserva sus nueve columnas, limita la anchura de acciones y permite scroll horizontal. El resto de tablas legacy y previews usa el mismo contenedor enfocable.
- Borradores tiene estado vacío, acción de registro y filas separadas. Aportes mantiene columnas, detalle, formularios y plegables.
- Botones comunes distinguen acciones principales, secundarias y destructivas. Se conserva la separación entre checkbox, motivo y confirmación.
- Administración, conflictos y video usan los mismos formularios, mensajes y tipografía.
- Se añadieron nombres accesibles a controles que carecían de ellos, incluidos campos de nomenclatura y destinos de división. No se modificaron nombres internos, valores enviados ni validaciones.

## Pruebas y resultados

Se ejecutaron con bases sintéticas temporales/en memoria y Edge headless ya disponible. No se inició `app.py` contra una base real ni se ejecutaron acciones de Railway.

### Comparación con HEAD

Para distinguir regresiones de fallos previos se exportó `997351e` mediante `git archive` a un directorio temporal y se ejecutaron las mismas 195 pruebas existentes:

- **HEAD: 195 pruebas, 19 fallos reportados**, incluidos subcasos.
- Un fallo era una expectativa HTML antigua (`<button>` sin clase), ya incompatible con Clasificar de la Fase Visual 1.
- Los otros **18 fallos reportados** corresponden a respuestas 409: uno en `test_occurrence_grammar_routes` y 17 casos/subcasos de `test_submission_lexical_ui`. Esos archivos y expectativas funcionales no se modificaron.
- **Resultado final ampliado: 200 pruebas, 18 fallos reportados**, todos presentes en HEAD; sin errores ni fallos nuevos. Se corrigieron únicamente las expectativas de texto/estructura afectadas por esta presentación.
- La comprobación final independiente `tests.test_visual_internal`: **5 pruebas correctas**.

La suite ampliada incluyó:

```text
test_visual_internal
test_visual_pilots
test_post_smoke_functional
test_collaboration_activity
test_source_metadata_systematization
test_source_protection
test_source_retirement
test_source_split_merge
test_phase18c3_registration_flow
test_occurrence_grammar_routes
test_occurrence_submission_decoupling
test_submission_lexical_ui
test_submission_concept_resolution
test_conflict_routes
test_internal_catalog_routes
test_external_catalog_routes
test_catalog_publication
test_immediate_acceptance_routes
test_alternative_video
```

### Cobertura de la extracción de cabecera

Las pruebas nuevas comprueban listas exactas y orden de enlaces/grupos para Analyst, Reviewer y Master; restricciones GET/POST; prefijos de ruta; colaboradores activos y escapado de nombres; persistencia de selección y campos POST; exclusión de cabecera en errores 400/403/404/409/500 sin consultar colaboradores; catálogo interno y ausencia de cambios visuales internos en la rama pública. Las lecturas y la inspección del navegador verifican que el volcado de la base sintética no cambia.

También se compararon los scripts de los cuerpos HTML, nombres de campos y llamadas a `url_for` de todos los templates migrados contra HEAD: se conservaron. El diff de `access_control.py` se limita al import de `render_template` y la sustitución de la generación HTML por su renderizado.

## Verificación visual 1280 / 390

Se recorrieron **16 vistas a ambos anchos**: Inicio, registro, edición de ocurrencia, gramática, resumen, Fuentes, edición de fuente, Borradores, Aportes, pendientes, editar concepto, Colaboradores, Actualizar catálogo, Publicaciones, Conflictos y nuevo conflicto.

Se verificaron: CSS cargado, un único `main`, título y cabecera visibles, banner de pruebas, ausencia de desbordamiento de página, nombres accesibles de controles, plegables y persistencia del colaborador. Se revisaron capturas locales y se ajustaron las columnas de Fuentes para evitar encabezados comprimidos. Las cinco vistas piloto también mantienen su prueba de navegador existente a 1280/390.

Las capturas son opcionales mediante `VISUAL_SCREENSHOT_DIR` y se guardaron en el directorio temporal del sistema, fuera del repositorio.

## Archivos modificados

- `access_control.py`
- `static/visual-base.css`
- `templates/_source_creation_fields.html`
- `templates/_source_distribution_history.html`
- `templates/_submission_concept_resolution.html`
- `templates/_submission_lexical_preview.html`
- `templates/_submission_lexical_review.html`
- `templates/_visual_base.html`
- `templates/actualizar_catalogo.html`
- `templates/aportes.html`
- `templates/borradores.html`
- `templates/catalogo_lesico.html`
- `templates/colaboradores.html`
- `templates/confirmar_aceptacion_inmediata.html`
- `templates/conflictos.html`
- `templates/detalle_conflicto.html`
- `templates/dividir_fuente.html`
- `templates/editar_alternativa.html`
- `templates/editar_concepto.html`
- `templates/editar_fuente.html`
- `templates/editar_ocurrencia.html`
- `templates/fuentes.html`
- `templates/fuentes_retiradas.html`
- `templates/fusionar_fuentes.html`
- `templates/gestionar_video_alternativa.html`
- `templates/gramatica_ocurrencia.html`
- `templates/nueva_ocurrencia.html`
- `templates/nuevo_conflicto.html`
- `templates/publicaciones.html`
- `templates/resumen_registro.html`
- `templates/retirar_fuente.html`
- `templates/revision_aportes.html`
- `templates/source_distribution_preview.html`
- `templates/source_history.html`
- `templates/source_operation_error.html`
- `templates/source_period_conflicts.html`
- `templates/source_protection.html`
- `templates/trabajo.html`
- `tests/test_phase18c3_registration_flow.py`
- `tests/test_source_retirement.py`
- `tests/test_source_split_merge.py`

## Archivos creados

- `templates/_internal_header.html`
- `templates/_internal_role.html`
- `tests/test_visual_internal.py`
- `docs/VISUAL_PHASE2.md`

## Pendientes reales para Fase Visual 3

- Validar densidad con volúmenes grandes de aportes, nombres extensos y fuentes de distintos tipos; priorizar mejoras concretas observadas en uso.
- Revisar foco y asociación de errores de validación a campos particulares con una especificación explícita, sin interpretar mensajes de backend.
- Cualquier unificación de navegación operativa dentro del catálogo o cambio de respuestas de error requiere una autorización distinta: sus exclusiones actuales se conservaron.
- Los fallos de tests 409 anteriores quedan fuera de esta fase y documentados; no se alteró el workflow para resolverlos.

## Cierre

Se ejecutaron `git diff --check`, `git status --short`, `git --no-pager diff --stat` y `git --no-pager diff --name-only`. Sin errores de whitespace. Los archivos nuevos sin seguimiento no aparecen en `git diff --stat`.

No se ejecutaron `git add`, commit, push ni acciones de Railway. No se modificaron consultas, rutas, permisos, condiciones de acceso, modelos, migraciones ni backend de negocio.
