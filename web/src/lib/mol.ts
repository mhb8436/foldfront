/**
 * One lazy loader for 3Dmol.js.
 *
 * 3Dmol is large, so it is code-split and arrives only when a structure is
 * first opened. Shared here so the structure viewer and the residue picker
 * pull from a single cached import rather than bundling it twice.
 *
 * Bundled, not from a CDN: on an isolated network the CDN is unreachable and
 * the viewer would never appear - the same trap as the webfonts.
 */

export type Mol = typeof import('3dmol')
export type Viewer = ReturnType<Mol['createViewer']>

let loader: Promise<Mol> | null = null

export function load3Dmol(): Promise<Mol> {
  loader ??= import('3dmol').then((m) => (m.default ?? m) as Mol)
  return loader
}
