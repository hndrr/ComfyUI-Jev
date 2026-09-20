"""Run Skill Choice through ComfyUI with local files and a mocked Jev API."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_jev import ROOT, api, n
from test_suggestions import reply
from test_execution import Server

import nodes
from comfy_extras.nodes_preview_any import PreviewAny
from comfy_extras.nodes_primitive import PrimitivesExtension
from execution import PromptExecutor, validate_prompt


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
        nodes.NODE_CLASS_MAPPINGS['PreviewAny'] = PreviewAny
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name).resolve()
        for name, description, body in [('copy', 'Write advertising copy', 'COPY GUIDANCE'),
                                         ('music', 'Arrange background music', 'MUSIC GUIDANCE'),
                                         ('review', 'Review generated images', 'REVIEW GUIDANCE')]:
            path = self.directory / name / 'SKILL.md'
            path.parent.mkdir()
            path.write_text(f'---\nname: {name}\ndescription: {description}\n---\n{body}\n')
        self.graph = json.loads((ROOT / 'examples/04_skill_choice.api.json').read_text())
        self.graph['2']['inputs']['directory'] = 'custom'
        self.graph['2']['inputs']['directory.path'] = str(self.directory)
        patcher = patch.object(api, 'evaluate', side_effect=self.evaluate)
        self.transport = patcher.start()
        self.addCleanup(patcher.stop)
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
        output = (await self.executor.caches.outputs.get('2')).outputs
        return output[0][0], output[1][0]

    async def test_reads_files_and_updates_cached_results(self):
        text, bundle = await self.run_graph('Write advertising copy')
        self.assertIn('COPY GUIDANCE', text)
        self.assertNotIn('MUSIC GUIDANCE', text)
        self.assertEqual(bundle['skills'][0]['path'], str(self.directory / 'copy/SKILL.md'))
        self.assertEqual((await self.executor.caches.outputs.get('3')).outputs[0][0], text)
        self.assertEqual(self.transport.await_count, 2)
        await self.run_graph('Write advertising copy')
        self.assertEqual(self.transport.await_count, 2)
        text, _ = await self.run_graph('Arrange background music')
        self.assertIn('MUSIC GUIDANCE', text)
        self.assertNotIn('COPY GUIDANCE', text)
        self.assertEqual(self.transport.await_count, 4)
        path = self.directory / 'music/SKILL.md'
        path.write_text(path.read_text() + 'NEW DIRECTION\n')
        text, _ = await self.run_graph('Arrange background music')
        self.assertIn('NEW DIRECTION', text)
        self.assertEqual(self.transport.await_count, 6)
        self.graph['2']['inputs']['refresh'] = 1
        await self.run_graph('Arrange background music')
        self.assertEqual(self.transport.await_count, 8)

    async def test_no_skill_reaches_preview_as_empty_output(self):
        text, bundle = await self.run_graph('2 + 2?')
        self.assertEqual(text, '')
        self.assertEqual(bundle, {'skills': []})
        self.assertEqual(self.transport.await_count, 1)

    async def test_automatic_and_discovered_directory_execute_through_comfy(self):
        config = self.directory / 'profile'
        skill = config / 'skills' / 'copy' / 'SKILL.md'
        skill.parent.mkdir(parents=True)
        skill.write_text((self.directory / 'copy' / 'SKILL.md').read_text())
        shared_root = self.directory / '.agents' / 'skills'
        shared_skill = shared_root / 'music' / 'SKILL.md'
        shared_skill.parent.mkdir(parents=True)
        shared_skill.write_text((self.directory / 'music' / 'SKILL.md').read_text())
        self.graph['2']['inputs'].pop('directory.path')
        with patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(config)}), patch.object(n.folder_paths, 'base_path', str(self.directory)), patch.object(Path, 'home', return_value=self.directory / 'home'):
            for selection, prompt, expected in (
                ('automatic', 'Write copy with background music', {str(skill), str(shared_skill)}),
                (str(config / 'skills'), 'Write advertising copy', {str(skill)}),
                (str(shared_root), 'Arrange background music', {str(shared_skill)}),
            ):
                self.graph['2']['inputs']['directory'] = selection
                _, bundle = await self.run_graph(prompt)
                self.assertEqual({record['path'] for record in bundle['skills']}, expected)
            self.assertEqual(self.transport.await_count, 6)
            self.graph['2']['inputs']['directory'] = 'automatic'
            skill.write_text(skill.read_text() + 'NEW DIRECTION\n')
            text, _ = await self.run_graph('Write advertising copy')
            self.assertIn('NEW DIRECTION', text)
            self.assertEqual(self.transport.await_count, 8)

    async def test_prompt_changes_individual_strengths(self):
        text, bundle = await self.run_graph('Write copy with background music')
        self.assertIn('Application priority: 1.8', text)
        self.assertIn('Application priority: 0.6', text)
        self.assertNotIn('REVIEW GUIDANCE', text)
        self.assertEqual([(item['name'], item['strength']) for item in bundle['skills']], [('copy', 1.8), ('music', 0.6)])
        self.assertEqual(self.transport.await_count, 2)
        await self.run_graph('Write copy with background music')
        self.assertEqual(self.transport.await_count, 2)
        _, bundle = await self.run_graph('Arrange music with copy')
        self.assertEqual([(item['name'], item['strength']) for item in bundle['skills']], [('music', 1.8), ('copy', 0.6)])
        self.assertEqual(self.transport.await_count, 4)

    def test_saved_workflow_matches_node_inputs_and_links(self):
        workflow = json.loads((ROOT / 'examples/04_skill_choice.workflow.json').read_text())
        graph = json.loads((ROOT / 'examples/04_skill_choice.api.json').read_text())
        links = {link[0]: link for link in workflow['links']}
        by_id = {node['id']: node for node in workflow['nodes']}
        for node in workflow['nodes']:
            self.assertNotIn('title', node)
            expected = graph[str(node['id'])]
            self.assertEqual(node['type'], expected['class_type'])
            for name, value in expected['inputs'].items():
                if isinstance(value, list):
                    slot = next(slot for slot in node['inputs'] if slot['name'] == name)
                    link = links[slot['link']]
                    self.assertEqual([str(link[1]), link[2]], value)
                    self.assertEqual(link[3], node['id'])
                    self.assertIn(link[0], by_id[link[1]]['outputs'][link[2]]['links'])
                    self.assertEqual(node['inputs'][link[4]]['name'], name)
                else:
                    self.assertEqual(node['widgets_values_named'][name], value)
        node = by_id[2]
        schema = n.JevSkillChoice.INPUT_TYPES()
        kind, config = schema['required']['directory']
        self.assertEqual(kind, 'COMFY_DYNAMICCOMBO_V3')
        self.assertEqual(config['options'][0]['key'], 'automatic')
        self.assertEqual(config['options'][-1]['key'], 'custom')
        self.assertEqual(node['widgets_values_named']['directory'], 'automatic')
        self.assertEqual(next(slot['type'] for slot in node['inputs'] if slot['name'] == 'directory'), kind)
        names = list(schema['required']) + list(schema['optional'])
        self.assertEqual([slot['name'] for slot in node['inputs']], names)
        widget_names = names[:names.index('refresh') + 1] + ['control_after_generate'] + names[names.index('refresh') + 1:]
        self.assertEqual(node['widgets_values'], [node['widgets_values_named'][name] for name in widget_names])
        self.assertEqual(node['widgets_values_named']['api_key'], '')
        self.assertEqual(node['widgets_values_named']['control_after_generate'], 'fixed')
