// The owner's words for the route's model, shared by the four charts of docs/model/route.md.

export const NEXT = {
  init: "Run /ratchet:init",
  tracker: "Set up the tracker",
  adopt: "Run /ratchet:adopt",
  hooks: "Install the commit hooks",
  grill: "Write the glossary and design brief",
  specify: "Write the next spec",
  "conjecture-spec": "State the spec's conjecture",
  split: "Split the spec into tickets",
  triage: "Agree a ticket for agents",
  conjecture: "State the ticket's conjecture",
  build: "Build the ticket",
  drain: "Build every ready ticket at once",
  building: "The ticket is built on its branch",
  blocked: "Finish the blocking ticket first",
  "blocked-run": "Answer what the blocked run asked",
  answer: "Answer the questions the drain parked",
  "land-guard": "Land the guard edits the drain parked",
  "change-ticket": "Change the ticket the drain parked",
  safeguard: "Bind the safeguard the drain waits on",
  fill: "Fill the rows the upgrade left",
  review: "Review the architecture",
  architecture: "Record the architecture",
  resolve: "Mark the merged ticket resolved",
};

export const checkout = (v) => {
  if (v.head === "records") return "A landing run stopped part-way";
  if (v.parked) return "A records branch waits for your merge";
  if (v.head === "work") return v.tree === "code" ? "Code uncommitted on a branch" : "A clean branch to merge";
  if (v.tree === "records") return "Records uncommitted";
  if (v.tree === "code") return "Code uncommitted on the default branch";
  return "Nothing unfinished";
};

const STEP = {
  OwnerInits: "you run /ratchet:init",
  InitStops: "init stops before its questions",
  OwnerSetsUpTracker: "you set up the tracker",
  OwnerAdopts: "you run /ratchet:adopt",
  OlderPluginAdopts: "an older plugin's adoption",
  AdoptionStops: "the adoption stops part-way",
  AgentInstallsHooks: "an agent installs the hooks",
  OwnerGrills: "you write the glossary and brief",
  OwnerWritesSpec: "you write a spec",
  OwnerStatesSpecConjecture: "you state the spec's conjecture",
  OwnerSplitsSpec: "you split the spec",
  OwnerAgreesTicket: "you agree a ticket",
  OwnerWorksTicketThemself: "you finish a ticket yourself",
  OwnerStatesConjecture: "you state the ticket's conjecture",
  AgentBuildsTicket: "an agent builds the ticket",
  BuildStops: "the build stops part-way",
  FeatureRunBlocks: "the build run blocks",
  OwnerAnswersBlockedRun: "you answer the blocked run",
  DrainMergesTicket: "the drain builds and merges a ticket",
  DrainParksTicket: "the drain parks a ticket",
  DrainParksForASafeguard: "the drain merges one and parks the next for a safeguard",
  OwnerAnswersOnTheTicket: "you answer on the ticket",
  OwnerLandsGuardEdits: "you land the guard edits",
  OwnerChangesTicket: "you change the ticket",
  OwnerResolvesTicket: "you mark the ticket resolved",
  UpgradeLeavesRowsToFill: "an upgrade leaves rows for an agent to fill",
  AgentFillsRows: "an agent fills the rows",
  KindOfCodeArrives: "a new kind of code arrives",
  KindThatReopensTheArchitectureArrives: "a kind that reopens the architecture arrives",
  AgentLandsTheReview: "an agent lands the architecture review",
  AgentBindsSafeguard: "an agent binds the safeguard",
  OwnerRecordsArchitecture: "you run /ratchet:architecture",
  SomeoneEditsCode: "someone edits code",
  AgentCommitsOnABranch: "an agent commits on a branch",
  BranchMerges: "the branch merges",
  AgentLandsRecords: "an agent lands the records",
  RecordsRunStops: "the landing run stops",
  OwnerMergesRecords: "you merge the records branch",
};
export const step_ = (action) => STEP[action];
