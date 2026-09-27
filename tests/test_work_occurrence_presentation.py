import unittest
from source_details import occurrence_presentation


class WorkOccurrencePresentationTests(unittest.TestCase):
    def present(self, **values):
        return occurrence_presentation(dict(source_name='Fuente',legacy_source_code='25ID',**values))

    def test_source_id_and_absence(self):
        self.assertEqual(self.present()['source_display'],'(25ID) Fuente')
        self.assertEqual(occurrence_presentation({'source_name':'Fuente'})['source_display'],'Fuente')

    def test_printed_and_no_technical_markers(self):
        item=self.present(source_type='MATERIAL_IMPRESO',source_detail_1_status='VALUE',source_detail_1='Sección',
            source_detail_2_status='VALUE',source_detail_2='15')
        self.assertEqual(item['locator_display'],'Submaterial / sección: Sección · Página: 15')
        item=self.present(source_type='OTRO',source_detail_1_status='UNKNOWN',source_detail_1='UNKNOWN',source_detail_2_status='NA',source_detail_2='NA',source_locator='NA')
        self.assertEqual(item['locator_display'],'')

    def test_time_applicability_and_override(self):
        for source_type, override, expected in [('VIDEO_POR_SENA',1,True),('VARIOS_VIDEOS_VARIAS_SENAS',0,False),
                                               ('UN_VIDEO_VARIAS_SENAS',None,True)]:
            with self.subTest(source_type=source_type):
                item=self.present(source_type=source_type,source_detail_2_status='VALUE',source_detail_2='2:15',
                                  source_detail_2_applicability_override=override)
                self.assertEqual('2:15' in item['locator_display'],expected)
        self.assertEqual(self.present(source_type='VIDEO_POR_SENA',source_detail_2_status='UNKNOWN')['locator_display'],'')
        mesa=self.present(source_type='MESA_DE_TRABAJO',
                          source_detail_1_status='VALUE',source_detail_1='2026-09-27',
                          source_detail_2_status='VALUE',source_detail_2='Ana, Carlos')
        self.assertIn('Fecha: 2026-09-27',mesa['locator_display'])
        self.assertIn('Participantes: Ana, Carlos',mesa['locator_display'])
        self.assertEqual(self.present(source_type='MESA_DE_TRABAJO',source_locator='No mostrar')['locator_display'],'')

    def test_other_locator_and_existing_video_semantics(self):
        self.assertIsNone(self.present(hyperlink='javascript:alert(1)')['evidence_url'])
        self.assertEqual(self.present(hyperlink='https://example.org/video')['evidence_url'],'https://example.org/video')
        self.assertEqual(self.present(source_type='OTRO',source_locator='Ficha 8')['locator_display'],'Ficha 8')
        # Existing VALUE is an explicit legacy indication that time applies.
        self.assertIn('Tiempo: 1:20',self.present(source_type='VIDEO_POR_SENA',source_detail_2_status='VALUE',source_detail_2='1:20')['locator_display'])
