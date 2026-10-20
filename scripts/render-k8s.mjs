import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const pinnedBaseDigests = new Set(
  ['app/Dockerfile', 'api/Dockerfile', 'collector/Dockerfile']
    .flatMap((path) => [...readFileSync(join(root, path), 'utf8').matchAll(/@sha256:([a-f0-9]{64})/g)])
    .map((match) => `sha256:${match[1]}`),
);

function imageOverride(reference, name) {
  // Require a registry-qualified name, semantic version and actual digest input.
  // Never manufacture a digest for an image that has not been built/published.
  const match = typeof reference === 'string' && reference.match(
    /^([a-z0-9.-]+(?::[0-9]+)?\/[a-z0-9._/-]+):(\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?)@(sha256:[a-f0-9]{64})$/,
  );
  if (!match || reference.includes('.invalid/') || /^sha256:0+$/.test(match[3])) {
    throw new Error(`${name} requires registry/repository:major.minor.patch@sha256:<64 lowercase hex characters>.`);
  }
  if (pinnedBaseDigests.has(match[3])) {
    throw new Error(`${name} uses a Dockerfile base-image digest; provide the digest of the built and published workload image.`);
  }
  return { name, newName: match[1], newTag: match[2], digest: match[3] };
}

function cloudNativePgImage(reference) {
  const match = typeof reference === 'string' && reference.match(
    /^ghcr\.io\/cloudnative-pg\/postgresql:(\d+\.\d+)-(minimal|standard|system)-(bookworm|trixie)@(sha256:[a-f0-9]{64})$/,
  );
  if (!match || /^sha256:0+$/.test(match[4])) {
    throw new Error('CloudNativePG Cluster spec.imageName requires an exact upstream version, image flavor, OS version, and nonzero SHA256 digest.');
  }
}

function main() {
  const args = process.argv.slice(2);
  if (args.length !== 1 || args[0]?.startsWith('-')) {
    throw new Error('Usage: bun run k8s:render <release-input.json>. Supply published immutable image references; this command only renders.');
  }
  const releasePath = resolve(args[0]);
  let release;
  try {
    release = JSON.parse(readFileSync(releasePath, 'utf8'));
  } catch {
    throw new Error('The release input must be readable JSON with webImage, apiImage, and collectorImage references.');
  }
  const images = [
    imageOverride(release.webImage, 'registry.invalid/local-stack/web'),
    imageOverride(release.apiImage, 'registry.invalid/local-stack/api'),
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
      throw new Error('An unsubstituted registry.invalid image reference remains in the rendered resources.');
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
      const resource = Bun.YAML.parse(document);
      if (resource?.apiVersion === 'postgresql.cnpg.io/v1' && resource?.kind === 'Cluster') {
        cloudNativePgImage(resource.spec?.imageName);
      }
      checkContainers(resource);
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
