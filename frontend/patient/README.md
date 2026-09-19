# Patient dashboard

Light React + Vite app: prescription history with PDF downloads, upcoming reminders,
and "ask about my history". Login is the Cognito **patient** pool (phone + password);
the API resolves the patient from the token, so this app never sends a patient id.

```
npm install
npm run dev      # http://localhost:5174
```

Set `VITE_API_BASE` and `VITE_COGNITO_CLIENT_ID` (PatientUserPoolClientId) in `.env`.
