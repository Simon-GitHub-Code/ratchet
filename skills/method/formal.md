# Formal specs

A state machine whose rules are cheap to get subtly wrong and expensive to discover late gets a formal specification before it is coded. The specs are the design; code is linked to them by convention and checked by fitness tests, not generated from them. Changing the behaviour means changing the spec first and keeping the model checker green.

## Writing one

`formal/specs/<Name>` in the notation the binding names, with its model configuration beside it. The spec opens by naming the ADRs it formalises and contains constants, variables, a type invariant, the initial state, one named action per transition, the next-state relation, fairness, then three groups: safety invariants, action properties, liveness properties. The configuration is a small finite model whose bounds are justified in a comment; deadlock checking is switched off only where a liveness property covers progress, and the comment says so.

## Linking code to it

- The implementing module declares `SPEC = "<Name>"` and a function that maps its state to exactly the spec's variables.
- It ships a model test, `tests/model/test_<name>`: a stateful property test with one rule per action in the next-state relation, one invariant per spec invariant and action property, and a constant naming the liveness properties the model checker proved.
- Fitness tests check that a declared spec exists, that its model test exists and names it, and that the mirroring is one to one: variables, actions, invariants. The reader of the spec notation is a small dependency-free parser of a subset that fails loudly outside it, so tier 1 needs no checker runtime.

## Checking it

- Tier 2 runs the parser and the model checker over every spec (`just formal`), reading the checker's output as well as its exit code, since some versions exit 0 on semantic errors. The checker binary is pinned by version and checksum.
- Tier 2 replays recorded traces from the model tests through the checker (`just traces`): the first state satisfies the initial predicate and every step satisfies the next-state relation or stutters. The trace validator is itself tested against a reference implementation it must accept and a deliberately broken one it must reject.
