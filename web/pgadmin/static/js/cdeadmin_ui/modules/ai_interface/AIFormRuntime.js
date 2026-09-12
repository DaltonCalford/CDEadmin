/////////////////////////////////////////////////////////////
// Deterministic runtime rules for normative AI form contracts.
/////////////////////////////////////////////////////////////

const SIMPLE_CONDITION = /^([a-zA-Z0-9_.-]+)\s*(==|!=)\s*([a-zA-Z0-9_.-]+)$/;

function clone(value) {
  if(value === undefined) return undefined;
  return JSON.parse(JSON.stringify(value));
}

export function fieldsForAIForm(form) {
  return form.sections.flatMap((section) => section.fields);
}

export function initialAIFormValues(form, initialValues={}) {
  return Object.fromEntries(fieldsForAIForm(form).map((field) => [
    field.id,
    clone(Object.prototype.hasOwnProperty.call(initialValues, field.id) ?
      initialValues[field.id] : field.default),
  ]));
}

function pathValue(values, path) {
  if(Object.prototype.hasOwnProperty.call(values, path)) return values[path];
  return path.split('.').reduce((value, part) => value?.[part], values);
}

function scalar(value) {
  if(value === 'true') return true;
  if(value === 'false') return false;
  if(value === 'null') return null;
  if(value !== '' && Number.isFinite(Number(value))) return Number(value);
  return value;
}

export function evaluateAICondition(condition, values={}, context={}) {
  const rule = String(condition || '').trim();
  if(rule === 'always') return true;
  if(rule === 'readonly') return false;
  if(Object.prototype.hasOwnProperty.call(context.conditions || {}, rule)) {
    return context.conditions[rule] === true;
  }
  if(rule.includes(' or ')) {
    return rule.split(' or ').some((part) =>
      evaluateAICondition(part, values, context));
  }
  const match = rule.match(SIMPLE_CONDITION);
  if(!match) return false;
  const actual = pathValue({...context, ...values}, match[1]);
  const expected = scalar(match[3]);
  return match[2] === '==' ? actual === expected : actual !== expected;
}

export function visibleAIFormFields(form, values={}, context={}) {
  return fieldsForAIForm(form).filter((field) =>
    evaluateAICondition(field.visibility, values, context));
}

function missing(value, component) {
  if(component === 'Checkbox' || component === 'ToggleSwitch') {
    return typeof value !== 'boolean';
  }
  if(Array.isArray(value)) return value.length === 0;
  return value === null || value === undefined ||
    (typeof value === 'string' && value.trim() === '');
}

export function validateAIFormValues(form, values={}, context={}) {
  const errors = {};
  for(const field of visibleAIFormFields(form, values, context)) {
    const value = values[field.id];
    if(field.required && missing(value, field.component)) {
      errors[field.id] = `${field.label} is required.`;
      continue;
    }
    if(value !== null && value !== undefined && value !== '' &&
        ['NumberField', 'UnitNumberField'].includes(field.component) &&
        !Number.isFinite(Number(value))) {
      errors[field.id] = `${field.label} must be a number.`;
      continue;
    }
    const validator = context.fieldValidators?.[field.id];
    const result = validator?.(value, values, field);
    if(result === false) errors[field.id] = field.validation || `${field.label} is invalid.`;
    else if(typeof result === 'string' && result) errors[field.id] = result;
  }
  return {valid: Object.keys(errors).length === 0, errors};
}

export function redactedAIFormValues(form, values={}) {
  const secretIds = new Set(fieldsForAIForm(form)
    .filter((field) => field.secret).map((field) => field.id));
  return Object.fromEntries(Object.entries(values)
    .filter(([fieldId]) => !secretIds.has(fieldId))
    .map(([fieldId, value]) => [fieldId, clone(value)]));
}

export function transientAIFormSubmission(form, values={}) {
  return Object.freeze({
    formId: form.form_id,
    values: clone(values),
    persistedValues: redactedAIFormValues(form, values),
    secretFieldIds: fieldsForAIForm(form)
      .filter((field) => field.secret).map((field) => field.id),
  });
}

export function isAIFormActionEnabled(action, form, values, validation,
  context={}) {
  const availability = context.actionAvailability || {};
  if(Object.prototype.hasOwnProperty.call(availability, action.id)) {
    return availability[action.id] === true;
  }
  if(action.command && Object.prototype.hasOwnProperty.call(
    availability, action.command)) return availability[action.command] === true;
  if(Object.prototype.hasOwnProperty.call(
    context.conditions || {}, action.enabled_when)) {
    return context.conditions[action.enabled_when] === true;
  }
  const rule = action.enabled_when.toLowerCase();
  if(rule === 'always') return true;
  if(rule === 'query non-empty') {
    return String(values.query_text ?? values.query ?? '').trim().length > 0;
  }
  if(rule === 'reason set') return !missing(values.reason, 'TextArea');
  if(rule === 'note set') return !missing(values.note, 'TextArea');
  if(rule === 'profile exists') return context.profileExists === true;
  if(rule.includes('permission') && context.hasActionPermission !== true) {
    return false;
  }
  if(rule.includes('test not failed') && context.testFailed === true) return false;
  if(rule.includes('valid') || rule.includes('validation') ||
      rule.includes('required fields') || rule.includes('configuration') ||
      rule.includes('within site limits') || rule.includes('no conflicts') ||
      rule.includes('weights sum')) return validation.valid;
  return false;
}
