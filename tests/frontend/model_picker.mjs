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
    const boxChoices = {openrouter:['typesafe/jev-1.14','~typesafe/jev-latest'], typesafe:['jev-1.14.0','jev-preview']};
    const boxKnown = Object.values(boxChoices).flat();
    const boxModel = {name:'model', value:'typesafe/jev-1.14', options:{}};
    const boxProvider = {name:'provider', value:'openrouter'};
    const box = {widgets:[boxModel,boxProvider]};
    update(box, boxChoices);
    check(boxModel.value === 'typesafe/jev-1.14', 'Box retains pinned OpenRouter default');
    boxProvider.value = 'typesafe'; update(box, boxChoices);
    check(boxModel.value === 'jev-1.14.0', 'Box switches pinned provider ID');
    check(boxModel.options.values.join() === boxChoices.typesafe.join(), 'Box only offers direct Box models');
    boxModel.value = 'jev-preview'; boxProvider.value = 'openrouter'; update(box, boxChoices);
    check(boxModel.value === '~typesafe/jev-latest', 'Box preview alias');
    boxProvider.value = 'typesafe'; update(box, boxChoices);
    check(boxModel.value === 'jev-preview', 'Box reverse alias avoids unsupported direct latest');
    boxModel.value = 'future/box'; update(box, boxChoices, true, boxKnown);
    check(boxModel.value === 'future/box', 'Plain Box combo preserves saved IDs without DynamicCombo custom');
    check(!boxKnown.includes('future/box') && !boxChoices.typesafe.includes('future/box'), 'Box schema choices are not mutated');
    return passed;
}

export function checkModelRegistration(extension) {
    let passed = 0;
    const check = (ok, message) => { if (!ok) throw new Error(message); passed++; };
    const choices = {openrouter:['typesafe/jev-1.14','~typesafe/jev-latest'], typesafe:['jev-1.14.0','jev-preview']};
    const options = Object.values(choices).flat();
    for (const nodeName of ['JevFindBox', 'JevReferenceEvaluate', 'JevInterpret', 'JevSkillChoice']) {
        class TestNode {
            constructor() {
                this.widgets = [{name:'model',value:'typesafe/jev-1.14',options:{}},
                    {name:'provider',value:'openrouter'}, {name:'api_key',value:'dummy-router'}];
            }
            onConfigure(config) {
                if (config.savedProvider) {
                    this.widgets[1].value = config.savedProvider;
                    this.widgets[2].value = config.savedKey;
                    this.widgets[1].callback(config.savedProvider);
                }
            }
        }
        extension.beforeRegisterNodeDef(TestNode, {name:nodeName,input:{required:{model:[options,{jev_provider_models:choices}]}}});
        const node = new TestNode(); node.onNodeCreated();
        check(node.widgets[0].options.values.join() === choices.openrouter.join(), `${nodeName} schema initializes`);
        node.widgets[1].callback('openrouter');
        check(node.widgets[2].value === 'dummy-router', `${nodeName} unchanged provider retains its key`);
        node.widgets[1].value = 'typesafe'; node.widgets[1].callback('typesafe');
        check(node.widgets[0].value === 'jev-1.14.0' && node.widgets[2].value === '', `${nodeName} switches model and clears router key`);
        node.widgets[2].value = 'dummy-direct';
        node.widgets[1].value = 'openrouter'; node.widgets[1].callback('openrouter');
        check(node.widgets[2].value === '', `${nodeName} clears direct key in reverse direction`);
        node.onConfigure({savedProvider:'typesafe',savedKey:'dummy-saved-direct'});
        check(node.widgets[2].value === 'dummy-saved-direct', `${nodeName} restores a saved provider/key together`);
        node.widgets[0].value = 'jev-preview'; node.onConfigure({});
        check(node.widgets[0].value === 'jev-preview', `${nodeName} workflow restore preserves model`);
        node.widgets[1].value = 'openrouter'; node.widgets[1].callback('openrouter');
        check(node.widgets[2].value === '', `${nodeName} restored key is cleared on a later user switch`);
        node.widgets[1].value = 'typesafe'; node.widgets[1].callback('typesafe');
        check(node.widgets[2].value === '', `${nodeName} old keys are not cached when switching back`);
    }
    return passed;
}
