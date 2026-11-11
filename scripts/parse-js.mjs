// Syntax-check the plugin's JavaScript and TypeScript sources on any Node from 22.13.
// Usage: node scripts/parse-js.mjs workflow <file> | adapter <file>...
//
// `node --check` cannot do either job on Node 24: it rejects the workflow's top-level `return`,
// and it does not strip the types of a .ts file. So each is converted first, explicitly.
//   workflow  The Workflow runtime runs the script as the body of an async function, where
//             `return` and `await` are legal. Compile the file as exactly that, with the one
//             `export` (`export const meta`) turned into a plain declaration. Nothing runs.
//   adapter   Strip the types with node:module's stripTypeScriptTypes, then hand the
//             JavaScript to `node --check` as an ES module on stdin. Types are replaced by
//             spaces, so an error points at the original line and column.
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
