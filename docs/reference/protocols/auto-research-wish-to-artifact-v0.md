# Auto Research Wish-to-Artifact v0

Auto Research accepts an open question today and can already produce
hypotheses, dev and held-out evidence, terminal decisions, independent review,
and Explore findings. This protocol closes the delivery contract around those
existing records. It does not add another scheduler, research store, or
capability.

## Contract

`auto_research_delivery_contract_v0` wraps the existing
`research_contract_v0` with:

- one original public-safe wish and a stable `wish_id`;
- assumptions and non-goals;
- required artifact declarations;
- acceptance criteria bound to exact hypothesis ids;
- fallback artifacts and reentry conditions for unsuccessful outcomes.

Normalization derives `contract_revision` from the complete normalized
contract. Recording the contract does not imply user acceptance. Evidence
produced from the delivery contract carries the atomic lineage tuple
(`wish_id`, `contract_ref`, `contract_revision`). All three fields must be
present and match exactly; partial or mismatched lineage is excluded from
verification. A compact contract event is
appended once per wish and revision. Legacy `research_contract_v0` packets
retain their prior event shape and remain supported.

## Receipt

```bash
loopx auto-research artifact-receipt \
  --contract delivery-contract.public.json \
  --format json
```

The command is read-only. It folds the current contract revision, Auto Research
evidence graph, terminal decisions, independent reviews, and artifact
references into `auto_research_artifact_receipt_v0`.

The aggregate status is one of:

| Status | Meaning |
| --- | --- |
| `verified` | Every required criterion has a current promoted decision, the required review, and every declared artifact. |
| `partial` | Some criterion or artifact is verified, but the complete delivery contract is not satisfied. |
| `inconclusive` | Evidence, a terminal decision, or a required independent review is still missing or conflicting. |
| `not_fulfilled` | Current evidence supports a terminal retired decision for at least one required criterion, with any required independent review. |
| `stale` | The recorded evidence or terminal decision belongs to an older contract revision. |

`not_fulfilled` is intentionally stronger than an unsuccessful attempt. One
failed command, one regressed dev result, or an unresolved retry does not prove
that the wish cannot be fulfilled.

## Failure Feedback

Every non-verified receipt includes:

- `failure_kinds`: typed reasons derived from current evidence, decision, and
  review state;
- `unmet_criteria`: required contract criteria not satisfied;
- `verified_boundary`: criteria that remain verified;
- `missing_required_artifact_refs`: declared required artifacts not observed;
- `fallback_artifact_refs`: declared fallback artifacts that were actually
  observed;
- `reentry_conditions`: owner-authored conditions plus precise criterion-level
  repair hints.

The receipt does not infer user acceptance. It also does not install or promote
a Skill, mutate an extension, spend quota, or write project state. A
`learning_disposition=candidate` only means that a verified or terminally
not-fulfilled outcome is eligible for a later governed experience review.

## Staleness

A terminal decision remains bound to the hypothesis evidence revision. The
hypothesis and evidence now also carry the delivery `contract_revision`.
Changing the wish assumptions, required artifacts, acceptance criteria, or
failure policy creates a new contract revision. A receipt for that new revision
must not reuse the old revision's evidence as current delivery proof.

## Public Boundary

The delivery contract and receipt use public-safe text and opaque or relative
artifact references. They do not contain raw logs, raw trajectories,
credentials, private material, or absolute local paths. Environment-specific
replay state remains adapter-owned; only bounded public evidence references may
enter this contract.
