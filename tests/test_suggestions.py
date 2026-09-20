import json
import unittest
from unittest.mock import patch

from test_jev import api, n, response_for, s


CANDIDATES = [
    {'description': f'Skill {i}: description', 'content': f'COMPLETE BODY {i}\nSecond paragraph.',
     'value': {'skill_path': f'/existing/skill-{i}/SKILL.md', 'strength': 1.0}}
    for i in range(4)
]


def reply(questions, winner=None, fits=None, gated=False, scores=None):
    response = response_for(questions)
    if 'gate_work' in questions:
        for key, value in {'work': 0.05 if gated else 0.9, 'procedure': 0.05 if gated else 0.8,
                           'general': 0.95 if gated else 0.1}.items():
            response['answers']['gate_' + key]['noul'] = value
    if 'which' in questions:
        choices = list(questions['which']['criteria'])
        choice = response['answers']['which']
        choice['probabilities'] = {key: (len(choices) - i) / sum(range(1, len(choices) + 1)) for i, key in enumerate(choices)}
        if winner:
            choice['choice'] = winner
    for key, value in (fits or {}).items():
        response['answers']['fits_' + key]['noul'] = value
    for key, value in (scores or {}).items():
        answer = response['answers']['applicability_' + key]
        answer['score'] = value
        answer['confidence'] = 0.1
        answer['probabilities'] = {str(i): max(0, 1 - abs(i - value)) for i in range(5)}
    return response


class SuggestionTests(unittest.IsolatedAsyncioTestCase):
    async def select(self, **kwargs):
        return await n.JevInterpret.execute(
            'Create an image prompt.', 'Choose useful skill guidance for this request.', 'suggest',
            {'model': 'jev-latest'}, provider='openrouter', candidates_json=s.dumps(CANDIDATES), **kwargs,
        )

    async def test_two_batched_requests_and_original_values(self):
        seen = []
        async def evaluate(state, questions, model, **kwargs):
            seen.append(questions)
            self.assertEqual(state, 'Create an image prompt.')
            self.assertEqual(kwargs['provider'], 'openrouter')
            if len(seen) == 1:
                self.assertEqual(len(questions), 4)
                self.assertNotIn('COMPLETE BODY', json.dumps(questions))
                self.assertNotIn('/existing/', json.dumps(questions))
                return reply(questions)
            self.assertEqual(set(questions['which']['criteria']), {'c0', 'c1', 'c2'})
            self.assertIn('COMPLETE BODY 1\nSecond paragraph.', questions['which']['criteria']['c1']['content'])
            self.assertNotIn('COMPLETE BODY 3', json.dumps(questions))
            return reply(questions, winner='c1', fits={'c0': 0.2, 'c1': 0.9, 'c2': 0.1})
        with patch.object(api, 'evaluate', side_effect=evaluate) as transport:
            output = await self.select()
        self.assertEqual(transport.await_count, 2)
        self.assertEqual(json.loads(output.result[0]), [CANDIDATES[1]['value']])
        self.assertEqual(output.result[1]['selected'], ['c1'])
        self.assertEqual(set(json.loads(output.result[2])), {'rank', 'verify'})

    async def test_gate_skips_verification_and_empty_catalog_skips_all(self):
        with patch.object(api, 'evaluate', side_effect=lambda state, questions, model, **kw: reply(questions, gated=True)) as transport:
            output = await self.select()
            self.assertEqual(output.result[0], '[]')
            self.assertEqual(output.result[1]['reason'], 'skill_not_needed')
            self.assertEqual(transport.await_count, 1)
            output = await n.JevInterpret.execute('hi', 'Choose', 'suggest', {'model': 'jev-latest'}, candidates_json='[]')
            self.assertEqual(output.result[0], '[]')
            self.assertEqual(output.result[1]['reason'], 'empty_catalog')
            self.assertEqual(transport.await_count, 1)

    async def test_rejects_an_unsuitable_winner_even_if_another_candidate_passes(self):
        async def evaluate(state, questions, model, **kwargs):
            return reply(questions) if 'gate_work' in questions else reply(questions, winner='c0', fits={'c0': 0.1, 'c1': 0.9, 'c2': 0.2})
        with patch.object(api, 'evaluate', side_effect=evaluate):
            output = await self.select()
            self.assertEqual(output.result[0], '[]')
            self.assertEqual(output.result[1]['reason'], 'no_suitable_candidate')

    async def test_multiple_selections_use_independent_fit_and_stable_order(self):
        async def evaluate(state, questions, model, **kwargs):
            response = reply(questions)
            if 'gate_work' not in questions:
                response = reply(questions, winner='c2', fits={'c0': 0.5, 'c1': 0.49, 'c2': 0.9})
            return response
        with patch.object(api, 'evaluate', side_effect=evaluate):
            output = await self.select(max_selections=2)
            self.assertEqual(json.loads(output.result[0]), [CANDIDATES[2]['value'], CANDIDATES[0]['value']])

    async def test_bad_answers_and_values_do_not_become_paths(self):
        for answers in (None, {}, {'which': {'type': 'noul', 'noul': 0.9}}):
            with patch.object(api, 'evaluate', return_value={'answers': answers}):
                with self.assertRaises(ValueError):
                    await self.select()
        async def invented(state, questions, model, **kwargs):
            response = reply(questions)
            response['answers']['which']['choice'] = '/not/a/catalog/path'
            return response
        with patch.object(api, 'evaluate', side_effect=invented):
            with self.assertRaisesRegex(ValueError, 'not a candidate'):
                await self.select()
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            s.text_candidates(candidates_json=s.dumps([CANDIDATES[0], CANDIDATES[0]]))

    async def test_existing_choice_can_return_structured_values(self):
        async def evaluate(state, questions, model, **kwargs):
            return response_for(questions)
        with patch.object(api, 'evaluate', side_effect=evaluate):
            output = await n.JevInterpret.execute('brief', 'Choose', 'choice', {'model': 'jev-latest'}, candidates_json=s.dumps(CANDIDATES))
            self.assertEqual(json.loads(output.result[0]), CANDIDATES[0]['value'])

    async def test_automatic_strength_scores_are_batched_and_independent(self):
        seen = []
        async def evaluate(state, questions, model, **kwargs):
            seen.append(questions)
            if 'gate_work' in questions:
                return reply(questions)
            self.assertEqual(set(questions), {f'{kind}_c{i}' for i in range(3) for kind in ('fits', 'applicability')})
            self.assertIn('COMPLETE BODY 1', questions['applicability_c1']['instructions']['candidate']['content'])
            return reply(questions, fits={'c0': 0.99, 'c1': 0.5, 'c2': 0.9}, scores={'c0': 1.2, 'c1': 3.6, 'c2': 4})
        original = s.dumps(CANDIDATES)
        with patch.object(api, 'evaluate', side_effect=evaluate) as transport:
            output = await self.select(skill_strength='automatic', max_selections=3)
        self.assertEqual(transport.await_count, 2)
        values = json.loads(output.result[0])
        self.assertEqual(values, [{**CANDIDATES[i]['value'], 'strength': strength} for i, strength in [(2, 2), (1, 1.8), (0, 0.6)]])
        self.assertEqual(output.result[1]['strengths'], {'c2': 2, 'c1': 1.8, 'c0': 0.6})
        self.assertEqual(s.dumps(CANDIDATES), original)

    async def test_automatic_strength_omits_zero_and_failed_fit_before_limit(self):
        async def evaluate(state, questions, model, **kwargs):
            if 'gate_work' in questions:
                return reply(questions)
            return reply(questions, fits={'c0': 0.99, 'c1': 0.49, 'c2': 0.5}, scores={'c0': 0, 'c1': 4, 'c2': 3})
        with patch.object(api, 'evaluate', side_effect=evaluate):
            output = await self.select(skill_strength='automatic', max_selections=1)
        self.assertEqual(json.loads(output.result[0]), [{**CANDIDATES[2]['value'], 'strength': 1.5}])

    async def test_automatic_strength_ties_keep_catalog_order(self):
        async def evaluate(state, questions, model, **kwargs):
            if 'gate_work' in questions:
                result = reply(questions, winner='c2')
                result['answers']['which']['probabilities'] = {'c0': 0.2, 'c1': 0.3, 'c2': 0.5, 'c3': 0}
                return result
            return reply(questions, scores={'c0': 2, 'c1': 2, 'c2': 2})
        with patch.object(api, 'evaluate', side_effect=evaluate):
            output = await self.select(skill_strength='automatic', max_selections=2)
        self.assertEqual(output.result[1]['selected'], ['c0', 'c1'])

    async def test_automatic_strength_empty_and_gated_results(self):
        with patch.object(api, 'evaluate', side_effect=lambda state, questions, model, **kw: reply(questions, gated=True)) as transport:
            output = await self.select(skill_strength='automatic')
            self.assertEqual(output.result[0], '[]')
            self.assertEqual(output.result[1]['strengths'], {})
            output = await n.JevInterpret.execute('hi', 'Choose', 'suggest', {'model': 'jev-latest'}, candidates_json='[]', skill_strength='automatic')
            self.assertEqual(output.result[0], '[]')
            self.assertEqual(transport.await_count, 1)

    async def test_automatic_strength_rejects_bad_candidates_before_api(self):
        for value in ('text', {}, {'skill_path': ''}, {'skill_path': 3}):
            with self.subTest(value=value), patch.object(api, 'evaluate') as transport:
                with self.assertRaisesRegex(ValueError, 'candidate c0.*skill_path'):
                    await n.JevInterpret.execute('brief', 'Choose', 'suggest', {'model': 'jev-latest'},
                                                 candidates_json=s.dumps([{'description': 'Skill', 'value': value}]), skill_strength='automatic')
                transport.assert_not_called()

    async def test_automatic_strength_requires_valid_score_answers(self):
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
                    await self.select(skill_strength='automatic')
