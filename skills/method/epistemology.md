# The merge rule

A change is merged when no criticism of it is open. Everything else in the method is machinery for raising criticisms early, deterministically where possible, and recording the fate of each.

Six words carry the rule; the stance in [SKILL.md](SKILL.md) says how to work by it. The words come from Popper and Deutsch by way of the `conjecture:lexicon` skill, which defines the full set; the model already knows them, so the method uses each word and never re-explains it.

- **conjecture**: a bold guess that answers a problem, stated so it can be wrong. A change, a design, a diagnosis and a test are each one.
- **alternative**: the strongest competing answer, put the way its best supporter would put it. A diagnosis with no alternative on the table has refuted nothing yet.
- **refuter**: what would show the conjecture wrong, written before anyone looks. A test is a refuter made executable; a gate is a refuter made deterministic.
- **criticism**: an attempt to find where the conjecture is wrong, one claim at a time. It is answered by **revising** the conjecture so the criticism no longer applies, or by a **counter** that explains why it misses. Dismissal, reweighting and silence are not answers.
- **open**: a criticism nobody has answered yet. A counter answers it only while the counter itself has no open criticism. A red gate is an open criticism; so are a finding, a dispute and an empty section of the pull request.
- **stands**: no criticism is open. That is the strongest status there is: a green suite is unrefuted so far, never verified.

## Where each word is written down

| Word | Record | Who checks |
| --- | --- | --- |
| conjecture, refuter | the Conjecture section of the pull request; the Refutation criterion of every ADR; the brief's conjecture about the solution | the conformance gate checks the section exists; the reviewer reads it |
| alternative | the Considered options of an ADR; the alternative explanation a diagnosis names before the fix | the reviewer |
| criticism | a red gate, a critic finding, a review issue, a tool finding | the gates, the critic, the reviewer |
| counter | a dispute against a gate; a register entry with its reason; an answer in the pull request's Criticism section | the owner |
| open, stands | the pipeline's forced verdict; the merge | the script, then a human |

## Three cautions

- The method is criticisable in writing, never in action: a criticism of a rule is a dispute, and the owner decides.
- Narrowing a claim until no criticism applies is a loophole: the narrowed claim must still solve the original problem.
- "Verified" and "proved" keep their precise meaning in formal methods, relative to a model that is itself a conjecture.

The research behind the stance, with sources, is in `docs/research/critical-rationalism.md` of the plugin.
