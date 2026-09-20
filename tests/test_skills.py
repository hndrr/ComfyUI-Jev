import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_jev import api, n
from test_suggestions import reply


class SkillChoiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        for i in range(4):
            path = self.root / f'skill-{i}' / 'SKILL.md'
            path.parent.mkdir()
            path.write_text(f'---\nname: skill-{i}\ndescription: Skill {i} description\n---\nCOMPLETE BODY {i}\nSecond paragraph.\n')

    async def select(self, **kwargs):
        return await n.JevSkillChoice.execute(
            'Create an image prompt.', str(self.root), 'Choose useful guidance.', {'model': 'jev-latest'},
            provider='openrouter', shortlist_size=3, **kwargs,
        )

    async def test_automatic_strength_and_loaded_bodies(self):
        async def evaluate(state, questions, model, **kwargs):
            self.assertEqual(state, 'Create an image prompt.')
            self.assertEqual(kwargs['provider'], 'openrouter')
            if 'gate_work' in questions:
                self.assertNotIn('COMPLETE BODY', json.dumps(questions))
                self.assertNotIn(str(self.root), json.dumps(questions))
                return reply(questions)
            self.assertEqual(set(questions), {f'{kind}_c{i}' for i in range(3) for kind in ('fits', 'applicability')})
            self.assertIn('COMPLETE BODY 1', questions['applicability_c1']['instructions']['candidate']['content'])
            self.assertNotIn('COMPLETE BODY 3', json.dumps(questions))
            return reply(questions, fits={'c0': 0.99, 'c1': 0.5, 'c2': 0.9}, scores={'c0': 1.2, 'c1': 3.6, 'c2': 4})
        with patch.object(api, 'evaluate', side_effect=evaluate) as transport:
            output = await self.select()
        self.assertEqual(transport.await_count, 2)
        records = output.result[1]['skills']
        self.assertEqual([(item['name'], item['strength']) for item in records], [('skill-2', 2), ('skill-1', 1.8), ('skill-0', 0.6)])
        for record in records:
            self.assertEqual(record['content'], Path(record['path']).read_text())
            self.assertIn(record['content'], output.result[0])
        self.assertEqual(output.result[2]['strengths'], {'c2': 2, 'c1': 1.8, 'c0': 0.6})
        self.assertEqual(set(json.loads(output.result[3])), {'rank', 'verify'})

    async def test_zero_and_failed_fit_are_omitted(self):
        async def evaluate(state, questions, model, **kwargs):
            if 'gate_work' in questions:
                return reply(questions)
            return reply(questions, fits={'c0': 0.99, 'c1': 0.49, 'c2': 0.5}, scores={'c0': 0, 'c1': 4, 'c2': 3})
        with patch.object(api, 'evaluate', side_effect=evaluate):
            output = await self.select(max_selections=1)
        self.assertEqual([(item['name'], item['strength']) for item in output.result[1]['skills']], [('skill-2', 1.5)])

    async def test_ties_keep_file_order(self):
        async def evaluate(state, questions, model, **kwargs):
            if 'gate_work' in questions:
                result = reply(questions, winner='c2')
                result['answers']['which']['probabilities'] = {'c0': 0.2, 'c1': 0.3, 'c2': 0.5, 'c3': 0}
                return result
            return reply(questions, scores={'c0': 2, 'c1': 2, 'c2': 2})
        with patch.object(api, 'evaluate', side_effect=evaluate):
            output = await self.select(max_selections=2)
        self.assertEqual(output.result[2]['selected'], ['c0', 'c1'])

    async def test_uniform_mode_uses_choice_and_unit_strength(self):
        async def evaluate(state, questions, model, **kwargs):
            self.assertFalse(any(key.startswith('applicability_') for key in questions))
            return reply(questions, winner='c1', fits={} if 'gate_work' in questions else {'c0': 0.1, 'c1': 0.9, 'c2': 0.1})
        with patch.object(api, 'evaluate', side_effect=evaluate):
            output = await self.select(strength_mode='uniform')
        self.assertEqual([(item['name'], item['strength']) for item in output.result[1]['skills']], [('skill-1', 1)])

    async def test_empty_and_gated_results(self):
        with patch.object(api, 'evaluate', side_effect=lambda state, questions, model, **kw: reply(questions, gated=True)) as transport:
            output = await self.select()
            self.assertEqual(output.result[0], '')
            self.assertEqual(output.result[1], {'skills': []})
            self.assertEqual(output.result[2]['strengths'], {})
            self.assertEqual(transport.await_count, 1)
            empty = self.root / 'empty'
            empty.mkdir()
            output = await n.JevSkillChoice.execute('hi', str(empty), 'Choose', {'model': 'jev-latest'})
            self.assertEqual(output.result[1], {'skills': []})
            self.assertEqual(transport.await_count, 1)

    async def test_bad_scores_and_invented_choices_fail(self):
        for invalid in (None, -1, 5, float('nan'), '2'):
            async def evaluate(state, questions, model, **kwargs):
                result = reply(questions)
                if 'applicability_c0' in questions:
                    if invalid is None:
                        del result['answers']['applicability_c0']
                    else:
                        result['answers']['applicability_c0']['score'] = invalid
                return result
            with self.subTest(invalid=invalid), patch.object(api, 'evaluate', side_effect=evaluate):
                with self.assertRaisesRegex(ValueError, 'candidate c0'):
                    await self.select()
        async def invented(state, questions, model, **kwargs):
            result = reply(questions)
            result['answers']['which']['choice'] = '/not/a/skill'
            return result
        with patch.object(api, 'evaluate', side_effect=invented):
            with self.assertRaisesRegex(ValueError, 'not a candidate'):
                await self.select()

    async def test_invalid_settings_fail_before_api(self):
        with patch.object(api, 'evaluate') as transport:
            with self.assertRaisesRegex(ValueError, 'strength_mode'):
                await self.select(strength_mode='invalid')
            with self.assertRaisesRegex(ValueError, 'directory'):
                await n.JevSkillChoice.execute('hi', '', 'Choose', {'model': 'jev-latest'})
            transport.assert_not_called()

    def test_metadata_paths_and_symlink_cycles(self):
        path = self.root / 'skill-0' / 'SKILL.md'
        path.write_text('---\nname: Custom name\ndescription: |\n  First line\n  Second line\n---\nFull body\n')
        (self.root / 'link').symlink_to(self.root / 'skill-0', target_is_directory=True)
        (self.root / 'skill-0' / 'loop').symlink_to(self.root, target_is_directory=True)
        records = n.skills.read_skills(self.root.name, self.root.parent)
        self.assertEqual(len(records), 4)
        self.assertEqual(records[0]['name'], 'Custom name')
        self.assertEqual(records[0]['description'], 'First line\nSecond line\n')
        self.assertEqual(records[0]['content'], path.read_text())
        self.assertEqual(records[0]['path'], str(path))
        path.write_text('Body without front matter')
        record = n.skills.read_skills(str(self.root), self.root.parent)[0]
        self.assertEqual(record['name'], 'skill-0')
        self.assertEqual(record['description'], record['content'])
        path.write_text('---\n---\nBody with empty front matter')
        record = n.skills.read_skills(str(self.root), self.root.parent)[0]
        self.assertEqual(record['name'], 'skill-0')
        self.assertEqual(record['description'], record['content'])

    def test_invalid_front_matter_is_actionable(self):
        path = self.root / 'skill-0' / 'SKILL.md'
        for text, error in [('', 'empty Skill'), ('---\nname: broken', 'unclosed'),
                            ('---\nname: [broken\n---\nBody', 'invalid front matter'),
                            ('---\n- item\n---\nBody', 'mapping'),
                            ('---\nfalse\n---\nBody', 'mapping'),
                            ('---\nname: 7\n---\nBody', 'name must be')]:
            path.write_text(text)
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, error):
                n.skills.read_skills(str(self.root), self.root.parent)

    def test_fingerprint_detects_edits_additions_and_removals(self):
        fingerprint = lambda: n.JevSkillChoice.fingerprint_inputs(str(self.root))
        before = fingerprint()
        path = self.root / 'skill-0' / 'SKILL.md'
        path.write_text(path.read_text() + 'Changed instructions')
        after = fingerprint()
        self.assertNotEqual(before, after)
        added = self.root / 'added' / 'SKILL.md'
        added.parent.mkdir()
        added.write_text('Additional instructions')
        self.assertNotEqual(after, fingerprint())
        added.unlink()
        self.assertEqual(after, fingerprint())
