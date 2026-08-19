'use client';

import { useState, useRef, useEffect, useCallback } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useCompanions, useCreateCompanion } from '@/hooks/useCompanions';
import { useSelectedCompanion } from '@/components/providers';
import Icon from '@/components/ui/icon';

export default function CompanionDropdown() {
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { data: companions } = useCompanions();
  const { selectedCompanionId, setSelectedCompanionId } = useSelectedCompanion();
  const createCompanionMutation = useCreateCompanion();

  const selectedCompanion = companions?.find(c => c.id === selectedCompanionId);

  // Close dropdown on outside click
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }
    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [isOpen]);

  const setCompanionInUrl = useCallback((companionId: string) => {
    const params = new URLSearchParams(searchParams.toString());
    params.set('companion', companionId);
    const query = params.toString();
    router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
  }, [searchParams, pathname, router]);

  // Keep URL in sync when companion selection changes elsewhere in the app.
  useEffect(() => {
    if (!selectedCompanionId) return;
    if (searchParams.get('companion') === selectedCompanionId) return;
    setCompanionInUrl(selectedCompanionId);
  }, [selectedCompanionId, searchParams, setCompanionInUrl]);

  const handleCreateBlank = async () => {
    try {
      const uuid = crypto.randomUUID();
      const companionName = uuid.substring(0, 8);
      const createdCompanion = await createCompanionMutation.mutateAsync({
        name: companionName,
        description: `AI companion ${companionName}`,
        config: {
          system_prompt: {
            full_system_prompt: 'You are a helpful and friendly AI companion. Keep your responses conversational and engaging.',
            identity: 'AI Companion',
            personality: 'Helpful and friendly',
            style: 'Conversational',
            backstory: '',
            additional_instructions: '',
            self_image: 'I am a helpful AI assistant'
          },
          memory: {
            enabled: false,
            core_memories: [],
            memory_evaluation_prompt: '',
            recency: 0.995,
            top_k: 50,
            min_saliency: 0.2
          },
          voice: {
            preset: 'gpt4o-mini-elevenlabs',
            voice_name: 'Alloy',
          },
          inference: {
            model: 'openai-gpt4o-mini',
            temperature: 0.7,
            max_output_tokens: null,
          }
        }
      });
      const createdCompanionId =
        typeof createdCompanion === 'object' && createdCompanion
          ? (createdCompanion as { id?: string }).id
          : undefined;
      if (createdCompanionId) {
        setSelectedCompanionId(createdCompanionId);
        setCompanionInUrl(createdCompanionId);
      }
      setIsOpen(false);
    } catch (error) {
      console.error('Failed to create companion:', error);
      alert('Failed to create companion. Please try again.');
    }
  };

  const handleCreateGuided = () => {
    setIsOpen(false);
    router.push('/onboarding');
  };

  const handleSelectCompanion = (id: string) => {
    setSelectedCompanionId(id);
    setCompanionInUrl(id);
    setIsOpen(false);
  };

  return (
    <div ref={dropdownRef} className="relative">
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center justify-between gap-3 px-4 py-2.5 bg-[var(--color-dropdown-bg)] text-white hover:text-white/80 transition-colors min-w-[200px]"
      >
        <span className="text-base font-medium">
          {selectedCompanion?.name || 'Select Companion'}
        </span>
        <Icon
          name={isOpen ? 'chevron-up' : 'chevron-down'}
          size={16}
          className="text-white/60"
        />
      </button>

      {isOpen && (
        <div className="absolute top-full left-0 mt-2 w-64 bg-[var(--color-dropdown-bg)] border border-white/10 shadow-xl z-50 py-2">
          {/* Scrollable companions list */}
          <div className="max-h-60 overflow-y-auto">
            {companions && companions.length > 0 ? (
              <>
                {companions.map((companion) => (
                  <button
                    type="button"
                    key={companion.id}
                    onClick={() => handleSelectCompanion(companion.id)}
                    className={`w-full text-left px-4 py-2.5 text-base transition-colors ${
                      companion.id === selectedCompanionId
                        ? 'text-white'
                        : 'text-[var(--color-option-text)] hover:text-white'
                    }`}
                  >
                    {companion.name}
                  </button>
                ))}
              </>
            ) : (
              <div className="px-4 py-2.5 text-base text-[var(--color-option-text)]">No companions yet</div>
            )}
          </div>

          {/* Always visible create options */}
          <div className="border-t border-white/10 mt-2 pt-2">
            <button
              type="button"
              onClick={handleCreateGuided}
              disabled={createCompanionMutation.isPending}
              className="w-full text-left px-4 py-2.5 text-base text-[var(--color-link-text)] hover:text-white transition-colors disabled:opacity-50"
            >
              Create New – Guided
            </button>
            <button
              type="button"
              onClick={handleCreateBlank}
              disabled={createCompanionMutation.isPending}
              className="w-full text-left px-4 py-2.5 text-base text-[var(--color-link-text)] hover:text-white transition-colors disabled:opacity-50"
            >
              {createCompanionMutation.isPending ? 'Creating...' : 'Create New – Blank'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
