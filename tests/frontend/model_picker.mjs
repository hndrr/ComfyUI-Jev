// Shared with the browser check; no DOM, network, or test framework required.
export function checkModelPicker(update) {
    let passed = 0;
    const check = (ok, message) => { if (!ok) throw new Error(message); passed++; };
    const choices = {typesafe: ['jev-latest', 'jev-preview', 'jev-1.13.0', 'custom'], openrouter: ['vendor/decision', '~typesafe/jev-latest', 'typesafe/jev-1.13']};
    const known = [...choices.typesafe, ...choices.openrouter, 'vendor/retired'];
    const model = {name:'model', value:'jev-latest', options:{}, callback(value) {
        node.widgets = node.widgets.filter(w => w.name !== 'model.model_id');
        if (value === 'custom') node.widgets.push({name:'model.model_id',value:''});
    }};
    const provider = {name:'provider',value:'typesafe'};
    const node = {widgets:[model,provider]};
    update(node, choices);
    check(model.options.values.join() === choices.typesafe.join(), 'TypeSafe options');
    provider.value = 'openrouter'; update(node, choices);
    check(model.options.values.join() === choices.openrouter.join(), 'Decisions-only options');
    check(model.value === '~typesafe/jev-latest', 'Jev alias when switching');
    check(!model.options.values.includes('custom'), 'No new OpenRouter custom selection');
    provider.value = 'typesafe'; update(node, choices);
    check(model.value === 'jev-latest', 'Reverse alias when switching');
    provider.value = 'openrouter'; model.value = 'vendor/retired'; update(node, choices, true, known);
    check(model.value === 'vendor/retired' && model.options.values.includes('vendor/retired'), 'Retained saved model');
    model.value = 'vendor/offline-saved'; update(node, choices, true, known);
    check(model.value === 'custom', 'Missing-cache model uses legacy serialization');
    check(node.widgets.find(w => w.name === 'model.model_id').value === 'vendor/offline-saved', 'Preserved exact saved ID');
    update(node, choices, true, known);
    check(model.value === 'custom' && node.widgets.find(w => w.name === 'model.model_id').value === 'vendor/offline-saved', 'Repeated restore preserves legacy ID');
    update(node, choices);
    check(model.value === '~typesafe/jev-latest' && !model.options.values.includes('custom'), 'Fresh choice removes compatibility option');
    check(!choices.openrouter.includes('vendor/retired') && !choices.openrouter.includes('custom'), 'Catalog choices are not mutated');
    return passed;
}
