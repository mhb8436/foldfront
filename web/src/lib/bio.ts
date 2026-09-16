/**
 * Just enough sequence and structure parsing to tell a person what they
 * pasted or dropped, before a run is started on it.
 *
 * This is not the parser the pipeline uses - that one lives in the original
 * and runs on the server. This only has to be right about the things a
 * summary line says: how many records, how long, which chains.
 */

export interface FastaRecord {
  id: string
  length: number
}

export interface FastaSummary {
  records: FastaRecord[]
  /** Text that had no `>` header at all is probably not FASTA. */
  ok: boolean
}

export function parseFasta(text: string): FastaSummary {
  const records: FastaRecord[] = []
  let current: FastaRecord | null = null
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim()
    if (!line) continue
    if (line.startsWith('>')) {
      current = { id: line.slice(1).split(/\s+/)[0] || `seq${records.length + 1}`, length: 0 }
      records.push(current)
    } else if (current) {
      //  Whitespace and digits inside a sequence line are formatting, not residues.
      current.length += line.replace(/[\s\d]/g, '').length
    }
  }
  return { records, ok: records.length > 0 }
}

export interface PdbSummary {
  chains: string[]
  atoms: number
  ok: boolean
  /** mmCIF, which this does not read. Its ATOM rows are whitespace-tokenised,
      and reading column 22 of one gives you a letter of the residue name. */
  cif: boolean
}

export function looksLikeCif(text: string): boolean {
  return /^data_/m.test(text) || text.includes('_atom_site.')
}

/** Chains and atom count from ATOM/HETATM records. Column 22 is the chain. */
export function summarizePdb(text: string): PdbSummary {
  if (looksLikeCif(text)) return { chains: [], atoms: 0, ok: false, cif: true }
  const chains = new Set<string>()
  let atoms = 0
  for (const line of text.split(/\r?\n/)) {
    if (!line.startsWith('ATOM') && !line.startsWith('HETATM')) continue
    atoms += 1
    const chain = line.charAt(21).trim()
    if (chain && line.startsWith('ATOM')) chains.add(chain)
  }
  return { chains: [...chains].sort(), atoms, ok: atoms > 0, cif: false }
}

/**
 * Whether a field holds a path rather than content.
 *
 * The server decides the same way (engine/payloads.py, StageInput.text), on
 * the trimmed value: a leading `>` or any whitespace means content, and a
 * single unbroken token is a path. Deciding by newline got both edges wrong -
 * a path with Enter after it read as content, a one-line record read as a
 * path. The console has to agree, or it would describe the value one way and
 * the run would read it another.
 */
export function looksLikePath(value: string): boolean {
  const v = value.trim()
  return v.length > 0 && !v.startsWith('>') && !/\s/.test(v)
}

/**
 * A bare amino-acid sequence with no header - the most common thing a
 * biologist pastes. By the rule above it is a path, and the run would fail
 * looking for a file by that name; the field can at least say what is missing.
 */
export function looksLikeBareSequence(value: string): boolean {
  const v = value.trim()
  return v.length >= 10 && !/\s/.test(v) && /^[A-Za-z*-]+$/.test(v)
}

export function formatResidues(records: FastaRecord[]): string {
  if (records.length === 0) return ''
  const lengths = records.map((r) => r.length)
  if (records.length <= 4) return lengths.join(', ') + ' 잔기'
  const min = Math.min(...lengths)
  const max = Math.max(...lengths)
  return min === max ? `${min} 잔기` : `${min}–${max} 잔기`
}
