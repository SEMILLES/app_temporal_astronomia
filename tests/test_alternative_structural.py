import sqlite3
import unittest
import json
from unittest.mock import patch

import database
from alternative_preconditions import relevant_state
from edit_concurrency import fingerprint
from alternative_structural import (
    StructuralAlternativeError, retire_preview, apply_retire, merge_preview,
    apply_merge, split_preview, apply_split, move_preview, apply_move,
    component_move_preview, apply_component_move, lexical_component,
)


class StructuralAlternativeTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(":memory:");self.db.row_factory=sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON");database.crear_esquema(self.db)
        self.db.execute("INSERT INTO source(source_name,start_year,end_year,end_year_status) VALUES('S',1900,1900,'known')")
        self.db.executemany("INSERT INTO concept(preferred_label) VALUES(?)",[("ORIGIN",),("DESTINATION",)])
        self.db.executemany("INSERT INTO alternative(concept_id,working_label) VALUES(1,?)",[("1a",),("2a",),("3a",)])
        self.db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(2,'1a')")
        for index,alt in enumerate((1,1,2),1):
            self.db.execute("INSERT INTO occurrence(source_id,original_gloss,occurrence_year) VALUES(1,?,?)",(f"O{index}",1900+index))
            self.db.execute("INSERT INTO occurrence_concept_reference(occurrence_id,concept_id) VALUES(?,1)",(index,))
            self.db.execute("INSERT INTO assignment(occurrence_id,alternative_id) VALUES(?,?)",(index,alt))
        self.db.execute("INSERT INTO alternative_morphology(alternative_id,component_count_not_applicable,free_permutation) VALUES(1,1,'N/A')")
        self.db.execute("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter) VALUES(1,2,'CM_1')")
        self.db.execute("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter) VALUES(1,3,'MOV_M1')")
        self.db.commit();self.actor={"access_role":"reviewer","collaborator_id":None}

    def tearDown(self):self.db.close()

    def component_apply(self, selected=2, **kwargs):
        preview = component_move_preview(self.db, selected, 2)
        return apply_component_move(self.db, selected, 2, reason='Traslado documentado',
                                    actor=self.actor, expected_fingerprint=preview['fingerprint'], **kwargs)

    def test_component_transitive_middle_preserves_all_evidence_and_labels(self):
        self.db.execute('UPDATE alternative_relation SET alternative_low_id=2 WHERE alternative_high_id=3')
        self.db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,'4a')")
        self.db.execute("INSERT INTO media_asset(storage_key,mime_type) VALUES('synthetic-video','video/mp4')")
        self.db.execute("INSERT INTO alternative_media(alternative_id,media_asset_id,role,created_access_role) VALUES(2,1,'catalog_video','reviewer')")
        self.db.execute('INSERT INTO occurrence_media(occurrence_id,media_asset_id) VALUES(1,1)')
        self.db.execute("INSERT INTO alternative_component(alternative_morphology_id,position,component_label) VALUES(1,1,'test')")
        self.db.commit()
        tables = ('assignment', 'occurrence', 'occurrence_concept_reference', 'alternative_morphology',
                  'alternative_component', 'alternative_relation', 'media_asset', 'alternative_media', 'occurrence_media')
        before = {t: [tuple(r) for r in self.db.execute('SELECT * FROM '+t)] for t in tables}
        dump = '\n'.join(self.db.iterdump())
        preview = component_move_preview(self.db, 2, 2)
        self.assertEqual(dump, '\n'.join(self.db.iterdump()))
        self.assertEqual(preview['alternative_ids'], [1, 2, 3])
        events = self.component_apply()
        self.assertTrue(all(events))
        self.assertEqual(dict(self.db.execute('SELECT alternative_id,concept_id FROM alternative')), {1:2,2:2,3:2,4:2,5:1})
        for t in tables:
            self.assertEqual(before[t], [tuple(r) for r in self.db.execute('SELECT * FROM '+t)], t)
        for key in ('origin_labels', 'destination_labels'):
            for r in preview[key]:
                self.assertEqual(self.db.execute('SELECT working_label FROM alternative WHERE alternative_id=?', (r['alternative_id'],)).fetchone()[0], r['proposed_label'])
        event = self.db.execute("SELECT * FROM activity_event WHERE event_type='alternative_component_moved'").fetchone()
        data = json.loads(event['comment'])
        self.assertEqual(data['selected_alternative_id'], 2)
        self.assertEqual(data['alternative_ids'], [1,2,3])
        self.assertEqual(data['relation_ids'], [1,2])
        self.assertEqual((data['source_concept_id'],data['destination_concept_id']), (1,2))
        self.assertEqual((data['origin_renumber_event_id'],data['destination_renumber_event_id']), events)
        self.assertEqual(data['reason'], 'Traslado documentado')

    def test_component_pair_ignores_history(self):
        self.db.execute('UPDATE alternative_relation SET is_current=0 WHERE alternative_high_id=3')
        self.db.commit()
        self.assertEqual(lexical_component(self.db,2)['alternative_ids'], [1,2])
        self.component_apply()
        self.assertEqual(self.db.execute('SELECT concept_id FROM alternative WHERE alternative_id=3').fetchone()[0],1)

    def test_component_allows_empty_origin_and_master(self):
        self.actor['access_role'] = 'master'
        self.component_apply()
        self.assertEqual(self.db.execute('SELECT count(*) FROM alternative WHERE concept_id=1').fetchone()[0],0)

    def test_component_invalid_destination_and_analyst(self):
        for dest in (1,999):
            with self.assertRaises(StructuralAlternativeError): component_move_preview(self.db,2,dest)
        self.actor['access_role']='analyst'
        before='\n'.join(self.db.iterdump())
        with self.assertRaises(StructuralAlternativeError): self.component_apply()
        self.assertEqual(before,'\n'.join(self.db.iterdump()))

    def test_component_cross_concept_and_retired_endpoint_block(self):
        for sql in ('UPDATE alternative SET concept_id=2 WHERE alternative_id=3',
                    "UPDATE alternative SET retired_at=CURRENT_TIMESTAMP WHERE alternative_id=3"):
            self.db.execute(sql)
            with self.assertRaisesRegex(StructuralAlternativeError, 'inconsistente'):
                component_move_preview(self.db,2,2)
            self.db.rollback()

    def test_component_missing_endpoint_blocks(self):
        self.db.execute('PRAGMA foreign_keys=OFF')
        self.db.execute('UPDATE alternative_relation SET alternative_high_id=999 WHERE alternative_high_id=3')
        self.db.commit()
        with self.assertRaisesRegex(StructuralAlternativeError,'endpoint'):
            component_move_preview(self.db,2,2)

    def test_component_stale_membership_blocks_without_events(self):
        from edit_concurrency import StaleEdit
        preview=component_move_preview(self.db,2,2)
        self.db.execute('UPDATE alternative_relation SET is_current=0 WHERE alternative_high_id=3')
        self.db.commit()
        before='\n'.join(self.db.iterdump())
        with self.assertRaises(StaleEdit):
            apply_component_move(self.db,2,2,reason='Cambio',actor=self.actor,expected_fingerprint=preview['fingerprint'])
        self.assertEqual(before,'\n'.join(self.db.iterdump()))

    def test_component_capacity_blocks(self):
        for i in range(24):
            aid=self.db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,'9a')").lastrowid
            self.db.execute("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter) VALUES(1,?,'CM_1')",(aid,))
        self.db.commit()
        before='\n'.join(self.db.iterdump())
        with self.assertRaisesRegex(StructuralAlternativeError,'VARIANT_CAPACITY_EXCEEDED'):
            component_move_preview(self.db,2,2)
        self.assertEqual(before,'\n'.join(self.db.iterdump()))

    def test_component_rollback_after_partial_update_and_after_events(self):
        preview=component_move_preview(self.db,2,2)
        for trigger in (
            "CREATE TRIGGER fail BEFORE UPDATE OF concept_id ON alternative WHEN NEW.alternative_id=2 BEGIN SELECT RAISE(ABORT,'partial'); END",
            "CREATE TRIGGER fail BEFORE INSERT ON activity_event WHEN NEW.event_type='alternative_component_moved' BEGIN SELECT RAISE(ABORT,'event'); END"):
            self.db.execute(trigger);self.db.commit()
            before='\n'.join(self.db.iterdump())
            # Bypass only the cloned simulation so the injected failure occurs in the real transaction.
            with patch('alternative_structural.component_move_preview', return_value=preview):
                with self.assertRaises(sqlite3.IntegrityError):
                    apply_component_move(self.db,2,2,reason='Fallo',actor=self.actor,expected_fingerprint=preview['fingerprint'])
            self.assertEqual(before,'\n'.join(self.db.iterdump()))
            self.db.execute('DROP TRIGGER fail');self.db.commit()

    def test_retire_requires_complete_resolution_and_preserves_history(self):
        with self.assertRaises(StructuralAlternativeError):retire_preview(self.db,1,{1:2})
        preview=retire_preview(self.db,1,{1:2,2:"unassigned"})
        self.assertEqual(len(preview["occurrences"]),2);self.assertIsNone(self.db.execute("SELECT retired_at FROM alternative WHERE alternative_id=1").fetchone()[0])
        apply_retire(self.db,1,{1:2,2:"unassigned"},expected_fingerprint=retire_preview(self.db,1,{1:2,2:"unassigned"})["fingerprint"],reason="retiro documentado",actor=self.actor)
        self.assertIsNotNone(self.db.execute("SELECT retired_at FROM alternative WHERE alternative_id=1").fetchone()[0])
        self.assertEqual(self.db.execute("SELECT count(*) FROM assignment WHERE occurrence_id=1").fetchone()[0],2)
        self.assertEqual(self.db.execute("SELECT count(*) FROM assignment WHERE occurrence_id=2 AND is_current=1").fetchone()[0],0)
        self.assertEqual(self.db.execute("SELECT count(*) FROM alternative_relation WHERE is_current=1 AND (alternative_low_id=1 OR alternative_high_id=1)").fetchone()[0],0)
        self.assertEqual(self.db.execute("SELECT is_current FROM alternative_morphology WHERE alternative_id=1").fetchone()[0],0)

    def test_merge_union_reassigns_deduplicates_and_keeps_target_morphology(self):
        self.db.execute("INSERT INTO alternative_morphology(alternative_id,component_count,free_permutation) VALUES(2,2,'NO')")
        self.db.execute("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter) VALUES(2,3,'MOV_M1')");self.db.commit()
        preview=merge_preview(self.db,1,2,"union");self.assertEqual(preview["relations_created"],[])
        apply_merge(self.db,1,2,"union",expected_fingerprint=merge_preview(self.db,1,2,"union")["fingerprint"],reason="misma alternativa",actor=self.actor)
        self.assertEqual(self.db.execute("SELECT count(*) FROM assignment WHERE alternative_id=2 AND is_current=1").fetchone()[0],3)
        self.assertEqual(self.db.execute("SELECT count(*) FROM alternative_morphology WHERE alternative_id=2 AND is_current=1").fetchone()[0],1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM alternative_relation WHERE alternative_low_id=2 AND alternative_high_id=3 AND phonological_parameter='MOV_M1' AND is_current=1").fetchone()[0],1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM activity_event WHERE event_type='alternative_merged'").fetchone()[0],1)

    def test_split_creates_new_ids_without_morphology_or_relations(self):
        ids,event=apply_split(self.db,1,{1:1,2:2},2,expected_fingerprint=split_preview(self.db,1,{1:1,2:2},2)["fingerprint"],reason="dos formas",actor=self.actor)
        self.assertEqual(len(ids),2);self.assertTrue(all(item>4 for item in ids))
        self.assertEqual(self.db.execute("SELECT count(*) FROM alternative_morphology WHERE alternative_id IN (?,?)",ids).fetchone()[0],0)
        self.assertEqual(self.db.execute("SELECT count(*) FROM alternative_relation WHERE is_current=1 AND (alternative_low_id IN (?,?) OR alternative_high_id IN (?,?))",(*ids,*ids)).fetchone()[0],0)
        labels=[r[0] for r in self.db.execute("SELECT working_label FROM alternative WHERE alternative_id IN (?,?)",ids)]
        self.assertTrue(all(label[-1:].isalpha() and label[:-1].isdigit() for label in labels))
        with self.assertRaises(StructuralAlternativeError):split_preview(self.db,2,{3:1},1)

    def test_move_preserves_identity_assignment_context_morphology(self):
        self.db.execute("UPDATE alternative_relation SET is_current=0 WHERE alternative_low_id=1 OR alternative_high_id=1")
        self.db.commit()
        assignment=self.db.execute("SELECT assignment_id FROM assignment WHERE occurrence_id=1 AND is_current=1").fetchone()[0]
        context=tuple(self.db.execute("SELECT concept_id,concept_proposal_id FROM occurrence_concept_reference WHERE occurrence_id=1 AND is_current=1").fetchone())
        apply_move(self.db,1,2,expected_fingerprint=move_preview(self.db,1,2)["fingerprint"],reason="reclasificaciÃ³n",actor=self.actor)
        self.assertEqual(self.db.execute("SELECT concept_id FROM alternative WHERE alternative_id=1").fetchone()[0],2)
        self.assertEqual(self.db.execute("SELECT assignment_id FROM assignment WHERE occurrence_id=1 AND is_current=1").fetchone()[0],assignment)
        self.assertEqual(tuple(self.db.execute("SELECT concept_id,concept_proposal_id FROM occurrence_concept_reference WHERE occurrence_id=1 AND is_current=1").fetchone()),context)
        self.assertEqual(self.db.execute("SELECT count(*) FROM alternative_morphology WHERE alternative_id=1 AND is_current=1").fetchone()[0],1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM alternative_relation WHERE is_current=1 AND (alternative_low_id=1 OR alternative_high_id=1)").fetchone()[0],0)
        with self.assertRaises(StructuralAlternativeError):move_preview(self.db,1,2)

    def test_move_preview_blocks_one_current_relation_without_changes(self):
        before = "\n".join(self.db.iterdump())
        with self.assertRaisesRegex(StructuralAlternativeError, "no puede trasladarse individualmente"):
            move_preview(self.db,1,2)
        self.assertEqual(before, "\n".join(self.db.iterdump()))
        self.assertEqual(self.db.execute("SELECT concept_id FROM alternative WHERE alternative_id=1").fetchone()[0],1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM alternative_relation WHERE is_current=1 AND (alternative_low_id=1 OR alternative_high_id=1)").fetchone()[0],2)

    def test_apply_move_blocks_multiple_current_relations_without_events(self):
        before = "\n".join(self.db.iterdump())
        expected = fingerprint(relevant_state(self.db, 1, 2))
        with self.assertRaisesRegex(StructuralAlternativeError, "grupo léxico"):
            apply_move(self.db,1,2,expected_fingerprint=expected,reason="reclasificaciÃ³n",actor=self.actor)
        self.assertEqual(before, "\n".join(self.db.iterdump()))
        self.assertEqual(self.db.execute("SELECT count(*) FROM renumber_event").fetchone()[0],0)
        self.assertEqual(self.db.execute("SELECT count(*) FROM activity_event").fetchone()[0],0)

    def test_historical_relations_do_not_block_move(self):
        self.db.execute("UPDATE alternative_relation SET is_current=0 WHERE alternative_low_id=1 OR alternative_high_id=1")
        self.db.commit()
        preview = move_preview(self.db,1,2)
        apply_move(self.db,1,2,expected_fingerprint=preview["fingerprint"],reason="reclasificaciÃ³n",actor=self.actor)
        self.assertEqual(self.db.execute("SELECT concept_id FROM alternative WHERE alternative_id=1").fetchone()[0],2)
        self.assertEqual(self.db.execute("SELECT count(*) FROM alternative_relation WHERE is_current=0 AND (alternative_low_id=1 OR alternative_high_id=1)").fetchone()[0],2)

    def test_rollback_is_atomic(self):
        self.db.execute("CREATE TRIGGER fail_activity BEFORE INSERT ON activity_event WHEN NEW.event_type='alternative_retired' BEGIN SELECT RAISE(ABORT,'synthetic');END");self.db.commit()
        with self.assertRaises(sqlite3.IntegrityError):apply_retire(self.db,1,{1:2,2:2},expected_fingerprint=retire_preview(self.db,1,{1:2,2:2})["fingerprint"],reason="fallar",actor=self.actor)
        self.assertIsNone(self.db.execute("SELECT retired_at FROM alternative WHERE alternative_id=1").fetchone()[0])
        self.assertEqual(self.db.execute("SELECT count(*) FROM assignment WHERE alternative_id=1 AND is_current=1").fetchone()[0],2)
        self.assertEqual(self.db.execute("SELECT count(*) FROM alternative_relation WHERE is_current=1 AND (alternative_low_id=1 OR alternative_high_id=1)").fetchone()[0],2)


if __name__=="__main__":unittest.main()
