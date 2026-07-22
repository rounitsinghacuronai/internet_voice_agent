import { Sidebar } from "@/components/layout/sidebar";
import { Topbar } from "@/components/layout/topbar";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AuthGuard } from "@/components/auth/auth-guard";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthGuard>
      <TooltipProvider delayDuration={200}>
        <div className="flex h-screen overflow-hidden bg-background">
          <div className="hidden shrink-0 lg:block">
            <Sidebar />
          </div>
          <div className="flex min-w-0 flex-1 flex-col">
            <Topbar />
            <main className="flex-1 overflow-y-auto">
              <div className="mx-auto max-w-[1600px] animate-fade-in p-4 sm:p-6">{children}</div>
            </main>
          </div>
        </div>
      </TooltipProvider>
    </AuthGuard>
  );
}
