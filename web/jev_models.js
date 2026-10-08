import { app } from "../../scripts/app.js";

// Keep the existing widget names/order and DynamicCombo serialization intact.
// The server's full schema also retains historical IDs for saved workflows.
const aliases = { "jev-latest": "~typesafe/jev-latest", "jev-1.13.0": "typesafe/jev-1.13" };

export function updateModelChoices(node, byProvider, preserveSaved = false, knownModels = []) {
    const model = node.widgets?.find(w => w.name === "model");
    const provider = node.widgets?.find(w => w.name === "provider");
    if (!model || !provider) return;
    if (preserveSaved && model.value && knownModels.length && !knownModels.includes(model.value)) {
        // A workflow moved to an offline machine may have no matching cache.
        // Reuse the legacy custom wire shape instead of losing its saved ID.
        const savedId = model.value;
        model.value = "custom";
        model.callback?.(model.value);
        const customId = node.widgets.find(w => w.name === "model.model_id");
        if (customId) customId.value = savedId;
    }
    const choices = [...(byProvider[provider.value] ?? [])];
    if (preserveSaved && model.value && !choices.includes(model.value)) {
        choices.unshift(model.value);
    }
    model.options.values = choices;
    if (!choices.includes(model.value) && choices.length) {
        const alias = provider.value === "openrouter"
            ? aliases[model.value]
            : Object.keys(aliases).find(key => aliases[key] === model.value);
        const preferred = provider.value === "openrouter" ? "~typesafe/jev-latest" : "jev-latest";
        model.value = [alias, preferred].find(id => choices.includes(id)) ?? choices[0];
        model.callback?.(model.value);
    }
    node.setDirtyCanvas?.(true, true);
}

app.registerExtension({
    name: "Jev.DecisionsModels",
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (!["JevInterpret", "JevSkillChoice"].includes(nodeData.name)) return;
        const choices = nodeData.input.required.model[1].jev_provider_models;
        const knownModels = nodeData.input.required.model[1].options.map(option => option.key);
        if (!choices) return;
        const created = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function (...args) {
            const result = created?.apply(this, args);
            const provider = this.widgets?.find(w => w.name === "provider");
            if (provider) {
                const callback = provider.callback;
                provider.callback = (...values) => {
                    callback?.apply(provider, values);
                    updateModelChoices(this, choices);
                };
            }
            updateModelChoices(this, choices);
            return result;
        };
        const configure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function (...args) {
            const result = configure?.apply(this, args);
            updateModelChoices(this, choices, true, knownModels);
            return result;
        };
    },
});
