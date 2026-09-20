import json
import unittest
from unittest.mock import patch

from test_jev import api, n, response_for, s


CANDIDATES = [
    {'description': f'Approach {i}: description', 'content': f'COMPLETE BODY {i}\nSecond paragraph.',
     'value': {'prompt': f'Image prompt {i}', 'weight': 1.0}}
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
            'Create an image prompt.', 'Choose a useful creative approach for this request.', 'suggest',
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
