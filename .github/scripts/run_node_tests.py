"""Run the complete suite against real ComfyUI; missing tests and skips fail CI."""
import argparse
from contextlib import ExitStack
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
REQUIRED_MODULES = {
    'test_execution', 'test_jev', 'test_model_catalog', 'test_multimodal',
    'test_skill_workflow', 'test_skills', 'test_suggestions',
}
MINIMUM_TESTS = 89


def test_ids(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from test_ids(test)
        else:
            yield test.id()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--comfy-dir', type=Path, required=True)
    options = parser.parse_args()
    comfy = options.comfy_dir.resolve()
    if not (comfy / 'comfy_api/latest/__init__.py').is_file() or not (comfy / 'execution.py').is_file():
        parser.error('--comfy-dir must point to a ComfyUI source checkout')
    # A repository-root cwd/PYTHONPATH can shadow ComfyUI's top-level nodes.py.
    sys.path[:] = [str(comfy), *(p for p in sys.path if Path(p or os.getcwd()).resolve() != ROOT)]
    os.chdir(comfy)
    for name in ('HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'HF_HUB_DISABLE_TELEMETRY', 'DO_NOT_TRACK'):
        os.environ[name] = '1'

    attempted_network = []

    def deny_network(*args, **kwargs):
        attempted_network.append(True)
        raise AssertionError('Real network access is forbidden in node tests; mock the transport')

    with ExitStack() as stack:
        # Applied before imports as well as during execution. Asyncio's local
        # socketpair still works; DNS and outbound connections cannot reach APIs.
        for target in ('socket.socket.connect', 'socket.socket.connect_ex', 'socket.create_connection', 'socket.getaddrinfo'):
            stack.enter_context(patch(target, side_effect=deny_network))
        runtime = stack.enter_context(tempfile.TemporaryDirectory(prefix='jev-node-tests-'))
        from comfy.cli_args import args
        args.cpu = True
        args.disable_xformers = True
        args.disable_dynamic_vram = True
        args.base_directory = runtime
        loader = unittest.TestLoader()
        suite = loader.discover(str(ROOT / 'tests'))
        ids = list(test_ids(suite))
        modules = {test_id.split('.')[0] for test_id in ids}
        missing = REQUIRED_MODULES - modules
        if loader.errors or missing or len(ids) < MINIMUM_TESTS:
            for error in loader.errors:
                print(error, file=sys.stderr)
            print(f'Incomplete discovery: {len(ids)} tests; missing modules: {sorted(missing)}', file=sys.stderr)
            return 1
        import nodes
        if Path(nodes.__file__).resolve() != comfy / 'nodes.py':
            raise RuntimeError('Tests imported a different nodes.py instead of the selected ComfyUI')
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        complete = result.testsRun == len(ids) and not result.skipped
        print(f'Executed {result.testsRun}/{len(ids)} tests; skipped={len(result.skipped)}; network_attempts={len(attempted_network)}')
        return 0 if result.wasSuccessful() and complete and not attempted_network else 1


if __name__ == '__main__':
    raise SystemExit(main())
