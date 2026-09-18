# Doctor dashboard

React + Vite. Runs against the in-browser mock by default; set `VITE_API_BASE`
(and `VITE_ID_TOKEN`) in `.env` to hit the deployed API — see `.env.example` and
`docs/API_CONTRACT.md`.

```
npm install
npm run dev      # http://localhost:5173
npm run build
```

Transcript source is scripted (`src/stt.ts`); the Sarvam mic client plugs into
`sarvamSource()` there.
