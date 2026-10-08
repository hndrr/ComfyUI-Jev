import { readFile } from 'node:fs/promises';
import { checkModelPicker } from '../../tests/frontend/model_picker.mjs';

const source = await readFile(new URL('../../web/jev_models.js', import.meta.url), 'utf8');
// Stub only the ComfyUI extension registration; exercise the shipped selector.
const moduleSource = source.replace('import { app } from "../../scripts/app.js";', 'const app = { registerExtension() {} };');
const { updateModelChoices } = await import(`data:text/javascript;base64,${Buffer.from(moduleSource).toString('base64')}`);
console.log(`Model picker: ${checkModelPicker(updateModelChoices)} assertions passed`);
