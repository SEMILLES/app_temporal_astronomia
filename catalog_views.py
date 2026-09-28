"""Collection views over one canonical catalog or frozen publication."""
from dataclasses import dataclass


@dataclass(frozen=True)
class CatalogView:
    code: str
    collection_code: str | None
    title: str
    subtitle: str
    filter_dimension: str
    brand_line: str
    footer_brand: str
    theme: str
    navigation_label: str
    filter_label: str
    filter_placeholder: str
    filter_param: str


CATALOG_VIEWS = {
    'analizada': CatalogView(
        'analizada', None, 'Colección Analizada · LeSiCo',
        'Conceptos, alternativas y relaciones de variación documentados en LSC.',
        'semantic_fields', 'SEMILLES', 'LeSiCo · SEMILLES', 'analizada',
        'Colección Analizada', 'Campo semántico', 'Buscar campos semánticos', 'campo'),
    'academica': CatalogView(
        'academica', 'academic-vocabulary', 'Vocabulario académico en LSC',
        'Conceptos, alternativas y relaciones de variación documentados para contextos académicos universitarios.',
        'knowledge_areas', 'SEMILLES · Universidad Nacional de Colombia',
        'LeSiCo · SEMILLES · Universidad Nacional de Colombia', 'academica',
        'Vocabulario Académico', 'Área de conocimiento', 'Buscar áreas de conocimiento', 'area'),
}


def scope_catalog(projection, view):
    """Select concepts without mutating or rewriting their frozen content."""
    return {**projection, 'concepts': [
        concept for concept in projection.get('concepts', [])
        if view.collection_code is None or any(
            membership.get('code') == view.collection_code
            for membership in concept.get('collections', []))
    ]}
