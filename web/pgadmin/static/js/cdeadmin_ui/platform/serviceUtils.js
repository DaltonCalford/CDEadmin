/////////////////////////////////////////////////////////////
// CDEadmin shared platform-service invariants.
/////////////////////////////////////////////////////////////

export const SECRET_KEY = /(?:password|passwd|secret|token|private.?key|credential)/i;

export function immutable(value) {
  if(!value || typeof value !== 'object' || Object.isFrozen(value)) return value;
  Object.values(value).forEach(immutable);
  return Object.freeze(value);
}

export function plainObject(value, label) {
  if(!value || Array.isArray(value) || typeof value !== 'object') {
    throw new TypeError(`${label} must be an object.`);
  }
  return value;
}

export function noRawSecrets(value, path='value') {
  if(!value || typeof value !== 'object') return;
  for(const [key, child] of Object.entries(value)) {
    if(SECRET_KEY.test(key) && key !== 'credentialRef' && key !== 'credential_ref') {
      throw new TypeError(`Raw credential field is forbidden at ${path}.${key}.`);
    }
    noRawSecrets(child, `${path}.${key}`);
  }
}

export function platformValue(value, label, maximum=1024) {
  const result = String(value ?? '').trim();
  const hasControl = [...result].some((character) => character.charCodeAt(0) < 32);
  if(!result || result.length > maximum || hasControl) {
    throw new TypeError(`${label} is invalid.`);
  }
  return result;
}

export function abortError(message='Operation was cancelled.') {
  const error = new Error(message);
  error.name = 'AbortError';
  return error;
}
