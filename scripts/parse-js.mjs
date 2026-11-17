import { spawnSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { stripTypeScriptTypes } from 'node:module'

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor

function workflow(source) {
  new AsyncFunction(source.replace(/^export\s+/gm, ''))
}

function adapter(source) {
  const checked = spawnSync(process.execPath, ['--check', '--input-type=module'], {
    input: stripTypeScriptTypes(source, { mode: 'strip' }),
    encoding: 'utf8',
  })
  if (checked.status !== 0) throw new SyntaxError(checked.stderr.trim())
}

const KINDS = { workflow, adapter }
const [kind, ...files] = process.argv.slice(2)
if (!Object.hasOwn(KINDS, kind) || files.length === 0) {
  console.error('usage: node scripts/parse-js.mjs workflow <file> | adapter <file>...')
  process.exit(2)
}
let failed = false
for (const file of files) {
  try {
    KINDS[kind](readFileSync(file, 'utf8'))
  } catch (error) {
    failed = true
    console.error(`${file}: ${error.message}`)
  }
}
process.exit(failed ? 1 : 0)
