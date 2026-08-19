import CompanionControls from '@/components/dashboard/companion-controls';
import CompanionSimulator from '@/components/dashboard/companion-simulator';
import FirstTimeUserHandler from '@/components/onboarding/first-time-user-handler';

export default async function Home() {
 return (
    <FirstTimeUserHandler enableOnboarding={true}>
        <div className="grid h-full min-h-0 grid-cols-[3fr_4fr]">
          <div className="min-h-0 border-r border-white/20 bg-[var(--color-panel-bg)] px-4 py-4">
            <CompanionControls />
          </div>
          <div className="min-h-0 bg-[var(--color-conversation-bg)]">
            <CompanionSimulator />
          </div>
        </div>
    </FirstTimeUserHandler>
  );
}
