# The merge rule

A change is merged when no criticism of it is open. Six words carry the rule: this page defines each and says where it is written down, and the stance in [SKILL.md](SKILL.md) says how to work by it.

## The six words

The words come from Popper and Deutsch.

- **conjecture**: a bold guess that answers a problem, stated so it can be wrong. A change, a design, a diagnosis and a test are each one.
- **alternative**: the strongest competing answer, put the way its best supporter would put it. A diagnosis with no alternative on the table has refuted nothing yet.
- **refuter**: what would show the conjecture wrong, written before anyone looks.
- **criticism**: an attempt to find where the conjecture is wrong, one claim at a time. It is answered by **revising** the conjecture so the criticism no longer applies, or by a **counter** that explains why it misses.
- **open**: a criticism nobody has answered yet. A counter answers it only while the counter itself has no open criticism. A red gate is an open criticism, and so are a finding, a dispute and an empty section of the pull request.
- **stands**: no criticism is open. That is the strongest status there is.

## Where each word is written down

| Word | Record | Who checks |
| --- | --- | --- |
| conjecture, refuter | the Conjecture section of the pull request; the Refutation criterion of every ADR; the ticket's Conjecture section | the conformance gate checks the pull request's and the ADR's; the feature preflight checks the ticket's; the reviewer reads it |
| alternative | the Considered options of an ADR; the alternative explanation a diagnosis names before the fix | the reviewer |
| criticism | a red gate, a critic finding, a review issue, a tool finding | the gates, the critic, the reviewer |
| counter | a dispute against a gate; a register entry with its reason; an answer in the pull request's Criticism section | the owner |
| open, stands | the pipeline's forced verdict; the merge | the script, then the merge judge, and the owner for what it does not pass |

## Cautions

- Narrowing a claim until no criticism applies is a loophole: the narrowed claim must still solve the original problem.
- "Verified" and "proved" keep their precise meaning in formal methods, relative to a model that is itself a conjecture.

For the sources behind the stance, read `docs/research/critical-rationalism.md` of the plugin.
