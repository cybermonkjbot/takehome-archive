import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');

function imageOverride(reference, name) {
  // Require a registry-qualified name, semantic version and actual digest input.
  // Never manufacture a digest for an image that has not been built/published.
  const match = typeof reference === 'string' && reference.match(
    /^([a-z0-9.-]+(?::[0-9]+)?\/[a-z0-9._/-]+):(\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?)@(sha256:[a-f0-9]{64})$/,
  );
  if (!match || reference.includes('.invalid/') || /^sha256:0+$/.test(match[3])) {
    throw new Error(`${name} requires registry/repository:major.minor.patch@sha256:<64 lowercase hex characters>.`);
  }
  return { name, newName: match[1], newTag: match[2], digest: match[3] };
}

function main() {
  const args = process.argv.slice(2);
  if (args.length > 1 || args[0]?.startsWith('-')) {
    throw new Error('Usage: bun run k8s:render [release.json]. This command only renders; deployment is not implemented.');
  }
  const releasePath = args[0] ? resolve(args[0]) : join(root, 'release.json');
  let release;
  try {
    release = JSON.parse(readFileSync(releasePath, 'utf8'));
  } catch {
    throw new Error('Provide release.json using the keys in release.example.json and real published image references.');
  }
  const images = [
    imageOverride(release.webImage, 'registry.invalid/local-stack/web'),
    imageOverride(release.apiImage, 'registry.invalid/local-stack/api'),
    imageOverride(release.logApiImage, 'registry.invalid/local-stack/log-api'),
    imageOverride(release.collectorImage, 'registry.invalid/local-stack/collector'),
  ];

  // An ephemeral overlay keeps per-release values out of the source base.
  // Only this process's explicitly created temporary directory is removed.
  // Kustomize rejects an ancestor base (for example a child overlay importing
  // ".."). Keep this overlay a sibling of k8s so its base is not an ancestor.
  const directory = mkdtempSync(join(root, '.render-'));
  try {
    writeFileSync(join(directory, 'kustomization.yaml'), JSON.stringify({
      apiVersion: 'kustomize.config.k8s.io/v1beta1',
      kind: 'Kustomization',
      resources: ['../k8s'],
      images,
    }));
    const result = spawnSync('kubectl', ['kustomize', directory], {
      encoding: 'utf8',
      maxBuffer: 10 * 1024 * 1024,
    });
    if (result.error) {
      throw new Error('kubectl with Kustomize support must be installed to render. No cluster access is required.');
    }
    if (result.status !== 0) {
      throw new Error(result.stderr.trim() || 'Kustomize rendering failed.');
    }
    if (result.stdout.includes('registry.invalid/')) {
      throw new Error('An unresolved image placeholder remains in the rendered resources.');
    }
    // Inspect parsed container fields, including init/ephemeral containers;
    // formatting or YAML quoting must not bypass the immutable-image gate.
    function checkContainers(value) {
      if (!value || typeof value !== 'object') return;
      for (const [key, child] of Object.entries(value)) {
        if (['containers', 'initContainers', 'ephemeralContainers'].includes(key)) {
          if (!Array.isArray(child)) throw new Error(`Invalid rendered ${key}.`);
          for (const container of child) imageOverride(container.image, 'rendered image');
        }
        checkContainers(child);
      }
    }
    for (const document of result.stdout.split(/^---\s*$/m)) {
      checkContainers(Bun.YAML.parse(document));
    }
    process.stdout.write(result.stdout);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
}

try {
  main();
} catch (error) {
  console.error(`Render blocked: ${error.message}`);
  process.exitCode = 1;
}
