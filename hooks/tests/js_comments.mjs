import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { join } from 'node:path'

const acorn = createRequire(import.meta.url)('internal/deps/acorn/acorn/dist/acorn')

const [root, ...files] = process.argv.slice(2)
const records = []
for (const file of files) {
  acorn.parse(readFileSync(join(root, file), 'utf8'), {
    ecmaVersion: 'latest',
    sourceType: 'module',
    allowReturnOutsideFunction: true,
    locations: true,
    onComment: (block, text, start, end, from) => {
      text.split('\n').forEach((part, offset) => {
        records.push(`${file}\t${from.line + offset}\tcomment\t${part.trim()}\n`)
      })
    },
  })
}
process.stdout.write(records.join(''))
