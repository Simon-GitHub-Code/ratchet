# Critical rationalism as an epistemology for agents that build software

Research notes for the second pillar of the founding document. Written 2026-11-07. Every claim is cited; where a source is secondary or the primary text was unavailable, the note says so.

## 1. Sources

| Source | What it is | What it contributed |
| --- | --- | --- |
| [Veritula home](https://veritula.com) | Dennis Hackethal's site "where people post ideas and criticisms, and the site works out which ideas can be rationally adopted". Tagline "A Bit of Truth". | The core rule (pending criticism, recursive), the vocabulary, the blue/red idea/criticism display, bounties for criticism of software claimed bug-free. |
| [How does Veritula work?](https://veritula.com/discussions/how-does-veritula-work) | The site's own rules discussion, itself held as criticisable ideas. | Exact mechanics: immutable ideas, revise/counter-criticise/accept defeat, "Battle-tested", ancillary ideas, supersedes, the `should_adopt?` pseudocode. |
| [Rational Decision-Making (#5748)](https://veritula.com/ideas/5748/latest) | The rule as a single idea, 8 versions, with its standing criticisms. | The canonical wording of the adoption rule and the strongest live objections (GR/QM, "rewording loophole"). |
| [What Makes One Idea Better Than Another?](https://veritula.com/discussions/what-makes-one-idea-better-than-another) | Debate between Hackethal and Benjamin Davies on hard-to-vary vs pending criticisms. | The "prefer by pending criticisms, not by scores" position; verisimilitude rejected via Tichý/Miller; severity is itself a guess open to criticism. |
| [Fallibilism vs. Cynicism](https://veritula.com/discussions/fallibilism-vs-cynicism) | Discussion on absolute truth vs certainty. | "We can never know that we have reached the truth" does not mean nothing is true; ideas with no pending criticism are assumed true. |
| [Veritula MCP](https://veritula.com/mcp) and [Bounties](https://veritula.com/bounties) | Tooling and incentive pages. | Criticisms are "pending or addressed"; bounty rule: paid if the criticism "has no pending criticisms by the deadline". |
| [Hackethal, "Hard to Vary or Hardly Usable?"](https://blog.dennishackethal.com/posts/hard-to-vary-or-hardly-usable) | Blog essay (updated 2026-09-06); same title as the Conjecture Con 2026 talk (YouTube 3LfEPLmjxNk, not fetched). | The critique of Deutsch's criterion and the replacement rule. The written record of the talk's position. |
| [Veritula bounty #8](https://veritula.com/bounties/8) | USD 3,600 bounty for a program that ranks two explanations by hard-to-vary-ness. | The "counting variants" proposal and its status. |
| [Hackethal, "Don't Take Fallibilism Too Far"](https://blog.dennishackethal.com/posts/don-t-take-fallibilism-too-far), ["Crypto-Fallibilism"](https://blog.dennishackethal.com/posts/crypto-fallibilism), ["Choosing between Theories"](https://blog.dennishackethal.com/posts/choosing-between-theories), ["Error Prevention"](https://blog.dennishackethal.com/posts/error-prevention-error-correction-s-forgotten-b), ["Executable Ideas"](https://blog.dennishackethal.com/posts/executable-ideas) | Essays (several marked "older post, I don't necessarily endorse it anymore"). | Truth vs certainty; induction "doesn't work"; symmetry-breaking by elimination; "trust me, it's different this time" is not an answer; "if you can't program it, you haven't understood it". |
| [Hackethal, "The Structure of Rational Thought" (abstract)](https://independent.academia.edu/DennisHackethal) | Paper abstract (page returned 403; abstract via search). | Ideas and criticisms as a DAG; "an idea is rationally preferable iff it has no pending criticisms"; computable. |
| [Hackethal wixsite essay](https://dennishackethal079.wixsite.com/dennis-hackethal/post/dennis-hackethal-and-veritula-turning-critical-rationalism-into-a-practical-tool) | Short profile of Veritula. | "a programmatic implementation of Karl Popper's philosophy of critical rationalism". |
| [Farley, "Science and Software Development"](https://www.davefarley.net/?p=278) | 2019 post on *The Beginning of Infinity*. | Tests as "a falsification mechanism"; code as "our best current theory". |
| [Farley, "What is Modern Software Engineering?"](https://www.davefarley.net/?p=352), [TDD basics](https://www.davefarley.net/?p=180) | Companion posts. | "optimise to be great at learning"; "find the problem, and fix it quickly"; a test-first failure "is testing the test". |
| [Deutsch, "Simple refutation of the 'Bayesian' philosophy of science"](https://daviddeutsch.org.uk/?p=628) | Blog note. | Exactly what is rejected and the negation/conjunction arguments. |
| [Deutsch, "The logic of experimental tests" (arXiv 1508.02048)](https://arxiv.org/abs/1508.02048) | Paper; body read from the PDF. | Tests defined without credences; "problematic" vs "refuted"; refutation needs a rival; "strictly explanationless predictions cannot exist". |
| [SEP, Karl Popper](https://plato.stanford.edu/entries/popper/) | Reference article. | Induction, demarcation, corroboration, verisimilitude's failure, the swamp. |
| [Wikipedia, W. W. Bartley III](https://en.wikipedia.org/wiki/W._W._Bartley_III); Hauptli, ["A Dilemma for Bartley's Pancritical Rationalism"](https://philarchive.org/archive/HAUADF) | Secondary. | PCR "holds all positions open to criticism, including PCR"; the self-reference objection. |
| Miller, *Critical Rationalism: A Restatement and Defence* (publisher/library descriptions only) | Secondary. | "rationality does not depend on good reasons"; critique of Bayesianism. |
| Popper and Miller (1983), via [Eells's critique](https://api.philpapers.org/rec/EELOTA) | Secondary. | "all probabilistic support is purely deductive". |

Deutsch's *The Beginning of Infinity* (BoI) is cited from memory of the text and corroborated by Farley and Hackethal's translation work; quotations from it are kept to the well-known formulations.

## 2. Core concepts

**Fallibilism.** All knowledge is conjectural; there is no criterion of truth that certifies an idea. Popper: "we can never know that we have reached the truth", and separately "truth, which is objective and absolute" versus "certainty, which is subjective" (both quoted by [Hackethal](https://blog.dennishackethal.com/posts/don-t-take-fallibilism-too-far)). Caveat: Hackethal's "epistemological cynics" take it too far: "all ideas are false" is self-refuting and makes error meaningless. Fallibilism says we may be wrong, not that we are.

**Conjecture.** A proposed solution to a problem, put forward boldly and held tentatively. Popper's schema P1 -> TT -> EE -> P2 ([SEP](https://plato.stanford.edu/entries/popper/)). Deutsch: "Conjecture, and the correction of apparent errors and deficiencies, are the only processes at work" and "no such event as 'accepting' a theory, distinct from conjecturing it in the first place, ever happens" ([arXiv 1508.02048](https://arxiv.org/abs/1508.02048)). Caveat: "most conjectures are themselves errors, and there need not be a right error to make next" (same).

**Criticism.** An idea that objects to another idea; the only mechanism by which conjectures are improved or discarded. Veritula: "Any idea can be criticized, and any criticism can also be criticized" ([veritula.com](https://veritula.com)). Caveat: a criticism is not an attack; de Wit's rule quoted on Veritula is "Criticize, don't defend or attack".

**Refutation vs confirmation.** Observations consistent with a theory are not support: "experimental results consistent with a theory T do not constitute support for T. That is because they are merely explicanda" ([Deutsch](https://arxiv.org/abs/1508.02048)). Popper allows corroboration only as "the positive result of a genuinely 'risky' prediction", and a theory is "corroborated (but never verified)" ([SEP](https://plato.stanford.edu/entries/popper/)). Caveat: Lakatos's point that a failed prediction may refute an auxiliary assumption, not the theory (Galle and Neptune), so refutation is itself tentative.

**The problem of induction and the inductivist trap.** No number of instances establishes a universal: "it is logically impossible to verify a universal proposition by reference to experience" ([SEP](https://plato.stanford.edu/entries/popper/)). Hackethal: "epistemologically, induction doesn't work", and in the urn example "the drawn beads' colors are no indication whatsoever for the next bead's" ([Choosing between Theories](https://blog.dennishackethal.com/posts/choosing-between-theories)). The "inductivist trap" is not a term in any source; it names the habit of treating repetition as evidence. Caveat: people do use induction psychologically, and Hackethal concedes this; the claim is that it is not a method.

**Justified true belief, and why justification is rejected.** Knowledge is "neither justified nor believed" (Miller, via [Wikipedia](https://en.wikipedia.org/wiki/Critical_rationalism)). Popper's basic statements are accepted by decision: "a basic statement cannot be justified by them, no more than by thumping the table"; the structure of science is piles driven into a swamp with no "firm ground" ([SEP](https://plato.stanford.edu/entries/popper/)). Deutsch quoted by Hackethal: "justified belief, is a chimera". Miller's thesis: "rationality does not depend on good reasons" and deductive support begs the question because the conclusion is already in the premises. Caveat (Kieren, on Hackethal's blog): "Justified doesn't have to mean something is proven infallibly true", it can just mean there are reasons in favour. The critical rationalist reply is that those reasons are either criticisms of rivals or nothing.

**Error correction as the engine of progress.** Deutsch: "the methodology of science is to seek out, and apparently to correct, apparent flaws, conflicts or deficiencies in our explanations" ([arXiv](https://arxiv.org/abs/1508.02048)); BoI's moral imperative "not to destroy the means of correcting mistakes" (quoted by [Hackethal](https://blog.dennishackethal.com/posts/honesty-and-popperian-epistemology)). Caveat: Hackethal argues correction is "only half the battle": known errors should be prevented, and "trust me, it's different this time" does not revive a criticised idea ([Error Prevention](https://blog.dennishackethal.com/posts/error-prevention-error-correction-s-forgotten-b)).

**Good explanations are hard to vary.** BoI ch. 1: a good explanation is hard to vary while still accounting for what it purports to account for. Deutsch's operational version: an explanation is bad to the extent that "(i) it seems not to account for its explicanda; or (ii) it seems to conflict with explanations that are otherwise good; or (iii) it could easily be adapted to account for anything (so it explains nothing)" ([arXiv](https://arxiv.org/abs/1508.02048)). Caveat: Hackethal's critique, section 4 below.

**Problems are soluble; problems are inevitable.** BoI's twin slogans: because knowledge is always incomplete, new problems always arise; because any evil is due to lack of knowledge and knowledge is creatable, any problem that does not violate the laws of physics is soluble. Deutsch: "scientific methodology consists of locating and then solving problems" ([arXiv](https://arxiv.org/abs/1508.02048)). Caveat: soluble does not mean soluble by this attempt, by this agent, or at this cost.

**The Principle of Optimism.** BoI ch. 9: "All evils are caused by insufficient knowledge." Deutsch distinguishes it from blind optimism ("proceeding as if we know bad outcomes will not happen"); it is "a way of explaining failure, not prophesying success" (as summarised by reviewers; wording approximate). Caveat: it licenses looking for the explanation, not repeating the same action.

**Verisimilitude.** Popper's "closeness to the truth" via truth-content and falsity-content. Tichý and Miller (1974) showed the comparison "can be satisfied only when the theories are true"; Popper conceded the formal error ([SEP](https://plato.stanford.edu/entries/popper/)). Hackethal: "Popper's definition of verisimilitude leads to a contradiction" and rejects "closer to truth" talk unless formalised ([Veritula](https://veritula.com/discussions/what-makes-one-idea-better-than-another)). Deutsch keeps a partial ordering: "'truer' and 'better' are both only partial orderings". Caveat: use "nearer to the truth" as an aspiration, never as a measure.

**Avoiding Bayesianism.** What is rejected: Deutsch's definition, "(1) the objective of science is, or should be, to increase our 'credence' for true theories, and that (2) the credences held by a rational thinker obey the probability calculus" ([Deutsch](https://daviddeutsch.org.uk/?p=628)). Arguments: the negation of an explanation "is not an explanation at all", so no credence-like quantity q with 1-q for the negation fits; the conjunction of QM and GR "is provably false, and therefore has zero probability. Yet it embodies some understanding of the world". Popper and Miller (1983): "all probabilistic support is purely deductive" ([via Eells](https://api.philpapers.org/rec/EELOTA)). The alternative: "'Bayesian' credences are eliminated from the methodology of science, but rational expectations are given an objective meaning independent of subjective beliefs": to expect x under T means that if not-x occurred, "T would become (more) problematic" ([arXiv](https://arxiv.org/abs/1508.02048)). Hackethal: "they'd be wrong to mistake their feelings about the theory for a truth criterion (or probability criterion)". Decide between rivals by criticism; adopt the one with no pending criticism. Caveat: Deutsch admits Bayesian statistics works "for certain types of problems" (Walker interview summary); the rejection is of credences as the epistemology, not of probability as a tool inside a theory.

**Criticism must be itself criticisable.** Veritula: a criticism is pending only if it "can't have any pending criticisms itself, and so on, in a deeply nested fashion" ([#5114](https://veritula.com/discussions/how-does-veritula-work)). Hackethal's reply to "how do we decide which test is more severe?": "just guess" and proceed "if nobody has a pending criticism of the guess". Caveat: the recursion has no floor; a flood of counter-criticisms can keep a bad idea alive, which Veritula's own meta-discussion admits ("no safeguards exist against bad actors").

**Tradition of criticism.** Popper: "the growth of knowledge depends entirely upon disagreement" (quoted on [veritula.com](https://veritula.com)); "the rational decision is always: adopt critical methods which have themselves withstood severe criticism" (quoted by [Hackethal](https://blog.dennishackethal.com/posts/choosing-between-theories)). Caveat: a tradition can ossify into a ritual of nitpicking; Veritula's meta discussion repeatedly worries about "bulk criticism" and sabotage.

**Tests as attempted refutations.** Deutsch: "A test of a theory is an experiment whose result could make the theory problematic"; a crucial test is one "such that every possible result will make all but one of those theories problematic" ([arXiv](https://arxiv.org/abs/1508.02048)). Farley: automated testing "is best thought-of, used, as a falsification mechanism" ([Farley](https://www.davefarley.net/?p=278)). Caveat: Deutsch's "problematic" is weaker than "refuted": "in the absence of a good rival explanation, an explanatory theory cannot be refuted by experiment: at most it can be made problematic".

**Standing criticism and neutralising (Veritula).** A criticism "stands" (is pending) until addressed by revision or by a counter-criticism that is itself unproblematic; a problematic counter-criticism cannot neutralise. Section 3 gives the rules. Caveat: "What counts as 'addressing' a criticism?" is an open idea (#3022); "nuh-uh" would itself be criticised "for lacking substance immediately".

**Explanations vs predictions.** Deutsch: "any theory, to be testable, must at least explain why a particular experiment would constitute a test, because it must explain which variables would be confounding factors and which wouldn't. So strictly explanationless predictions cannot exist" ([arXiv](https://arxiv.org/abs/1508.02048), fn 2). Hackethal's own definition: "An explanation is a statement about how to solve a problem" ([What's an Explanation?](https://blog.dennishackethal.com/posts/what-s-an-explanation)). Caveat: an explanation can be false and still be an explanation (Newton), so "it explains" is never "it is true".

**The beginning of infinity.** Unbounded improvement: there is no final state; knowledge growth is "a cycle of problems, proposed solutions, criticism, and further problems, with no final, unchangeable state" ([wixsite essay](https://dennishackethal079.wixsite.com/dennis-hackethal/post/dennis-hackethal-and-veritula-turning-critical-rationalism-into-a-practical-tool)). Caveat: unbounded in principle; any particular project has a budget.

**Authority and justificationism.** Deutsch's "renunciation of authority": the traditional role of methodology was to provide "confirmation of their truth, justification, probability, credence, reason for believing, reason for relying upon, or 'secure foundations'"; "no such authority exists, nor is needed" ([arXiv](https://arxiv.org/abs/1508.02048), sec. 4). Bartley's pancritical rationalism "holds all positions open to criticism, including PCR", decoupling criticism from justification ([Wikipedia](https://en.wikipedia.org/wiki/W._W._Bartley_III)). Caveat: Hauptli's dilemma, that PCR must either exempt its own commitment or admit non-tentative criticism; Popper never accepted Bartley's view.

**Rational decision = eliminating options by criticism, not weighing evidence.** Hackethal: the critical approach aims at "eliminating one of the conflicting theories, not elevating the other" ([Choosing between Theories](https://blog.dennishackethal.com/posts/choosing-between-theories)); "We can rationally prefer A over B if A has no pending criticisms whereas B has at least one" ([Veritula](https://veritula.com/discussions/what-makes-one-idea-better-than-another)). Davies: "Progress does not require a complete ranking of every live explanation." Caveat: when several ideas survive, Hackethal allows adopting at random or preferring the most battle-tested; the method under-determines the choice and says so.

## 3. Veritula's mechanics

Source: [How does Veritula work?](https://veritula.com/discussions/how-does-veritula-work), [#5748](https://veritula.com/ideas/5748/latest), [home](https://veritula.com), [MCP](https://veritula.com/mcp), [bounties](https://veritula.com/bounties).

Structure.
- An **idea** is "discrete and immutable". A **criticism** is an idea that objects to another idea, so criticisms follow the same rules and form a tree; a criticism of a criticism is a **counter-criticism**. Ideas display blue, criticisms red.
- Non-criticism replies (questions, remarks) are **ancillary ideas**; they "do not invert their respective parents' statuses". The two actions on any idea are "Comment..." and "Criticize...".
- "Each criticism should be submitted separately, so the author must answer each one individually." A criticism that applies to several ideas is submitted once per idea. Criticisms "should be formulated as concisely as possible".

The status rule (quoted from the home page): "An idea is rationally adoptable only if it has no pending criticisms. A criticism is pending if it has no pending criticisms of its own." Idea #5748 states it as a decision rule: "It's rational to adopt only those ideas which, as written, don't have pending criticisms, and to reject ideas that do." Idea #5114 gives the recursion in code: `should_adopt?(idea)` is true when `pending_criticisms(idea)` is empty, and `pending_criticisms` filters the idea's criticisms by `should_adopt?`. #5666: pending criticism and rational adoptability "are defined in terms of each other" and are "a package deal". Hackethal's paper abstract states the same as a theorem over a DAG: an idea is rationally preferable iff it has no pending criticisms, and the procedure is computable.

Statuses and labels.
- No criticisms: tentatively **unproblematic** ("Rationally adoptable"). Unaddressed criticism: **problematic** ("Not rationally adoptable"). "Veritula automatically marks ideas accordingly." Chronicles count "17 adoptable, 4 rejectable".
- **Battle-tested** (#1948): "It means that the idea has at least three criticisms, all of which have been addressed." "The label is _not_ an indicator of an idea's future success." When two conflicting ideas both have no pending criticisms, prefer the more battle-tested one (Popper's "critical preference").
- Veritula does not implement hard-to-vary: "Veritula does not implement Deutsch's epistemology of good vs bad explanations" (#3020); it implements Popper plus "unanimous consent", meaning an idea is accepted when nobody has a standing objection.

Addressing a criticism (three moves, from the how-it-works page).
1. **Revise**: "If you agree, you can revise the idea so the criticism no longer applies." The revision is a new version (I -> I2), numbered "Nth of N versions", labelled "revised after [user]'s criticism"; old versions remain, and a criticism may be marked "Criticism of an earlier version". Guidance: "put it in a revision, not a comment"; "Comments are for commentary or criticism, not elaborations on the same idea." A partly criticised idea "should be revised to adjust or exclude the criticized parts" (#2220).
2. **Counter-criticise**: "If you disagree, you submit a criticism of the criticism", "thus addressing them and restoring the idea" (#2221). An unanswered counter-criticism makes the criticism "non-decisive" and the idea regains its status. "A problematic counter-criticism cannot neutralize."
3. **Accept defeat**: "If neither is possible, the idea is '(tentatively) defeated' and should be abandoned."

**Supersedes**: when revising a criticism, "Supersedes previous version?" neutralises the older version "so it isn't counted twice"; checked by default for criticisms.

What counts as a criticism. There is no formal validity test; validity is itself settled by criticism. Rules of thumb on the site: criticisms "should be addressed, not dismissed" (#2063); "if a criticism is weak, its author should explain why in a counter-criticism" (#2065); counter-criticisms are needed before judging a criticism non-decisive (#3030, #4713); "An inability to improve on the criticized aspect of a system is not a refutation of that criticism" (#5086, the boat-with-a-hole: fix every hole "no matter their size, or choose a different boat"); arguments from incredulity do not count ([Can You Refute This?](https://blog.dennishackethal.com/posts/can-you-refute-this)). Severity cannot be computed: nobody knows how to determine "the severity or decisiveness of a criticism" programmatically (#4711), so severity claims are themselves ideas open to criticism.

Standing objections to the rule itself (pending on #5748): Davies, "By this criterion, it is irrational to adopt or advocate either GR or QM, as they have pending criticisms", and "This seems to create a rewording loophole" (narrow the idea until no criticism applies). Hackethal's answer is to adopt a bounded restatement that has no pending criticism; Davies replies that "a wrapper doesn't immunize X from its own flaws".

Bounties: "Submit a direct criticism that meets the terms ... If the owner doesn't reject it and it has no pending criticisms by the deadline, you get paid." Two standing bounties pay USD 50 per eligible criticism of "autopair.js is bug-free" and "Hiccdown is bug-free", a direct demonstration of the method applied to software.

## 4. Hard to vary: Deutsch's criterion and Hackethal's critique

Deutsch's position. BoI makes hard-to-vary the mark of a good explanation: an explanation whose details could be changed freely without loss (Persephone's myth) explains nothing, while axial tilt resists change and yields risky predictions (Farley's seasons example, [Farley](https://www.davefarley.net/?p=278)). In the testability paper (arXiv 2015) Deutsch states it as clause (iii) of badness, "it could easily be adapted to account for anything (so it explains nothing)", alongside (i) failing to account for its explicanda and (ii) conflicting with otherwise good explanations. He explicitly refuses to make it a score: "'truer' and 'better' are both only partial orderings", and methodology "does not (nor could it validly) provide criteria for accepting a theory" ([arXiv](https://arxiv.org/abs/1508.02048)).

Hackethal's critique ([blog](https://blog.dennishackethal.com/posts/hard-to-vary-or-hardly-usable); the Conjecture Con 2026 talk of the same title has no published transcript, abstract or slides that I could find; the YouTube page was not fetched; the essay and its [bounty](https://veritula.com/bounties/8) are the written record). The claims:
- The criterion is "fatally underspecified and impossible to apply"; by Deutsch's own test, "if you can't program it, you haven't understood it", Deutsch "hasn't understood his epistemology".
- Scores reintroduce arbitrariness ("Why not ±10,000?") and propagate oddly through nested criticisms; they "effectively weigh explanations", which enables self-coercion: a 500-point explanation can be adopted despite a pending 100-point criticism.
- Hardness to vary "is at least partly a property not of the explanation itself but of whoever is trying to change it".
- Scientists do not rank theories this way; they "have no rigorous way of knowing how good their explanations are", and would use HTV "as only one mode of criticism among many".
- The replacement: "It is rational to adopt only those ideas which, as written, don't have pending criticisms, and to reject ideas that do." "The increasing quality of an explanation is an _effect_ of critical activity, not its _means_." The progress-enabling ingredient is "not to ignore any criticisms".
- 2026 update: the bounty's counting-variants proposal (rank by "lower count = better" of working variants) "does seem to partially rehabilitate HTV specifically with regard to its previous computational underspecification", but "HTV is still not a description of rationality or of the cause of progress". The bounty closed with zero accepted submissions (the one submission was ruled AI-written). Hackethal also notes he prefers "hard to change" to "hard to vary" (fn 1).

Fair reading of the disagreement. Deutsch never proposed HTV as a scoring function; his own list makes it one of three badness criteria and his ordering is partial. Hackethal's target is the use of HTV as the operative decision rule, which many readers of BoI adopt. On the counting-variants proposal, both sides agree the "variants" are supplied by the user's judgement, so the program formalises bookkeeping, not the judgement. Where they genuinely differ: Deutsch's refutation requires a good rival ("in the absence of a good rival explanation, an explanatory theory cannot be refuted by experiment: at most it can be made problematic"), while Veritula's rule rejects any idea with a pending criticism whether or not a rival exists; Davies's GR/QM objection lives in exactly that gap.

Consequence for this method. The operative test is "no pending criticism" (unrefuted after criticism), because it is checkable and recordable. "Hard to vary" is kept as a named kind of criticism one can raise against an explanation ("this explanation could be adapted to fit any symptom"), not as a score and not as the adoption test. In the trigger table it is a secondary word; "pending criticism" and "attempted refutation" are primary.

## 5. How each concept applies to building and verifying software with agents

- A test is an attempted refutation of a specific conjecture about the code. Write it as "if the code is right, this cannot happen" and run it to see it fail first: Farley's "you get to see it fail, so you are testing the test" ([TDD](https://www.davefarley.net/?p=180)). A test that cannot fail is not a test.
- A green suite means "unrefuted so far", never "verified". Popper's "corroborated (but never verified)" is the exact register. Report it that way: "all 412 attempted refutations failed to refute".
- A design decision (ADR) is a conjecture and carries a pre-registered refutation criterion: the observable result that would make it problematic. Deutsch's definition of expectation applies directly: "if the experiment were performed and did not result in x, T would become (more) problematic".
- A review is criticism, and must itself be answerable. Record each review point as a separate criticism (Veritula: one criticism per submission, "formulated as concisely as possible"), and answer each by revision, counter-criticism, or acceptance. "Addressed, not dismissed."
- An unanswered criticism blocks. The merge rule is Veritula's adoption rule: a change is adoptable iff no criticism of it is pending. Severity is not scored; if the author thinks a criticism is tiny, the move is a counter-criticism saying why, which the critic can answer.
- "Nothing found" is not evidence of absence unless the search was severe. A grep that returned nothing refutes only the conjecture that the literal string exists. State what was searched and what a hit would have looked like; otherwise the finding is an untested conjecture.
- A bug fix is a conjectured explanation of the symptom. Prefer the explanation that is hard to vary: it names the mechanism and predicts a reproduction that would fail without the fix. Hackethal's practice: seek observations "so I could still reproduce the bug" and falsify the fix, rather than collecting runs where it did not recur ([Choosing between Theories](https://blog.dennishackethal.com/posts/choosing-between-theories)). "Flaky", "timing", "cache" with no mechanism are explanations that fit any symptom and so explain nothing (Deutsch's clause iii).
- Refutation needs a rival. When one explanation of a failure is problematic but no rival is on the table, Deutsch says it is problematic, not refuted. The move is to conjecture a rival, then design the crucial test "such that every possible result will make all but one of those theories problematic".
- Do not weigh evidence; eliminate rivals. List the candidate causes or designs, then find for each the criticism that kills it. What survives is adopted, not because it scored highest but because nothing stands against it. If several survive, prefer the most battle-tested or say the choice is open.
- Keep a record of criticisms and their fate. Every criticism, its answer (revision, counter-criticism, defeat), and the version of the idea it applied to. This is the Veritula structure and it is what makes "unrefuted" an auditable claim instead of a feeling.
- Make errors cheap to find and fix. Farley: "find the problem, and fix it quickly"; "optimise to be great at learning" ([Farley](https://www.davefarley.net/?p=352)). Small reversible changes, fast feedback, and (Hackethal) prevention of known errors: a criticism that was upheld once stays upheld; "it's different this time" needs a new argument that answers the old criticism.
- The inductivist trap in software: "it passed N times", "it has worked in prod for a year", "I ran it and it looked fine" are instance counts, not refutations. Replace example-based assurance with laws: a property test states the universal and lets the machine search for the counter-example. Repetition without an explanation of why it must hold is Hackethal's beads.
- Replace confidence language with the criticism that would change the conclusion. "I'm 90% sure" is a credence; the critical-rationalist statement is "this holds unless X; I checked X by Y". Deutsch: methodology "is not about anyone's beliefs or disbeliefs".
- Explanationless prediction is not allowed: a claim "this will scale" or "this is safe" must say which variables would confound the test; "strictly explanationless predictions cannot exist".
- Optimism, correctly read: every bug has an explanation that can be found ("problems are soluble"), so "cannot reproduce" is a problem statement, not a conclusion. It does not license retrying the same action; the Principle of Optimism "is a way of explaining failure".
- Executable ideas: "If you really want to battle-test an idea (and your understanding of it), code it up" ([Hackethal](https://blog.dennishackethal.com/posts/executable-ideas)). A design that cannot be expressed as a runnable check is underspecified in exactly the sense Hackethal charges HTV with.
- Honesty is the engine: "not to ignore any criticisms". Crypto-fallibilism (saying "I might be wrong" while deflecting every specific criticism) is the failure mode to watch for in an agent's self-review.

## 6. Candidate agent directives

Each is positive, concept-tagged, and intended to change default behaviour.

1. State every claim about the code as a conjecture with the observation that would refute it, before running anything. [conjecture, pre-registered refutation]
2. Write the test as an attempted refutation and watch it fail before making it pass; treat a test that was never red as untested. [tests as attempted refutations; Farley]
3. Report a passing suite as "unrefuted by N attempted refutations", and name the classes of input those attempts did not cover. [refutation vs confirmation]
4. When you believe something works because it worked before, write the universal law it relies on and encode it as a property test. [problem of induction]
5. Explain a failure by naming the mechanism and a reproduction that fails without the fix; reject an explanation that would fit any symptom. [hard to vary, as a criticism]
6. Before fixing, conjecture at least one rival explanation and design the check that makes all but one problematic. [crucial test; refutation needs a rival]
7. Decide between designs by eliminating: for each candidate, find the criticism that kills it, and adopt what survives. [eliminate rivals, not weigh]
8. Record every criticism separately with its target version, and answer each by revision, counter-criticism, or explicit defeat. [Veritula: pending criticism, revise]
9. Treat a criticism with no answer as blocking; a counter-criticism must itself be criticisable and stated in substance. [standing criticism, neutralise]
10. When you judge a criticism trivial, say why in a counter-criticism the critic can answer, rather than lowering its weight. [severity is itself a conjecture]
11. Prefer, among surviving options, the one that has withstood the most criticisms, and say so when the choice is otherwise open. [battle-tested, critical preference]
12. Replace "I'm fairly confident" with "this holds unless X; I checked X by Y". [avoiding Bayesianism]
13. When a search finds nothing, state what a hit would have looked like and how severe the search was before concluding absence. [severe test]
14. When you cannot reproduce a bug, write the problem statement and the next attempted refutation; "cannot reproduce" closes nothing. [problems are soluble]
15. After a refutation, change the conjecture, not the test; when you must change the test, say what criticism of the test justifies it. [error correction; no immunising]
16. Revive a previously refuted approach only with an argument that answers the recorded criticism; "it's different this time" is not an answer. [error prevention]
17. Make every change small and reversible so a refutation is cheap to act on. [error correction, Farley]
18. Keep refutations auditable: the record must let a reader see which criticisms stand and which were answered, without the author's reasoning. [tradition of criticism]
19. Treat the method itself as criticisable: when a rule blocks you, write the criticism of the rule rather than route around it. [pancritical rationalism]
20. Treat "the reviewer approved it" and "the docs say so" as criticisms not yet raised, not as justification. [authority and justificationism]
21. When two surviving explanations conflict, say that at least one is false and keep both as problematic rather than merging them. [fallibilism; Deutsch on QM and GR]
22. Prefer an explanation that predicts something risky over one that merely accords with what you already saw. [corroboration via risky prediction]
23. Express a design as a runnable check; if it cannot be run, say which part is underspecified. [executable ideas]
24. Treat "nearer to the truth" as a direction, and never report a numeric closeness-to-correct. [verisimilitude]
25. Expect your own best fix to be wrong somewhere and name where you would look first. [fallibilism, honesty]

## 7. Leading words and trigger phrases

| Term | Concept | Gloss | Whose wording |
| --- | --- | --- | --- |
| conjecture | conjecture | a proposed solution held tentatively | Popper |
| attempted refutation | tests | an experiment whose result could make the theory problematic | Popper; Deutsch's definition |
| unrefuted (so far) | status | survived all attempted refutations to date | Popper |
| corroborated, never verified | refutation vs confirmation | passed risky tests; not thereby true | Popper (SEP) |
| risky prediction | severity | a prediction that could easily have been false | Popper |
| severe test | severity | a test with a real chance of refuting | Popper |
| crucial test | rivals | result makes all but one rival problematic | Popper; Deutsch |
| problematic | status | has an apparent flaw; not yet refuted | Deutsch |
| refuted (tentatively) | status | problematic and a good rival exists | Deutsch |
| explicandum | explanation | the thing the theory is meant to account for | Deutsch |
| good explanation | explanation | accounts for its explicanda, no conflict, hard to vary | Deutsch |
| hard to vary | explanation (secondary trigger) | cannot be changed without losing its explanatory fit | Deutsch; Hackethal prefers "hard to change" |
| easily adapted to account for anything | bad explanation | explains nothing | Deutsch (clause iii) |
| explanationless prediction | explanation vs prediction | a prediction with no account of its confounders; cannot exist | Deutsch |
| rival explanation | elimination | a competing account of the same explicanda | Deutsch |
| pending criticism | Veritula | a criticism with no pending criticism of its own | Hackethal (primary trigger) |
| standing criticism | Veritula | same, in plain words; "outstanding criticisms" on the site | Hackethal (#1903) |
| rationally adoptable | Veritula | has no pending criticisms | Hackethal |
| addressed | Veritula | answered by revision or counter-criticism | Hackethal |
| counter-criticism | Veritula | a criticism of a criticism | Hackethal |
| neutralise | Veritula | make a criticism non-decisive via an unproblematic counter-criticism | Hackethal |
| revise, not comment | Veritula | put the fix in a new version of the idea | Hackethal |
| accept defeat | Veritula | abandon the idea as tentatively defeated | Hackethal |
| battle-tested | Veritula | three or more criticisms, all addressed | Hackethal |
| unanimous consent | Veritula | adopt only what nobody has a standing objection to | Hackethal (from TCS) |
| criticize, don't defend or attack | criticism | the stance in a critical discussion | de Wit, quoted on Veritula |
| eliminate, don't elevate | decision | reject one rival rather than praise the other | Hackethal |
| breaking the symmetry | decision | "X contradicts Y" cuts both ways; find the criticism that settles it | Temple, via Hackethal |
| justificationism | authority | the demand that beliefs be supported before held | Bartley; Popper |
| renunciation of authority | authority | no confirmation, credence or foundation is needed | Deutsch (sec. 4 title) |
| pancritical | criticism of method | every position, including this one, is open to criticism | Bartley |
| credence | Bayesianism (to avoid) | degree of belief obeying the probability calculus | Deutsch's definition |
| explanatory power, not credence | Bayesianism alternative | what science creates | Deutsch |
| problem of induction | induction | no set of instances establishes a universal | Hume; Popper |
| inductivist trap | induction | treating "it worked N times" as a reason | this document's coinage; not in the sources |
| error correction | progress | locate and solve problems; reversible changes | Popper; Deutsch; Farley |
| error prevention | progress | do not repeat a known, recorded error | Hackethal |
| problems are soluble / inevitable | optimism | any non-physics-violating problem can be solved; new ones always arise | Deutsch |
| Principle of Optimism | optimism | all evils are caused by insufficient knowledge | Deutsch |
| fallible | fallibilism | may be wrong, not certain; still may be true | Popper |
| crypto-fallibilism | fallibilism | professing fallibility while deflecting criticism | Hackethal |
| verisimilitude | truth | closeness to truth; formally undefined for false theories | Popper; refuted by Tichý and Miller |
| falsification mechanism | tests | what an automated test suite is for | Farley |
| best current theory | code | what the code base is | Farley |
| optimise for learning | process | the aim of an engineering process | Farley |
| if you can't program it, you haven't understood it | executable ideas | underspecification test | Deutsch, quoted by Hackethal |
| pre-registered | tests | the refutation criterion stated before the run | not in these sources; experimental-science usage |

## 8. What does not transfer, and where to be careful

- The Principle of Optimism is not "try again". It says the failure has an explanation that knowledge can reach; it says nothing about retrying the same action. An agent that loops on a red test is practising blind optimism, which Deutsch separately names and rejects.
- Fallibilism is not refusing to conclude. Hackethal's cynics are the failure mode: "every idea has errors" lets one never adopt anything. The rule is: adopt what has no pending criticism, and say what would change your mind.
- Refusing credences is not refusing to state uncertainty. The substitute is explicit: name the unchecked condition and the test that would check it. An agent forbidden from saying "probably" must not therefore say "certainly".
- Criticisms are not free and not equal in cost, even though Veritula scores none of them. Veritula itself admits flooding and sabotage are unsolved. A method for agents needs a budget and an owner who can declare a criticism out of scope, and that declaration must itself be a recorded, criticisable idea.
- Veritula's "reject anything with a pending criticism" is stricter than Deutsch's "problematic but not refuted without a rival". Davies's GR/QM objection stands on Veritula; for software, the practical reading is: a pending criticism blocks adoption of the change, but does not by itself oblige reverting a running system that has no rival.
- The "rewording loophole": narrowing a claim until no criticism applies is allowed by the letter of the rule. Require that the narrowed claim still solves the original problem.
- Refutation is tentative too (Duhem, Lakatos): a red test may refute the test, the fixture, or the environment. Record which conjecture the red refutes before acting.
- Hard to vary is not a score. Both Deutsch (partial order) and Hackethal (underspecified; "one kind of criticism among many") reject numeric ranking. Use it as a named criticism; use "pending criticism" as the gate.
- Pancritical rationalism applied to the method has a limit: some rules (guards, safety constraints) are deliberately exempt from the agent's unilateral revision. Keep them criticisable in writing, not in action; the owner decides.
- "Verified" and "proved" survive in formal methods with a precise meaning relative to a model. Keep that meaning, and attach the caveat that the model is a conjecture.
- Farley's "optimise for learning" can be read as licence for endless experiments. The critical-rationalist reading is narrower: each experiment is an attempted refutation of a stated conjecture.
