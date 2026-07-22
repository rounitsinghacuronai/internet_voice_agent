"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2, Lock, User } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { LogoMark } from "@/components/layout/logo";
import { useLogin } from "@/lib/hooks";
import { setToken } from "@/lib/auth";
import { config } from "@/lib/config";

export default function LoginPage() {
  const router = useRouter();
  const login = useLogin();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    login.mutate(
      { username, password },
      {
        onSuccess: (res) => { setToken(res.token); router.replace("/"); },
        onError: () => setError("Invalid username or password."),
      },
    );
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center gap-3 text-center">
          <div className="flex h-16 w-16 items-center justify-center overflow-hidden rounded-2xl bg-white p-1.5 shadow-sm">
            <LogoMark className="h-full w-full object-contain" />
          </div>
          <div>
            <h1 className="text-xl font-bold">{config.brand.name}</h1>
            <p className="text-sm text-muted-foreground">{config.brand.product}</p>
          </div>
        </div>

        <Card>
          <CardContent className="p-6">
            <form onSubmit={submit} className="space-y-4">
              <div>
                <label className="mb-1 block text-sm font-medium">Username</label>
                <div className="relative">
                  <User className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                  <Input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="admin" className="pl-9" autoFocus />
                </div>
              </div>
              <div>
                <label className="mb-1 block text-sm font-medium">Password</label>
                <div className="relative">
                  <Lock className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                  <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="••••••••" className="pl-9" />
                </div>
              </div>
              {error && <p className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">{error}</p>}
              <Button type="submit" className="w-full" disabled={login.isPending}>
                {login.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : null} Sign in
              </Button>
            </form>
          </CardContent>
        </Card>
        <p className="mt-4 text-center text-xs text-muted-foreground">Role-based access · sessions expire after 12 hours</p>
      </div>
    </div>
  );
}
