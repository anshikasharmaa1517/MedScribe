# frontend

Not built yet. Planned: React + Vite, deployed to Amplify Hosting.

- Doctor dashboard — live transcript pane, draft prescription with green/amber/red
  medicine chips (`AUTO` / `CONFIRM` / `RESOLVE`), flag resolution, approve action.
  A `RESOLVE` item must block the approve button.
- Patient dashboard — light read-only view of prescriptions and history.

`src/types/draft.ts` must mirror the draft-state contract in
`docs/CLAUDE_CODE_CONTEXT.md` §6.
