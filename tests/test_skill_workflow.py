"""Exercise the installed Skill Loader and AgentRuntime with mocked paid providers."""
import copy
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

from test_jev import ROOT, api, n
from test_suggestions import reply
from test_execution import Server

import nodes
from comfy_extras.nodes_preview_any import PreviewAny
from comfy_extras.nodes_primitive import PrimitivesExtension
from execution import PromptExecutor, validate_prompt

PACKS = [ROOT.parent / 'ComfyUI-Skills-Loader', ROOT.parent / 'ComfyUI-AgentRuntime']
AVAILABLE = all((path / '__init__.py').is_file() for path in PACKS)
if AVAILABLE:
    sys.path[:0] = [str(path) for path in PACKS]
    from comfyui_skills import nodes as skill_nodes
    from comfyui_agent_runtime import nodes as agent_nodes
    from comfyui_agent_runtime.providers.base import AgentResult


@unittest.skipUnless(AVAILABLE, 'Install Skill Loader and AgentRuntime to run integration tests')
class SkillWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        saved = dict(nodes.NODE_CLASS_MAPPINGS)
        def restore():
            nodes.NODE_CLASS_MAPPINGS.clear()
            nodes.NODE_CLASS_MAPPINGS.update(saved)
        self.addCleanup(restore)
        for extension in (n.JevExtension(), PrimitivesExtension()):
            for cls in await extension.get_node_list():
                nodes.NODE_CLASS_MAPPINGS[cls.GET_SCHEMA().node_id] = cls
        nodes.NODE_CLASS_MAPPINGS.update(skill_nodes.NODE_CLASS_MAPPINGS)
        nodes.NODE_CLASS_MAPPINGS['AgentRuntimeRun'] = agent_nodes.AgentRuntimeRun
        nodes.NODE_CLASS_MAPPINGS['PreviewAny'] = PreviewAny
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for name, description, body in [('copy', 'Write advertising copy', 'COPY GUIDANCE'),
                                         ('music', 'Arrange background music', 'MUSIC GUIDANCE'),
                                         ('review', 'Review generated images', 'REVIEW GUIDANCE')]:
            path = self.directory / name / 'SKILL.md'
            path.parent.mkdir()
            path.write_text(f'---\nname: {name}\ndescription: {description}\n---\n{body}\n')
        self.graph = json.loads((ROOT / 'examples/04_skill_suggestion.api.json').read_text())
        self.graph['2']['inputs']['directory'] = str(self.directory)
        self.provider = MagicMock()
        self.provider.is_available.return_value = True
        self.provider.run.return_value = AgentResult('codex', 'Finished production text', 'mock response')
        self.stack.enter_context(patch.object(agent_nodes, 'get_provider', return_value=self.provider))
        self.transport = self.stack.enter_context(patch.object(api, 'evaluate', side_effect=self.evaluate))
        self.executor = PromptExecutor(Server(), cache_type=False, cache_args={'ram': 0, 'ram_inactive': 0})

    async def evaluate(self, state, questions, model, **kwargs):
        if state == '2 + 2?':
            return reply(questions, gated=True)
        target = 'music' if state.startswith('Arrange') else 'copy'
        choices = questions['which']['criteria'] if 'which' in questions else {
            key.removeprefix('fits_'): question['instructions']['candidate']
            for key, question in questions.items() if key.startswith('fits_')
        }
        winner = next(key for key, value in choices.items() if target in json.dumps(value).lower())
        result = reply(questions, winner=winner)
        if 'which' in questions:
            result['answers']['which']['probabilities'] = {key: float(key == winner) for key in choices}
        for key in choices:
            if 'fits_' + key in questions:
                relevant = any(name in state.lower() and name in json.dumps(choices[key]).lower() for name in ('copy', 'music'))
                result['answers']['fits_' + key]['noul'] = 0.9 if relevant else 0.1
                if 'applicability_' + key in questions:
                    score = 3.6 if key == winner else 1.2 if relevant else 0
                    result['answers']['applicability_' + key] = reply(questions, scores={key: score})['answers']['applicability_' + key]
        return result

    async def run_graph(self, prompt):
        self.graph['1']['inputs']['value'] = prompt
        graph = copy.deepcopy(self.graph)
        valid, error, outputs, details = await validate_prompt('skills', graph, None)
        self.assertTrue(valid, (error, details))
        self.assertFalse(details)
        await self.executor.execute_async(graph, 'skills', execute_outputs=outputs)
        self.assertTrue(self.executor.success, self.executor.status_messages)
        return self.provider.run.call_args.args[0]

    async def test_prompt_routes_real_skill_bodies_into_agent_runtime(self):
        request = await self.run_graph('Write advertising copy')
        self.assertIn('<task>\nWrite advertising copy\n</task>', request.prompt)
        self.assertIn('COPY GUIDANCE', request.prompt)
        self.assertIn(str(self.directory / 'copy/SKILL.md'), request.prompt)
        self.assertNotIn('MUSIC GUIDANCE', request.prompt)
        self.assertEqual(self.transport.await_count, 2)
        self.assertEqual((await self.executor.caches.outputs.get('5')).outputs[0][0], 'Finished production text')
        self.graph['5']['inputs']['instruction'] += ' Keep it short.'
        await self.run_graph('Write advertising copy')
        self.assertEqual(self.transport.await_count, 2)
        request = await self.run_graph('Arrange background music')
        self.assertIn('MUSIC GUIDANCE', request.prompt)
        self.assertNotIn('COPY GUIDANCE', request.prompt)
        self.assertEqual(self.transport.await_count, 4)
        path = self.directory / 'music/SKILL.md'
        path.write_text(path.read_text() + 'NEW DIRECTION\n')
        request = await self.run_graph('Arrange background music')
        self.assertIn('NEW DIRECTION', request.prompt)
        self.assertEqual(self.transport.await_count, 6)

    async def test_no_skill_is_a_working_empty_stack(self):
        request = await self.run_graph('2 + 2?')
        self.assertEqual(request.prompt, '2 + 2?')
        self.assertEqual(self.transport.await_count, 1)
        self.assertEqual((await self.executor.caches.outputs.get('4')).outputs[1][0], {'skills': []})

    async def test_prompt_changes_individual_strengths_in_actual_agent_request(self):
        request = await self.run_graph('Write copy with background music')
        self.assertIn('<agent_skill strength="1.8" direction="apply">\n<name>copy</name>', request.prompt)
        self.assertIn('<agent_skill strength="0.6" direction="apply">\n<name>music</name>', request.prompt)
        self.assertNotIn('REVIEW GUIDANCE', request.prompt)
        self.assertIn('<task>\nWrite copy with background music\n</task>', request.prompt)
        bundle = (await self.executor.caches.outputs.get('4')).outputs[1][0]
        self.assertEqual([(item['name'], item['strength']) for item in bundle['skills']], [('copy', 1.8), ('music', 0.6)])
        self.assertEqual(self.transport.await_count, 2)
        await self.run_graph('Write copy with background music')
        self.assertEqual(self.transport.await_count, 2)
        request = await self.run_graph('Arrange music with copy')
        self.assertIn('<agent_skill strength="1.8" direction="apply">\n<name>music</name>', request.prompt)
        self.assertIn('<agent_skill strength="0.6" direction="apply">\n<name>copy</name>', request.prompt)
        self.assertEqual(self.transport.await_count, 4)

    async def test_saved_workflow_links_and_no_renamed_titles(self):
        workflow = json.loads((ROOT / 'examples/04_skill_suggestion.workflow.json').read_text())
        graph = json.loads((ROOT / 'examples/04_skill_suggestion.api.json').read_text())
        links = {link[0]: link for link in workflow['links']}
        self.assertEqual(len(workflow['nodes']), len(graph))
        for node in workflow['nodes']:
            self.assertNotIn('title', node)
            expected = graph[str(node['id'])]
            self.assertEqual(node['type'], expected['class_type'])
            slots = {slot['name']: slot for slot in node['inputs']}
            for name, value in expected['inputs'].items():
                if isinstance(value, list):
                    link = links[slots[name]['link']]
                    self.assertEqual([str(link[1]), link[2]], value)
                    self.assertEqual(link[3], node['id'])
                else:
                    self.assertEqual(node['widgets_values_named'][name], value)
