import { useState, type FormEvent } from "react";
import { Navigate } from "react-router-dom";
import { Button, ErrorNote, Field, Input } from "../components/ui";
import { useAuth } from "../lib/auth";

export function Login() {
  const { token, signIn } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  if (token) return <Navigate to="/leads" replace />;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signIn(email, password);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-full items-center justify-center px-4 py-12">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex items-end gap-1.5" aria-hidden>
          <span className="h-7 w-3 rounded-sm bg-cold" />
          <span className="h-10 w-3 rounded-sm bg-warm" />
          <span className="h-14 w-3 rounded-sm bg-hot" />
        </div>
        <h1 className="wide text-3xl font-black">Lead desk</h1>
        <p className="mt-2 text-ink-2">Sign in to see who is waiting for a reply.</p>
        <form onSubmit={submit} className="mt-8 flex flex-col gap-4">
          <Field label="Email" htmlFor="email">
            <Input id="email" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
          </Field>
          <Field label="Password" htmlFor="password">
            <Input
              id="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </Field>
          {error ? <ErrorNote error={error} /> : null}
          <Button type="submit" tone="primary" busy={busy} className="h-10">
            Sign in
          </Button>
        </form>
        <p className="mt-6 text-xs leading-relaxed text-ink-3">
          No account yet? Create one on the server with <code>python -m scripts.create_user</code>.
        </p>
      </div>
    </div>
  );
}
