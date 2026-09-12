import {createHash} from 'crypto';
import * as fs from 'fs';
import * as path from 'path';
import Ajv2020 from 'ajv/dist/2020';
import {SCREEN_SPEC_SCHEMA} from 'sources/cdeadmin_ui';
import {
  AI_COMMAND_CATALOG, AI_DISCOVERY_CONTRACT_SUMMARY,
  AI_DISCOVERY_STATE_MACHINES, AI_INTERFACE_MANIFEST,
  AI_PERMISSION_CATALOG, DISCOVERY_COMMAND_CATALOG,
  DISCOVERY_INTELLIGENCE_MANIFEST, DISCOVERY_PERMISSION_CATALOG,
  DISCOVERY_RANKING_DEFAULTS, validateAIDiscoveryContractPackage,
} from 'sources/cdeadmin_ui/specifications/ai_discovery_zero_grey';

const PACKAGE_ROOT = path.resolve(
  __dirname,
  '../../../pgadmin/static/js/cdeadmin_ui/specifications/' +
  'ai_discovery_zero_grey'
);

function filesBelow(directory) {
  return fs.readdirSync(directory, {withFileTypes: true}).flatMap((entry) => {
    const filename = path.join(directory, entry.name);
    return entry.isDirectory() ? filesBelow(filename) : [filename];
  }).sort();
}

function readJson(filename) {
  return JSON.parse(fs.readFileSync(filename, 'utf8'));
}

function relativeFiles(directory, extension) {
  return filesBelow(directory).filter((filename) => filename.endsWith(extension));
}

describe('AI Interface and Discovery normative contract package', () => {
  test('preserves every package-manifest resource byte for byte', () => {
    const manifest = readJson(path.join(PACKAGE_ROOT, 'PACKAGE_MANIFEST.json'));
    expect(manifest.files).toHaveLength(179);
    for(const item of manifest.files) {
      const content = fs.readFileSync(path.join(PACKAGE_ROOT, item.path));
      expect(content).toHaveLength(item.bytes);
      expect(createHash('sha256').update(content).digest('hex')).toBe(item.sha256);
    }
  });

  test('parses the exact JSON and SVG corpus without external SVG links', () => {
    const report = readJson(path.join(PACKAGE_ROOT, 'VALIDATION_REPORT.json'));
    const jsonFiles = relativeFiles(path.join(PACKAGE_ROOT, 'machine'), '.json');
    const svgFiles = relativeFiles(path.join(PACKAGE_ROOT, 'svg'), '.svg');
    expect(jsonFiles).toHaveLength(report.jsonFilesParsed);
    expect(svgFiles).toHaveLength(report.svgFilesParsed);
    expect(report.errors).toEqual([]);
    jsonFiles.forEach((filename) => expect(() => readJson(filename)).not.toThrow());
    for(const filename of svgFiles) {
      const content = fs.readFileSync(filename, 'utf8');
      expect(content).toMatch(/<svg\b/);
      expect(content).not.toMatch(/(?:href|src)=["'](?:https?:|file:|\/\/)/i);
    }
  });

  test('validates every form, screen and standalone JSON schema', () => {
    const ajv = new Ajv2020({allErrors: true, strict: false});
    const formSchema = readJson(path.join(
      PACKAGE_ROOT, 'machine/schemas/form-contract.schema.json'
    ));
    const validateForm = ajv.compile(formSchema);
    const forms = relativeFiles(path.join(PACKAGE_ROOT, 'machine/forms'), '.json');
    const screens = relativeFiles(path.join(PACKAGE_ROOT, 'machine/screens'), '.json');
    expect(forms).toHaveLength(53);
    expect(screens).toHaveLength(64);
    for(const filename of forms) {
      const form = readJson(filename);
      expect(validateForm(form)).toBe(true);
      expect(form.spec_gaps).toEqual([]);
    }
    const validateScreen = ajv.compile(SCREEN_SPEC_SCHEMA);
    for(const filename of screens) {
      const screen = readJson(filename);
      expect(validateScreen(screen)).toBe(true);
      expect(screen.spec_gaps).toEqual([]);
    }
    const schemas = relativeFiles(
      path.join(PACKAGE_ROOT, 'machine/schemas'), '.json'
    );
    expect(schemas).toHaveLength(9);
    schemas.forEach((filename) => expect(() => new Ajv2020({
      strict: false,
    }).compile(readJson(filename))).not.toThrow());
  });

  test('reconciles exact module, command, permission and state identities', () => {
    expect(validateAIDiscoveryContractPackage()).toEqual({
      specificationVersion: '1.0',
      modules: ['cdeadmin.ai_interface', 'cdeadmin.discovery_intelligence'],
      screens: 64,
      forms: 53,
      commands: 96,
      permissions: 26,
      assetTypes: 19,
      connectorTypes: 7,
      stateMachines: 7,
    });
    expect(AI_COMMAND_CATALOG.commands.map((item) => item.id)).toEqual(
      AI_INTERFACE_MANIFEST.commands
    );
    expect(DISCOVERY_COMMAND_CATALOG.commands.map((item) => item.id)).toEqual(
      DISCOVERY_INTELLIGENCE_MANIFEST.commands
    );
    expect(AI_PERMISSION_CATALOG.permissions.map((item) => item[0])).toEqual(
      AI_INTERFACE_MANIFEST.permissions
    );
    expect(DISCOVERY_PERMISSION_CATALOG.permissions.map((item) => item[0])).toEqual(
      DISCOVERY_INTELLIGENCE_MANIFEST.permissions
    );
    expect(Object.keys(AI_DISCOVERY_STATE_MACHINES)).toHaveLength(7);
    expect(Object.values(DISCOVERY_RANKING_DEFAULTS.balanced.weights).reduce(
      (total, value) => total + value, 0
    )).toBeCloseTo(1, 12);
  });

  test('exports deeply immutable normative machine contracts', () => {
    expect(Object.isFrozen(AI_DISCOVERY_CONTRACT_SUMMARY)).toBe(true);
    expect(Object.isFrozen(AI_INTERFACE_MANIFEST.commands)).toBe(true);
    expect(Object.isFrozen(DISCOVERY_RANKING_DEFAULTS.balanced.weights)).toBe(true);
    expect(() => AI_INTERFACE_MANIFEST.commands.push('ai.guessed')).toThrow();
  });
});
